"""
Tests for the character-data live-sync machinery:

  * `_char_data_signature` — what counts as a "change" worth nudging the UI
    (character-owned state) vs. what's ignored (market price noise).
  * `_CharPubSub` — the per-account version + Condition backing the SSE push.
  * `_fetch_one_char_data` — bumps the account version only when the fetched
    bundle actually changed.
  * `do_char_data` — surfaces ESI's loyalty Last-Modified/Expires so the UI can
    show an honest "LP as of …" timestamp.
"""
import threading
import time
from pathlib import Path

import pytest

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


@pytest.fixture(autouse=True)
def _legacy_mode(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    lp_web._REQUEST.account = None
    yield
    lp_web._REQUEST.account = None


def _acct(chars):
    ids = list(chars.keys())
    a = lp_web.Account(ids[0])
    for cid, val in chars.items():
        a.characters[cid] = {"character_id": cid, "scopes": [],
                             "refresh_token": "x", **val}
    a.active_char_id = ids[0]
    return a


# ── Signature: what nudges the UI ─────────────────────────────────────────────

class TestSignature:
    def _base(self):
        return {"wallet": 1_000.0, "total_sp": 5_000_000, "unallocated_sp": 0,
                "loyalty": [{"corp_id": 1, "loyalty_points": 50_000}],
                "skillqueue": [{"skill_id": 3300, "finished_level": 5,
                                "finish_date": "2026-08-01T00:00:00Z"}],
                "jobs": [{"job_id": 9, "status": "active", "end": "2026-07-08T00:00:00Z"}],
                "market_orders": [{"order_id": 7, "price": 100.0, "volume_remain": 3,
                                   "market_sell": 120.0}]}

    def test_identical_state_same_signature(self):
        assert lp_web._char_data_signature(self._base()) == \
            lp_web._char_data_signature(self._base())

    def test_wallet_change_bumps_signature(self):
        a = self._base(); b = self._base(); b["wallet"] = 2_000.0
        assert lp_web._char_data_signature(a) != lp_web._char_data_signature(b)

    def test_loyalty_change_bumps_signature(self):
        a = self._base(); b = self._base()
        b["loyalty"] = [{"corp_id": 1, "loyalty_points": 60_000}]
        assert lp_web._char_data_signature(a) != lp_web._char_data_signature(b)

    def test_market_price_movement_ignored(self):
        """A change in the *market* sell price (not the capsuleer's own state)
        must not change the signature — otherwise every fetch would spam pushes."""
        a = self._base(); b = self._base()
        b["market_orders"][0]["market_sell"] = 999.0
        assert lp_web._char_data_signature(a) == lp_web._char_data_signature(b)

    def test_own_order_price_change_bumps_signature(self):
        a = self._base(); b = self._base()
        b["market_orders"][0]["price"] = 105.0  # user relisted
        assert lp_web._char_data_signature(a) != lp_web._char_data_signature(b)


# ── Pub/sub ───────────────────────────────────────────────────────────────────

class TestCharPubSub:
    def test_version_starts_at_zero_and_bumps(self):
        ps = lp_web._CharPubSub()
        assert ps.version("k") == 0
        ps.bump("k")
        assert ps.version("k") == 1
        ps.bump("k")
        assert ps.version("k") == 2

    def test_wait_returns_immediately_when_version_differs(self):
        ps = lp_web._CharPubSub()
        ps.bump("k")  # now version 1
        t0 = time.time()
        ver, sweep = ps.wait("k", last_version=0, last_sweep=0, timeout=5)
        assert ver == 1 and sweep == 0
        assert time.time() - t0 < 1  # did not block

    def test_wait_times_out_when_nothing_changes(self):
        ps = lp_web._CharPubSub()
        t0 = time.time()
        ver, sweep = ps.wait("k", last_version=0, last_sweep=0, timeout=0.2)
        assert ver == 0 and sweep == 0
        assert time.time() - t0 >= 0.2

    def test_wait_wakes_on_bump_from_another_thread(self):
        ps = lp_web._CharPubSub()
        threading.Timer(0.1, lambda: ps.bump("k")).start()
        ver, _ = ps.wait("k", last_version=0, last_sweep=0, timeout=5)
        assert ver == 1

    def test_wait_wakes_on_sweep_even_without_data_change(self):
        """A background sweep wakes every waiter so all clients re-publish the
        shared countdown, even accounts whose data didn't change."""
        ps = lp_web._CharPubSub()
        threading.Timer(0.1, ps.announce_sweep).start()
        ver, sweep = ps.wait("k", last_version=0, last_sweep=0, timeout=5)
        assert ver == 0 and sweep == 1  # data unchanged, sweep advanced

    def test_forget_clears_version(self):
        ps = lp_web._CharPubSub()
        ps.bump("k")
        ps.forget("k")
        assert ps.version("k") == 0


# ── Bump on real fetch ────────────────────────────────────────────────────────

class TestFetchBumpsOnChange:
    def _setup(self, monkeypatch, tmp_path, wallet):
        cache = tmp_path / "cache"
        cache.mkdir(exist_ok=True)
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: wallet)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 1, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: ([], {}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: ([], {}))

    def _acct(self):
        return _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})

    def test_change_bumps_version(self, monkeypatch, tmp_path):
        acct = self._acct()
        bumps = []
        monkeypatch.setattr(lp_web._CHAR_PUBSUB, "bump", lambda k: bumps.append(k))
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)

        self._setup(monkeypatch, tmp_path, wallet=1_000.0)
        lp_web._fetch_one_char_data(acct, 1)            # first sight → bump
        lp_web._CHAR_DATA_CACHE.pop(1, None)            # force a re-fetch

        self._setup(monkeypatch, tmp_path, wallet=2_000.0)  # wallet changed
        lp_web._fetch_one_char_data(acct, 1)            # → bump again
        assert bumps == [id(acct), id(acct)]

    def test_unchanged_data_does_not_bump(self, monkeypatch, tmp_path):
        acct = self._acct()
        bumps = []
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)

        self._setup(monkeypatch, tmp_path, wallet=1_000.0)
        lp_web._fetch_one_char_data(acct, 1)            # first sight (seeds sig)
        monkeypatch.setattr(lp_web._CHAR_PUBSUB, "bump", lambda k: bumps.append(k))
        lp_web._CHAR_DATA_CACHE.pop(1, None)            # force re-fetch, same data
        lp_web._fetch_one_char_data(acct, 1)
        assert bumps == []                              # identical → no nudge


