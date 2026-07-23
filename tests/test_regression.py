"""
Regression tests covering bugs found during development.

Run:  pytest tests/ -v

Bugs covered:
- /api/corps returning 500 → JS crash (.filter is not a function)
- ALL_CORPS assigned non-array value from error response
- /api/scan with no corp returning 500 instead of 400
- Unknown routes returning wrong status
- HTML response missing version string
"""
import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Load lp-web.py (hyphen in filename requires importlib)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def _acct(chars=None, active=None):
    """A legacy Account for direct-call tests. chars = {cid: name}."""
    ids = list((chars or {}).keys())
    a = lp_web.Account(ids[0] if ids else 1)
    for cid, name in (chars or {}).items():
        a.characters[cid] = {"character_id": cid, "name": name,
                             "scopes": [], "refresh_token": "x"}
    a.active_char_id = active if (active in a.characters) else (ids[0] if ids else None)
    return a


def _use_account(acct):
    """Bind the current request thread to an account (for direct handler calls)."""
    lp_web._REQUEST.account = acct

from lp_core import LPError  # noqa: E402


import pytest as _pytest_for_fixture  # noqa: E402


@_pytest_for_fixture.fixture(autouse=True)
def _reset_request_ctx():
    """Isolate the per-thread request account between tests."""
    lp_web._REQUEST.account = None
    yield
    lp_web._REQUEST.account = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def http_get(url):
    """GET url → (parsed_body, status_code). Handles 4xx/5xx without raising."""
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def http_sse_events(url):
    """GET an SSE endpoint → list of parsed `data:` event dicts. Used for the
    scan endpoints, which stream `progress`/`result`/`error` events."""
    events = []
    with urllib.request.urlopen(url) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def http_post_json(url, data):
    """POST JSON body → (parsed_body, status_code). Handles 4xx/5xx without raising."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_server(tmp_path):
    """Real HTTP server with an isolated temp cache directory."""
    orig_cache = lp_web.CACHE_DIR
    orig_corps = lp_web.NPC_CORPS[:]
    lp_web.CACHE_DIR = tmp_path
    lp_web.NPC_CORPS.clear()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), lp_web.Handler)
    port = srv.server_address[1]
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01),
                     daemon=True).start()

    yield f"http://127.0.0.1:{port}", tmp_path

    srv.shutdown()
    lp_web.CACHE_DIR = orig_cache
    lp_web.NPC_CORPS.clear()
    lp_web.NPC_CORPS.extend(orig_corps)


# ---------------------------------------------------------------------------
# get_npc_corps() — graceful fallback
# ---------------------------------------------------------------------------

class TestGetNpcCorps:
    """Bug: ESI failure caused 500 with {error:...}; JS then crashed on .filter()."""

    def setup_method(self):
        lp_web.NPC_CORPS.clear()

    def test_returns_empty_list_on_connection_error(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError("ESI down")):
            result = lp_web.get_npc_corps()
        assert result == []

    def test_returns_empty_list_on_http_error(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.get_npc_corps()
        assert result == []

    def test_return_type_is_always_list(self, tmp_path):
        """JS relies on Array.isArray() — server must always respond with a list."""
        lp_web.CACHE_DIR = tmp_path
        with patch.object(lp_web.SESSION, "get", side_effect=RuntimeError("unexpected")):
            result = lp_web.get_npc_corps()
        assert isinstance(result, list)

    def test_loads_from_disk_cache_without_esi(self, tmp_path):
        """Cached npc_corps.json must be returned with no ESI call at all."""
        lp_web.CACHE_DIR = tmp_path
        expected = [{"id": 1000001, "name": "Caldari Navy"}]
        (tmp_path / "npc_corps.json").write_text(json.dumps(expected))

        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI when cached")):
            result = lp_web.get_npc_corps()

        assert result == expected

    def test_in_memory_cache_prevents_second_esi_call(self, tmp_path):
        """Once loaded, subsequent calls must not hit ESI again."""
        lp_web.CACHE_DIR = tmp_path
        corps = [{"id": 1, "name": "Test Corp"}]
        (tmp_path / "npc_corps.json").write_text(json.dumps(corps))

        lp_web.get_npc_corps()  # prime in-memory cache

        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI for second call")):
            result = lp_web.get_npc_corps()

        assert result == corps


# ---------------------------------------------------------------------------
# _resolve_corp_names() — binary-split around bad IDs
# ---------------------------------------------------------------------------

def _fake_esi_post(valid_ids, corp_ids):
    """Build a SESSION.post replacement mimicking ESI /universe/names/.

    Mirrors real ESI: the whole batch 404s if ANY id is not resolvable.
    Resolvable ids return category 'corporation' if in corp_ids, else 'character'.
    """
    valid_ids = set(valid_ids)
    corp_ids = set(corp_ids)

    def _post(url, json=None, **kwargs):
        ids = json
        resp = MagicMock()
        if all(i in valid_ids for i in ids):
            resp.status_code = 200
            resp.json.return_value = [
                {"id": i, "name": f"Corp {i}",
                 "category": "corporation" if i in corp_ids else "character"}
                for i in ids
            ]
        else:
            resp.status_code = 404
            resp.json.return_value = {"error": "Ensure all IDs are valid before resolving."}
        return resp

    return _post


class TestResolveCorpNames:
    """Bug: a single unresolvable id 404'd the whole batch, the code then
    iterated over the error dict's keys and crashed → /api/corps returned []."""

    def test_all_valid_returns_all_corps(self):
        ids = [1, 2, 3, 4]
        post = _fake_esi_post(valid_ids=ids, corp_ids=ids)
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 2, 3, 4}

    def test_one_bad_id_does_not_lose_the_batch(self):
        """The key regression: id 3 is unresolvable; 1,2,4 must still come back."""
        ids = [1, 2, 3, 4]
        post = _fake_esi_post(valid_ids={1, 2, 4}, corp_ids={1, 2, 4})
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 2, 4}

    def test_filters_non_corporation_categories(self):
        ids = [1, 2, 3]
        post = _fake_esi_post(valid_ids=ids, corp_ids={1, 3})  # 2 is a character
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 3}

    def test_error_dict_response_does_not_crash(self):
        """Single bad id returns a 404 dict — must be skipped, not crash."""
        post = _fake_esi_post(valid_ids=set(), corp_ids=set())
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names([999])
        assert result == []

    def test_empty_input_returns_empty(self):
        with patch.object(lp_web.SESSION, "post",
                          side_effect=AssertionError("must not POST for empty input")):
            assert lp_web._resolve_corp_names([]) == []

    def test_load_npc_corps_recovers_around_bad_id(self):
        """End-to-end: /npccorps/ lists a bad id; loader still returns the rest."""
        lp_web.NPC_CORPS.clear()
        all_ids = [1000180, 1000181, 9999999]  # last one is dead
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = all_ids
        post = _fake_esi_post(valid_ids={1000180, 1000181}, corp_ids={1000180, 1000181})
        import tempfile
        cache = Path(tempfile.mkdtemp())
        lp_web.CACHE_DIR = cache
        with patch.object(lp_web.SESSION, "get", return_value=get_resp), \
             patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._load_npc_corps()
        assert {c["id"] for c in result} == {1000180, 1000181}
        # and the recovered list was cached to disk
        assert (cache / "npc_corps.json").exists()


# ---------------------------------------------------------------------------
# /api/corps HTTP endpoint
# ---------------------------------------------------------------------------

