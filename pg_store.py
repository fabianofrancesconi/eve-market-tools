#!/usr/bin/env python3
"""
Optional Postgres-backed persistence for the deployed (Railway) monolith.

The local tool keeps all its state in the cache dir (JSON files + a small SQLite
DB). When deployed to Railway the container filesystem is ephemeral, so durable
*user* state — SSO tokens, the per-app settings blobs, and per-character synced
settings — is stored in Postgres instead. This is gated entirely on the
``DATABASE_URL`` environment variable:

    * ``DATABASE_URL`` set   -> read/write Postgres (this module).
    * ``DATABASE_URL`` unset -> callers fall back to the original file/SQLite
      behaviour, so ``python lp-web.py`` and the test-suite are unchanged.

Only durable state lives here. Disposable, rebuildable caches (the SDE SQLite,
ESI/market/name JSON caches) stay on disk — persisted via a Railway volume, not
worth putting in the database.

The monolith is a threaded stdlib ``http.server`` (not async), so this uses the
*sync* psycopg 3 driver with a small connection pool. psycopg is imported lazily
so merely importing this module never requires it — only actually using Postgres
does.
"""
import os
import threading

_pool = None
_pool_lock = threading.Lock()
_schema_ready = False


def _normalize_dsn(url):
    """Return a libpq-compatible DSN.

    Railway hands out ``postgresql://…``. SQLAlchemy-style ``+driver`` suffixes
    (e.g. ``postgresql+asyncpg://`` — what the redesign branch uses) are not
    understood by libpq/psycopg, and the legacy ``postgres://`` scheme should be
    normalised too. Everything else passes through untouched.
    """
    if url.startswith("postgresql+"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres://"):
        return "postgresql://" + url.split("://", 1)[1]
    return url


def enabled():
    """True when a DATABASE_URL is configured (i.e. use Postgres, not files)."""
    return bool(os.environ.get("DATABASE_URL"))


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                dsn = _normalize_dsn(os.environ["DATABASE_URL"])
                pool = ConnectionPool(
                    dsn, min_size=1, max_size=4,
                    kwargs={"autocommit": True}, open=True,
                )
                _ensure_schema(pool)
                _pool = pool
    return _pool


def _ensure_schema(pool):
    """Create the monolith's tables once. Names are ``mono_*``-prefixed so they
    can't collide with the redesign branch's own tables in a shared database."""
    global _schema_ready
    if _schema_ready:
        return
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_kv ("
            "key TEXT PRIMARY KEY, "
            "value JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_user_settings ("
            "character_id BIGINT PRIMARY KEY, "
            "settings_json JSONB NOT NULL, "
            "updated_at DOUBLE PRECISION NOT NULL)")
    _schema_ready = True


# ── key/value store (settings blobs, delivered-jobs counter, order events, tokens)

def kv_get(key, default=None):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT value FROM mono_kv WHERE key = %s", (key,)).fetchone()
    return row[0] if row else default


def kv_set(key, value):
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_kv (key, value, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET "
            "value = EXCLUDED.value, updated_at = now()",
            (key, Jsonb(value)))


# ── per-character synced settings (mirror of the local user_settings.sqlite) ──

def user_settings_get(character_id):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT settings_json FROM mono_user_settings WHERE character_id = %s",
            (character_id,)).fetchone()
    return row[0] if row else None


def user_settings_set(character_id, data, updated_at):
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_user_settings (character_id, settings_json, updated_at) "
            "VALUES (%s, %s, %s) ON CONFLICT (character_id) DO UPDATE SET "
            "settings_json = EXCLUDED.settings_json, updated_at = EXCLUDED.updated_at",
            (character_id, Jsonb(data), updated_at))
