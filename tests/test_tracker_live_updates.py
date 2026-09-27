"""
The tracker board updates itself when the underlying data changes — no reload.

  * Server: saving an account's tracked builds / sell ledger / listed units with
    NEW content bumps a per-account *tracker* version on _CharPubSub; the
    /api/char/stream SSE loop wakes on it and emits a {"type":"tracker"} event.
    Re-saving identical content does not (read paths re-save unchanged blobs, and
    waking the board for those would loop: push → re-pull → re-save → push).
  * Client: the stream handler re-pulls just the board on "tracker" (and on
    "hello", to catch up after a reconnect); the board only re-renders when the
    server's builds actually differ; each sweep quietly refreshes stale market
    reads behind the visible tiles / focused card.
"""
import threading
import time
from pathlib import Path

import pytest

import importlib.util
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)

_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()
_CHAR_JS = (_ROOT / "static" / "js" / "char.js").read_text()


def _sim_fn(name, src=_IND_JS):
    start = src.index(f"function {name}(")
    rest = src[start + 1:]
    end = rest.find("\nfunction ")
    return rest if end < 0 else rest[:end]


@pytest.fixture(autouse=True)
def _legacy_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    lp_web._REQUEST.account = None
    monkeypatch.setattr(lp_web, "IND_BUILDS_PATH", tmp_path / "builds.json")
    monkeypatch.setattr(lp_web, "IND_SELL_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(lp_web, "IND_LISTED_UNITS_PATH", tmp_path / "listed.json")
    lp_web._TRACKER_DIGESTS.clear()
    yield
    lp_web._REQUEST.account = None


def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "scopes": [], "refresh_token": "x",
                       "name": "Main"}
    a.active_char_id = 1
    return a


class TestTrackerVersion:
    def test_bump_tracker_is_separate_from_char_version(self):
        ps = lp_web._CharPubSub()
        ps.bump_tracker("k")
        assert ps.tracker_version("k") == 1
        assert ps.version("k") == 0

    def test_wait_wakes_on_tracker_bump_when_asked(self):
        ps = lp_web._CharPubSub()
        threading.Timer(0.1, lambda: ps.bump_tracker("k")).start()
        t0 = time.time()
        ps.wait("k", 0, 0, timeout=5, last_tracker=0)
        assert time.time() - t0 < 2
        assert ps.tracker_version("k") == 1

    def test_wait_ignores_tracker_unless_asked(self):
        # Existing callers (no last_tracker) keep their old semantics.
        ps = lp_web._CharPubSub()
        ps.bump_tracker("k")
        t0 = time.time()
        ps.wait("k", 0, 0, timeout=0.2)
        assert time.time() - t0 >= 0.2

    def test_forget_clears_tracker_version(self):
        ps = lp_web._CharPubSub()
        ps.bump_tracker("k")
        ps.forget("k")
        assert ps.tracker_version("k") == 0


class TestSavesNotify:
    def _spy(self, monkeypatch):
        bumps = []
        monkeypatch.setattr(lp_web._CHAR_PUBSUB, "bump_tracker",
                            lambda k: bumps.append(k))
        return bumps

    def test_saving_builds_notifies(self, monkeypatch):
        bumps = self._spy(monkeypatch)
        acct = _acct()
        lp_web._save_tracked_builds(acct, [{"id": "a", "runs": 1}])
        assert bumps == [id(acct)]

    def test_identical_resave_does_not_notify(self, monkeypatch):
        bumps = self._spy(monkeypatch)
        acct = _acct()
        lp_web._save_tracked_builds(acct, [{"id": "a", "runs": 1}])
        lp_web._save_tracked_builds(acct, [{"id": "a", "runs": 1}])
        assert bumps == [id(acct)]
        lp_web._save_tracked_builds(acct, [{"id": "a", "runs": 2}])
        assert bumps == [id(acct), id(acct)]

    def test_ledger_save_notifies_on_change(self, monkeypatch):
        bumps = self._spy(monkeypatch)
        acct = _acct()
        lp_web._save_sell_ledger(acct, {"34": []})
        lp_web._save_sell_ledger(acct, {"34": []})
        lp_web._save_sell_ledger(acct, {"34": [{"transaction_id": 1}]})
        assert len(bumps) == 2

    def test_blobs_are_tracked_independently(self, monkeypatch):
        # Same content in two different blobs is still two changes.
        bumps = self._spy(monkeypatch)
        acct = _acct()
        lp_web._save_tracked_builds(acct, [])
        lp_web._save_sell_ledger(acct, [])
        assert len(bumps) == 2

    def test_listed_units_notify_goes_through_digest(self):
        src = (_ROOT / "lp-web.py").read_text()
        assert '_notify_tracker(acct, "ind_listed_units", store)' in src


