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
import time

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
        # ── multi-user model (v1.81+) ──────────────────────────────────────
        # An account is a set of linked EVE characters; a browser session
        # (cookie) points at one account. Settings are per-account.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_accounts ("
            "account_id BIGINT PRIMARY KEY, "        # = the first linked char id
            "data JSONB NOT NULL, "                  # {characters:[...], active_char_id}
            "updated_at DOUBLE PRECISION NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_char_account ("
            "character_id BIGINT PRIMARY KEY, "      # reverse index: char -> account
            "account_id BIGINT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_sessions ("
            "sid TEXT PRIMARY KEY, "
            "account_id BIGINT NOT NULL, "
            "created_at DOUBLE PRECISION NOT NULL, "
            "last_seen DOUBLE PRECISION NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_account_settings ("
            "account_id BIGINT PRIMARY KEY, "
            "settings_json JSONB NOT NULL, "
            "updated_at DOUBLE PRECISION NOT NULL)")
        # ── replica-safe counters (v1.82+) ─────────────────────────────────
        # One row per (account, character) instead of a single read-modify-write
        # JSON blob, so concurrent writers (e.g. >1 replica) don't clobber each
        # other. Updates take a row lock (SELECT … FOR UPDATE) via with_*().
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_delivered_jobs ("
            "account_id BIGINT NOT NULL, "
            "character_id BIGINT NOT NULL, "
            "data JSONB NOT NULL, "
            "updated_at DOUBLE PRECISION NOT NULL, "
            "PRIMARY KEY (account_id, character_id))")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_order_state ("
            "account_id BIGINT NOT NULL, "
            "character_id BIGINT NOT NULL, "
            "prev JSONB NOT NULL, "
            "sales JSONB NOT NULL, "
            "PRIMARY KEY (account_id, character_id))")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mono_order_events ("
            "account_id BIGINT NOT NULL, "
            "event_id TEXT NOT NULL, "
            "character_id BIGINT NOT NULL, "
            "ts DOUBLE PRECISION NOT NULL, "
            "dismissed BOOLEAN NOT NULL DEFAULT FALSE, "
            "data JSONB NOT NULL, "
            "PRIMARY KEY (account_id, event_id))")
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


# ── accounts (a set of linked EVE characters) ────────────────────────────────

def account_get(account_id):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT data FROM mono_accounts WHERE account_id = %s",
            (account_id,)).fetchone()
    return row[0] if row else None


def account_set(account_id, data, updated_at):
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_accounts (account_id, data, updated_at) "
            "VALUES (%s, %s, %s) ON CONFLICT (account_id) DO UPDATE SET "
            "data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
            (account_id, Jsonb(data), updated_at))


def account_delete(account_id):
    with _get_pool().connection() as conn:
        conn.execute("DELETE FROM mono_accounts WHERE account_id = %s", (account_id,))


# ── character -> account reverse index ───────────────────────────────────────

def char_account_get(character_id):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT account_id FROM mono_char_account WHERE character_id = %s",
            (character_id,)).fetchone()
    return row[0] if row else None


def char_account_set(character_id, account_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_char_account (character_id, account_id) VALUES (%s, %s) "
            "ON CONFLICT (character_id) DO UPDATE SET account_id = EXCLUDED.account_id",
            (character_id, account_id))


def char_account_delete(character_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_char_account WHERE character_id = %s", (character_id,))


# ── browser sessions (cookie sid -> account) ─────────────────────────────────

def session_get(sid):
    """Return the account_id for a session id, or None. Touches last_seen."""
    with _get_pool().connection() as conn:
        row = conn.execute(
            "UPDATE mono_sessions SET last_seen = %s WHERE sid = %s "
            "RETURNING account_id", (time.time(), sid)).fetchone()
    return row[0] if row else None


def session_set(sid, account_id):
    now = time.time()
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_sessions (sid, account_id, created_at, last_seen) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (sid) DO UPDATE SET "
            "account_id = EXCLUDED.account_id, last_seen = EXCLUDED.last_seen",
            (sid, account_id, now, now))


def session_delete(sid):
    with _get_pool().connection() as conn:
        conn.execute("DELETE FROM mono_sessions WHERE sid = %s", (sid,))


def sessions_sweep(max_idle_seconds):
    """Delete sessions idle longer than max_idle_seconds. Returns rows removed."""
    cutoff = time.time() - max_idle_seconds
    with _get_pool().connection() as conn:
        cur = conn.execute(
            "DELETE FROM mono_sessions WHERE last_seen < %s", (cutoff,))
        return cur.rowcount


# ── per-account settings (searches, filters, columns, ...) ───────────────────

