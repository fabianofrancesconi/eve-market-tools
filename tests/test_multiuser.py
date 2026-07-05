"""
Tests for the multi-user model (Postgres mode): browser sessions, per-account
isolation of settings / counters / last-scan, the auth gate, cookie handling,
and the one-time legacy→account migration.

Postgres itself isn't required — pg_store's accessors are replaced with a small
in-memory fake so the account/session logic can be exercised deterministically.
"""
import time
from pathlib import Path

import pytest

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)
from lp_core import LPError


class FakePG:
    """Minimal in-memory stand-in for the pg_store module (DATABASE_URL set)."""
    def __init__(self):
        self.kv = {}
        self.accounts = {}
        self.char_account = {}
        self.sessions = {}
        self.account_settings = {}
        self.user_settings = {}
        self.delivered_jobs = {}   # (aid, cid) -> data
        self.order_state = {}      # (aid, cid) -> (prev, sales)
        self.order_events = {}     # (aid, event_id) -> {ts, dismissed, data}

    def enabled(self):
        return True

    # kv
    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    # accounts
    def account_get(self, aid):
        return self.accounts.get(aid)

    def account_set(self, aid, data, ts):
        self.accounts[aid] = data

    def account_delete(self, aid):
        self.accounts.pop(aid, None)

    # char -> account
    def char_account_get(self, cid):
        return self.char_account.get(cid)

    def char_account_set(self, cid, aid):
        self.char_account[cid] = aid

    def char_account_delete(self, cid):
        self.char_account.pop(cid, None)

    # sessions
    def session_get(self, sid):
        s = self.sessions.get(sid)
        if s is None:
            return None
        s["last_seen"] = time.time()
        return s["account_id"]

    def session_set(self, sid, aid):
        self.sessions[sid] = {"account_id": aid, "last_seen": time.time()}

    def session_delete(self, sid):
        self.sessions.pop(sid, None)

    def sessions_sweep(self, max_idle):
        return 0

    # per-account settings
    def account_settings_get(self, aid):
        return self.account_settings.get(aid)

    def account_settings_set(self, aid, data, ts):
        self.account_settings[aid] = data

    # legacy per-char settings (read during migration)
    def user_settings_get(self, cid):
        return self.user_settings.get(cid)

    def all_account_ids(self):
        return list(self.accounts)

    # replica-safe counters
    def with_delivered_jobs(self, aid, cid, mutate):
        new_data, result = mutate(self.delivered_jobs.get((aid, cid)))
        if new_data is not None:
            self.delivered_jobs[(aid, cid)] = new_data
        return result

    def delivered_jobs_set(self, aid, cid, data):
        self.delivered_jobs[(aid, cid)] = data

    def with_order_state(self, aid, cid, mutate):
        prev, sales = self.order_state.get((aid, cid), ({}, {}))
        events, new_prev, new_sales, result = mutate(prev, sales)
        for ev in events:
            self.order_events.setdefault((aid, ev["id"]),
                {"ts": ev["ts"], "dismissed": False, "data": ev})
        self.order_state[(aid, cid)] = (new_prev, new_sales)
        return result

    def order_state_set(self, aid, cid, prev, sales):
        self.order_state[(aid, cid)] = (prev, sales)

    def order_events_active(self, aid, cutoff_ts):
        rows = [r for (a, _eid), r in self.order_events.items()
                if a == aid and not r["dismissed"] and r["ts"] >= cutoff_ts]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return [r["data"] for r in rows]

    def order_events_dismiss(self, aid, event_id):
        for (a, eid), r in self.order_events.items():
            if a == aid and (event_id == "all" or eid == event_id):
                r["dismissed"] = True


@pytest.fixture
def pg(monkeypatch):
    fake = FakePG()
    monkeypatch.setattr(lp_web, "pg_store", fake)
    # fresh in-memory caches for each test
    monkeypatch.setattr(lp_web, "_SESSIONS", {})
    monkeypatch.setattr(lp_web, "_ACCOUNTS", {})
    lp_web._REQUEST.account = None
    yield fake
    lp_web._REQUEST.account = None


