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
    """Bring the monolith's schema up to date by running any pending migrations.

    The actual DDL lives in :mod:`pg_migrations` as an ordered, version-tracked
    list; this just runs it once per process. Table names are ``mono_*``-prefixed
    so they can't collide with the redesign branch's own tables in a shared
    database."""
    global _schema_ready
    if _schema_ready:
        return
    import pg_migrations
    pg_migrations.run(pool)
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


def session_touch(sid):
    """Bump a session's last_seen without resolving it. Used on the in-memory
    cache-hit path, where session_get (which also touches) is skipped — otherwise
    an actively-used session's DB last_seen never advances and the idle sweep
    deletes it after max_idle even though it's in daily use."""
    with _get_pool().connection() as conn:
        conn.execute(
            "UPDATE mono_sessions SET last_seen = %s WHERE sid = %s",
            (time.time(), sid))


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


# ── row-per-setting store (v1.129+) ──────────────────────────────────────────
# Every user-touchable preference is a single row, so two changes made seconds
# apart (a favorites toggle, a filter tweak, a column resize) each write only
# their own row and can never clobber one another. Favorites and build-location
# profiles get their own tables for the same reason. This replaces the old
# whole-document settings blob (and the fragile _preserve_* guards it needed).

def prefs_get_all(account_id):
    """Every stored preference for an account as ``{key: value}``."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM mono_prefs WHERE account_id = %s",
            (account_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def pref_set(account_id, key, value):
    """Upsert one preference row. Only this key's row is written."""
    from psycopg.types.json import Jsonb
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_prefs (account_id, key, value, updated_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (account_id, key) DO UPDATE SET "
            "value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
            (account_id, key, Jsonb(value), time.time()))


def pref_delete(account_id, key):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_prefs WHERE account_id = %s AND key = %s",
            (account_id, key))


def favorites_list(account_id):
    """The account's favorited blueprint ids (watchlist), oldest-added first."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT blueprint_id FROM mono_favorites WHERE account_id = %s "
            "ORDER BY added_at, blueprint_id", (account_id,)).fetchall()
    return [r[0] for r in rows]


def favorite_add(account_id, blueprint_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_favorites (account_id, blueprint_id, added_at) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (account_id, blueprint_id, time.time()))


def favorite_remove(account_id, blueprint_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_favorites WHERE account_id = %s AND blueprint_id = %s",
            (account_id, blueprint_id))


def profiles_list(account_id):
    """The account's build-location profiles, in display order."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT profile_id, name, system_index, role_bonus, facility_tax, "
            "scc_surcharge, pos FROM mono_profiles WHERE account_id = %s "
            "ORDER BY pos, profile_id", (account_id,)).fetchall()
    return [{"profile_id": r[0], "name": r[1], "system_index": r[2],
             "role_bonus": r[3], "facility_tax": r[4], "scc_surcharge": r[5],
             "pos": r[6]} for r in rows]


def profile_upsert(account_id, profile_id, name, system_index, role_bonus,
                   facility_tax, scc_surcharge, pos):
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_profiles (account_id, profile_id, name, system_index, "
            "role_bonus, facility_tax, scc_surcharge, pos) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (account_id, profile_id) DO UPDATE SET "
            "name=EXCLUDED.name, system_index=EXCLUDED.system_index, "
            "role_bonus=EXCLUDED.role_bonus, facility_tax=EXCLUDED.facility_tax, "
            "scc_surcharge=EXCLUDED.scc_surcharge, pos=EXCLUDED.pos",
            (account_id, profile_id, name, system_index, role_bonus,
             facility_tax, scc_surcharge, pos))


def profile_delete(account_id, profile_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_profiles WHERE account_id = %s AND profile_id = %s",
            (account_id, profile_id))


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


# ── notes (per-account tree of folders + notes) ────────────────────────────