def account_settings_get(account_id):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT settings_json FROM mono_account_settings WHERE account_id = %s",
            (account_id,)).fetchone()
    return row[0] if row else None


def account_settings_set(account_id, data, updated_at):
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_account_settings (account_id, settings_json, updated_at) "
            "VALUES (%s, %s, %s) ON CONFLICT (account_id) DO UPDATE SET "
            "settings_json = EXCLUDED.settings_json, updated_at = EXCLUDED.updated_at",
            (account_id, Jsonb(data), updated_at))


def all_account_ids():
    with _get_pool().connection() as conn:
        return [r[0] for r in conn.execute("SELECT account_id FROM mono_accounts").fetchall()]


# ── replica-safe counters: delivered jobs + order events ─────────────────────
# The read-modify-write is done inside one transaction holding a row lock, so
# two concurrent workers (even on separate replicas) can't lose an update.

def with_delivered_jobs(account_id, character_id, mutate):
    """Atomically update one character's delivered-jobs row. ``mutate(data|None)``
    returns ``(new_data|None, result)``; ``None`` new_data means no write."""
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn, conn.transaction():
        row = conn.execute(
            "SELECT data FROM mono_delivered_jobs WHERE account_id=%s AND "
            "character_id=%s FOR UPDATE", (account_id, character_id)).fetchone()
        new_data, result = mutate(row[0] if row else None)
        if new_data is not None:
            conn.execute(
                "INSERT INTO mono_delivered_jobs (account_id, character_id, data, "
                "updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (account_id, "
                "character_id) DO UPDATE SET data=EXCLUDED.data, "
                "updated_at=EXCLUDED.updated_at",
                (account_id, character_id, Jsonb(new_data), time.time()))
    return result


def delivered_jobs_set(account_id, character_id, data):
    """Unconditional upsert (used by the one-time counter migration)."""
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_delivered_jobs (account_id, character_id, data, "
            "updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (account_id, "
            "character_id) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at",
            (account_id, character_id, Jsonb(data), time.time()))


def with_order_state(account_id, character_id, mutate):
    """Atomically diff one character's orders. ``mutate(prev, sales)`` returns
    ``(new_event_dicts, new_prev, new_sales, result)``; new events are inserted
    (dedup by id), the per-char prev/sales snapshot is upserted, result returned."""
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn, conn.transaction():
        row = conn.execute(
            "SELECT prev, sales FROM mono_order_state WHERE account_id=%s AND "
            "character_id=%s FOR UPDATE", (account_id, character_id)).fetchone()
        prev = row[0] if row else {}
        sales = row[1] if row else {}
        events, new_prev, new_sales, result = mutate(prev, sales)
        for ev in events:
            conn.execute(
                "INSERT INTO mono_order_events (account_id, event_id, character_id, "
                "ts, dismissed, data) VALUES (%s,%s,%s,%s,FALSE,%s) "
                "ON CONFLICT (account_id, event_id) DO NOTHING",
                (account_id, ev["id"], character_id, ev["ts"], Jsonb(ev)))
        conn.execute(
            "INSERT INTO mono_order_state (account_id, character_id, prev, sales) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (account_id, character_id) DO UPDATE "
            "SET prev=EXCLUDED.prev, sales=EXCLUDED.sales",
            (account_id, character_id, Jsonb(new_prev), Jsonb(new_sales)))
    return result


def order_state_set(account_id, character_id, prev, sales):
    """Unconditional prev/sales upsert (used by the one-time counter migration)."""
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_order_state (account_id, character_id, prev, sales) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (account_id, character_id) DO UPDATE "
            "SET prev=EXCLUDED.prev, sales=EXCLUDED.sales",
            (account_id, character_id, Jsonb(prev), Jsonb(sales)))


def order_events_active(account_id, cutoff_ts):
    """Non-dismissed events newer than cutoff_ts, most-recent first."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT data FROM mono_order_events WHERE account_id=%s AND "
            "dismissed=FALSE AND ts>=%s ORDER BY ts DESC",
            (account_id, cutoff_ts)).fetchall()
    return [r[0] for r in rows]


def order_events_dismiss(account_id, event_id):
    """Dismiss one event, or all of the account's events when event_id=='all'."""
    with _get_pool().connection() as conn:
        if event_id == "all":
            conn.execute("UPDATE mono_order_events SET dismissed=TRUE WHERE "
                         "account_id=%s", (account_id,))
        else:
            conn.execute("UPDATE mono_order_events SET dismissed=TRUE WHERE "
                         "account_id=%s AND event_id=%s", (account_id, event_id))