# ── do_char_data exposes loyalty freshness ────────────────────────────────────

class TestLoyaltyAsOf:
    def test_loyalty_last_modified_surfaced(self, monkeypatch, tmp_path):
        cache = tmp_path / "cache"; cache.mkdir()
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        acct = _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})
        lp_web._REQUEST.account = acct
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 0.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points",
                            lambda *a, **k: ([], {"last_modified": "Tue, 07 Jul 2026 10:00:00 GMT",
                                                  "expires": "Tue, 07 Jul 2026 11:00:00 GMT"}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: ([], {}))

        out = lp_web.do_char_data({})
        assert out["loyalty_last_modified"] == "Tue, 07 Jul 2026 10:00:00 GMT"
        assert out["loyalty_expires"] == "Tue, 07 Jul 2026 11:00:00 GMT"


# ── Research / copy jobs surface to the frontend (busy-blueprint note) ─────────

class TestResearchJobsSurfaced:
    """The industry planner shows a "busy being researched" note on blueprints
    tied up in an ME/TE research or copy job. That relies on those jobs reaching
    the client bundle un-filtered (not just manufacturing, activity_id 1), each
    carrying its activity label + blueprint_type_id + end date."""

    def _mock(self, monkeypatch, tmp_path, jobs):
        cache = tmp_path / "cache"; cache.mkdir(exist_ok=True)
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_track_delivered_jobs", lambda *a, **k: 0)
        monkeypatch.setattr(lp_web, "resolve_names",
                            lambda ids, *a, **k: {i: f"Type {i}" for i in ids})
        monkeypatch.setattr(lp_web, "resolve_station_names",
                            lambda ids, *a, **k: {i: "Some Structure" for i in ids})
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 0.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: ([], {}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: jobs)
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: ([], {}))
        acct = _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})
        lp_web._REQUEST.account = acct
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)

    def test_me_research_job_surfaces_with_label(self, monkeypatch, tmp_path):
        self._mock(monkeypatch, tmp_path, [
            {"job_id": 42, "activity_id": 4, "blueprint_type_id": 999,
             "product_type_id": 999, "runs": 1, "status": "active",
             "start_date": "2026-07-19T00:00:00Z", "end_date": "2026-07-20T00:00:00Z",
             "facility_id": 60003760},
        ])
        out = lp_web.do_char_data({})
        job = next(j for j in out["jobs"] if j["job_id"] == 42)
        assert job["activity_id"] == 4
        assert job["activity"] == "ME Research"
        assert job["blueprint_type_id"] == 999
        assert job["end"] == "2026-07-20T00:00:00Z"

    def test_copy_and_te_jobs_are_not_dropped(self, monkeypatch, tmp_path):
        self._mock(monkeypatch, tmp_path, [
            {"job_id": 1, "activity_id": 3, "blueprint_type_id": 111,
             "product_type_id": 111, "runs": 1, "status": "active",
             "end_date": "2026-07-20T00:00:00Z", "facility_id": 1},
            {"job_id": 2, "activity_id": 5, "blueprint_type_id": 222,
             "product_type_id": 333, "runs": 10, "status": "paused",
             "end_date": "2026-07-21T00:00:00Z", "facility_id": 1},
        ])
        out = lp_web.do_char_data({})
        by_id = {j["job_id"]: j for j in out["jobs"]}
        assert by_id[1]["activity"] == "TE Research"
        assert by_id[2]["activity"] == "Copying"
        assert by_id[2]["blueprint_type_id"] == 222

    def test_delivered_research_job_is_excluded(self, monkeypatch, tmp_path):
        """A finished/delivered job frees the blueprint, so it must not surface as
        a busy note — only active/paused/ready jobs are kept."""
        self._mock(monkeypatch, tmp_path, [
            {"job_id": 7, "activity_id": 4, "blueprint_type_id": 555,
             "product_type_id": 555, "runs": 1, "status": "delivered",
             "end_date": "2026-07-01T00:00:00Z", "facility_id": 1},
        ])
        out = lp_web.do_char_data({})
        assert all(j["job_id"] != 7 for j in out["jobs"])