def _acct(fake, cid, name="Char"):
    """Persist a one-character account into the fake store and return it."""
    a = lp_web.Account(cid)
    a.characters[cid] = {"character_id": cid, "name": name, "scopes": [],
                         "refresh_token": "rt", "access_token": None, "expires_at": 0}
    a.active_char_id = cid
    lp_web._persist_account(a)
    with lp_web._REGISTRY_LOCK:
        lp_web._ACCOUNTS[cid] = a
    return a


# ── Sessions ──────────────────────────────────────────────────────────────────

class TestSessions:
    def test_new_session_resolves_back_to_account(self, pg):
        a = _acct(pg, 100, "Main")
        sid = lp_web._new_session(a)
        assert lp_web._resolve_session(sid) is a

    def test_session_rehydrates_from_store_after_cache_clear(self, pg):
        a = _acct(pg, 100, "Main")
        sid = lp_web._new_session(a)
        # Drop the in-memory caches — a fresh process / redeploy.
        lp_web._SESSIONS.clear()
        lp_web._ACCOUNTS.clear()
        resolved = lp_web._resolve_session(sid)
        assert resolved is not None
        assert resolved.account_id == 100
        assert 100 in resolved.characters

    def test_unknown_or_empty_session_is_none(self, pg):
        assert lp_web._resolve_session("nope") is None
        assert lp_web._resolve_session(None) is None

    def test_char_account_index_written(self, pg):
        _acct(pg, 100, "Main")
        assert pg.char_account.get(100) == 100


# ── Per-account isolation ─────────────────────────────────────────────────────

class TestIsolation:
    def test_settings_isolated_per_account(self, pg):
        a1 = _acct(pg, 1, "A")
        a2 = _acct(pg, 2, "B")
        lp_web.save_account_settings(a1, {"active_tab": "lp"})
        lp_web.save_account_settings(a2, {"active_tab": "ind"})
        assert lp_web.load_account_settings(a1) == {"active_tab": "lp"}
        assert lp_web.load_account_settings(a2) == {"active_tab": "ind"}

    def test_counters_isolated_per_account(self, pg):
        a1 = _acct(pg, 1, "A")
        a2 = _acct(pg, 2, "B")
        job = {"job_id": 7, "status": "delivered", "runs": 3, "product_type_id": 9}
        # First sight is baseline for each account independently.
        lp_web._track_delivered_jobs(a1, 1, [job], {})
        lp_web._track_delivered_jobs(a1, 1, [job, {"job_id": 8, "status": "delivered",
                                                   "runs": 5, "product_type_id": 9}], {})
        rt2 = lp_web._track_delivered_jobs(a2, 2, [job], {})
        # a2 has only ever seen its baseline → zero counted; separate table rows.
        assert rt2["total_runs"] == 0
        assert (1, 1) in pg.delivered_jobs
        assert (2, 2) in pg.delivered_jobs
        # a1 counted the second delivery (job 8); a2 never counted anything.
        assert pg.delivered_jobs[(1, 1)]["total_runs"] == 5
        assert pg.delivered_jobs[(2, 2)]["total_runs"] == 0

    def test_last_scan_isolated_per_account(self, pg):
        a1 = _acct(pg, 1, "A")
        a2 = _acct(pg, 2, "B")
        lp_web._save_last_scan(a1, "lp", {"rows": [1]})
        lp_web._save_last_scan(a2, "lp", {"rows": [2, 3]})
        assert lp_web._load_last_scan(a1, "lp") == {"rows": [1]}
        assert lp_web._load_last_scan(a2, "lp") == {"rows": [2, 3]}

    def test_order_events_isolated_per_account(self, pg):
        a1 = _acct(pg, 1, "A")
        a2 = _acct(pg, 2, "B")
        orders0 = [{"order_id": 1, "type_id": 34, "type_name": "Trit",
                    "volume_remain": 100, "price": 5.0, "is_buy_order": False}]
        lp_web._track_order_changes(a1, 1, orders0, {})
        orders1 = [{"order_id": 1, "type_id": 34, "type_name": "Trit",
                    "volume_remain": 80, "price": 5.0, "is_buy_order": False}]
        lp_web._track_order_changes(a1, 1, orders1, {})
        # a2 never traded → no events for it; a1 has one.
        assert len(lp_web._get_order_events(a1)) == 1
        assert lp_web._get_order_events(a2) == []


