"""Server-side Industry tradeability fill: priority order, the background job
(checkpointing into the saved scan, cancellation, status cursor), the Python
port of the live-depth gate, and the parallel / per-entry-fresh ESI fetchers."""
import importlib.util
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ind_core
import lp_core
from lp_core import JITA_STATION_ID, JITA_REGION_ID, PRICE_CACHE_TTL, fetch_prices_esi

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lp_web_fill", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def _row(bp, pid, pat=None, inst=None, **kw):
    r = {"blueprint_id": bp, "product_id": pid, "out_qty": 1, "runs": 1,
         "isk_per_hour_patient": pat, "isk_per_hour_instant": inst,
         "profit_patient": pat, "profit_instant": inst,
         "profit_best": max(x for x in (pat, inst, -1e18) if x is not None),
         "margin_patient": 0.1 if pat else None, "margin_instant": 0.2 if inst else None,
         "isk_per_hour_best": max(x for x in (pat, inst, -1e18) if x is not None),
         "total_profit_instant": inst, "bid": 10.0 if inst else None,
         "payback_runs_instant": None, "favorite": False, "owned_bp_me_te": False,
         "daily_vol": None, "tradeability": None}
    r.update(kw)
    return r


def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Pilot", "scopes": [], "refresh_token": "x"}
    a.active_char_id = 1
    return a


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(lp_web, "IND_LAST_SCAN_PATH", tmp_path / "ind_last_scan.json")
    monkeypatch.setattr(lp_web, "_IND_FILL_JOBS", {})
    monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)
    lp_web._REQUEST.account = None
    yield
    lp_web._REQUEST.account = None


# ── priority order ───────────────────────────────────────────────────────────

class TestFillPriority:
    def test_interleaves_patient_and_instant_leaders(self):
        rows = [_row(1, 101, pat=900, inst=1), _row(2, 102, pat=800, inst=2),
                _row(3, 103, pat=1, inst=950), _row(4, 104, pat=2, inst=850)]
        # patient #1, instant #1, patient #2, instant #2
        assert ind_core.fill_priority(rows) == [101, 103, 102, 104]

    def test_skips_unprofitable_and_dedups_products(self):
        rows = [_row(1, 101, pat=500), _row(2, 101, pat=400),   # same product twice
                _row(3, 102, pat=-5, inst=-1), _row(4, 103, inst=100)]
        assert ind_core.fill_priority(rows) == [101, 103]

    def test_pinned_rows_follow_the_head_not_the_tail(self):
        rows = [_row(i, 100 + i, pat=1000 - i) for i in range(10)]
        rows.append(_row(99, 999, pat=1, favorite=True))
        order = ind_core.fill_priority(rows, head=3)
        assert order[:3] == [100, 101, 102]
        assert order[3] == 999


# ── live-depth gate (Python port of applyLiveDepth) ──────────────────────────

class TestApplyLiveDepth:
    def test_thin_buy_book_blanks_instant_figures(self):
        r = _row(1, 101, pat=100, inst=200, out_qty=10)
        ind_core.apply_live_depth(r, {"buy_volume": 5.0, "sell_volume": 50.0, "bid": 9.0})
        assert r["profit_instant"] is None and r["isk_per_hour_instant"] is None
        assert r["bid"] is None
        assert r["profit_best"] == 100 and r["isk_per_hour_best"] == 100

    def test_no_bid_blanks_instant(self):
        r = _row(1, 101, pat=100, inst=200)
        ind_core.apply_live_depth(r, {"buy_volume": 999.0, "sell_volume": 0.0, "bid": None})
        assert r["profit_instant"] is None

    def test_deep_book_keeps_instant(self):
        r = _row(1, 101, pat=100, inst=200)
        ind_core.apply_live_depth(r, {"buy_volume": 50.0, "sell_volume": 1.0, "bid": 9.0})
        assert r["profit_instant"] == 200

    def test_unknown_depth_is_noop(self):
        r = _row(1, 101, pat=100, inst=200)
        ind_core.apply_live_depth(r, {"buy_volume": None, "sell_volume": None, "bid": None})
        assert r["profit_instant"] == 200

    def test_apply_liquidity_none_entry_just_retires_spinner(self):
        r = _row(1, 101, pat=100)
        ind_core.apply_liquidity(r, None)
        assert r["liq_loaded"] is True and r["daily_vol"] is None

    def test_apply_liquidity_sets_days_to_sell(self):
        r = _row(1, 101, pat=100, out_qty=4, runs=5)
        ind_core.apply_liquidity(r, ind_core.liquidity_entry(10.0, {}))
        assert r["days_to_sell"] == 2.0
        assert r["tradeability"] == ind_core.tradeability(10.0)
        assert r["liq_loaded"] is True


