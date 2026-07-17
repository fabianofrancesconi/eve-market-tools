"""Tests for the versioned Postgres migration framework (pg_migrations).

The pure-logic tests (version ordering, the ``pending`` filter) run without a
database. The live migration tests need a real Postgres and are skipped unless
EMT_TEST_DATABASE_URL is set (same gate as test_pg_store_live).
"""
import os

import pytest

import pg_migrations


# ── pure logic (no database) ────────────────────────────────────────────────

def test_versions_are_unique_and_strictly_increasing():
    versions = [m[0] for m in pg_migrations.MIGRATIONS]
    assert versions == sorted(versions), "migrations must be in ascending order"
    assert len(versions) == len(set(versions)), "migration versions must be unique"


def test_every_migration_has_description_and_sql():
    for version, desc, sql in pg_migrations.MIGRATIONS:
        assert isinstance(version, int)
        assert desc and isinstance(desc, str)
        assert sql and sql.strip(), f"migration {version} has empty SQL"


def test_pending_returns_only_unapplied_in_order():
    all_versions = [m[0] for m in pg_migrations.MIGRATIONS]
    # Nothing applied → everything pending, in order.
    assert [m[0] for m in pg_migrations.pending(set())] == all_versions
    # Everything applied → nothing pending.
    assert pg_migrations.pending(set(all_versions)) == []
    # First applied → the rest pending.
    first = all_versions[0]
    assert [m[0] for m in pg_migrations.pending({first})] == all_versions[1:]


def test_pending_ignores_unknown_applied_versions():
    # A version recorded in the DB that we no longer know about is simply not
    # in MIGRATIONS; pending() must not choke on it.
    bogus = max(m[0] for m in pg_migrations.MIGRATIONS) + 999
    assert [m[0] for m in pg_migrations.pending({bogus})] == \
        [m[0] for m in pg_migrations.MIGRATIONS]


# ── live migration behaviour ────────────────────────────────────────────────

_TEST_DSN = os.environ.get("EMT_TEST_DATABASE_URL")
_live = pytest.mark.skipif(
    not _TEST_DSN, reason="set EMT_TEST_DATABASE_URL to run live Postgres tests")

_ALL_TABLES = (
    "mono_kv", "mono_user_settings", "mono_accounts", "mono_char_account",
    "mono_sessions", "mono_account_settings", "mono_delivered_jobs",
    "mono_order_state", "mono_order_events", "mono_notes", "mono_wallet_history",
    "mono_location_trail", "mono_exploration_sessions")


@_live
class TestLiveMigrations:
    @pytest.fixture
    def pool(self, monkeypatch):
        pytest.importorskip("psycopg")
        import pg_store
        monkeypatch.setenv("DATABASE_URL", _TEST_DSN)
        monkeypatch.setattr(pg_store, "_pool", None)
        monkeypatch.setattr(pg_store, "_schema_ready", False)
        from psycopg_pool import ConnectionPool
        p = ConnectionPool(pg_store._normalize_dsn(_TEST_DSN), min_size=1,
                           max_size=2, kwargs={"autocommit": True}, open=True)
        # Start from a clean slate: drop everything this suite manages.
        with p.connection() as conn:
            for t in _ALL_TABLES + (pg_migrations.SCHEMA_TABLE,):
                conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        yield p
        with p.connection() as conn:
            for t in _ALL_TABLES + (pg_migrations.SCHEMA_TABLE,):
                conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        p.close()

    def _tables(self, pool):
        with pool.connection() as conn:
            return {t for t in _ALL_TABLES
                    if conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]}

    def test_run_creates_all_tables_and_records_versions(self, pool):
        applied = pg_migrations.run(pool)
        assert applied == [m[0] for m in pg_migrations.MIGRATIONS]
        assert self._tables(pool) == set(_ALL_TABLES)
        with pool.connection() as conn:
            recorded = {r[0] for r in conn.execute(
                f"SELECT version FROM {pg_migrations.SCHEMA_TABLE}").fetchall()}
        assert recorded == {m[0] for m in pg_migrations.MIGRATIONS}

    def test_run_is_idempotent_second_call_is_noop(self, pool):
        pg_migrations.run(pool)
        assert pg_migrations.run(pool) == []  # nothing pending the second time
        assert self._tables(pool) == set(_ALL_TABLES)

    def test_adopts_a_database_created_by_old_inline_schema(self, pool):
        # Simulate a pre-migration DB: tables exist but no versions are recorded.
        pg_migrations.run(pool)
        with pool.connection() as conn:
            conn.execute(f"DELETE FROM {pg_migrations.SCHEMA_TABLE}")
        # Re-running must succeed (idempotent DDL) and re-stamp every version.
        applied = pg_migrations.run(pool)
        assert applied == [m[0] for m in pg_migrations.MIGRATIONS]
        assert self._tables(pool) == set(_ALL_TABLES)

    def test_late_columns_present(self, pool):
        pg_migrations.run(pool)
        with pool.connection() as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'mono_location_trail'").fetchall()}
        assert {"note", "hidden"} <= cols