# ── Auth gate + cookies (Handler pieces, no socket) ───────────────────────────

class _FakeHandler(lp_web.Handler):
    def __init__(self, cookie=""):
        self.headers = {"Cookie": cookie}
        self.sent = None

    def _send_json(self, obj, status=200):
        self.sent = (status, obj)


class TestGate:
    def test_cookie_parsing(self, pg):
        h = _FakeHandler("emt_sid=abc123; other=x")
        assert h._cookies().get("emt_sid") == "abc123"

    def test_unauthenticated_non_public_is_blocked(self, pg):
        h = _FakeHandler("")               # no session cookie
        h._setup_request()
        assert lp_web.current_account() is None
        assert h._gate("/api/scan") is False
        assert h.sent[0] == 401
        assert h.sent[1].get("login_required") is True

    def test_public_paths_allowed_without_session(self, pg):
        h = _FakeHandler("")
        h._setup_request()
        for p in ("/", "/api/auth/login", "/api/auth/status", "/callback", "/favicon.ico"):
            assert h._gate(p) is True

    def test_valid_session_passes_gate(self, pg):
        a = _acct(pg, 100, "Main")
        sid = lp_web._new_session(a)
        h = _FakeHandler(f"emt_sid={sid}")
        h._setup_request()
        assert lp_web.current_account() is a
        assert h._gate("/api/scan") is True

    def test_legacy_mode_never_gates(self, monkeypatch):
        # No DATABASE_URL → legacy account, gate is a no-op even for API paths.
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)
        h = _FakeHandler("")
        h._setup_request()
        assert lp_web.current_account() is lp_web._LEGACY_ACCOUNT
        assert h._gate("/api/scan") is True


class TestCookiesAndLogout:
    def test_expire_cookie_header_clears(self, pg):
        h = lp_web._expire_cookie_header()
        assert h.startswith("emt_sid=;")
        assert "Max-Age=0" in h and "HttpOnly" in h

    def test_session_cookie_header_has_flags(self, pg):
        h = lp_web._cookie_header("abc")
        assert h.startswith("emt_sid=abc;")
        assert "HttpOnly" in h and "SameSite=Lax" in h and "Max-Age=" in h

    def test_logout_deletes_session_and_account(self, pg):
        a = _acct(pg, 100, "Main")
        sid = lp_web._new_session(a)
        lp_web._REQUEST.account = a
        lp_web.do_auth_logout({})       # full logout
        assert pg.session_get(sid) is None      # session row gone
        assert pg.account_get(100) is None      # account row gone
        assert sid not in lp_web._SESSIONS      # dropped from cache


class TestScanCap:
    def test_saturated_scans_return_503(self, pg):
        acquired = 0
        while lp_web._SCAN_SLOTS.acquire(blocking=False):
            acquired += 1
        try:
            h = _FakeHandler("")
            h._handle_sse_scan({}, lambda q, emit=None: {}, "lp")
            assert h.sent[0] == 503
        finally:
            for _ in range(acquired):
                lp_web._SCAN_SLOTS.release()

    def test_slot_released_after_scan(self, pg):
        # A completed scan must return its slot (no leak). _FakeHandler lacks the
        # SSE socket methods, so stub the streaming bits to exercise the finally.
        h = _FakeHandler("")
        h.send_response = lambda *a, **k: None
        h.send_header = lambda *a, **k: None
        h.end_headers = lambda *a, **k: None
        h._sse_emit = lambda data: None
        before = lp_web._SCAN_SLOTS._value
        h._handle_sse_scan({}, lambda q, emit=None: {"rows": []}, "arb")
        assert lp_web._SCAN_SLOTS._value == before