# ── background job ───────────────────────────────────────────────────────────

def _scan(rows, scanned_at=1000.5):
    return {"station_id": JITA_STATION_ID, "station_name": "Jita", "market_group": "all",
            "runs": 1, "count": len(rows), "scanned_at": scanned_at,
            "favorites_only": False, "owned_only": False, "rows": rows}


def _fake_fetchers(calls=None):
    def hist(ids, region, sess, cache, **k):
        if calls is not None:
            calls.append(sorted(ids))
        return {tid: 100.0 for tid in ids}

    def live(ids, sess, **k):
        # 102's buy book is empty -> its instant figures must be blanked.
        return {tid: {"buy_max": 9.0, "sell_min": 11.0,
                      "buy_volume": 0.0 if tid == 102 else 500.0, "sell_volume": 50.0}
                for tid in ids}
    return hist, live


class TestFillJob:
    def test_job_scores_tail_and_checkpoints_into_saved_scan(self):
        rows = [_row(1, 101, pat=900, inst=10, liq_loaded=True, daily_vol=5.0),
                _row(2, 102, pat=800, inst=700), _row(3, 103, pat=1, inst=2)]
        hist, live = _fake_fetchers()
        with patch.object(lp_web, "fetch_history_volumes", side_effect=hist), \
             patch.object(lp_web, "fetch_prices_esi", side_effect=live):
            job = lp_web._start_ind_fill(_acct(), _scan(rows), background=False)
            assert job.pending == [102, 103]   # the inline-scored row isn't refetched
            job.run()
        assert job.status == "done" and job.done == 2
        saved = json.loads(lp_web.IND_LAST_SCAN_PATH.read_text())
        by = {r["product_id"]: r for r in saved["rows"]}
        assert all(r["liq_loaded"] for r in saved["rows"])
        assert by[103]["daily_vol"] == 100.0 and by[103]["tradeability"] is not None
        assert by[102]["profit_instant"] is None          # live-depth gate applied
        assert by[101]["daily_vol"] == 5.0                # untouched

    def test_job_does_not_mutate_the_callers_scan(self):
        rows = [_row(1, 101, pat=900)]
        scan = _scan(rows)
        hist, live = _fake_fetchers()
        with patch.object(lp_web, "fetch_history_volumes", side_effect=hist), \
             patch.object(lp_web, "fetch_prices_esi", side_effect=live):
            lp_web._start_ind_fill(_acct(), scan, background=False).run()
        assert "liq_loaded" not in scan["rows"][0]

    def test_fetches_in_priority_order(self, monkeypatch):
        monkeypatch.setattr(lp_web, "IND_FILL_BATCH", 1)
        rows = [_row(1, 101, pat=900, inst=1), _row(2, 102, pat=800, inst=2),
                _row(3, 103, pat=1, inst=950)]
        calls = []
        hist, live = _fake_fetchers(calls)
        with patch.object(lp_web, "fetch_history_volumes", side_effect=hist), \
             patch.object(lp_web, "fetch_prices_esi", side_effect=live):
            lp_web._start_ind_fill(_acct(), _scan(rows), background=False).run()
        assert calls == [[101], [103], [102]]

    def test_failed_round_retires_rows_without_scores(self, monkeypatch):
        monkeypatch.setattr(lp_web.threading.Event, "wait", lambda self, t=None: False)
        rows = [_row(1, 101, pat=900)]
        with patch.object(lp_web, "fetch_history_volumes",
                          side_effect=lp_core.ESIRateLimited("limited")), \
             patch.object(lp_web, "fetch_prices_esi", return_value={}):
            job = lp_web._start_ind_fill(_acct(), _scan(rows), background=False)
            job.run()
        assert job.status == "done"
        assert job.entries == [(101, None)]
        saved = json.loads(lp_web.IND_LAST_SCAN_PATH.read_text())
        assert saved["rows"][0]["liq_loaded"] is True

    def test_new_scan_cancels_old_job_and_it_never_saves(self):
        acct = _acct()
        old = lp_web._start_ind_fill(acct, _scan([_row(1, 101, pat=9)], 1.0), background=False)
        new = lp_web._start_ind_fill(acct, _scan([_row(2, 202, pat=9)], 2.0), background=False)
        assert old.cancelled.is_set() and not new.cancelled.is_set()
        hist, live = _fake_fetchers()
        with patch.object(lp_web, "fetch_history_volumes", side_effect=hist), \
             patch.object(lp_web, "fetch_prices_esi", side_effect=live):
            new.run()
            old.run()   # superseded: must not overwrite the newer scan
        assert old.status == "cancelled"
        saved = json.loads(lp_web.IND_LAST_SCAN_PATH.read_text())
        assert saved["scanned_at"] == 2.0

    def test_status_cursor_returns_only_new_entries(self, monkeypatch):
        monkeypatch.setattr(lp_web, "IND_FILL_BATCH", 1)
        acct = _acct()
        lp_web._REQUEST.account = acct
        rows = [_row(1, 101, pat=900), _row(2, 102, pat=800)]
        hist, live = _fake_fetchers()
        with patch.object(lp_web, "fetch_history_volumes", side_effect=hist), \
             patch.object(lp_web, "fetch_prices_esi", side_effect=live):
            lp_web._start_ind_fill(acct, _scan(rows, 1000.5), background=False).run()
        st = lp_web.do_ind_fill_status({"scanned_at": ["1000.5"], "since": ["0"]})
        assert st["status"] == "done" and st["total"] == 2 and st["cursor"] == 2
        assert set(st["entries"]) == {"101", "102"}
        st2 = lp_web.do_ind_fill_status({"scanned_at": ["1000.5"], "since": ["1"]})
        assert set(st2["entries"]) == {"102"}

    def test_status_for_other_scan_points_at_latest(self):
        acct = _acct()
        lp_web._REQUEST.account = acct
        lp_web._start_ind_fill(acct, _scan([_row(1, 101, pat=9)], 2000.0), background=False)
        st = lp_web.do_ind_fill_status({"scanned_at": ["1000.5"]})
        assert st == {"status": "none", "latest_scanned_at": 2000.0}

    def test_status_none_without_job(self):
        lp_web._REQUEST.account = _acct()
        assert lp_web.do_ind_fill_status({"scanned_at": ["1"]}) == {"status": "none"}

    def test_snapshot_is_independent_copy(self):
        job = lp_web._start_ind_fill(_acct(), _scan([_row(1, 101, pat=9)]), background=False)
        snap = job.snapshot()
        snap["rows"][0]["daily_vol"] = 123
        assert job.scan["rows"][0]["daily_vol"] is None