class TestStreamEmitsTracker:
    def test_stream_waits_on_and_emits_tracker(self):
        src = (_ROOT / "lp-web.py").read_text()
        start = src.index("def _handle_char_stream(")
        fn = src[start:src.index("def do_GET(", start)]
        assert "last_tracker=last_tr" in fn
        assert '"type": "tracker"' in fn
        # A tracker event is a real write — no redundant heartbeat after it.
        assert 'elif not tracker_changed and not self._sse_comment("ping")' in fn


class TestClientLiveBoard:
    def test_stream_handler_routes_tracker_pushes(self):
        start = _CHAR_JS.index("function openCharStream(")
        fn = _CHAR_JS[start:_CHAR_JS.index("function closeCharStream(")]
        assert 'm.type==="tracker"' in fn
        assert "indOnTrackerPush()" in fn
        # "hello" (reconnect) also catches the board up.
        assert '(m.type==="tracker" || m.type==="hello")' in fn
        # Each sweep refreshes stale market reads behind the board.
        assert "indRefreshLiveMarket()" in fn

    def test_push_is_coalesced(self):
        fn = _sim_fn("indOnTrackerPush")
        assert "clearTimeout(_trackerPushTimer)" in fn
        assert "setTimeout(refreshIndBuilds" in fn

    def test_refresh_only_rerenders_on_real_change(self):
        fn = _sim_fn("refreshIndBuilds")
        assert '"/api/ind/builds"' in fn
        assert "raw!==IND.buildsRaw" in fn
        # Tiles keep their lane while the fresh roll-up is in flight.
        assert "mergeSummaryBuilds(SUMMARY.data)" in fn
        # A build deleted elsewhere drops out of focus instead of dangling.
        assert "IND.focusedBuild=null" in fn
        assert "loadSummary()" in fn
        # The initial load seeds the comparison baseline.
        assert "IND.buildsRaw=JSON.stringify(IND.builds)" in _sim_fn("loadIndBuilds")

    def test_market_reads_refresh_quietly_when_stale(self):
        fn = _sim_fn("_deciderEnsure")
        assert "_DECIDER_TTL" in fn
        assert "_fetchDeciderLive(b, true)" in fn
        assert "_fetchDeciderMarket(b, true)" in fn
        # A failed background refresh keeps the last good read on screen.
        assert "if(!ok && quiet && st.live) return" in _sim_fn("_fetchDeciderLive")
        assert "if(!ok && quiet && st.market) return" in _sim_fn("_fetchDeciderMarket")
        # The board's fetch entry points go through it.
        assert "_deciderEnsure(b, true)" in _sim_fn("_prefetchListedFlags")
        assert "_deciderEnsure(b, stage!==\"building\")" in _sim_fn("_wireInsight")

    def test_sweep_refresh_targets_only_what_is_shown(self):
        fn = _sim_fn("indRefreshLiveMarket")
        assert 'ACTIVE_TAB!=="ind"' in fn and 'IND.mode!=="summary"' in fn
        assert 'stage==="listed"' in fn
        assert "IND.focusedBuild" in fn


