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
        cur = ps.wait_for_change("k", last_version=0, timeout=5)
        assert cur == 1
        assert time.time() - t0 < 1  # did not block

    def test_wait_times_out_when_no_change(self):
        ps = lp_web._CharPubSub()
        t0 = time.time()
        cur = ps.wait_for_change("k", last_version=0, timeout=0.2)
        assert cur == 0
        assert time.time() - t0 >= 0.2

    def test_wait_wakes_on_bump_from_another_thread(self):
        ps = lp_web._CharPubSub()
        threading.Timer(0.1, lambda: ps.bump("k")).start()
        cur = ps.wait_for_change("k", last_version=0, timeout=5)
        assert cur == 1

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
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: [])

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
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: [])

        out = lp_web.do_char_data({})
        assert out["loyalty_last_modified"] == "Tue, 07 Jul 2026 10:00:00 GMT"
        assert out["loyalty_expires"] == "Tue, 07 Jul 2026 11:00:00 GMT"