class TestScanWiring:
    def test_scan_retires_unprofitable_rows_server_side(self):
        src = (_ROOT / "lp-web.py").read_text(encoding="utf-8")
        body = src[src.index("def do_ind_scan("):src.index("def do_ind_liquidity(")]
        assert "if not row_is_profitable(r):" in body
        assert "ind_core.fill_priority(rows)" in body

    def test_sse_handler_starts_fill_before_emitting_result(self):
        src = (_ROOT / "lp-web.py").read_text(encoding="utf-8")
        h = src[src.index("def _handle_sse_scan("):]
        assert h.index("_start_ind_fill(current_account(), result)") < \
            h.index('emit({"type": "result", **result})')

    def test_save_scan_ignores_industry_blobs(self, tmp_path, monkeypatch):
        from io import BytesIO
        monkeypatch.setattr(lp_web, "LP_LAST_SCAN_PATH", tmp_path / "lp_last_scan.json")
        body = json.dumps({"tab": "ind", "blob": {"rows": [{"x": 1}], "scanned_at": 1}}).encode()
        handler = MagicMock()
        handler.path = "/api/save-scan"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        lp_web.Handler.do_POST(handler)
        assert not lp_web.IND_LAST_SCAN_PATH.exists()


class TestFrontendWiring:
    def test_page_polls_server_fill(self):
        html = lp_web.FRONTEND_SOURCE
        assert "async function pollIndFill(" in html
        assert "/api/ind/fill-status?" in html
        assert "IND_FILL_TOKEN" in html
        # The browser no longer drives the fill nor beacons the (multi-MB) scan.
        assert "fillIndTradeability" not in html
        assert 'persistScan("ind"' not in html
        # Rows spin only while a fill is actually running.
        assert "function _indLiqSpin(r){ return IND.fillTotal>0 && !r.liq_loaded; }" in html

    def test_restore_resumes_following_the_fill(self):
        html = lp_web.FRONTEND_SOURCE
        restore = html[html.index("async function restoreLastScans("):]
        restore = restore[:restore.index("return restored;")]
        assert "pollIndFill();" in restore

    def test_owned_preview_does_not_poll(self):
        html = lp_web.FRONTEND_SOURCE
        preview = html[html.index("function loadOwnedPreview("):]
        preview = preview[:preview.index("function closeIndDetail(")]
        assert "pollIndFill" not in preview