class TestApiCorpsEndpoint:
    """The endpoint must always return HTTP 200 with a JSON array."""

    def test_returns_200_when_esi_is_down(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert isinstance(data, list)

    def test_returns_cached_corps(self, tmp_server):
        base, cache = tmp_server
        corps = [{"id": 1000180, "name": "State Protectorate"}]
        (cache / "npc_corps.json").write_text(json.dumps(corps))
        data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert data == corps

    def test_response_is_always_a_json_array(self, tmp_server):
        """Protects against the .filter-is-not-a-function bug on the client."""
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert isinstance(data, list)

    def test_content_type_is_json(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            with urllib.request.urlopen(f"{base}/api/corps") as r:
                ct = r.headers.get("Content-Type")
        assert "application/json" in ct


# ---------------------------------------------------------------------------
# /api/scan endpoint
# ---------------------------------------------------------------------------

class TestApiScanEndpoint:
    def test_missing_corp_streams_error_event(self, tmp_server):
        # /api/scan streams SSE now; a bad request surfaces as an `error` event
        # rather than an HTTP 400.
        base, _ = tmp_server
        events = http_sse_events(f"{base}/api/scan")
        assert events and events[-1]["type"] == "error"
        assert events[-1]["error"]

    def test_empty_corp_streams_error_event(self, tmp_server):
        base, _ = tmp_server
        events = http_sse_events(f"{base}/api/scan?corp=&corp_id=")
        assert events and events[-1]["type"] == "error"
        assert events[-1]["error"]

    def test_do_scan_raises_lperror_with_no_corp(self):
        with pytest.raises(LPError):
            lp_web.do_scan({})

    def test_do_scan_raises_lperror_with_blank_corp(self):
        with pytest.raises(LPError):
            lp_web.do_scan({"corp": [""], "corp_id": [""]})

    def test_do_scan_includes_output_volume_in_rows(self, tmp_path):
        """output_volume = packaged m³/unit × qty is present in every scan row."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40, "isk_per_lp_best": 0.45,
            "max_units": 10,
            "total_profit_patient": 4500.0, "total_profit_instant": 4000.0,
            "total_profit_best": 4500.0, "buy_volume": 1000,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 10.0}):
            result = lp_web.do_scan(q)
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert "output_volume" in row
        assert row["output_volume"] == 50.0  # 10.0 m³/unit × 5 qty

    def test_do_scan_output_volume_none_when_unavailable(self, tmp_path):
        """output_volume is None when the ESI volume lookup fails."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 202, "quantity": 1, "lp_cost": 500}]
        fake_sellable = [{
            "offer_id": 2, "name_id": 202, "qty": 1, "lp_cost": 500,
            "isk_cost": 0, "req_cost": 0, "ask": 50.0, "bid": 45.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.09, "isk_per_lp_instant": 0.08, "isk_per_lp_best": 0.09,
            "max_units": 20,
            "total_profit_patient": 900.0, "total_profit_instant": 800.0,
            "total_profit_best": 900.0, "buy_volume": 500,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["2000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={202: "Other Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={202: None}):
            result = lp_web.do_scan(q)
        assert result["rows"][0]["output_volume"] is None

    def test_do_scan_emits_progress_events(self, tmp_path):
        """do_scan streams monotonic progress events via emit() so the browser can
        show a progress bar rather than an indefinite spinner."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        events = []
        with patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=([], [])), \
             patch.object(lp_web, "resolve_names", return_value={}), \
             patch.object(lp_web, "resolve_volumes", return_value={}):
            lp_web.do_scan(q, emit=events.append)
        assert events, "expected progress events"
        assert all(e["type"] == "progress" for e in events)
        pcts = [e["pct"] for e in events]
        assert pcts == sorted(pcts)  # monotonically non-decreasing
        assert all(0 <= p <= 100 for p in pcts)

    def test_do_scan_rows_carry_liquidity_placeholders(self, tmp_path):
        """Scan rows expose type_id/sell_volume + null saturation fields so the
        background /api/liquidity fill can patch them in place."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40, "isk_per_lp_best": 0.45,
            "max_units": 10,
            "total_profit_patient": 4500.0, "total_profit_instant": 4000.0,
            "total_profit_best": 4500.0, "buy_volume": 1000,
            "sell_volume": 8000, "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 10.0}):
            row = lp_web.do_scan(q)["rows"][0]
        assert row["type_id"] == 101
        assert row["sell_volume"] == 8000
        assert row["daily_vol"] is None
        assert row["days_to_clear"] is None
        assert row["tradeability"] is None
        assert row["liq_loaded"] is False


# ---------------------------------------------------------------------------
# /api/liquidity endpoint (market-saturation background fill)
# ---------------------------------------------------------------------------

class TestDoLiquidity:
    def test_returns_saturation_keyed_by_offer(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "max_units": 10,
            "profit_per": 450.0, "sell_volume": 1000, "ask": 100.0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "instant": ["0"], "station": ["60003760"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_volumes", return_value={101: 200}), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: 120.0}), \
             patch.object(lp_web, "fetch_sell_order_stats",
                          return_value={"age_seconds": 28800.0}):
            result = lp_web.do_liquidity(q)
        assert "liquidity" in result
        entry = result["liquidity"][1]
        assert entry["daily_vol"] == 200
        assert entry["days_to_clear"] == 5.0
        # ask (100) below fair value (120) -> hold the list price at fair value
        assert entry["list_price"] == 120.0
        assert entry["floor_age"] == 28800.0  # 8h, from the order's issued stamp
        assert set(entry) == {"daily_vol", "days_to_clear", "list_price", "floor_age"}

    def test_history_fetched_for_hub_region(self, tmp_path):
        """Amarr station → daily volume pulled from the Domain region, not Forge."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [{"offer_id": 1, "name_id": 101, "qty": 1,
                          "max_units": 1, "profit_per": 1.0, "sell_volume": 0,
                          "ask": 100.0}]
        q = {"corp_id": ["1000"], "lp": ["1000"], "station": ["60008494"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: None}), \
             patch.object(lp_web, "fetch_sell_order_stats", return_value=None), \
             patch.object(lp_web, "fetch_history_volumes",
                          return_value={101: 5}) as m:
            lp_web.do_liquidity(q)
        assert m.call_args[0][1] == 10000043  # Domain region id

    def test_floor_age_deduped_per_reward_type(self, tmp_path):
        """Two offers rewarding the same type share one order-book call."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [
            {"offer_id": 1, "name_id": 101, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
            {"offer_id": 2, "name_id": 101, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
        ]
        q = {"corp_id": ["1000"], "lp": ["1000"], "station": ["60003760"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: None}), \
             patch.object(lp_web, "fetch_history_volumes", return_value={101: 5}), \
             patch.object(lp_web, "fetch_sell_order_stats",
                          return_value={"age_seconds": 3600.0}) as m:
            result = lp_web.do_liquidity(q)
        assert m.call_count == 1  # deduped: one call for the shared type 101
        assert result["liquidity"][1]["floor_age"] == 3600.0
        assert result["liquidity"][2]["floor_age"] == 3600.0

    def test_emit_streams_progress_with_partial_liquidity(self, tmp_path):
        """With emit set, do_liquidity streams progress events carrying a partial
        liquidity payload and an N / M counter so the browser de-spins rows as each
        reward type resolves — and the union of all partials covers every offer."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": t, "quantity": 1, "lp_cost": 1000}
                       for t in (101, 102, 103)]
        fake_sellable = [
            {"offer_id": 1, "name_id": 101, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
            {"offer_id": 2, "name_id": 102, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
            {"offer_id": 3, "name_id": 103, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
        ]
        q = {"corp_id": ["1000"], "lp": ["1000"], "station": ["60003760"]}
        events = []
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_prices", return_value={}), \
             patch.object(lp_web, "fetch_history_volumes",
                          return_value={101: 5, 102: 5, 103: 5}), \
             patch.object(lp_web, "fetch_sell_order_stats",
                          return_value={"age_seconds": 3600.0}):
            result = lp_web.do_liquidity(q, emit=events.append)
        assert events, "expected progress events"
        assert all(e["type"] == "progress" for e in events)
        # Counter is monotonic and lands on total.
        dones = [e["done"] for e in events]
        assert dones == sorted(dones)
        assert events[-1]["done"] == events[-1]["total"] == 3
        assert all(0 <= e["pct"] <= 100 for e in events)
        # Every offer appears across the streamed partials (nothing left spinning).
        streamed = set()
        for e in events:
            streamed.update(e.get("liquidity", {}))
        assert streamed == {"1", "2", "3"}
        # The full dict is still returned for non-streaming callers.
        assert set(result["liquidity"]) == {1, 2, 3}


# ---------------------------------------------------------------------------
# SESSION retry on stale pooled connections
# ---------------------------------------------------------------------------

class TestSessionRetry:
    """Bug: leaving the page open for a while left SESSION holding a pooled
    keep-alive connection that ESI/Fuzzwork had since closed server-side. The
    next reused connection raised ConnectionError('RemoteDisconnected'),
    uncaught, surfacing a raw 500 in the UI. A mounted Retry adapter should
    retry transparently on a fresh connection instead."""

    def test_https_and_http_adapters_have_retry_mounted(self):
        for scheme in ("https://", "http://"):
            adapter = lp_web.SESSION.get_adapter(scheme + "example.com")
            assert adapter.max_retries.total >= 1
            assert adapter.max_retries.connect >= 1


# ---------------------------------------------------------------------------
# /api/settings endpoint
# ---------------------------------------------------------------------------

class TestApiSettingsEndpoint:
    def test_returns_200(self, tmp_server):
        base, _ = tmp_server
        data, status = http_get(f"{base}/api/settings")
        assert status == 200

    def test_returns_settings_shape(self, tmp_server):
        """The authoritative settings response always carries the three stores."""
        base, _ = tmp_server
        data, _ = http_get(f"{base}/api/settings")
        assert isinstance(data, dict)
        assert "prefs" in data and "favorites" in data and "profiles" in data

    def test_reflects_stored_prefs(self, tmp_server, monkeypatch, tmp_path):
        base, cache = tmp_server
        monkeypatch.setattr(lp_web, "PREFS_PATH", cache / "prefs.json")
        # File mode: whatever is in the pref store comes back on the GET.
        lp_web.pref_set(lp_web._LEGACY_ACCOUNT, "active_tab", "ind")
        data, _ = http_get(f"{base}/api/settings")
        assert data["prefs"]["active_tab"] == "ind"

    def test_invalid_patch_returns_400_not_500(self, tmp_server):
        """A bad request to a POST settings endpoint is a 400 (LPError), not a
        500 — do_POST must map LPError like do_GET does."""
        base, _ = tmp_server
        data, status = http_post_json(f"{base}/api/prefs", {"patch": "not json"})
        assert status == 400
        assert "error" in data


# ---------------------------------------------------------------------------
# Row-per-setting store: prefs / favorites / profiles (server-authoritative)
# ---------------------------------------------------------------------------

class TestSettingsStore:
    def _setup(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        lp_web.PREFS_PATH = tmp_path / "prefs.json"
        lp_web.FAVORITES_PATH = tmp_path / "favorites.json"
        lp_web.PROFILES_PATH = tmp_path / "profiles.json"
        _use_account(_acct({42: "T"}, active=42))

    def test_pref_roundtrip_keeps_json_types(self, tmp_path):
        self._setup(tmp_path)
        lp_web.do_prefs({"patch": [json.dumps({"trade_weight": 0.75,
                                               "col_vis": {"ask": False}})]})
        stored = lp_web.prefs_all(lp_web.current_account())
        assert stored["trade_weight"] == 0.75
        assert stored["col_vis"] == {"ask": False}

    def test_independent_patches_do_not_clobber(self, tmp_path):
        """The whole point of the redesign: two separate patches (as if from two
        rapid user actions) each set their own key; neither erases the other."""
        self._setup(tmp_path)
        lp_web.do_prefs({"patch": [json.dumps({"ind.favorites_removed": 1})]})
        lp_web.do_prefs({"patch": [json.dumps({"arb.max_jumps": "8"})]})
        lp_web.do_prefs({"patch": [json.dumps({"corp": "Sisters of EVE"})]})
        stored = lp_web.prefs_all(lp_web.current_account())
        assert stored["arb.max_jumps"] == "8"
        assert stored["corp"] == "Sisters of EVE"
        assert stored["ind.favorites_removed"] == 1

    def test_invalid_patch_rejected(self, tmp_path):
        self._setup(tmp_path)
        with pytest.raises(LPError):
            lp_web.do_prefs({"patch": ["not json"]})

    def test_favorites_add_and_remove(self, tmp_path):
        self._setup(tmp_path)
        lp_web.do_favorites({"blueprint_id": ["23560"], "on": ["1"]})
        lp_web.do_favorites({"blueprint_id": ["587"], "on": ["1"]})
        assert set(lp_web.favorites_all(lp_web.current_account())) == {23560, 587}
        lp_web.do_favorites({"blueprint_id": ["23560"], "on": ["0"]})
        assert lp_web.favorites_all(lp_web.current_account()) == [587]

    def test_profile_save_and_delete(self, tmp_path):
        self._setup(tmp_path)
        lp_web.do_profiles_save({"profile": [json.dumps(
            {"profile_id": "p1", "name": "Sotiyo", "system_index": 4.2,
             "role_bonus": 3, "facility_tax": 1, "scc_surcharge": 4})]})
        profs = lp_web.profiles_all(lp_web.current_account())
        assert len(profs) == 1 and profs[0]["name"] == "Sotiyo"
        assert profs[0]["system_index"] == 4.2
        lp_web.do_profiles_delete({"profile_id": ["p1"]})
        assert lp_web.profiles_all(lp_web.current_account()) == []

    def test_settings_endpoint_bundles_all_three(self, tmp_path):
        self._setup(tmp_path)
        lp_web.do_prefs({"patch": [json.dumps({"active_tab": "ind"})]})
        lp_web.do_favorites({"blueprint_id": ["999"], "on": ["1"]})
        out = lp_web.do_settings({})
        assert out["prefs"]["active_tab"] == "ind"
        assert out["favorites"] == [999]
        assert out["profiles"] == []

    def test_profile_non_numeric_field_is_bad_request(self, tmp_path):
        """A non-numeric profile field is a clean LPError (400), not a 500."""
        self._setup(tmp_path)
        with pytest.raises(LPError):
            lp_web.do_profiles_save({"profile": [json.dumps(
                {"profile_id": "p1", "name": "X", "system_index": "abc"})]})

    def test_file_mode_concurrent_pref_writes_keep_every_key(self, tmp_path):
        """The file-mode store must not lose keys when many setPref-equivalent
        writes for DIFFERENT keys run concurrently (the lost-write race)."""
        import threading as _t
        self._setup(tmp_path)
        acct = lp_web.current_account()
        keys = [f"k{i}" for i in range(40)]
        barrier = _t.Barrier(len(keys))

        def w(k):
            barrier.wait()
            lp_web.pref_set(acct, k, k)

        threads = [_t.Thread(target=w, args=(k,)) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stored = lp_web.prefs_all(acct)
        missing = [k for k in keys if stored.get(k) != k]
        assert not missing, f"lost keys under concurrency: {missing}"


# ---------------------------------------------------------------------------
# Legacy whole-blob → row-per-setting migration
# ---------------------------------------------------------------------------

class TestSettingsBlobExplode:
    def test_flattens_sections_and_lifts_lists(self):
        blob = {
            "corp": "SoE", "active_tab": "ind",
            "arb": {"region": "10000002", "max_jumps": "8"},
            "ind": {"station": "60003760",
                    "favorites": "[23560, 587]",
                    "profiles": [{"profile_id": "p1", "name": "Sotiyo"}],
                    "favorites_cleared": "0"},
            "_server_synced": True,
        }
        prefs, favorites, profiles = lp_web._explode_settings_blob(blob)
        assert prefs["corp"] == "SoE"
        assert prefs["arb.region"] == "10000002"
        assert prefs["ind.station"] == "60003760"
        assert "_server_synced" not in prefs
        assert "ind.favorites_cleared" not in prefs
        assert "ind.favorites" not in prefs and "ind.profiles" not in prefs
        assert favorites == [23560, 587]
        assert profiles == [{"profile_id": "p1", "name": "Sotiyo"}]


# ---------------------------------------------------------------------------
# HTTP routing
# ---------------------------------------------------------------------------

class TestHttpRouting:
    def test_root_returns_200_html(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type")

    def test_root_contains_app_title(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            body = r.read()
        # Logo is split across elements: "EVE <span>MARKET TOOLS</span>"
        assert b"MARKET TOOLS" in body
        assert b"EVE Market Tools" in body  # <title> tag

    def test_root_contains_version(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            body = r.read()
        assert lp_web.__version__.encode() in body

    def test_unknown_path_returns_404(self, tmp_server):
        base, _ = tmp_server
        _, status = http_get(f"{base}/api/doesnotexist")
        assert status == 404

    def test_unknown_path_returns_json_error(self, tmp_server):
        base, _ = tmp_server
        data, _ = http_get(f"{base}/api/doesnotexist")
        assert "error" in data

    @pytest.mark.parametrize("path", sorted(lp_web.TAB_ROUTES))
    def test_tab_url_serves_app_shell(self, tmp_server, path):
        # Deep-linking / refreshing on a tab URL must serve the SPA shell so the
        # client can render that module — not 404.
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}{path}") as r:
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type")
            assert lp_web.__version__.encode() in r.read()

    def test_tab_routes_cover_every_tab(self):
        # The clean URL map in the front-end must have a matching server route
        # for each non-root tab, or a refresh there would 404.
        for path in ("/arbitrage", "/industry", "/character", "/exploration"):
            assert path in lp_web.TAB_ROUTES
        # Front-end path<->tab maps are present and consistent.
        assert 'const TAB_PATH = {' in lp_web.FRONTEND_SOURCE
        assert 'const PATH_TAB = {' in lp_web.FRONTEND_SOURCE
        assert 'history.pushState' in lp_web.FRONTEND_SOURCE
        assert '"popstate"' in lp_web.FRONTEND_SOURCE


# ---------------------------------------------------------------------------
# Custom tooltip system (data-tip + tooltip engine replaced native title=)
# ---------------------------------------------------------------------------

class TestTooltips:
    def test_tooltip_engine_present(self):
        # The themed tooltip element + mousemove engine must be wired up.
        assert 'id="tooltip"' in lp_web.FRONTEND_SOURCE
        assert "#tooltip.show" in lp_web.FRONTEND_SOURCE
        assert "[data-tip]" in lp_web.FRONTEND_SOURCE

    def test_uses_data_tip_not_native_title(self):
        # Column headers and controls now use data-tip, not title=.
        assert "data-tip=" in lp_web.FRONTEND_SOURCE
        assert 'c.tip?` data-tip=' in lp_web.FRONTEND_SOURCE

    def test_no_stale_native_title_on_controls(self):
        # The refresh/columns controls must not fall back to native title=.
        assert 'title="Re-fetch' not in lp_web.FRONTEND_SOURCE
        assert 'title="Choose visible columns"' not in lp_web.FRONTEND_SOURCE

    def test_sidebar_kpi_cards(self):
        # The detail-panel KPI grid lays out 3 per row and shows BOTH sell-mode
        # profits side by side instead of a single "Total profit" card.
        assert "repeat(3,1fr)" in lp_web.FRONTEND_SOURCE
        assert '<div class="l">List profit</div>' in lp_web.FRONTEND_SOURCE
        assert '<div class="l">Instant-sell profit</div>' in lp_web.FRONTEND_SOURCE
        # The old single-mode card is gone.
        assert '<div class="l">Total profit</div>' not in lp_web.FRONTEND_SOURCE
        # Revenue is covered by the profit-breakdown comparison, not a KPI card.
        assert '<div class="l">Revenue</div>' not in lp_web.FRONTEND_SOURCE
        # Item cost and redemption ISK are combined into one card; the separate
        # "Item cost" / "Redemption ISK" cards are gone (they live in the cost
        # breakdown table). A suggested-list-price card takes the freed slot.
        assert '<div class="l">Item + ISK cost</div>' in lp_web.FRONTEND_SOURCE
        assert '<div class="l">Suggested list / unit</div>' in lp_web.FRONTEND_SOURCE
        assert '<div class="l">Item cost</div>' not in lp_web.FRONTEND_SOURCE
        assert '<div class="l">Redemption ISK</div>' not in lp_web.FRONTEND_SOURCE
        # The store ISK charge is still labelled "Redemption ISK" (cost
        # breakdown / recipe), never the deprecated "ISK fee".
        assert "Redemption ISK" in lp_web.FRONTEND_SOURCE
        assert "ISK fee" not in lp_web.FRONTEND_SOURCE

    def test_profit_breakdown_waterfall(self):
        # The Sale section is a profit waterfall: gross sell value, the fee
        # deductions, net revenue subtotal, and the final profit line.
        html = lp_web.FRONTEND_SOURCE
        assert "Profit breakdown" in html
        assert "Sell value (walking buy orders)" in html
        assert "Sell value (listed at ask)" in html
        assert "− Sales tax" in html
        assert "− Broker fee" in html
        assert "Net revenue" in html
        assert "− Items cost" in html
        assert "− Redemption ISK" in html
        # The deprecated "Store ISK Fee"/"Store ISK fee" labels are gone.
        assert "Store ISK" not in html

    def test_detail_panel_uses_selected_hub_not_hardcoded_jita(self):
        # The detail panel must label prices with the chosen hub, not a
        # hardcoded "Jita" — the market is user-selectable.
        html = lp_web.FRONTEND_SOURCE
        assert "Jita ask / bid" not in html
        assert "Costs use the live ${hub} order book." in html
        assert "Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → ${hub}" in html

    def test_chart_stat_chips_have_labels_and_tooltips(self):
        # The Current / ATH / vs 30d MA chips use labelled k/v markup and
        # carry data-tip tooltips.
        assert '<span class="k">Current</span>' in lp_web.FRONTEND_SOURCE
        assert '<span class="k">ATH</span>' in lp_web.FRONTEND_SOURCE
        assert '<span class="k">vs 30d MA</span>' in lp_web.FRONTEND_SOURCE
        assert "All-time high daily average" in lp_web.FRONTEND_SOURCE
        assert "30-day moving average" in lp_web.FRONTEND_SOURCE
        assert ".chart-stats .k" in lp_web.FRONTEND_SOURCE


# ---------------------------------------------------------------------------
# Dual-mode redesign (v1.11.0): the Sell-mode toggle is gone; patient and
# instant figures are shown side by side everywhere.
# ---------------------------------------------------------------------------

class TestDualModeComparison:
    def test_sell_mode_dropdown_removed(self):
        # The single-mode <select id="instant"> control is gone.
        assert 'id="instant"' not in lp_web.FRONTEND_SOURCE
        assert ">Sell mode<" not in lp_web.FRONTEND_SOURCE

    def test_paired_isk_per_lp_columns(self):
        # The table exposes both sell-mode ISK/LP columns.
        assert '{k:"isk_per_lp_patient"' in lp_web.FRONTEND_SOURCE
        assert '{k:"isk_per_lp_instant"' in lp_web.FRONTEND_SOURCE
        # ...and the old single column is gone.
        assert '{k:"isk_per_lp",' not in lp_web.FRONTEND_SOURCE

    def test_paired_total_profit_columns(self):
        assert '{k:"total_profit_patient"' in lp_web.FRONTEND_SOURCE
        assert '{k:"total_profit_instant"' in lp_web.FRONTEND_SOURCE
        assert '{k:"total_profit",' not in lp_web.FRONTEND_SOURCE

    def test_mode_labels_are_list_and_instant_sell(self):
        html = lp_web.FRONTEND_SOURCE
        # Column headers and KPI cards use "List" / "Instant-sell" wording.
        assert 't:"List ISK/LP"' in html
        assert 't:"Instant-sell ISK/LP"' in html
        assert 't:"List profit"' in html
        assert 't:"Instant-sell profit"' in html
        # The earlier "· sell" / "· buy" shorthand is gone.
        assert "ISK/LP · sell" not in html
        assert "Profit · buy" not in html

    def test_default_sort_is_best_of_two(self):
        assert 'sort:{key:"isk_per_lp_best", dir:-1}' in lp_web.FRONTEND_SOURCE

    def test_winning_mode_highlight_styled(self):
        # The better of the two sell-mode cells gets a .win highlight.
        assert "td.win" in lp_web.FRONTEND_SOURCE
        assert 'cls+=" win"' in lp_web.FRONTEND_SOURCE

    def test_scan_response_carries_both_modes(self, tmp_path):
        """do_scan rows expose patient/instant/best ISK-per-LP and total profit."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 1, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40,
            "isk_per_lp_best": 0.45, "max_units": 5,
            "total_profit_patient": 2250.0, "total_profit_instant": 2000.0,
            "total_profit_best": 2250.0, "buy_volume": 1000,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["5000"], "tax": ["0.045"],
             "broker": ["0.015"], "station": ["60003760"]}
        with patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 1.0}):
            result = lp_web.do_scan(q)
        row = result["rows"][0]
        assert row["isk_per_lp_patient"] == 0.45
        assert row["isk_per_lp_instant"] == 0.40
        assert row["isk_per_lp_best"] == 0.45
        assert row["total_profit_patient"] == 2250.0
        assert row["total_profit_instant"] == 2000.0
        assert row["total_profit_best"] == 2250.0
        # The deprecated single-mode flag is no longer in the response.
        assert "instant" not in result


# ---------------------------------------------------------------------------
# Drag-to-reorder columns (v1.12.0): headers are draggable and the chosen
# order is persisted via /api/prefs alongside the column widths.
# ---------------------------------------------------------------------------

class TestColumnReorder:
    def test_do_prefs_persists_col_order(self, tmp_path):
        # A prefs patch persists each key as its own row and preserves JSON types.
        lp_web.CACHE_DIR = tmp_path
        lp_web.PREFS_PATH = tmp_path / "prefs.json"
        _use_account(_acct({42: "T"}, active=42))
        lp_web.do_prefs({"patch": [json.dumps(
            {"col_order": ["name", "ask", "bid"], "col_layout_v": 6})]})
        stored = lp_web.prefs_all(lp_web.current_account())
        assert stored["col_order"] == ["name", "ask", "bid"]
        assert stored["col_layout_v"] == 6

    def test_do_prefs_null_value_deletes_key(self, tmp_path):
        # A null in the patch removes that key's row (and nothing else).
        lp_web.CACHE_DIR = tmp_path
        lp_web.PREFS_PATH = tmp_path / "prefs.json"
        _use_account(_acct({42: "T"}, active=42))
        lp_web.do_prefs({"patch": [json.dumps({"corp": "X", "market": "Jita"})]})
        lp_web.do_prefs({"patch": [json.dumps({"corp": None})]})
        stored = lp_web.prefs_all(lp_web.current_account())
        assert "corp" not in stored
        assert stored["market"] == "Jita"

    def test_headers_are_draggable(self):
        # Each <th> opts into HTML5 drag-and-drop.
        assert '<th draggable="true" data-k="${c.k}"' in lp_web.FRONTEND_SOURCE

    def test_reorder_wiring_present(self):
        html = lp_web.FRONTEND_SOURCE
        # The drag helpers and per-header wiring must be hooked up.
        assert "function wireLPColDrag(" in html
        assert "function reorderLPCols(" in html
        assert "function orderedCols(" in html
        assert "wireLPColDrag(th);" in html
        # visCols now derives from the user order, not raw COLS.
        assert "function visCols(){ return orderedCols()" in html


# ---------------------------------------------------------------------------
# Column formatters that read the row object must declare rowCtx:true
# (v1.14.0 regression: the "List @" column used fmtListPrice — which reads
#  r.liq_loaded — without rowCtx, so the render loop passed `undefined` as the
#  row and threw "Cannot read properties of undefined (reading 'liq_loaded')").
#
# The render loop calls `c.rowCtx ? c.f(v,r) : c.f(v)`, so any formatter that
# touches the row argument MUST be on a column flagged rowCtx:true.
# ---------------------------------------------------------------------------

class TestColumnFormatterRowContext:
    def _cols_block(self):
        html = lp_web.FRONTEND_SOURCE
        start = html.index("const COLS = [")
        end = html.index("];", start)
        return html[start:end]

    def _col_lines(self):
        return [ln for ln in self._cols_block().splitlines() if "{k:" in ln]

    def _row_ctx_named_formatters(self):
        """Named formatters declared `function fmtX(v, r)` read the row arg."""
        return set(re.findall(r"function\s+(fmt\w+)\s*\(\s*v\s*,\s*r\s*\)",
                              lp_web.FRONTEND_SOURCE))

    def test_heuristics_find_the_expected_formatters(self):
        # Guard against the regexes silently matching nothing.
        assert len(self._col_lines()) >= 12
        assert {"fmtListPrice", "fmtVolPerDay", "fmtDays", "fmtTrade"} \
            <= self._row_ctx_named_formatters()

    def test_list_price_column_has_rowctx(self):
        line = next(ln for ln in self._col_lines() if '{k:"list_price"' in ln)
        assert "f:fmtListPrice" in line
        assert "rowCtx:true" in line

    def test_every_row_reading_column_declares_rowctx(self):
        row_named = self._row_ctx_named_formatters()
        offenders = []
        for ln in self._col_lines():
            key = re.search(r'\{k:"([^"]+)"', ln).group(1)
            inline_row = re.search(r"f:\s*\(\s*v\s*,\s*r\s*\)\s*=>", ln) is not None
            named_row = any(re.search(r"f:\s*" + re.escape(n) + r"\b", ln)
                            for n in row_named)
            if (inline_row or named_row) and "rowCtx:true" not in ln:
                offenders.append(key)
        assert not offenders, \
            f"columns read the row arg but lack rowCtx:true: {offenders}"

    def test_col_order_persisted_and_restored(self):
        html = lp_web.FRONTEND_SOURCE
        # Saved as its own pref key (server-authoritative, no whole-blob push)...
        assert "setPref('col_order', STATE.colOrder)" in html
        # ...and restored on load, guarded by the layout version.
        assert "if(s.col_order && s.col_layout_v==COL_LAYOUT_VERSION){" in html

    def test_drag_does_not_trigger_sort(self):
        # A header click at the tail of a drag must not re-sort.
        assert "if(LP_DRAG_KEY){ return; }" in lp_web.FRONTEND_SOURCE


# ---------------------------------------------------------------------------
# /api/history endpoint
# ---------------------------------------------------------------------------

_FAKE_HISTORY = [
    {"date": "2024-01-01", "average": 100.0, "highest": 110.0, "lowest": 90.0,
     "order_count": 5, "volume": 200},
    {"date": "2024-01-03", "average": 105.0, "highest": 115.0, "lowest": 95.0,
     "order_count": 6, "volume": 300},
    {"date": "2024-01-02", "average": 102.0, "highest": 112.0, "lowest": 92.0,
     "order_count": 4, "volume": 150},
]


class TestDoHistory:
    def test_fetches_from_esi_and_returns_sorted(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert "history" in result
        dates = [d["date"] for d in result["history"]]
        assert dates == sorted(dates)

    def test_uses_disk_cache(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        import time as _t
        cache_data = {"_ts": _t.time(), "data": _FAKE_HISTORY[:2]}
        import json as _j
        (tmp_path / "mhist_10000002_34.json").write_text(_j.dumps(cache_data))
        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI when cached")):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert len(result["history"]) == 2

    def test_refetches_when_cache_stale(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        import json as _j
        stale = {"_ts": 0.0, "data": []}
        (tmp_path / "mhist_10000002_34.json").write_text(_j.dumps(stale))
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert len(result["history"]) == 3

    def test_defaults_to_forge_region(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp) as m:
            lp_web.do_history({"type_id": ["34"]})
        url = m.call_args[0][0]
        assert "10000002" in url

    def test_writes_cache_file(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert (tmp_path / "mhist_10000002_34.json").exists()


class TestApiHistoryEndpoint:
    def test_returns_200_with_history_key(self, tmp_server):
        base, _ = tmp_server
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            data, status = http_get(f"{base}/api/history?type_id=34&region_id=10000002")
        assert status == 200
        assert "history" in data
        assert isinstance(data["history"], list)

    def test_missing_type_id_returns_500(self, tmp_server):
        base, _ = tmp_server
        _, status = http_get(f"{base}/api/history")
        assert status == 500

    def test_history_sorted_by_date(self, tmp_server):
        base, _ = tmp_server
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            data, _ = http_get(f"{base}/api/history?type_id=34&region_id=10000002")
        dates = [d["date"] for d in data["history"]]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Industry module routing (v1.19.0) — endpoints exist, isolate, and 404 right.
# The scan/detail data path is covered by ind_core unit tests; here we guard the
# web wiring (settings merge, prefs persistence, unknown-subpath status).
# ---------------------------------------------------------------------------

class TestIndustryLoginRequired:
    """The Industry planner has no manual ME/TE/skill inputs — it only means
    anything with a real character's owned blueprints and trained skills, so
    login is mandatory rather than an optional fallback."""

    def test_scan_without_login_raises(self, monkeypatch):
        _use_account(None)
        with pytest.raises(LPError):
            lp_web.do_ind_scan({})

    def test_detail_without_login_raises(self, monkeypatch):
        _use_account(None)
        with pytest.raises(LPError):
            lp_web.do_ind_detail({"blueprint_id": ["681"]})


class TestIndustryRoutes:
    def test_settings_includes_store_shape(self, tmp_server):
        base, _ = tmp_server
        body, status = http_get(f"{base}/api/settings")
        assert status == 200
        assert "prefs" in body and "favorites" in body and "profiles" in body

    def test_ind_prefs_roundtrip(self, tmp_server, tmp_path, monkeypatch):
        # Industry prefs are dotted keys in the shared pref store now.
        base, cache = tmp_server
        monkeypatch.setattr(lp_web, "PREFS_PATH", cache / "prefs.json")
        body, status = http_post_json(f"{base}/api/prefs", {"patch": json.dumps(
            {"ind.job_rate": "6", "ind.profile": "2", "ind.hide_t2": "1"})})
        assert status == 200 and body["ok"] is True
        saved = lp_web.prefs_all(lp_web._LEGACY_ACCOUNT)
        assert saved["ind.job_rate"] == "6"
        assert saved["ind.hide_t2"] == "1"

    def test_ind_prefs_persists_col_order(self, tmp_server, tmp_path, monkeypatch):
        # The industry column order must survive a reload, like the LP store.
        base, cache = tmp_server
        monkeypatch.setattr(lp_web, "PREFS_PATH", cache / "prefs.json")
        order = ["_fav", "product_name", "_timer", "tech_level"]
        body, status = http_post_json(
            f"{base}/api/prefs", {"patch": json.dumps({"ind.col_order": order})})
        assert status == 200 and body["ok"] is True
        assert lp_web.prefs_all(lp_web._LEGACY_ACCOUNT)["ind.col_order"] == order

    def test_ind_prefs_persists_col_widths_and_vis(self, tmp_server, tmp_path, monkeypatch):
        base, cache = tmp_server
        monkeypatch.setattr(lp_web, "PREFS_PATH", cache / "prefs.json")
        body, status = http_post_json(f"{base}/api/prefs", {"patch": json.dumps(
            {"ind.col_widths": {"product_name": 260}, "ind.col_vis": {"ask": False}})})
        assert status == 200 and body["ok"] is True
        saved = lp_web.prefs_all(lp_web._LEGACY_ACCOUNT)
        assert saved["ind.col_widths"] == {"product_name": 260}
        assert saved["ind.col_vis"] == {"ask": False}

    def test_ind_columns_reorderable(self):
        html = lp_web.FRONTEND_SOURCE
        # Headers are draggable and the order is resolved through indOrderedCols().
        assert "function indOrderedCols(" in html
        assert "function reorderIndCols(" in html
        # Rendering is scoped to visible columns, which are built on the order.
        assert "function indVisCols(){ return indOrderedCols()" in html
        assert "thead.innerHTML=\"<tr>\"+vc.map" in html
        # Order is saved (its own pref key) and restored.
        assert "setPref('ind.col_order', IND.colOrder)" in html
        assert "if(ind.col_order){ try{" in html

    def test_ind_columns_resizable_and_toggleable(self):
        html = lp_web.FRONTEND_SOURCE
        # Resize: colgroup + resizer handle + drag wiring, mirroring the LP store.
        assert '<table id="ind-tbl"><colgroup id="ind-cg">' in html
        assert "function startIndResize(" in html
        assert '<span class="resizer"></span>' in html
        # Visibility: a Columns picker toggles IND.colVis and re-renders.
        assert 'id="indColPickerBtn"' in html
        assert 'id="indColPicker"' in html
        assert "IND.colVis[cb.dataset.k]=cb.checked" in html
        # Widths/visibility persist as their own pref keys.
        assert "setPref('ind.col_widths', IND.colw)" in html
        assert "setPref('ind.col_vis', IND.colVis)" in html

    def test_ind_search_has_clear_button(self):
        html = lp_web.FRONTEND_SOURCE
        assert 'id="ind-search-clear" class="search-clear hidden"' in html
        assert "function updateIndSearchClear(){" in html
        assert '$("#ind-search-clear").addEventListener("click"' in html

    def test_owned_only_includes_char_blueprints(self, monkeypatch):
        """owned_only=1 loads blueprints the character owns via ESI."""
        html = lp_web.FRONTEND_SOURCE
        assert "loadOwnedPreview" in html
        assert 'owned_only:"1"' in html or "owned_only" in html

    def test_hidden_bps_pref_persisted(self, tmp_server, tmp_path, monkeypatch):
        """hidden_bps is stored as its own dotted pref key."""
        base, cache = tmp_server
        monkeypatch.setattr(lp_web, "PREFS_PATH", cache / "prefs.json")
        body, status = http_post_json(
            f"{base}/api/prefs", {"patch": json.dumps({"ind.hidden_bps": [681, 682]})})
        assert status == 200
        assert lp_web.prefs_all(lp_web._LEGACY_ACCOUNT)["ind.hidden_bps"] == [681, 682]

    def test_ind_section_chips_in_html(self):
        """The industry tab has collapsible section chips."""
        html = lp_web.FRONTEND_SOURCE
        assert 'id="ind-chips"' in html
        assert "ind-chip" in html
        assert "IND.sections" in html
        assert "toggleHidden" in html

    def test_unknown_ind_subpath_404(self, tmp_server):
        base, _ = tmp_server
        body, status = http_get(f"{base}/api/ind/bogus")
        assert status == 404


class TestIndustryTradeabilityFill:
    """v1.36.0: the scan scores only the top rows inline; a background fill
    (/api/ind/liquidity) scores every other item in chunks, with a spinner."""

    def test_liquidity_scores_each_type(self, tmp_server):
        base, _ = tmp_server
        fake = {34: 1000.0, 35: None, 36: 0.0}  # traded / never traded / zero vol
        with patch.object(lp_web, "fetch_history_volumes", return_value=fake):
            data, status = http_get(f"{base}/api/ind/liquidity?type_ids=34,35,36")
        assert status == 200
        liq = data["liquidity"]
        assert liq["34"]["daily_vol"] == 1000.0
        assert liq["34"]["tradeability"] > 0
        # Never traded -> no daily volume and no score (renders "—", not a 0).
        assert liq["35"]["daily_vol"] is None
        assert liq["35"]["tradeability"] is None
        # Traded but zero recent volume -> a real, lowest score.
        assert liq["36"]["tradeability"] == 0

    def test_liquidity_parses_only_integer_ids(self, tmp_server):
        base, _ = tmp_server
        seen = {}

        def fake(ids, region, sess, cache, **k):
            seen["ids"] = set(ids)
            return {}

        with patch.object(lp_web, "fetch_history_volumes", side_effect=fake):
            http_get(f"{base}/api/ind/liquidity?type_ids=34,abc,,36")
        assert seen["ids"] == {34, 36}

    def test_liquidity_empty_when_no_ids(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web, "fetch_history_volumes", return_value={}):
            data, status = http_get(f"{base}/api/ind/liquidity?type_ids=")
        assert status == 200
        assert data["liquidity"] == {}

    def test_scan_marks_scored_rows_loaded(self):
        # The scan must flag the rows it scored inline so the client only spins +
        # backfills the unscored remainder.
        src = Path(lp_web.__file__).read_text(encoding="utf-8")
        assert 'r["liq_loaded"] = True' in src

    def test_frontend_background_fill_wired(self):
        html = lp_web.FRONTEND_SOURCE
        assert "function fillIndTradeability(" in html
        assert "/api/ind/liquidity?" in html
        assert "fillIndTradeability();" in html        # kicked off after a scan
        assert "IND_FILL_TOKEN" in html                # stale-fill cancellation
        # Pending rows spin in both market-depth columns until their score lands.
        assert "!r.liq_loaded ? _SPIN" in html

    def test_restore_resumes_fill_for_unscored_rows(self):
        """v1.66.8: restoring a cached scan with liq_loaded=false rows must
        trigger fillIndTradeability() so spinners don't persist forever."""
        html = lp_web.FRONTEND_SOURCE
        assert "if(IND.rows.some(r=>!r.liq_loaded)) fillIndTradeability()" in html


# ---------------------------------------------------------------------------
# Delivered-runs counter (character tab) — cumulative, persisted, baseline on
# first sight so history before this feature existed is never counted.
# ---------------------------------------------------------------------------

class TestDeliveredRunsTracker:
    def _job(self, job_id, status="delivered", runs=10, product_type_id=165):
        return {"job_id": job_id, "status": status, "runs": runs,
                "product_type_id": product_type_id}

    def test_first_sight_is_baseline_not_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        jobs = [self._job(1), self._job(2, status="active", runs=5)]
        rt = lp_web._track_delivered_jobs(_acct(), 42, jobs, {165: "Test Frigate"})
        assert rt["total_runs"] == 0
        assert rt["total_jobs"] == 0

    def test_new_delivery_after_baseline_is_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(_acct(), 42, [self._job(1)], {165: "Test Frigate"})
        rt = lp_web._track_delivered_jobs(
            _acct(), 42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        assert rt["total_runs"] == 25
        assert rt["total_jobs"] == 1

    def test_same_job_not_double_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(_acct(), 42, [self._job(1)], {165: "Test Frigate"})
        lp_web._track_delivered_jobs(_acct(), 42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        rt = lp_web._track_delivered_jobs(_acct(), 42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        assert rt["total_runs"] == 25
        assert rt["total_jobs"] == 1

    def test_persists_across_calls(self, tmp_path, monkeypatch):
        path = tmp_path / "jobs.json"
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", path)
        lp_web._track_delivered_jobs(_acct(), 42, [self._job(1)], {165: "Test Frigate"})
        lp_web._track_delivered_jobs(_acct(), 42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        saved = json.loads(path.read_text())
        assert saved["42"]["total_runs"] == 25

    def test_separate_characters_tracked_independently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(_acct(), 1, [self._job(1)], {})
        lp_web._track_delivered_jobs(_acct(), 2, [self._job(1)], {})  # same job_id, different char
        rt1 = lp_web._track_delivered_jobs(_acct(), 1, [self._job(1), self._job(2, runs=7)], {})
        rt2 = lp_web._track_delivered_jobs(_acct(), 2, [self._job(1), self._job(2, runs=9)], {})
        assert rt1["total_runs"] == 7
        assert rt2["total_runs"] == 9


# ---------------------------------------------------------------------------
# Character data bundle (v1.32.0) — a failing market-orders call (missing
# scope on the user's own EVE app, or stale token) must not take down the
# rest of the character tab (wallet, jobs, LP) with it.
# ---------------------------------------------------------------------------

class TestCharDataOrdersIsolation:
    def _login(self, monkeypatch):
        import time as _time
        lp_web._CHAR_DATA_CACHE.clear()
        acct = lp_web.Account(42)
        acct.characters[42] = {
            "character_id": 42, "name": "Test Char", "scopes": [],
            "refresh_token": "RT", "access_token": "TOK",
            "expires_at": _time.time() + 3600,
        }
        acct.active_char_id = 42
        acct.skill_profiles[42] = {}
        acct.bp_me_tes[42] = {}
        _use_account(acct)

    def _http_error(self, status):
        resp = MagicMock(status_code=status)
        return requests.HTTPError(response=resp)

    def test_orders_failure_does_not_break_rest_of_bundle(self, tmp_path, monkeypatch):
        self._login(monkeypatch)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 1_000_000.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 5_000_000, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: ([], {}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])

        def _raise_403(*a, **k):
            raise self._http_error(403)
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", _raise_403)

        out = lp_web.do_char_data({})
        assert out["wallet"] == 1_000_000.0
        assert out["total_sp"] == 5_000_000
        assert out["market_orders"] == []
        assert "esi-markets.read_character_orders.v1" in out["market_orders_error"]

    def test_orders_success_has_no_error(self, tmp_path, monkeypatch):
        self._login(monkeypatch)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 0.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: ([], {}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: ([], {}))

        out = lp_web.do_char_data({})
        assert out["market_orders"] == []
        assert out["market_orders_error"] is None

    def test_orders_table_has_total_value_column(self):
        """Total value = remaining units x listed price, next to the Price column."""
        html = lp_web.FRONTEND_SOURCE
        assert ">Total value</th>" in html
        assert "fmtISK((o.volume_remain??0)*o.price)" in html


# ---------------------------------------------------------------------------
# SESSION retry policy — a stale pooled keep-alive connection to ESI/Fuzzwork
# (server closed it after being idle) surfaces as a ConnectionError on the
# next reused connection. urllib3's Retry only auto-retries that class of
# error ("read error") for methods it considers safe by default, which
# excludes POST — so bulk lookups like resolve_names()'s POST to
# /universe/names/ (used by every /api/char/data poll) went unretried while
# the equivalent GET calls were silently retried, causing a 500 on
# /api/char/data whenever it happened to land on a POST.
# ---------------------------------------------------------------------------

class TestSessionRetryCoversPost:
    def test_post_is_in_the_allowed_retry_methods(self):
        assert "POST" in lp_web._RETRY.allowed_methods

    def test_get_is_still_in_the_allowed_retry_methods(self):
        assert "GET" in lp_web._RETRY.allowed_methods

    def test_stale_connection_on_post_is_retried_transparently(self):
        """A ConnectionError on the first attempt of a POST must not surface —
        the mounted adapter should retry it on a fresh connection and return
        the eventual successful response, exactly like it already does for
        GET (see the SESSION.mount comment)."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        attempts = {"n": 0}

        class FlakyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                attempts["n"] += 1
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                if attempts["n"] == 1:
                    # Simulate the server closing a stale keep-alive connection:
                    # drop it without writing any response at all.
                    self.close_connection = True
                    return
                body = b"[]"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a, **k):
                pass

        srv = HTTPServer(("127.0.0.1", 0), FlakyHandler)
        port = srv.server_address[1]
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01),
                         daemon=True).start()
        try:
            r = lp_web.SESSION.post(f"http://127.0.0.1:{port}/universe/names/", json=[1])
            assert r.status_code == 200
            assert attempts["n"] == 2
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# XSS: SSO callback error must be HTML-escaped
# ---------------------------------------------------------------------------

class TestSSOCallbackXSS:
    """v1.67.3: error param from EVE SSO must be HTML-escaped to prevent XSS."""

    def test_error_param_is_escaped(self, tmp_server):
        base, _ = tmp_server
        xss_payload = "<script>alert(1)</script>"
        r = requests.get(f"{base}/callback?error={xss_payload}")
        assert "&lt;script&gt;" in r.text
        assert "<script>alert(1)</script>" not in r.text


# ---------------------------------------------------------------------------
# Settings persistence: don't clobber the saved corp with a blank field
# ---------------------------------------------------------------------------

class TestSettingsPersistenceGate:
    """setPref() must not push to the server before loadSettings() has applied
    the authoritative state — otherwise applying the fetched values into the DOM
    (or a boot-time refresh) would echo defaults straight back over the server."""

    def test_setpref_is_gated_until_settings_applied(self):
        src = lp_web.FRONTEND_SOURCE
        assert "let _settingsReady = false;" in src
        # setPref writes the in-memory mirror but sends nothing while not ready.
        assert "if(!_settingsReady) return;" in src
        # loadSettings opens the gate once the server response is applied.
        assert "markSettingsApplied()" in src

    def test_no_localstorage_settings_blob(self):
        """The old localStorage settings cache is gone — the server is the sole
        source of truth, so nothing writes the whole-settings blob locally."""
        src = lp_web.FRONTEND_SOURCE
        assert "localStorage.setItem(LS_KEY" not in src
        assert "settingsBlob(" not in src
        assert "suppressServerSync" not in src


# ---------------------------------------------------------------------------
# Live character sync: SSE push wiring on the client
# ---------------------------------------------------------------------------

class TestCharStreamClient:
    """v1.91.0: the browser opens an SSE stream so the backend can nudge it to
    re-pull character data the instant new data is detected."""

    def test_char_stream_client_wired(self):
        src = lp_web.FRONTEND_SOURCE
        assert 'new EventSource("/api/char/stream")' in src
        assert "function openCharStream" in src
        assert "function closeCharStream" in src

    def test_char_stream_route_registered(self):
        assert '/api/char/stream' in lp_web.FRONTEND_SOURCE


# ---------------------------------------------------------------------------
# Industry detail: what-if ME/TE simulation override
# ---------------------------------------------------------------------------

class TestIndDetailSimMeTe:
    """The planner lets you override ME/TE for one detail request (session-only,
    never persisted). sim_me/sim_te win over the character/owned baseline, are
    clamped to EVE's ranges (ME 0–10, TE 0–20), and flip detail['sim_me_te']."""

    def _build_sde(self, tmp_path):
        from tests.test_ind_core import _fake_session
        import ind_core
        ind_core.build_sde_db(tmp_path, session=_fake_session())

    def _setup(self, tmp_path, monkeypatch):
        self._build_sde(tmp_path)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        acct = lp_web.Account(42)
        acct.characters[42] = {"character_id": 42, "name": "T", "scopes": [],
                               "refresh_token": "x"}
        acct.active_char_id = 42
        _use_account(acct)
        monkeypatch.setattr(lp_web, "fetch_prices",
                            lambda tids, *a, **k: {t: {"sell_min": 100.0, "buy_max": 90.0}
                                                   for t in tids})
        monkeypatch.setattr(lp_web.ind_core, "fetch_adjusted_prices",
                            lambda *a, **k: {})
        monkeypatch.setattr(lp_web, "resolve_names",
                            lambda tids, *a, **k: {t: str(t) for t in tids})
        monkeypatch.setattr(lp_web, "resolve_volumes",
                            lambda tids, *a, **k: {t: 1.0 for t in tids})
        monkeypatch.setattr(lp_web, "fetch_history_volumes",
                            lambda tids, *a, **k: {list(tids)[0]: 100.0})
        monkeypatch.setattr(lp_web.arb_core, "fetch_type_orders",
                            lambda *a, **k: [])
        return acct

    def test_no_sim_uses_baseline_and_flag_false(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        d = lp_web.do_ind_detail({"blueprint_id": ["681"]})
        assert d["me_used"] == 0 and d["te_used"] == 0
        assert d["sim_me_te"] is False

    def test_sim_overrides_me_te_and_sets_flag(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        d = lp_web.do_ind_detail({"blueprint_id": ["681"],
                                  "sim_me": ["10"], "sim_te": ["20"]})
        assert d["me_used"] == 10 and d["te_used"] == 20
        assert d["sim_me_te"] is True

    def test_sim_reduces_material_quantities(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        base = lp_web.do_ind_detail({"blueprint_id": ["681"]})
        simmed = lp_web.do_ind_detail({"blueprint_id": ["681"], "sim_me": ["10"]})
        base_q = {m["type_id"]: m["eff_qty"] for m in base["required_items"]}
        sim_q = {m["type_id"]: m["eff_qty"] for m in simmed["required_items"]}
        # ME 10 shaves material use versus the unresearched baseline.
        assert any(sim_q[t] < base_q[t] for t in base_q)

    def test_sim_clamped_to_valid_ranges(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        d = lp_web.do_ind_detail({"blueprint_id": ["681"],
                                  "sim_me": ["99"], "sim_te": ["-5"]})
        assert d["me_used"] == 10   # capped at 10
        assert d["te_used"] == 0    # floored at 0

    def test_sim_ignores_blank_and_garbage(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        d = lp_web.do_ind_detail({"blueprint_id": ["681"],
                                  "sim_me": [""], "sim_te": ["abc"]})
        assert d["me_used"] == 0 and d["te_used"] == 0
        assert d["sim_me_te"] is False


# ---------------------------------------------------------------------------
# Industry detail: T2 (invention) blueprint with prices NOT refreshed
# ---------------------------------------------------------------------------

class TestIndDetailT2RegionId:
    """v1.110.2: do_ind_detail assigned `region_id` only inside two branches —
    `if refresh_prices:` and `if not bp.get("invention"):`. For a T2 item (which
    IS invented, so the second branch is skipped) opened WITHOUT a price refresh,
    neither branch ran, and the unconditional read of region_id for the region
    name / history lookup blew up with UnboundLocalError. This exercises exactly
    that path (T2 blueprint 700 from the ind_core SDE fixtures, refresh off)."""

    def _build_sde(self, tmp_path):
        from tests.test_ind_core import _fake_session
        import ind_core
        ind_core.build_sde_db(tmp_path, session=_fake_session())

    def test_t2_detail_without_refresh_does_not_raise(self, tmp_path, monkeypatch):
        self._build_sde(tmp_path)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        # A logged-in account is required; no owned BPs/skills needed here.
        acct = lp_web.Account(42)
        acct.characters[42] = {"character_id": 42, "name": "T", "scopes": [],
                               "refresh_token": "x"}
        acct.active_char_id = 42
        _use_account(acct)

        # Stub every network-touching helper so only the region_id code path runs.
        monkeypatch.setattr(lp_web, "fetch_prices",
                            lambda tids, *a, **k: {t: {"sell_min": 1.0, "buy_max": 1.0}
                                                   for t in tids})
        monkeypatch.setattr(lp_web.ind_core, "fetch_adjusted_prices",
                            lambda *a, **k: {})
        monkeypatch.setattr(lp_web, "resolve_names",
                            lambda tids, *a, **k: {t: str(t) for t in tids})
        monkeypatch.setattr(lp_web, "resolve_volumes",
                            lambda tids, *a, **k: {t: 1.0 for t in tids})
        monkeypatch.setattr(lp_web, "fetch_history_volumes",
                            lambda tids, *a, **k: {list(tids)[0]: 100.0})

        # Blueprint 700 makes T2 product 12005 (Ishtar) — it is invented, so the
        # BPO region branch is skipped; refresh_prices defaults off.
        detail = lp_web.do_ind_detail({"blueprint_id": ["700"]})
        assert detail["region_name"] == lp_web.REGION_NAMES.get(10000002)
        assert detail["esi_prices"] is False
        # station_id is exposed so tracked-build snapshots can re-price against
        # the same hub when comparing frozen values to current prices.
        assert detail["station_id"] == lp_web.JITA_STATION_ID
