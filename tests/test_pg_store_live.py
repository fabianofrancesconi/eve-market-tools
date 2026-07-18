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
           "mono_char_account", "mono_sessions", "mono_account_settings",
           "mono_delivered_jobs", "mono_order_state", "mono_order_events",
           "mono_prefs", "mono_favorites", "mono_profiles")


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


class TestPrefsRows:
    def test_pref_roundtrip_and_isolation(self):
        assert pg_store.prefs_get_all(1) == {}
        pg_store.pref_set(1, "active_tab", "lp")
        pg_store.pref_set(1, "arb.max_jumps", "8")
        pg_store.pref_set(2, "active_tab", "ind")
        assert pg_store.prefs_get_all(1) == {"active_tab": "lp", "arb.max_jumps": "8"}
        assert pg_store.prefs_get_all(2) == {"active_tab": "ind"}

    def test_pref_delete_and_json_types(self):
        pg_store.pref_set(1, "col_vis", {"ask": False})
        pg_store.pref_set(1, "trade_weight", 0.75)
        assert pg_store.prefs_get_all(1) == {"col_vis": {"ask": False}, "trade_weight": 0.75}
        pg_store.pref_delete(1, "trade_weight")
        assert pg_store.prefs_get_all(1) == {"col_vis": {"ask": False}}


class TestFavoritesRows:
    def test_add_remove_and_order(self):
        assert pg_store.favorites_list(1) == []
        pg_store.favorite_add(1, 23560)
        pg_store.favorite_add(1, 587)
        pg_store.favorite_add(1, 23560)  # idempotent
        assert set(pg_store.favorites_list(1)) == {23560, 587}
        pg_store.favorite_remove(1, 23560)
        assert pg_store.favorites_list(1) == [587]
        assert pg_store.favorites_list(2) == []


class TestProfilesRows:
    def test_upsert_list_delete(self):
        assert pg_store.profiles_list(1) == []
        pg_store.profile_upsert(1, "p1", "Sotiyo", 4.2, 3, 1, 4, 0)
        pg_store.profile_upsert(1, "p2", "Azbel", 3.1, 2, 1, 4, 1)
        rows = pg_store.profiles_list(1)
        assert [r["name"] for r in rows] == ["Sotiyo", "Azbel"]
        assert rows[0]["system_index"] == 4.2
        pg_store.profile_upsert(1, "p1", "Sotiyo XL", 5.0, 3, 1, 4, 0)  # replace
        assert pg_store.profiles_list(1)[0]["name"] == "Sotiyo XL"
        pg_store.profile_delete(1, "p1")
        assert [r["profile_id"] for r in pg_store.profiles_list(1)] == ["p2"]


class TestDeliveredJobs:
    def test_with_delivered_jobs_read_modify_write(self):
        # First call: row is None, mutate writes an entry.
        def mut1(data):
            assert data is None
            return {"total_runs": 3, "seen_job_ids": [1]}, "r1"
        assert pg_store.with_delivered_jobs(10, 20, mut1) == "r1"

        # Second call: sees the persisted row, updates it.
        def mut2(data):
            assert data == {"total_runs": 3, "seen_job_ids": [1]}
            return {"total_runs": 8, "seen_job_ids": [1, 2]}, "r2"
        assert pg_store.with_delivered_jobs(10, 20, mut2) == "r2"

    def test_none_new_data_skips_write(self):
        pg_store.delivered_jobs_set(10, 20, {"total_runs": 5})
        assert pg_store.with_delivered_jobs(10, 20, lambda d: (None, "noop")) == "noop"
        # unchanged
        assert pg_store.with_delivered_jobs(10, 20, lambda d: (None, d))["total_runs"] == 5

    def test_isolated_per_account_and_char(self):
        pg_store.delivered_jobs_set(1, 100, {"total_runs": 1})
        pg_store.delivered_jobs_set(1, 200, {"total_runs": 2})
        pg_store.delivered_jobs_set(2, 100, {"total_runs": 3})
        assert pg_store.with_delivered_jobs(1, 100, lambda d: (None, d))["total_runs"] == 1
        assert pg_store.with_delivered_jobs(1, 200, lambda d: (None, d))["total_runs"] == 2
        assert pg_store.with_delivered_jobs(2, 100, lambda d: (None, d))["total_runs"] == 3


class TestOrderEventsLive:
    def test_state_and_events_roundtrip(self):
        ev = {"id": "1_1000", "ts": time.time(), "order_id": 1, "sold": 5}

        def mutate(prev, sales):
            assert prev == {} and sales == {}
            return [ev], {"1": {"volume_remain": 95}}, {"1": {"sold": 5}}, "done"
        assert pg_store.with_order_state(7, 300, mutate) == "done"

        # event is active and readable
        active = pg_store.order_events_active(7, 0)
        assert len(active) == 1 and active[0]["id"] == "1_1000"

        # state persisted → next diff sees prev
        seen = {}
        def mutate2(prev, sales):
            seen["prev"], seen["sales"] = prev, sales
            return [], prev, sales, None
        pg_store.with_order_state(7, 300, mutate2)
        assert seen["prev"] == {"1": {"volume_remain": 95}}
        assert seen["sales"] == {"1": {"sold": 5}}

    def test_duplicate_event_id_ignored(self):
        ev = {"id": "dup", "ts": time.time(), "sold": 1}
        pg_store.with_order_state(7, 1, lambda p, s: ([ev], {}, {}, None))
        pg_store.with_order_state(7, 1, lambda p, s: ([ev], {}, {}, None))
        assert len(pg_store.order_events_active(7, 0)) == 1

    def test_expiry_cutoff_and_dismiss(self):
        now = time.time()
        old = {"id": "old", "ts": now - 10 * 86400, "sold": 1}
        fresh = {"id": "fresh", "ts": now, "sold": 2}
        pg_store.with_order_state(7, 1, lambda p, s: ([old, fresh], {}, {}, None))
        # cutoff excludes the old one
        active = pg_store.order_events_active(7, now - 7 * 86400)
        assert [e["id"] for e in active] == ["fresh"]
        # dismiss the fresh one → nothing active
        pg_store.order_events_dismiss(7, "fresh")
        assert pg_store.order_events_active(7, now - 7 * 86400) == []

    def test_dismiss_all(self):
        pg_store.with_order_state(7, 1, lambda p, s: (
            [{"id": "a", "ts": time.time(), "sold": 1},
             {"id": "b", "ts": time.time(), "sold": 2}], {}, {}, None))
        pg_store.order_events_dismiss(7, "all")
        assert pg_store.order_events_active(7, 0) == []

    def test_events_isolated_per_account(self):
        pg_store.with_order_state(1, 9, lambda p, s: (
            [{"id": "x", "ts": time.time(), "sold": 1}], {}, {}, None))
        pg_store.with_order_state(2, 9, lambda p, s: (
            [{"id": "y", "ts": time.time(), "sold": 2}], {}, {}, None))
        assert [e["id"] for e in pg_store.order_events_active(1, 0)] == ["x"]
        assert [e["id"] for e in pg_store.order_events_active(2, 0)] == ["y"]


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