# ── next_sync_in reflects the server's background-refresh schedule ─────────────

class TestNextSyncSchedule:
    def _setup(self, monkeypatch, tmp_path):
        cache = tmp_path / "cache"; cache.mkdir()
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        for fn, val in (("fetch_wallet", 0.0), ("fetch_skillqueue", []),
                        ("fetch_industry_jobs", []), ("fetch_market_orders", ([], {}))):
            monkeypatch.setattr(lp_web.sso_core, fn, lambda *a, _v=val, **k: _v)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: ([], {}))
        acct = _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})
        lp_web._REQUEST.account = acct
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)

    def test_next_sync_counts_down_to_scheduled_sweep(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        # Next background sweep is ~200s away → the reported countdown matches it
        # (not a fixed 5:00), so a page reload shows the real remaining time.
        monkeypatch.setattr(lp_web, "_BG_NEXT_SYNC_TS", time.time() + 200)
        out = lp_web.do_char_data({})
        assert 195 <= out["next_sync_in"] <= 200

    def test_next_sync_falls_back_to_interval_before_loop_starts(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        # Loop hasn't established a schedule yet (or it's overdue) → interval.
        monkeypatch.setattr(lp_web, "_BG_NEXT_SYNC_TS", 0.0)
        out = lp_web.do_char_data({})
        assert out["next_sync_in"] == lp_web._BG_REFRESH_INTERVAL

    def test_force_sync_resets_countdown_to_full_interval(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        # Only ~12s left on the shared timer. A manual force-sync (refresh=1) must
        # push the next sweep out to a fresh full interval and report that — not
        # the 12s that were left — so the browser's countdown resets to 5:00.
        monkeypatch.setattr(lp_web, "_BG_NEXT_SYNC_TS", time.time() + 12)
        lp_web._BG_WAKE.clear()
        out = lp_web.do_char_data({"refresh": ["1"]})
        assert out["next_sync_in"] >= lp_web._BG_REFRESH_INTERVAL - 5
        # …and the background loop was signalled to honour the new deadline rather
        # than sweeping at the old (now-stale) one.
        assert lp_web._BG_WAKE.is_set()

    def test_plain_poll_does_not_reset_countdown(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        # A non-forced poll must leave the schedule alone — it reports whatever is
        # left and never wakes/reschedules the loop.
        monkeypatch.setattr(lp_web, "_BG_NEXT_SYNC_TS", time.time() + 40)
        lp_web._BG_WAKE.clear()
        out = lp_web.do_char_data({})
        assert 35 <= out["next_sync_in"] <= 40
        assert not lp_web._BG_WAKE.is_set()


# ── New data from a sweep reaches connected clients ───────────────────────────

class TestNewDataReachesClients:
    """The full requirement: a server-side sweep that picks up a new LP budget
    must (a) bump the account version — every open /api/char/stream waits on it,
    so all connected clients get told to refresh — and (b) make the next
    do_char_data serve the new value, so a re-pulling client actually receives it
    (regardless of which page it's on)."""

    def _mock(self, monkeypatch, tmp_path, loyalty):
        cache = tmp_path / "cache"; cache.mkdir(exist_ok=True)
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "resolve_corp_name", lambda cid, sess: "Sisters of EVE")
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 0.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points",
                            lambda *a, _lp=loyalty, **k: (_lp, {}))
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: ([], {}))

    def test_new_lp_bumps_version_and_is_served(self, monkeypatch, tmp_path):
        acct = _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})
        lp_web._REQUEST.account = acct
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)

        # Sweep #1 — baseline LP.
        self._mock(monkeypatch, tmp_path, [{"corporation_id": 1000179, "loyalty_points": 500000}])
        lp_web._fetch_one_char_data(acct, 1)
        v0 = lp_web._CHAR_PUBSUB.version(id(acct))

        # Sweep #2 — LP grew. The version must bump (all streams get nudged) and a
        # client re-pull must see the new budget.
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        self._mock(monkeypatch, tmp_path, [{"corporation_id": 1000179, "loyalty_points": 987654}])
        lp_web._fetch_one_char_data(acct, 1)
        assert lp_web._CHAR_PUBSUB.version(id(acct)) == v0 + 1

        out = lp_web.do_char_data({})
        assert any(l["loyalty_points"] == 987654 for l in out["loyalty"]), out["loyalty"]

    def test_unchanged_lp_does_not_renudge(self, monkeypatch, tmp_path):
        acct = _acct({1: {"name": "Main", "access_token": "tok",
                          "expires_at": time.time() + 3600}})
        lp_web._REQUEST.account = acct
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._CHAR_DATA_SIG.pop(1, None)
        self._mock(monkeypatch, tmp_path, [{"corporation_id": 1000179, "loyalty_points": 500000}])
        lp_web._fetch_one_char_data(acct, 1)
        v = lp_web._CHAR_PUBSUB.version(id(acct))
        # A later sweep returning the SAME LP (ESI's ~1h cache is unchanged) must
        # not bump — clients aren't told to refresh when nothing actually changed.
        lp_web._CHAR_DATA_CACHE.pop(1, None)
        lp_web._fetch_one_char_data(acct, 1)
        assert lp_web._CHAR_PUBSUB.version(id(acct)) == v
