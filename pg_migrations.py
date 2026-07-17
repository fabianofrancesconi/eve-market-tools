#!/usr/bin/env python3
"""
Versioned schema migrations for the Postgres-backed monolith (see ``pg_store``).

Previously the schema was created by a single inline block of
``CREATE TABLE IF NOT EXISTS`` / ``ALTER TABLE … ADD COLUMN IF NOT EXISTS``
statements that ran on every process start. That worked but had two problems:

  * every new schema change meant editing that opaque block, and there was no
    record of *which* changes a given database had actually seen; and
  * "add a column later" could only be expressed as another ``IF NOT EXISTS``
    guard bolted onto the create, so the DDL drifted away from a clean history.

This module replaces that with an ordered list of numbered migrations tracked in
a ``mono_schema_migrations`` table: each migration runs at most once per database
and is recorded, so start-up only executes the steps a database has not yet seen.

The migrations remain individually idempotent (``IF NOT EXISTS`` throughout) so
that a database created by the *old* inline block — which never recorded any
versions — is adopted cleanly: the first run re-executes every step harmlessly
and then stamps them all as applied. From then on start-up is a single
``SELECT`` when nothing is pending.

All table names stay ``mono_*``-prefixed so they can't collide with the redesign
branch's own tables in a shared database.
"""

# Name of the bookkeeping table that records which migrations have run.
SCHEMA_TABLE = "mono_schema_migrations"

# Ordered list of (version, description, sql). Versions must be unique and
# strictly increasing. NEVER edit or renumber a migration that has shipped —
# append a new one instead. Each SQL block must be idempotent (safe to re-run)
# so it can adopt a database created by the pre-migration inline schema.
MIGRATIONS = [
    (1, "kv store + per-character synced settings (baseline)", """
        CREATE TABLE IF NOT EXISTS mono_kv (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
        CREATE TABLE IF NOT EXISTS mono_user_settings (
            character_id BIGINT PRIMARY KEY,
            settings_json JSONB NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL);
    """),
    (2, "multi-user model: accounts, sessions, per-account settings (v1.81+)", """
        CREATE TABLE IF NOT EXISTS mono_accounts (
            account_id BIGINT PRIMARY KEY,
            data JSONB NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS mono_char_account (
            character_id BIGINT PRIMARY KEY,
            account_id BIGINT NOT NULL);
        CREATE TABLE IF NOT EXISTS mono_sessions (
            sid TEXT PRIMARY KEY,
            account_id BIGINT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            last_seen DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS mono_account_settings (
            account_id BIGINT PRIMARY KEY,
            settings_json JSONB NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL);
    """),
    (3, "replica-safe counters: delivered jobs, order state, order events (v1.82+)", """
        CREATE TABLE IF NOT EXISTS mono_delivered_jobs (
            account_id BIGINT NOT NULL,
            character_id BIGINT NOT NULL,
            data JSONB NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (account_id, character_id));
        CREATE TABLE IF NOT EXISTS mono_order_state (
            account_id BIGINT NOT NULL,
            character_id BIGINT NOT NULL,
            prev JSONB NOT NULL,
            sales JSONB NOT NULL,
            PRIMARY KEY (account_id, character_id));
        CREATE TABLE IF NOT EXISTS mono_order_events (
            account_id BIGINT NOT NULL,
            event_id TEXT NOT NULL,
            character_id BIGINT NOT NULL,
            ts DOUBLE PRECISION NOT NULL,
            dismissed BOOLEAN NOT NULL DEFAULT FALSE,
            data JSONB NOT NULL,
            PRIMARY KEY (account_id, event_id));
    """),
    (4, "notes tree (v1.87+)", """
        CREATE TABLE IF NOT EXISTS mono_notes (
            id TEXT NOT NULL,
            account_id BIGINT NOT NULL,
            parent_id TEXT,
            kind TEXT NOT NULL DEFAULT 'note',
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            pos INTEGER NOT NULL DEFAULT 0,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (account_id, id));
    """),
    (5, "wallet history (v1.88+)", """
        CREATE TABLE IF NOT EXISTS mono_wallet_history (
            account_id BIGINT NOT NULL,
            character_id BIGINT NOT NULL,
            ts DOUBLE PRECISION NOT NULL,
            balance DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (account_id, character_id, ts));
    """),
    (6, "exploration location trail + session journal (v1.101+)", """
        CREATE TABLE IF NOT EXISTS mono_location_trail (
            account_id BIGINT NOT NULL,
            character_id BIGINT NOT NULL,
            entered_at DOUBLE PRECISION NOT NULL,
            run_id TEXT NOT NULL,
            system_id BIGINT NOT NULL,
            system_name TEXT NOT NULL,
            security DOUBLE PRECISION,
            scanned BOOLEAN NOT NULL DEFAULT FALSE,
            cargo_isk DOUBLE PRECISION,
            PRIMARY KEY (account_id, character_id, entered_at));
        CREATE TABLE IF NOT EXISTS mono_exploration_sessions (
            account_id BIGINT NOT NULL,
            character_id BIGINT NOT NULL,
            run_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            started_at DOUBLE PRECISION NOT NULL,
            ended_at DOUBLE PRECISION,
            notes TEXT NOT NULL DEFAULT '',
            cargo_value DOUBLE PRECISION,
            PRIMARY KEY (account_id, character_id, run_id));
    """),
    (7, "per-system note on the location trail (v1.107+)", """
        ALTER TABLE mono_location_trail
            ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
    """),
    (8, "per-system manual hide on the location trail (v1.108+)", """
        ALTER TABLE mono_location_trail
            ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;
    """),
]


def _applied_versions(conn):
    """Return the set of migration versions already recorded in this database."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} ("
        "version INTEGER PRIMARY KEY, "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    rows = conn.execute(f"SELECT version FROM {SCHEMA_TABLE}").fetchall()
    return {r[0] for r in rows}


def pending(applied):
    """The migrations (from MIGRATIONS) not present in the ``applied`` set,
    in ascending version order."""
    return [m for m in MIGRATIONS if m[0] not in applied]


def run(pool):
    """Apply every migration this database has not yet seen, in order.

    Each pending migration runs inside its own transaction together with the
    bookkeeping insert, so a version is recorded if and only if its DDL
    committed. Returns the list of versions applied by this call (empty when the
    schema is already current)."""
    with pool.connection() as conn:
        applied = _applied_versions(conn)
    todo = pending(applied)
    done = []
    for version, _desc, sql in todo:
        with pool.connection() as conn, conn.transaction():
            conn.execute(sql)
            conn.execute(
                f"INSERT INTO {SCHEMA_TABLE} (version) VALUES (%s) "
                "ON CONFLICT (version) DO NOTHING",
                (version,))
        done.append(version)
    return done