def notes_list(account_id):
    """All notes/folders for an account, ordered by position."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, parent_id, kind, title, body, pos, created_at, updated_at "
            "FROM mono_notes WHERE account_id=%s ORDER BY pos",
            (account_id,)).fetchall()
    return [{"id": r[0], "parent_id": r[1], "kind": r[2], "title": r[3],
             "body": r[4], "pos": r[5], "created_at": r[6], "updated_at": r[7]}
            for r in rows]


def notes_upsert(account_id, note_id, parent_id, kind, title, body, pos):
    """Create or update a note/folder."""
    now = time.time()
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_notes (id, account_id, parent_id, kind, title, body, pos, "
            "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (account_id, id) DO UPDATE SET "
            "parent_id=EXCLUDED.parent_id, kind=EXCLUDED.kind, title=EXCLUDED.title, "
            "body=EXCLUDED.body, pos=EXCLUDED.pos, updated_at=EXCLUDED.updated_at",
            (note_id, account_id, parent_id, kind, title, body, pos, now, now))
    return now


def notes_delete(account_id, note_id):
    """Delete a note/folder and all its descendants."""
    with _get_pool().connection() as conn:
        with conn.transaction():
            to_delete = [note_id]
            queue = [note_id]
            while queue:
                pid = queue.pop()
                children = conn.execute(
                    "SELECT id FROM mono_notes WHERE account_id=%s AND parent_id=%s",
                    (account_id, pid)).fetchall()
                for row in children:
                    to_delete.append(row[0])
                    queue.append(row[0])
            for nid in to_delete:
                conn.execute(
                    "DELETE FROM mono_notes WHERE account_id=%s AND id=%s",
                    (account_id, nid))


# ── wallet history ─────────────────────────────────────────────────────────

def wallet_history_append(account_id, character_id, ts, balance):
    """Append a wallet snapshot. Ignores duplicates."""
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_wallet_history (account_id, character_id, ts, balance) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (account_id, character_id, ts, balance))


def wallet_history_query(account_id, since_ts):
    """Return {character_id: [(ts, balance), ...]} for all characters since a time."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT character_id, ts, balance FROM mono_wallet_history "
            "WHERE account_id=%s AND ts>=%s ORDER BY ts",
            (account_id, since_ts)).fetchall()
    result = {}
    for cid, ts, bal in rows:
        result.setdefault(cid, []).append((ts, bal))
    return result


