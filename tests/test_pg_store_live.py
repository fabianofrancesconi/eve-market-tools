"""
Live Postgres query tests for pg_store (the multi-user persistence layer).

These exercise the *actual* SQL — schema creation, upserts, the char->account
index, sessions (incl. last_seen + sweep), and per-account settings — against a
real database, plus one end-to-end session/settings roundtrip through lp-web.

Skipped unless a throwaway database is provided via EMT_TEST_DATABASE_URL, so the
default `pytest` run (and machines without psycopg/Postgres) are unaffected. CI
sets it to a Postgres service container; locally:

    docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=emt \
        -p 55432:5432 postgres:16
    EMT_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/emt \
        pytest tests/test_pg_store_live.py
"""
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
_TEST_DSN = os.environ.get("EMT_TEST_DATABASE_URL")
if not _TEST_DSN:
    pytest.skip("set EMT_TEST_DATABASE_URL to run live Postgres tests",
                allow_module_level=True)

import pg_store

_TABLES = ("mono_kv", "mono_user_settings", "mono_accounts",
           "mono_char_account", "mono_sessions", "mono_account_settings")


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    """Point pg_store at the test DB, ensure the schema, and start each test with
    empty tables."""
    monkeypatch.setenv("DATABASE_URL", _TEST_DSN)
    # Force a fresh pool bound to the test DSN + (re)create the schema.
    monkeypatch.setattr(pg_store, "_pool", None)
    monkeypatch.setattr(pg_store, "_schema_ready", False)
    pool = pg_store._get_pool()
    with pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(_TABLES))
    yield
    with pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(_TABLES))


class TestSchema:
    def test_enabled_and_tables_exist(self):
        assert pg_store.enabled() is True
        with pg_store._get_pool().connection() as conn:
            for t in _TABLES:
                # regclass raises if the table is missing
                conn.execute("SELECT to_regclass(%s) IS NOT NULL", (t,))
                assert conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]


class TestKV:
    def test_roundtrip_and_default(self):
        assert pg_store.kv_get("missing", "dflt") == "dflt"
        pg_store.kv_set("k", {"a": 1, "b": [2, 3]})
        assert pg_store.kv_get("k") == {"a": 1, "b": [2, 3]}

    def test_upsert_overwrites(self):
        pg_store.kv_set("k", {"v": 1})
        pg_store.kv_set("k", {"v": 2})
        assert pg_store.kv_get("k") == {"v": 2}


class TestAccounts:
    def test_set_get_delete(self):
        data = {"version": 2, "active_char_id": 5, "characters": [{"character_id": 5}]}
        pg_store.account_set(5, data, time.time())
        assert pg_store.account_get(5) == data
        pg_store.account_delete(5)
        assert pg_store.account_get(5) is None

    def test_upsert(self):
        pg_store.account_set(5, {"active_char_id": 5}, time.time())
        pg_store.account_set(5, {"active_char_id": 6}, time.time())
        assert pg_store.account_get(5)["active_char_id"] == 6


class TestCharAccountIndex:
    def test_set_get_delete(self):
        assert pg_store.char_account_get(100) is None
        pg_store.char_account_set(100, 100)
        pg_store.char_account_set(200, 100)   # alt joins same account
        assert pg_store.char_account_get(100) == 100
        assert pg_store.char_account_get(200) == 100
        pg_store.char_account_delete(200)
        assert pg_store.char_account_get(200) is None


class TestSessions:
    def test_set_get_delete(self):
        pg_store.session_set("sid1", 100)
        assert pg_store.session_get("sid1") == 100
        assert pg_store.session_get("nope") is None
        pg_store.session_delete("sid1")
        assert pg_store.session_get("sid1") is None

    def test_get_touches_last_seen(self):
        pg_store.session_set("sid1", 100)
        with pg_store._get_pool().connection() as conn:
            before = conn.execute(
                "SELECT last_seen FROM mono_sessions WHERE sid='sid1'").fetchone()[0]
        time.sleep(0.02)
        pg_store.session_get("sid1")
        with pg_store._get_pool().connection() as conn:
            after = conn.execute(
                "SELECT last_seen FROM mono_sessions WHERE sid='sid1'").fetchone()[0]
        assert after > before

    def test_sweep_removes_idle_only(self):
        pg_store.session_set("fresh", 1)
        pg_store.session_set("stale", 2)
        # Backdate the stale session well past any reasonable idle window.
        with pg_store._get_pool().connection() as conn:
            conn.execute("UPDATE mono_sessions SET last_seen = %s WHERE sid='stale'",
                         (time.time() - 40 * 24 * 3600,))
        removed = pg_store.sessions_sweep(30 * 24 * 3600)
        assert removed == 1
        assert pg_store.session_get("fresh") == 1
        assert pg_store.session_get("stale") is None


class TestAccountSettings:
    def test_set_get_and_isolation(self):
        assert pg_store.account_settings_get(1) is None
        pg_store.account_settings_set(1, {"active_tab": "lp"}, time.time())
        pg_store.account_settings_set(2, {"active_tab": "ind"}, time.time())
        assert pg_store.account_settings_get(1) == {"active_tab": "lp"}
        assert pg_store.account_settings_get(2) == {"active_tab": "ind"}


class TestUserSettingsLegacyRead:
    def test_set_get(self):
        # Written by the pre-multiuser code / read during migration.
        pg_store.user_settings_set(42, {"col_order": ["name"]}, time.time())
        assert pg_store.user_settings_get(42) == {"col_order": ["name"]}


# ── End-to-end through lp-web against the real DB ─────────────────────────────

class TestLpWebIntegration:
    def _lp_web(self, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "lp_web_live", Path(__file__).resolve().parent.parent / "lp-web.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Fresh in-memory caches; real pg_store (DATABASE_URL already set by _db).
        monkeypatch.setattr(mod, "_SESSIONS", {})
        monkeypatch.setattr(mod, "_ACCOUNTS", {})
        mod._REQUEST.account = None
        return mod

    def test_session_roundtrip_and_rehydrate(self, monkeypatch):
        lp = self._lp_web(monkeypatch)
        acct = lp.Account(100)
        acct.characters[100] = {"character_id": 100, "name": "Main", "scopes": [],
                                "refresh_token": "rt", "access_token": None, "expires_at": 0}
        acct.active_char_id = 100
        lp._persist_account(acct)
        sid = lp._new_session(acct)
        # Drop caches → must rehydrate purely from Postgres.
        lp._SESSIONS.clear()
        lp._ACCOUNTS.clear()
        resolved = lp._resolve_session(sid)
        assert resolved is not None
        assert resolved.account_id == 100
        assert 100 in resolved.characters
        assert lp.pg_store.char_account_get(100) == 100

    def test_account_settings_isolation_via_lpweb(self, monkeypatch):
        lp = self._lp_web(monkeypatch)
        a1, a2 = lp.Account(1), lp.Account(2)
        a1.characters[1] = {"character_id": 1, "name": "A"}
        a2.characters[2] = {"character_id": 2, "name": "B"}
        a1.active_char_id, a2.active_char_id = 1, 2
        lp.save_account_settings(a1, {"active_tab": "lp"})
        lp.save_account_settings(a2, {"active_tab": "ind"})
        assert lp.load_account_settings(a1) == {"active_tab": "lp"}
        assert lp.load_account_settings(a2) == {"active_tab": "ind"}