class TestLiveUpdateRobustness:
    def test_hydration_race_keeps_one_account_object(self, monkeypatch):
        # Two requests hydrating the same account at once must end up sharing one
        # Account: the tracker pubsub is keyed on id(acct), so a split leaves a
        # browser's stream listening on an object nobody bumps.
        racer = lp_web.Account(77)

        def account_get(aid):
            lp_web._ACCOUNTS[aid] = racer        # the other thread won meanwhile
            return {"characters": {}}
        monkeypatch.setattr(lp_web.pg_store, "account_get", account_get)
        monkeypatch.setattr(lp_web, "_hydrate_account", lambda aid, d: lp_web.Account(aid))
        monkeypatch.setattr(lp_web, "_ACCOUNTS", {})
        assert lp_web._get_account_by_id(77) is racer

    def test_session_cache_keeps_first_account(self, monkeypatch):
        first = lp_web.Account(78)
        monkeypatch.setattr(lp_web, "_SESSIONS", {})
        monkeypatch.setattr(lp_web.pg_store, "session_get",
                            lambda sid: (lp_web._SESSIONS.__setitem__(sid, first), 78)[1])
        monkeypatch.setattr(lp_web, "_get_account_by_id", lambda aid: lp_web.Account(aid))
        assert lp_web._resolve_session("sid-x") is first

    def test_forgetting_an_account_drops_its_digests(self):
        acct = _acct()
        lp_web._save_tracked_builds(acct, [{"id": "a"}])
        assert any(k[0] == id(acct) for k in lp_web._TRACKER_DIGESTS)
        lp_web._forget_tracker_digests(acct)
        assert not any(k[0] == id(acct) for k in lp_web._TRACKER_DIGESTS)
        src = (_ROOT / "lp-web.py").read_text()
        fn = src[src.index("def _forget_account("):src.index("def do_auth_logout(")]
        assert "_forget_tracker_digests(acct)" in fn

    def test_listed_units_notify_under_the_ledger_lock(self):
        src = (_ROOT / "lp-web.py").read_text()
        fn = src[src.index("def _record_listed_units("):src.index("def do_ind_summary(")]
        # Indented into the `with _SELL_LEDGER_LOCK:` block.
        assert '\n        _notify_tracker(acct, "ind_listed_units", store)' in fn


class TestClientRobustness:
    def test_refresh_always_repulls_the_summary(self):
        fn = _sim_fn("refreshIndBuilds")
        # reconcileBuilds only re-pulls it once a build is delivered; otherwise the
        # refresh must, or a new/edited planned build leaves the strip stale.
        assert "if(IND.builds.some(b=>b.done_at)) return;" in fn
        assert "reconcileBuilds(); return;" not in fn

    def test_refresh_drops_out_of_order_replies(self):
        fn = _sim_fn("refreshIndBuilds")
        assert "const seq=++_indBuildsSeq" in fn
        assert "if(seq!==_indBuildsSeq) return" in fn

    def test_background_data_never_rebuilds_a_dragged_slider(self):
        rp = _sim_fn("_deciderRepaintBody")
        assert "st.dragging" in rp and "st.bodyStale=true" in rp
        for fetcher in ("_fetchDeciderLive", "_fetchDeciderMarket"):
            fn = _sim_fn(fetcher)
            assert "_deciderRepaintBody(b)" in fn
            assert "_renderDeciderBody(b)" not in fn
        wire = _sim_fn("_wireBuildDecider")
        assert '"pointerdown"' in wire and "st.dragging=true" in wire
        assert '"pointerup"' in wire and "st.bodyStale" in wire

    def test_market_ttl_is_under_the_sweep(self):
        # The sweep fires every 5 min; a TTL of exactly 5 min made alternate
        # sweeps find the read "fresh" and skip it.
        import re
        m = re.search(r"const _DECIDER_TTL=(\d+)\*60\*1000;", _IND_JS)
        assert m and int(m.group(1)) < 5

    def test_escape_closes_the_focused_card(self):
        i = _IND_JS.index('if(e.key!=="Escape" || !IND.focusedBuild')
        handler = _IND_JS[i:i + 600]
        assert "IND.focusedBuild=null; renderIndBuilds();" in handler
        assert 'tag==="INPUT"' in handler
        assert ".ind-modal:not(.hidden)" in handler

    def test_building_countdown_zero_repaints_the_insight(self):
        i = _IND_JS.index("else if(inBuildCard){")
        assert "_renderInsight(bb)" in _IND_JS[i:i + 400]
        ins = _sim_fn("_insightInner")
        assert '(ready?"Finished ":"ETA ")' in ins

    def test_player_strings_are_escaped(self):
        assert "function _indEsc(" in _IND_JS
        ins = _sim_fn("_insightInner")
        assert "_indEsc(b.char_name)" in ins
        assert "_indEsc(close.character_name)" in ins
        assert "_indEsc(_buildJobLocation(b))" in ins
        assert "_indEsc(b.product_name" in _sim_fn("_buildTileHtml")