def wallet_history_compact(account_id, now=None):
    """Compact old data: 7–90 days → hourly averages, 90–365 days → daily averages,
    >365 days → delete. Operates per-character within a transaction."""
    if now is None:
        now = time.time()
    cutoff_7d = now - 7 * 86400
    cutoff_90d = now - 90 * 86400
    cutoff_365d = now - 365 * 86400

    with _get_pool().connection() as conn, conn.transaction():
        conn.execute(
            "DELETE FROM mono_wallet_history WHERE account_id=%s AND ts<%s",
            (account_id, cutoff_365d))

        rows = conn.execute(
            "SELECT character_id, ts, balance FROM mono_wallet_history "
            "WHERE account_id=%s AND ts<%s ORDER BY character_id, ts",
            (account_id, cutoff_7d)).fetchall()
        if not rows:
            return

        from collections import defaultdict
        char_points = defaultdict(list)
        for cid, ts, bal in rows:
            char_points[cid].append((ts, bal))

        for cid, points in char_points.items():
            hourly_buckets = defaultdict(list)
            daily_buckets = defaultdict(list)
            for ts, bal in points:
                if ts < cutoff_90d:
                    daily_buckets[int(ts // 86400)].append((ts, bal))
                else:
                    hourly_buckets[int(ts // 3600)].append((ts, bal))

            compacted = []
            for bucket in sorted(daily_buckets):
                pts = daily_buckets[bucket]
                avg_ts = sum(t for t, _ in pts) / len(pts)
                avg_bal = sum(b for _, b in pts) / len(pts)
                compacted.append((avg_ts, avg_bal))
            for bucket in sorted(hourly_buckets):
                pts = hourly_buckets[bucket]
                avg_ts = sum(t for t, _ in pts) / len(pts)
                avg_bal = sum(b for _, b in pts) / len(pts)
                compacted.append((avg_ts, avg_bal))

            if len(compacted) < len(points):
                conn.execute(
                    "DELETE FROM mono_wallet_history WHERE account_id=%s "
                    "AND character_id=%s AND ts<%s",
                    (account_id, cid, cutoff_7d))
                for avg_ts, avg_bal in compacted:
                    conn.execute(
                        "INSERT INTO mono_wallet_history "
                        "(account_id, character_id, ts, balance) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (account_id, cid, avg_ts, avg_bal))


# ── exploration location trail ───────────────────────────────────────────────

def location_trail_append(account_id, character_id, entered_at, run_id,
                          system_id, system_name, security):
    """Append one system-entry to a character's trail. Ignores duplicates."""
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_location_trail (account_id, character_id, entered_at, "
            "run_id, system_id, system_name, security) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (account_id, character_id, entered_at, run_id, system_id,
             system_name, security))


def location_trail_query(account_id, character_id, run_id=None, since_ts=0.0):
    """Trail rows for one character, oldest-first. Filtered to a single run when
    run_id is given, else every entry since since_ts."""
    with _get_pool().connection() as conn:
        if run_id is not None:
            rows = conn.execute(
                "SELECT entered_at, run_id, system_id, system_name, security, "
                "scanned, cargo_isk, note, hidden, cargo_scanned_at, cargo_expires "
                "FROM mono_location_trail WHERE account_id=%s "
                "AND character_id=%s AND run_id=%s ORDER BY entered_at",
                (account_id, character_id, run_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT entered_at, run_id, system_id, system_name, security, "
                "scanned, cargo_isk, note, hidden, cargo_scanned_at, cargo_expires "
                "FROM mono_location_trail WHERE account_id=%s "
                "AND character_id=%s AND entered_at>=%s ORDER BY entered_at",
                (account_id, character_id, since_ts)).fetchall()
    return [{"entered_at": r[0], "run_id": r[1], "system_id": r[2],
             "system_name": r[3], "security": r[4], "scanned": r[5],
             "cargo_isk": r[6], "note": r[7] or "", "hidden": bool(r[8]),
             "cargo_scanned_at": r[9], "cargo_expires": r[10]} for r in rows]


def location_trail_annotate(account_id, character_id, entered_at,
                            scanned=None, cargo_isk=None, note=None, hidden=None,
                            cargo_scanned_at=None, cargo_expires=None):
    """Update the scanned flag, cargo_isk, note, hidden, cargo_scanned_at and/or
    cargo_expires of one trail entry. A cargo_isk of "" (empty) clears it back to
    NULL; passing cargo_scanned_at="" / cargo_expires="" likewise clears them."""
    sets, params = [], []
    if scanned is not None:
        sets.append("scanned=%s")
        params.append(bool(scanned))
    if cargo_isk is not None:
        sets.append("cargo_isk=%s")
        params.append(cargo_isk if cargo_isk != "" else None)
    if note is not None:
        sets.append("note=%s")
        params.append(str(note))
    if hidden is not None:
        sets.append("hidden=%s")
        params.append(bool(hidden))
    if cargo_scanned_at is not None:
        sets.append("cargo_scanned_at=%s")
        params.append(cargo_scanned_at if cargo_scanned_at != "" else None)
    if cargo_expires is not None:
        sets.append("cargo_expires=%s")
        params.append(cargo_expires if cargo_expires != "" else None)
    if not sets:
        return
    params += [account_id, character_id, entered_at]
    with _get_pool().connection() as conn:
        conn.execute(
            f"UPDATE mono_location_trail SET {', '.join(sets)} "
            "WHERE account_id=%s AND character_id=%s AND entered_at=%s", params)


def location_trail_delete_run(account_id, character_id, run_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_location_trail WHERE account_id=%s AND "
            "character_id=%s AND run_id=%s", (account_id, character_id, run_id))


# ── exploration session journal ──────────────────────────────────────────────

def exploration_session_upsert(account_id, character_id, run_id, name,
                               started_at, ended_at=None, notes="", cargo_value=None):
    """Create or update a session record (full upsert of the mutable fields)."""
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO mono_exploration_sessions (account_id, character_id, run_id, "
            "name, started_at, ended_at, notes, cargo_value) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (account_id, character_id, run_id) DO UPDATE SET "
            "name=EXCLUDED.name, ended_at=EXCLUDED.ended_at, notes=EXCLUDED.notes, "
            "cargo_value=EXCLUDED.cargo_value",
            (account_id, character_id, run_id, name, started_at, ended_at,
             notes, cargo_value))


def exploration_session_patch(account_id, character_id, run_id, **fields):
    """Update only the given columns of one session (name/ended_at/notes/cargo_value)."""
    allowed = ("name", "ended_at", "notes", "cargo_value")
    sets, params = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=%s")
            params.append(fields[k])
    if not sets:
        return
    params += [account_id, character_id, run_id]
    with _get_pool().connection() as conn:
        conn.execute(
            f"UPDATE mono_exploration_sessions SET {', '.join(sets)} "
            "WHERE account_id=%s AND character_id=%s AND run_id=%s", params)


def exploration_sessions_list(account_id, character_id):
    """All of a character's sessions, newest first."""
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT run_id, name, started_at, ended_at, notes, cargo_value "
            "FROM mono_exploration_sessions WHERE account_id=%s AND character_id=%s "
            "ORDER BY started_at DESC", (account_id, character_id)).fetchall()
    return [{"run_id": r[0], "name": r[1], "started_at": r[2], "ended_at": r[3],
             "notes": r[4], "cargo_value": r[5]} for r in rows]


def exploration_session_get(account_id, character_id, run_id):
    with _get_pool().connection() as conn:
        row = conn.execute(
            "SELECT run_id, name, started_at, ended_at, notes, cargo_value "
            "FROM mono_exploration_sessions WHERE account_id=%s AND character_id=%s "
            "AND run_id=%s", (account_id, character_id, run_id)).fetchone()
    if not row:
        return None
    return {"run_id": row[0], "name": row[1], "started_at": row[2],
            "ended_at": row[3], "notes": row[4], "cargo_value": row[5]}


def exploration_session_delete(account_id, character_id, run_id):
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM mono_exploration_sessions WHERE account_id=%s AND "
            "character_id=%s AND run_id=%s", (account_id, character_id, run_id))