# ── Legacy → account migration ────────────────────────────────────────────────

class TestMigration:
    def test_migrates_v2_blob_and_settings(self, pg):
        pg.kv["eve_auth"] = {
            "version": 2, "active_char_id": 100,
            "characters": [
                {"character_id": 100, "name": "Main", "scopes": [], "refresh_token": "rt1"},
                {"character_id": 200, "name": "Alt", "scopes": [], "refresh_token": "rt2"},
            ],
        }
        pg.user_settings[100] = {"active_tab": "ind"}
        lp_web._migrate_legacy_auth()
        # account created keyed by the active char, both chars indexed to it
        assert 100 in pg.accounts
        assert pg.char_account[100] == 100
        assert pg.char_account[200] == 100
        # old per-character settings carried over to the account key
        assert pg.account_settings[100] == {"active_tab": "ind"}
        assert pg.kv.get("eve_auth_migrated") is True

    def test_migration_is_idempotent(self, pg):
        pg.kv["eve_auth"] = {
            "version": 2, "active_char_id": 5,
            "characters": [{"character_id": 5, "name": "X", "scopes": [], "refresh_token": "r"}],
        }
        lp_web._migrate_legacy_auth()
        # second run must not raise or duplicate
        pg.accounts.clear()
        lp_web._migrate_legacy_auth()
        assert pg.accounts == {}   # flag already set → no re-migration

    def test_no_legacy_blob_just_sets_flag(self, pg):
        lp_web._migrate_legacy_auth()
        assert pg.kv.get("eve_auth_migrated") is True
        assert pg.accounts == {}


class TestCounterMigration:
    def test_delivered_jobs_from_bare_and_namespaced(self, pg):
        _acct(pg, 100, "Main")          # account 100, char_account[100]=100
        pg.char_account[200] = 100      # alt also on account 100
        # bare (pre-1.81.0) historical counts
        pg.kv["ind_jobs_delivered"] = {
            "100": {"seen_job_ids": [1, 2], "total_runs": 50, "total_jobs": 5,
                    "since": 1.0, "by_product": {}},
        }
        # namespaced (post-1.81.0) re-baselined entry — should only union seen ids
        pg.kv["ind_jobs_delivered:100"] = {
            "100": {"seen_job_ids": [2, 3], "total_runs": 0, "total_jobs": 0,
                    "since": 2.0, "by_product": {}},
            "200": {"seen_job_ids": [9], "total_runs": 7, "total_jobs": 1,
                    "since": 3.0, "by_product": {}},
        }
        lp_web._migrate_counters()
        e100 = pg.delivered_jobs[(100, 100)]
        assert e100["total_runs"] == 50                       # historical kept
        assert set(e100["seen_job_ids"]) == {1, 2, 3}         # ids unioned
        assert pg.delivered_jobs[(100, 200)]["total_runs"] == 7
        assert pg.kv["counters_migrated"] is True

    def test_idempotent(self, pg):
        _acct(pg, 5, "X")
        pg.kv["ind_jobs_delivered:5"] = {
            "5": {"seen_job_ids": [], "total_runs": 3, "total_jobs": 1,
                  "since": 1.0, "by_product": {}}}
        lp_web._migrate_counters()
        pg.delivered_jobs.clear()
        lp_web._migrate_counters()      # flag set → no re-migration
        assert pg.delivered_jobs == {}


# ── Re-login resolves to the existing account (via the char index) ────────────

class TestReLogin:
    def test_known_character_rejoins_its_account(self, pg):
        a = _acct(pg, 100, "Main")
        # A fresh visit (no cookie) for a character already tied to an account
        # should resolve to that same account, not a new one.
        assert pg.char_account_get(100) == 100
        resolved = lp_web._get_account_by_id(pg.char_account_get(100))
        assert resolved.account_id == a.account_id