# ── lp_core fetchers ─────────────────────────────────────────────────────────

def _orders_resp(price=10.0):
    r = MagicMock()
    r.status_code = 200
    r.headers = {"X-Pages": "1"}
    r.json.return_value = [{"price": price, "volume_remain": 5, "is_buy_order": False,
                            "location_id": JITA_STATION_ID}]
    return r


class TestPriceCacheFreshness:
    def _path(self, tmp_path):
        return tmp_path / f"esi_prices_{JITA_STATION_ID}.json"

    def test_stale_entry_refetched_even_if_file_stamp_is_fresh(self, tmp_path):
        now = time.time()
        self._path(tmp_path).write_text(json.dumps({
            "_ts": now,
            "34": {"sell_min": 5.0, "buy_max": 4.0, "sell_volume": 1.0,
                   "buy_volume": 1.0, "_t": now - PRICE_CACHE_TTL - 60}}))
        session = MagicMock()
        session.get.return_value = _orders_resp(7.0)
        out = fetch_prices_esi([34], session, cache_dir=tmp_path)
        assert out[34]["sell_min"] == 7.0
        assert "_t" not in out[34]

    def test_legacy_entries_pinned_to_old_stamp_on_save(self, tmp_path):
        old = time.time() - 100
        self._path(tmp_path).write_text(json.dumps({
            "_ts": old, "35": {"sell_min": 1.0, "buy_max": 1.0,
                               "sell_volume": 1.0, "buy_volume": 1.0}}))
        session = MagicMock()
        session.get.return_value = _orders_resp()
        fetch_prices_esi([34], session, cache_dir=tmp_path)
        saved = json.loads(self._path(tmp_path).read_text())
        assert saved["35"]["_t"] == old
        assert saved["34"]["_t"] > old

    def test_failed_verify_not_cached(self, tmp_path):
        session = MagicMock()
        session.get.side_effect = RuntimeError("boom")
        out = fetch_prices_esi([34], session, cache_dir=tmp_path)
        assert out[34]["buy_volume"] is None
        assert "34" not in json.loads(self._path(tmp_path).read_text())

    def test_parallel_fetch_matches_serial(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _orders_resp(3.0)
        out = fetch_prices_esi(range(1, 30), session, cache_dir=tmp_path,
                               refresh=True, workers=8)
        assert sorted(out) == list(range(1, 30))
        assert all(v["sell_min"] == 3.0 for v in out.values())
        saved = json.loads(self._path(tmp_path).read_text())
        assert all(str(t) in saved for t in range(1, 30))


class TestHistoryWorkers:
    def test_parallel_history_fetch(self, tmp_path):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = [{"date": "2026-09-01", "volume": 10}]
        session = MagicMock()
        session.get.return_value = resp
        out = lp_core.fetch_history_volumes(range(1, 20), JITA_REGION_ID, session,
                                            tmp_path, workers=6)
        assert sorted(out) == list(range(1, 20))
        assert session.get.call_count == 19
        # Cached now: a second pass makes no calls.
        session.get.reset_mock()
        lp_core.fetch_history_volumes(range(1, 20), JITA_REGION_ID, session,
                                      tmp_path, workers=6)
        session.get.assert_not_called()
