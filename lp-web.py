#!/usr/bin/env python3
"""
EVE Market Tools — unified web UI.

Three apps in one local server:
  • LP Store  — ranks LP-store offers by ISK/LP with drill-down shopping lists.
  • Arbitrage — scans a region for negative-spread (instant-flip) opportunities.
  • Industry  — ranks manufacturable items (T1 + T2 invention) by ISK/hour after
                material, job-install and blueprint cost, from a local SDE copy.

    pip install requests
    python lp-web.py            # opens http://localhost:8765
    python lp-web.py --port 9000 --no-browser
"""
__version__ = "1.100.6"

import argparse
import base64
import concurrent.futures
import datetime
import html
import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="4" fill="#080d11"/>'
    b'<rect x="3" y="21" width="7" height="8" rx="1" fill="#4fc3f7"/>'
    b'<rect x="12.5" y="15" width="7" height="14" rx="1" fill="#4fc3f7"/>'
    b'<rect x="22" y="8" width="7" height="21" rx="1" fill="#c8a040"/>'
    b'<polyline points="6.5,19 16,13 25.5,6" stroke="#4caf76"'
    b' stroke-width="2.5" fill="none" stroke-linecap="round"'
    b' stroke-linejoin="round"/>'
    b'</svg>'
)
_FAVICON_B64 = base64.b64encode(_FAVICON_SVG).decode()

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import arb_core
import ind_core
import sso_core
import pg_store
from lp_core import (
    ESI, HEADERS, HIGH_SPREAD_PCT, JITA_STATION_ID, LPError, build_detail, default_cache_dir,
    TRADE_HUBS, enrich_liquidity, evaluate, fetch_history_prices,
    fetch_history_volumes,
    fetch_orderbook_jita, fetch_order_rank, fetch_prices, fetch_prices_esi,
    fetch_sell_order_stats, get_offers,
    load_json, resolve_corp_id, resolve_corp_name, resolve_names,
    resolve_station_names, resolve_station_region,
    resolve_volumes, save_json, suggested_list_price,
)

SESSION = requests.Session()
# The server process (and this session's pooled keep-alive connections) lives
# for as long as the browser tab is open. ESI/Fuzzwork close idle connections
# server-side after a while, so the next reused pooled connection raises
# ConnectionError("Remote end closed connection without response") — retry
# transparently on a fresh connection instead of surfacing that to the UI.
# urllib3 treats that as a "read" error and, by default, only retries it for
# methods it considers safe to repeat — which excludes POST. Every POST this
# app makes through SESSION (bulk /universe/names/ and /universe/ids/ lookups,
# SSO token exchange/refresh) is either a pure read or already single-use-safe,
# so add POST to the retryable set rather than let a stale connection surface
# as a 500 on whichever endpoint happened to reuse it (e.g. /api/char/data).
_RETRY = Retry(total=3, connect=3, read=3, backoff_factor=0.3,
               status_forcelist=(502, 503, 504),
               allowed_methods=frozenset(Retry.DEFAULT_ALLOWED_METHODS | {"POST"}))
SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY))
SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY))
CACHE_DIR = default_cache_dir()
SETTINGS_PATH = CACHE_DIR / "lp_web_settings.json"
ARB_SETTINGS_PATH = CACHE_DIR / "arb_settings.json"
IND_SETTINGS_PATH = CACHE_DIR / "ind_settings.json"
JOBS_TRACK_PATH = CACHE_DIR / "ind_jobs_delivered.json"  # cumulative delivered-run counter
ORDER_EVENTS_PATH = CACHE_DIR / "order_events.json"  # market order sale/fill events
IND_BUILDS_PATH = CACHE_DIR / "ind_tracked_builds.json"  # frozen build-batch snapshots
WALLET_HISTORY_PATH = CACHE_DIR / "wallet_history.json"  # ISK balance time-series
USER_SETTINGS_DB_PATH = CACHE_DIR / "user_settings.sqlite"  # per-character synced settings
LP_LAST_SCAN_PATH = CACHE_DIR / "lp_last_scan.json"
IND_LAST_SCAN_PATH = CACHE_DIR / "ind_last_scan.json"
REFRESHED_CORPS = set()

# ── EVE SSO / accounts / sessions ────────────────────────────────────────────
# An *account* is a set of linked EVE characters; a browser *session* (cookie)
# points at one account. All per-user state lives on the Account object, so
# concurrent users on the public deploy never see each other's characters.
#
# Two modes, gated on pg_store.enabled() (i.e. DATABASE_URL):
#   • multi-user (Postgres): one Account per session, resolved from the cookie;
#     unauthenticated requests to non-public endpoints are rejected (401).
#   • legacy single-user (local `python lp-web.py`, tests): one implicit global
#     account, no cookie and no gate — behaviour is identical to before.

class Account:
    """All state for one user (a set of linked EVE characters).

    `characters`, `skill_profiles` and `bp_me_tes` are only ever mutated/iterated
    under `self.lock`, so a scan running in one request thread can't collide with
    a login/logout in another."""
    def __init__(self, account_id=None):
        self.account_id = account_id            # int, = the first linked char id
        # character_id -> {character_id, name, scopes, refresh_token,
        #                  access_token, expires_at}
        self.characters: dict = {}
        self.active_char_id: int | None = None
        # runtime-only caches (refetched from ESI; never persisted)
        self.skill_profiles: dict = {}          # cid -> {skill_id: level}
        self.bp_me_tes: dict = {}               # cid -> {type_id: (me,te,bpo,runs)}
        self.bp_refreshed_at = 0.0              # last owned-blueprint refresh (throttle)
        self.lock = threading.RLock()


# Pending PKCE handshakes keyed by `state` -> {verifier, redirect_uri, ts}.
_PKCE: dict = {}
_PKCE_LOCK = threading.Lock()
_PKCE_TTL = 600  # abandoned handshakes are swept after 10 minutes

# In-memory caches: cookie sid -> Account, and account_id -> Account. Both are
# hydrated lazily from Postgres and guarded by _REGISTRY_LOCK.
_SESSIONS: dict = {}
_ACCOUNTS: dict = {}
_REGISTRY_LOCK = threading.RLock()
_SESSION_TTL = 30 * 24 * 3600  # idle sessions expire after 30 days

# The single implicit account used only in legacy (no-Postgres) mode.
_LEGACY_ACCOUNT = Account()

# Per-request context (ThreadingHTTPServer = one thread per request). `.account`
# is the resolved Account (or None when unauthenticated in multi-user mode).
_REQUEST = threading.local()

# The host:port the server is actually bound to, for the suggested callback URL.
_SERVER_PORT = 8765

REGION_NAMES = {
    10000002: "The Forge (Jita)",
    10000043: "Domain (Amarr)",
    10000032: "Sinq Laison (Dodixie)",
    10000042: "Metropolis (Hek)",
    10000030: "Heimatar (Rens)",
}

HUB_SYSTEM_IDS = {
    60003760: 30000142,   # Jita
    60008494: 30002187,   # Amarr
    60004588: 30002510,   # Rens
    60011866: 30002659,   # Dodixie
    60005686: 30002053,   # Hek
}

# Arb lookup caches — loaded lazily from disk on first arb scan, updated in-memory.
_ARB_STATION_CACHE: dict = {}
_ARB_VOLUME_CACHE: dict = {}
_ARB_SYSTEM_CACHE: dict = {}
_ARB_ROUTE_CACHE: dict = {}
_ARB_CACHES_LOADED = False
_ARB_CACHE_LOCK = threading.Lock()


def _ensure_arb_caches():
    global _ARB_STATION_CACHE, _ARB_VOLUME_CACHE, _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE, _ARB_CACHES_LOADED
    with _ARB_CACHE_LOCK:
        if not _ARB_CACHES_LOADED:
            _ARB_STATION_CACHE, _ARB_VOLUME_CACHE, _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE = \
                arb_core.load_lookup_cache(CACHE_DIR)
            _ARB_CACHES_LOADED = True


# ── LP scanner helpers ──────────────────────────────────────────────────────

# Durable user state (settings blobs, delivered-jobs counter, order events,
# tokens, per-character synced settings) goes to Postgres when DATABASE_URL is
# set (the Railway deploy), else to the original cache-dir files/SQLite — see
# pg_store. Disposable caches (SDE, ESI responses) always stay on disk.

# In multi-user (Postgres) mode the per-tool settings blobs and the per-field
# /api/prefs endpoints are obsolete: the client pushes its full settings blob to
# the account row via /api/settings/sync (the single source of truth), so these
# become no-ops there and only the local file store remains for no-login dev.

def load_settings():
    return {} if pg_store.enabled() else load_json(SETTINGS_PATH, {})


def save_settings(d):
    if not pg_store.enabled():
        save_json(SETTINGS_PATH, d)


def load_arb_settings():
    return {} if pg_store.enabled() else load_json(ARB_SETTINGS_PATH, {})


def save_arb_settings(d):
    if not pg_store.enabled():
        save_json(ARB_SETTINGS_PATH, d)


def load_ind_settings():
    return {} if pg_store.enabled() else load_json(IND_SETTINGS_PATH, {})


def save_ind_settings(d):
    if not pg_store.enabled():
        save_json(IND_SETTINGS_PATH, d)


# ── Account-scoped durable blobs (counters, order events, last scan) ──────────
# Keyed per account in Postgres so users never see each other's data; a plain
# file in legacy mode (single user).

def _acct_kv_load(acct, key, path, default):
    if pg_store.enabled():
        return pg_store.kv_get(f"{key}:{acct.account_id}", default) if acct else default
    return load_json(path, default)


def _acct_kv_save(acct, key, path, data):
    if pg_store.enabled():
        if acct:
            pg_store.kv_set(f"{key}:{acct.account_id}", data)
    else:
        save_json(path, data)


def _load_last_scan(acct, tag):
    key, path = (("lp_last_scan", LP_LAST_SCAN_PATH) if tag == "lp"
                 else ("ind_last_scan", IND_LAST_SCAN_PATH))
    return _acct_kv_load(acct, key, path, None)


def _save_last_scan(acct, tag, blob):
    key, path = (("lp_last_scan", LP_LAST_SCAN_PATH) if tag == "lp"
                 else ("ind_last_scan", IND_LAST_SCAN_PATH))
    _acct_kv_save(acct, key, path, blob)


# ── Per-character synced settings ────────────────────────────────────────────
# Full client-side settings blob, keyed by EVE character_id, so the same
# character sees identical settings (columns, filters, tabs, ...) on any
# device. Only used once a character is logged in via SSO; unauthenticated
# use keeps the file-based settings above (single local "device").

def _user_settings_conn():
    USER_SETTINGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_SETTINGS_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings ("
        "character_id INTEGER PRIMARY KEY, settings_json TEXT NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    return conn


def load_user_settings(character_id):
    """Return the synced settings dict for `character_id`, or None if this
    character has never synced settings from any device yet."""
    if pg_store.enabled():
        return pg_store.user_settings_get(character_id)
    conn = _user_settings_conn()
    try:
        row = conn.execute(
            "SELECT settings_json FROM user_settings WHERE character_id = ?",
            (character_id,),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def save_user_settings(character_id, data):
    if pg_store.enabled():
        pg_store.user_settings_set(character_id, data, time.time())
        return
    conn = _user_settings_conn()
    try:
        conn.execute(
            "INSERT INTO user_settings (character_id, settings_json, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(character_id) DO UPDATE SET "
            "settings_json = excluded.settings_json, updated_at = excluded.updated_at",
            (character_id, json.dumps(data), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


# ── EVE SSO helpers ───────────────────────────────────────────────────────────

def _eve_client_id():
    """The EVE application Client ID, from the EVE_CLIENT_ID environment variable
    (configured on the host, e.g. Railway) — not stored in the app settings."""
    return (os.environ.get("EVE_CLIENT_ID") or "").strip()


def _suggested_callback():
    return f"http://localhost:{_SERVER_PORT}/callback"


def _callback_url():
    """The redirect_uri to use — from the EVE_CALLBACK_URL environment variable
    when set (the deploy), else the suggested localhost callback (local dev)."""
    return (os.environ.get("EVE_CALLBACK_URL") or "").strip() or _suggested_callback()


# ── Account / session management ─────────────────────────────────────────────

def current_account():
    """The Account for the in-flight request, or None (unauthenticated)."""
    return getattr(_REQUEST, "account", None)


def require_account():
    """The current Account, or raise LPError. An account always has ≥1 linked
    character (login = EVE SSO), so this doubles as the login check."""
    acct = current_account()
    if acct is None or not acct.characters:
        raise LPError("Log in with EVE to continue.")
    return acct


# `_require_login` kept as an alias for the several handlers that used it.
def _require_login():
    require_account()


def _account_blob(acct):
    """The persistable form of an account (characters + active selection)."""
    return {
        "version": 2,
        "active_char_id": acct.active_char_id,
        "characters": [
            {"character_id": c["character_id"], "name": c["name"],
             "scopes": c["scopes"], "refresh_token": c["refresh_token"]}
            for c in acct.characters.values()
        ],
    }


def _persist_account(acct):
    """Durably store one account's characters + active selection."""
    data = _account_blob(acct)
    if pg_store.enabled():
        if acct.account_id is None:
            return
        pg_store.account_set(acct.account_id, data, time.time())
        for cid in acct.characters:
            pg_store.char_account_set(cid, acct.account_id)
    else:
        sso_core.save_tokens(CACHE_DIR, data)


def _hydrate_account(account_id, saved):
    """Build an Account from a persisted blob (v1 single-char or v2 multi)."""
    acct = Account(account_id)
    if saved.get("version") == 2:
        chars = saved.get("characters", [])
        active = saved.get("active_char_id")
    elif saved.get("refresh_token") and saved.get("character_id"):
        chars = [saved]
        active = saved["character_id"]
    else:
        chars, active = [], None
    for c in chars:
        cid = c["character_id"]
        acct.characters[cid] = {
            "character_id": cid, "name": c.get("name"),
            "scopes": c.get("scopes", []),
            "refresh_token": c["refresh_token"],
            "access_token": None, "expires_at": 0,
        }
    acct.active_char_id = active if active in acct.characters else next(iter(acct.characters), None)
    return acct


def _get_account_by_id(account_id):
    """Load (and cache) the account with this id from Postgres, or None."""
    with _REGISTRY_LOCK:
        acct = _ACCOUNTS.get(account_id)
    if acct is not None:
        return acct
    data = pg_store.account_get(account_id)
    if data is None:
        return None
    acct = _hydrate_account(account_id, data)
    with _REGISTRY_LOCK:
        _ACCOUNTS[account_id] = acct
    return acct


def _resolve_session(sid):
    """Resolve a cookie session id to its Account (multi-user mode), or None."""
    if not sid:
        return None
    with _REGISTRY_LOCK:
        acct = _SESSIONS.get(sid)
    if acct is not None:
        return acct
    account_id = pg_store.session_get(sid)
    if account_id is None:
        return None
    acct = _get_account_by_id(account_id)
    if acct is not None:
        with _REGISTRY_LOCK:
            _SESSIONS[sid] = acct
    return acct


def _new_session(acct):
    """Mint a session id bound to an account, cache and persist it."""
    sid = secrets.token_urlsafe(32)
    with _REGISTRY_LOCK:
        _SESSIONS[sid] = acct
    if pg_store.enabled():
        pg_store.session_set(sid, acct.account_id)
    return sid


def _migrate_legacy_auth():
    """One-time: fold the pre-multiuser global `eve_auth` blob into a real
    account so the existing Railway deploy keeps its linked characters and
    settings (users just re-authenticate once to get a session cookie)."""
    if pg_store.kv_get("eve_auth_migrated"):
        return
    saved = pg_store.kv_get("eve_auth", {}) or {}
    acct = _hydrate_account(None, saved)
    if acct.characters:
        acct.account_id = acct.active_char_id or next(iter(acct.characters))
        _persist_account(acct)
        # Carry the old per-character settings row over to the new account key.
        old = pg_store.user_settings_get(acct.account_id)
        if old is not None:
            pg_store.account_settings_set(acct.account_id, old, time.time())
        print(f"[auth] migrated legacy eve_auth → account {acct.account_id} "
              f"({len(acct.characters)} character(s))", file=sys.stderr)
    pg_store.kv_set("eve_auth_migrated", True)


def _migrate_counters():
    """One-time: move the delivered-runs counter from the old JSON kv blobs into
    the per-(account,character) mono_delivered_jobs table. Sources: the bare
    pre-multiuser ``ind_jobs_delivered`` blob and the v1.81.0 per-account
    ``ind_jobs_delivered:<aid>`` blobs. Each character is mapped to its account
    via the char→account index; the bare (historical) entry is preferred and the
    per-account entry only unions in already-seen job ids so nothing recounts.
    Order events are ephemeral (7-day) so they're not migrated — they simply
    re-baseline on the next character fetch, producing no spurious events."""
    if pg_store.kv_get("counters_migrated"):
        return
    merged = {}  # (account_id, character_id) -> entry

    def _add(aid, cid, entry, historical):
        key = (aid, cid)
        if key not in merged:
            merged[key] = entry
        elif historical:
            entry["seen_job_ids"] = list(
                set(entry.get("seen_job_ids", [])) | set(merged[key].get("seen_job_ids", [])))
            merged[key] = entry
        else:
            merged[key]["seen_job_ids"] = list(
                set(merged[key].get("seen_job_ids", [])) | set(entry.get("seen_job_ids", [])))

    bare = pg_store.kv_get("ind_jobs_delivered", {}) or {}
    for cid_str, entry in bare.items():
        aid = pg_store.char_account_get(int(cid_str))
        if aid is not None:
            _add(aid, int(cid_str), entry, historical=True)
    for aid in pg_store.all_account_ids():
        ns = pg_store.kv_get(f"ind_jobs_delivered:{aid}", {}) or {}
        for cid_str, entry in ns.items():
            _add(aid, int(cid_str), entry, historical=False)

    for (aid, cid), entry in merged.items():
        pg_store.delivered_jobs_set(aid, cid, entry)
    if merged:
        print(f"[counters] migrated delivered-jobs for {len(merged)} character(s)",
              file=sys.stderr)
    pg_store.kv_set("counters_migrated", True)


def _startup_restore():
    """On boot: legacy mode loads the single file-backed account; multi-user mode
    runs the one-time legacy→account migration (sessions load lazily per cookie)."""
    if pg_store.enabled():
        try:
            _migrate_legacy_auth()
            _migrate_counters()
        except Exception as e:  # noqa: BLE001
            print(f"[auth] legacy migration skipped: {type(e).__name__}: {e}",
                  file=sys.stderr)
        return
    saved = sso_core.load_tokens(CACHE_DIR)
    acct = _hydrate_account(None, saved)
    _LEGACY_ACCOUNT.characters = acct.characters
    _LEGACY_ACCOUNT.active_char_id = acct.active_char_id
    if _LEGACY_ACCOUNT.characters:
        # Register the implicit account so the 5-min background refresh loop
        # (which iterates _ACCOUNTS) keeps its ESI data — and wallet history —
        # current after a restart, not just after an in-session login.
        _LEGACY_ACCOUNT.account_id = (_LEGACY_ACCOUNT.active_char_id
                                      or next(iter(_LEGACY_ACCOUNT.characters)))
        with _REGISTRY_LOCK:
            _ACCOUNTS[_LEGACY_ACCOUNT.account_id] = _LEGACY_ACCOUNT
    if saved.get("version") != 2 and _LEGACY_ACCOUNT.characters:
        _persist_account(_LEGACY_ACCOUNT)  # upgrade v1 file to v2


def _sweep_expired():
    """Drop abandoned PKCE handshakes and idle sessions. Cheap; run on login and
    on a background timer so it happens even when nobody logs in for a while."""
    now = time.time()
    with _PKCE_LOCK:
        for st in [s for s, h in _PKCE.items() if now - h.get("ts", 0) > _PKCE_TTL]:
            _PKCE.pop(st, None)
    if pg_store.enabled():
        try:
            pg_store.sessions_sweep(_SESSION_TTL)
        except Exception:  # noqa: BLE001
            pass


def _sweep_loop(interval=3600):
    while True:
        time.sleep(interval)
        try:
            _sweep_expired()
        except Exception:  # noqa: BLE001
            pass


_BG_REFRESH_INTERVAL = 300  # 5 minutes
# Epoch of the next scheduled background refresh, so do_char_data can report an
# authoritative "next sync in …" that reflects the real server schedule rather
# than a value each browser invents on load. 0 until the loop establishes it.
_BG_NEXT_SYNC_TS = 0.0


def _bg_char_refresh_loop():
    """Background loop: periodically refresh all active accounts' ESI data
    so wallet history accumulates even when no browser is open. Runs on a fixed
    _BG_REFRESH_INTERVAL cadence (the sweep's own duration is absorbed into the
    interval) so the published next-sync time stays accurate."""
    global _BG_NEXT_SYNC_TS
    time.sleep(30)
    while True:
        _BG_NEXT_SYNC_TS = time.time() + _BG_REFRESH_INTERVAL
        try:
            accounts = list(_ACCOUNTS.values())
            for acct in accounts:
                with acct.lock:
                    char_ids = list(acct.characters.keys())
                if not char_ids:
                    continue
                for cid in char_ids:
                    try:
                        _fetch_one_char_data(acct, cid)
                    except Exception:
                        pass
        except Exception:
            pass
        # Tell every open stream the sweep is done so they re-publish the shared
        # countdown in lockstep (data-change nudges already fired per account).
        _CHAR_PUBSUB.announce_sweep()
        time.sleep(max(0, _BG_NEXT_SYNC_TS - time.time()))


def _warn_if_multi_replica():
    """This monolith caches sessions/accounts in-process and serializes token
    refresh per-process, so it must run as a single replica. Warn loudly if the
    host env suggests otherwise (the in-memory caches + per-account lock are only
    correct within one process — see the single-replica invariant)."""
    for var in ("RAILWAY_REPLICA_COUNT", "NUM_REPLICAS", "WEB_CONCURRENCY"):
        val = os.environ.get(var)
        if val and val.isdigit() and int(val) > 1:
            print(f"[WARN] {var}={val}: this app must run as a SINGLE replica — it "
                  "caches sessions/accounts in-process and serialises per-account "
                  "token refresh per-process. Multiple replicas can race.",
                  file=sys.stderr)


def _access_token(acct, cid=None):
    """A valid bearer token for one of the account's characters (defaults to the
    active one). Refreshes transparently when expired."""
    with acct.lock:
        if cid is None:
            cid = acct.active_char_id
        char = acct.characters.get(cid)
        if not char or not char.get("refresh_token"):
            raise LPError("Not logged in to EVE.")
        if char.get("access_token") and not sso_core.access_token_expired(char.get("expires_at")):
            return char["access_token"]
        client_id = _eve_client_id()
        if not client_id:
            raise LPError("EVE login is not configured (set EVE_CLIENT_ID).")
        tok = sso_core.refresh_access_token(client_id, char["refresh_token"], SESSION)
        claims = sso_core.decode_jwt_payload(tok["access_token"])
        char.update({
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", char["refresh_token"]),
            "expires_at": time.time() + int(tok.get("expires_in", 1200)),
            "name": claims.get("name") or char["name"],
            "scopes": claims.get("scopes") or char["scopes"],
        })
        _persist_account(acct)
        return char["access_token"]


def _refresh_skill_profile(acct, cid):
    """Pull the character's skills and cache {skill_id: level} for Industry."""
    try:
        skills = sso_core.fetch_skills(_access_token(acct, cid), cid, SESSION)
        profile = sso_core.skill_profile_from_skills(skills)
    except (LPError, requests.RequestException):
        profile = {}
    with acct.lock:
        acct.skill_profiles.setdefault(cid, {})
        if profile:
            acct.skill_profiles[cid] = profile


def _refresh_char_blueprints(acct, cid):
    """Pull the character's owned blueprints and cache each type's best ME/TE.
    Supplements from active industry jobs (a running manufacturing job proves
    ownership even if the blueprints endpoint hasn't caught up)."""
    try:
        token = _access_token(acct, cid)
        bps = sso_core.fetch_character_blueprints(token, cid, SESSION)
        bp_map = sso_core.owned_blueprint_lookup(bps)
        try:
            jobs = sso_core.fetch_industry_jobs(token, cid, SESSION)
            for j in jobs:
                if j.get("activity_id") != 1 or j.get("status") != "active":
                    continue
                bp_tid = j.get("blueprint_type_id")
                if bp_tid and bp_tid not in bp_map:
                    bp_map[bp_tid] = (0, 0, True, -1)
        except (LPError, requests.RequestException):
            pass
    except (LPError, requests.RequestException):
        bp_map = None
    with acct.lock:
        acct.bp_me_tes.setdefault(cid, {})
        if bp_map is not None:
            acct.bp_me_tes[cid] = bp_map


_BP_REFRESH_MIN_INTERVAL = 15  # seconds — dedupe the industry tab's preview burst


def _refresh_all_blueprints(acct, force=False):
    """Refresh owned-blueprint ownership for EVERY linked character, in parallel,
    so a blueprint transferred between alts is reflected on the next scan without
    a re-login. Throttled (unless forced) so the industry tab's rapid preview
    scans don't each re-hit ESI. Freshness is ultimately bounded by ESI's own
    ~1h cache on /characters/{id}/blueprints/."""
    with acct.lock:
        if not force and (time.time() - acct.bp_refreshed_at) < _BP_REFRESH_MIN_INTERVAL:
            return
        acct.bp_refreshed_at = time.time()
        char_ids = list(acct.characters.keys())
    if not char_ids:
        return
    if len(char_ids) == 1:
        _refresh_char_blueprints(acct, char_ids[0])
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(char_ids), 8)) as pool:
            list(pool.map(lambda c: _refresh_char_blueprints(acct, c), char_ids))


def do_auth_login(q):
    """Begin the PKCE handshake — returns the authorize URL to send the browser to."""
    client_id = _eve_client_id()
    if not client_id:
        raise LPError("EVE login is not configured — set the EVE_CLIENT_ID environment variable on the server.")
    _sweep_expired()
    verifier, challenge = sso_core.make_pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = _callback_url()
    with _PKCE_LOCK:
        _PKCE[state] = {"verifier": verifier, "redirect_uri": redirect_uri,
                        "ts": time.time()}
    url = sso_core.build_authorize_url(client_id, redirect_uri, sso_core.SCOPES, state, challenge)
    return {"url": url}


def do_auth_status(q):
    acct = current_account()
    if acct is None:
        # acct is None only in multi-user mode with no valid session — the
        # visitor must log in before the app is usable at all. (Legacy mode
        # always resolves to _LEGACY_ACCOUNT, so needs_login stays False there
        # and the app works without any EVE login.)
        return {"logged_in": False, "needs_login": True, "characters": [],
                "active_char_id": None, "character_id": None, "name": None,
                "scopes": []}
    with acct.lock:
        chars = [{"character_id": c["character_id"], "name": c["name"]}
                 for c in acct.characters.values()]
        active = acct.characters.get(acct.active_char_id)
        return {
            "logged_in": bool(acct.characters),
            "needs_login": False,
            "characters": chars,
            "active_char_id": acct.active_char_id,
            "character_id": acct.active_char_id,
            "name": active["name"] if active else None,
            "scopes": active["scopes"] if active else [],
        }


def do_auth_switch(q):
    """Switch the active character. It drives the LP budget, the Industry
    skills/BP calculations and the header wallet, so refresh that character's
    skill profile and blueprints while we're here."""
    acct = require_account()
    cid = None
    with acct.lock:
        if "active_char_id" in q:
            want = int(q["active_char_id"][0])
            if want in acct.characters:
                acct.active_char_id = want
                cid = want
        _persist_account(acct)
    if cid is not None:
        _refresh_skill_profile(acct, cid)
        _refresh_char_blueprints(acct, cid)
    return do_auth_status({})


def _forget_account(acct):
    """Drop an account and all its sessions from the caches + store."""
    _CHAR_PUBSUB.forget(id(acct))
    with _REGISTRY_LOCK:
        _ACCOUNTS.pop(acct.account_id, None)
        for sid in [s for s, a in _SESSIONS.items() if a is acct]:
            _SESSIONS.pop(sid, None)
            if pg_store.enabled():
                pg_store.session_delete(sid)
    if pg_store.enabled() and acct.account_id is not None:
        for cid in list(acct.characters):
            pg_store.char_account_delete(cid)
        pg_store.account_delete(acct.account_id)


def do_auth_logout(q):
    acct = current_account()
    if acct is None:
        return {"ok": True}
    char_id = q.get("char_id", [None])[0]
    with acct.lock:
        if char_id:
            cid = int(char_id)
            acct.characters.pop(cid, None)
            acct.skill_profiles.pop(cid, None)
            acct.bp_me_tes.pop(cid, None)
            if pg_store.enabled():
                pg_store.char_account_delete(cid)
            if acct.active_char_id == cid:
                acct.active_char_id = next(iter(acct.characters), None)
            remaining = bool(acct.characters)
        else:
            acct.characters.clear()
            acct.skill_profiles.clear()
            acct.bp_me_tes.clear()
            acct.active_char_id = None
            remaining = False
    if remaining:
        _persist_account(acct)
    else:
        _forget_account(acct)
    return {"ok": True}


_JOBS_TRACK_LOCK = threading.Lock()


ORDER_EVENT_EXPIRY = 7 * 24 * 3600  # auto-expire after 1 week


def _delivered_jobs_apply(entry, jobs, names):
    """Pure delivered-runs accumulator. Given a character's prior entry (or None
    for first sight), fold in newly-delivered jobs. Returns
    ``(new_entry_or_None, result)`` — new_entry is None when nothing changed."""
    first_seen = entry is None
    if entry is None:
        entry = {"seen_job_ids": [], "total_runs": 0, "total_jobs": 0,
                 "since": time.time(), "by_product": {}}
    seen = set(entry["seen_job_ids"])
    changed = first_seen
    for j in jobs:
        if j.get("status") != "delivered":
            continue
        jid = j.get("job_id")
        if jid is None or jid in seen:
            continue
        seen.add(jid)
        changed = True
        if first_seen:
            continue
        runs = j.get("runs") or 0
        entry["total_runs"] += runs
        entry["total_jobs"] += 1
        pid = str(j.get("product_type_id"))
        prod = entry["by_product"].setdefault(
            pid, {"name": names.get(j.get("product_type_id"), "?"), "runs": 0, "jobs": 0})
        prod["runs"] += runs
        prod["jobs"] += 1
    entry["seen_job_ids"] = list(seen)
    result = {"total_runs": entry["total_runs"], "total_jobs": entry["total_jobs"],
              "since": entry["since"], "by_product": entry["by_product"]}
    return (entry if changed else None), result


def _track_delivered_jobs(acct, cid, jobs, names):
    """Cumulative counter of runs/jobs the character has *delivered*, persisted
    across restarts. Only counts jobs newly seen as "delivered" — the first time
    a character is observed, its already-delivered jobs (ESI's 90-day completed
    window) are recorded as a baseline but not counted, so the counter only grows
    from the moment this feature started watching, as advertised to the user."""
    if pg_store.enabled():
        return pg_store.with_delivered_jobs(
            acct.account_id, cid,
            lambda entry: _delivered_jobs_apply(entry, jobs, names))
    with _JOBS_TRACK_LOCK:
        store = _acct_kv_load(acct, "ind_jobs_delivered", JOBS_TRACK_PATH, {})
        new_entry, result = _delivered_jobs_apply(store.get(str(cid)), jobs, names)
        if new_entry is not None:
            store[str(cid)] = new_entry
            _acct_kv_save(acct, "ind_jobs_delivered", JOBS_TRACK_PATH, store)
        return result


_WALLET_RECORD_LOCK = threading.Lock()
_WALLET_LAST_RECORDED = {}
_WALLET_PRUNE_LAST = 0.0


def _record_wallet_snapshot(acct, cid, balance):
    """Record a wallet balance data point for the given character."""
    if balance is None:
        return
    now = time.time()
    if now - _WALLET_LAST_RECORDED.get(cid, 0) < 60:
        return
    _WALLET_LAST_RECORDED[cid] = now
    if pg_store.enabled():
        pg_store.wallet_history_append(acct.account_id, cid, now, balance)
    else:
        with _WALLET_RECORD_LOCK:
            store = load_json(WALLET_HISTORY_PATH, {})
            series = store.setdefault(str(cid), [])
            series.append([now, balance])
            save_json(WALLET_HISTORY_PATH, store)
    _maybe_prune_wallet_history(acct)


def _compact_series(series, now):
    """Compact a list of [ts, balance] points:
    - Last 7 days: keep full resolution
    - 7–90 days old: one point per hour (average)
    - 90–365 days old: one point per day (average)
    - Older than 365 days: discard
    Returns (new_series, changed)."""
    cutoff_7d = now - 7 * 86400
    cutoff_90d = now - 90 * 86400
    cutoff_365d = now - 365 * 86400

    recent = []
    hourly_buckets = {}
    daily_buckets = {}

    for pt in series:
        ts = pt[0]
        if ts < cutoff_365d:
            continue
        elif ts < cutoff_90d:
            day_key = int(ts // 86400)
            daily_buckets.setdefault(day_key, []).append(pt)
        elif ts < cutoff_7d:
            hour_key = int(ts // 3600)
            hourly_buckets.setdefault(hour_key, []).append(pt)
        else:
            recent.append(pt)

    compacted = []
    for day_key in sorted(daily_buckets):
        pts = daily_buckets[day_key]
        avg_ts = sum(p[0] for p in pts) / len(pts)
        avg_bal = sum(p[1] for p in pts) / len(pts)
        compacted.append([avg_ts, avg_bal])
    for hour_key in sorted(hourly_buckets):
        pts = hourly_buckets[hour_key]
        avg_ts = sum(p[0] for p in pts) / len(pts)
        avg_bal = sum(p[1] for p in pts) / len(pts)
        compacted.append([avg_ts, avg_bal])
    compacted.extend(recent)

    changed = len(compacted) != len(series)
    return compacted, changed


def _maybe_prune_wallet_history(acct):
    """Compact old data at most once per hour."""
    global _WALLET_PRUNE_LAST
    now = time.time()
    if now - _WALLET_PRUNE_LAST < 3600:
        return
    _WALLET_PRUNE_LAST = now
    if pg_store.enabled():
        pg_store.wallet_history_compact(acct.account_id, now)
    else:
        with _WALLET_RECORD_LOCK:
            store = load_json(WALLET_HISTORY_PATH, {})
            any_changed = False
            for cid_str in list(store.keys()):
                series = store[cid_str]
                if not series:
                    continue
                new_series, changed = _compact_series(series, now)
                if changed:
                    store[cid_str] = new_series
                    any_changed = True
            if any_changed:
                save_json(WALLET_HISTORY_PATH, store)


def _order_expiry_ts(prev):
    """Unix timestamp at which a market order reaches the end of its listing
    duration (``issued`` + ``duration`` days). Returns None if we can't tell."""
    issued, duration = prev.get("issued"), prev.get("duration")
    if not issued or duration is None:
        return None
    try:
        s = issued.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        return dt.timestamp() + float(duration) * 86400
    except (ValueError, TypeError):
        return None


# An order that vanishes within this margin of its computed expiry time is
# treated as having expired rather than sold — covers clock skew and the gap
# between a sync and the exact expiry instant.
_EXPIRY_MARGIN = 6 * 3600


def _compute_order_deltas(prev_orders, last_sales, current_orders, names, char_name):
    """Pure diff of current vs previously-seen orders. Returns
    ``(new_events, new_prev, new_sales)`` — new_events are freshly-detected
    sale/fill/expiry events; new_prev/new_sales are the snapshots to persist."""
    now = time.time()
    last_sales = dict(last_sales)
    current_by_id = {str(o["order_id"]): o for o in current_orders if o.get("order_id")}

    new_events = []
    for oid_str, prev in prev_orders.items():
        cur = current_by_id.get(oid_str)
        prev_remain = prev.get("volume_remain", 0)
        # An order that disappeared with volume left may have expired (reached
        # its listing duration) rather than been fully bought out. Only sell
        # orders "expire" in a way worth flagging; a vanished order past its
        # computed end-of-life is counted as expired, not sold.
        expired = False
        if cur is None and prev_remain > 0:
            exp_ts = _order_expiry_ts(prev)
            if exp_ts is not None and now >= exp_ts - _EXPIRY_MARGIN:
                expired = True
        sold = prev_remain if cur is None else prev_remain - cur.get("volume_remain", 0)
        if sold > 0:
            new_events.append({
                "id": f"{oid_str}_{int(now)}",
                "ts": now,
                "order_id": int(oid_str),
                "type_name": prev.get("type_name") or names.get(prev.get("type_id"), "?"),
                "sold": sold,
                "price": prev.get("price", 0),
                "is_buy_order": prev.get("is_buy_order", False),
                "filled": cur is None and not expired,
                "expired": expired,
                "character_name": char_name,
                "dismissed": False,
            })
            if cur is not None:
                last_sales[oid_str] = {"ts": now, "sold": sold}

    last_sales = {k: v for k, v in last_sales.items() if k in current_by_id}
    new_prev = {
        str(o["order_id"]): {
            "volume_remain": o.get("volume_remain"),
            "type_id": o.get("type_id"),
            "type_name": o.get("type_name"),
            "price": o.get("price"),
            "is_buy_order": o.get("is_buy_order"),
            "issued": o.get("issued"),
            "duration": o.get("duration"),
        }
        for o in current_orders if o.get("order_id")
    }
    return new_events, new_prev, last_sales


def _track_order_changes(acct, cid, current_orders, names):
    """Compare current market orders with previously seen ones, recording sale/
    fill events. Returns ``(events, last_sales)``; callers use last_sales for the
    per-order "last sale" annotation (the events are read back via
    _get_order_events)."""
    char_name = acct.characters.get(cid, {}).get("name", "")
    if pg_store.enabled():
        def mutate(prev, sales):
            events, new_prev, new_sales = _compute_order_deltas(
                prev, sales, current_orders, names, char_name)
            return events, new_prev, new_sales, (events, new_sales)
        return pg_store.with_order_state(acct.account_id, cid, mutate)

    store = _acct_kv_load(acct, "order_events", ORDER_EVENTS_PATH, {})
    char_key, prev_key, sales_key = str(cid), f"_prev_{cid}", f"_sales_{cid}"
    now = time.time()
    events = [e for e in store.get(char_key, [])
              if now - e["ts"] < ORDER_EVENT_EXPIRY and not e.get("dismissed")]
    new_events, new_prev, new_sales = _compute_order_deltas(
        store.get(prev_key, {}), store.get(sales_key, {}), current_orders, names, char_name)
    events.extend(new_events)
    store[prev_key] = new_prev
    store[char_key] = events
    store[sales_key] = new_sales
    _acct_kv_save(acct, "order_events", ORDER_EVENTS_PATH, store)
    return events, new_sales


def _get_order_events(acct):
    """Return all non-dismissed, non-expired events across the account's chars."""
    now = time.time()
    if pg_store.enabled():
        return pg_store.order_events_active(acct.account_id, now - ORDER_EVENT_EXPIRY)
    store = _acct_kv_load(acct, "order_events", ORDER_EVENTS_PATH, {})
    all_events = []
    for key, val in store.items():
        if key.startswith("_prev_") or key.startswith("_sales_"):
            continue
        if isinstance(val, list):
            all_events.extend(
                e for e in val
                if not e.get("dismissed") and now - e["ts"] < ORDER_EVENT_EXPIRY
            )
    all_events.sort(key=lambda e: e["ts"], reverse=True)
    return all_events


def _dismiss_order_event(acct, event_id):
    """Mark a single event as dismissed, or dismiss all if event_id is 'all'."""
    if pg_store.enabled():
        pg_store.order_events_dismiss(acct.account_id, event_id)
        return
    store = _acct_kv_load(acct, "order_events", ORDER_EVENTS_PATH, {})
    for key, val in store.items():
        if key.startswith("_prev_") or key.startswith("_sales_"):
            continue
        if isinstance(val, list):
            for e in val:
                if event_id == "all" or e.get("id") == event_id:
                    e["dismissed"] = True
    _acct_kv_save(acct, "order_events", ORDER_EVENTS_PATH, store)


# ── Tracked builds ───────────────────────────────────────────────────────────
# A "tracked build" freezes the Industry detail panel's stats at the moment the
# user kicks off a manufacturing job in-game, so the exact economics they
# committed to stay visible days later even as market prices drift. Stored
# per-account as a list of {id, created_at, runs, snapshot:{…the /api/ind/detail
# blob…}, status}. Status is derived from the character's live ESI jobs, never
# stored authoritatively: a build with no matching active job is "awaiting"
# (a warning), one matched to an active manufacturing job of the same blueprint
# and run count is "building", and once that linked job has left ESI's active
# list the build is "done". Linking is done client-side against AUTH.data.jobs
# (the same source as the timers) so the server just persists the frozen blobs.
_MAX_TRACKED_BUILDS = 200


def _load_tracked_builds(acct):
    store = _acct_kv_load(acct, "ind_tracked_builds", IND_BUILDS_PATH, None)
    if not isinstance(store, list):
        return []
    return store


def _save_tracked_builds(acct, builds):
    _acct_kv_save(acct, "ind_tracked_builds", IND_BUILDS_PATH, builds)


def do_ind_builds_list(q):
    acct = current_account()
    if not acct:
        return {"builds": []}
    return {"builds": _load_tracked_builds(acct)}


def do_ind_builds_save(q):
    """Freeze a build snapshot. The client sends the full detail blob it is
    already showing (so the stored numbers exactly match what the user saw) plus
    the run count; the server stamps an id + created_at and prepends it."""
    acct = current_account()
    if not acct:
        return {"error": "not available"}
    raw = q.get("snapshot", [None])[0]
    if not raw:
        return {"error": "missing snapshot"}
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {"error": "bad snapshot"}
    if not isinstance(snapshot, dict):
        return {"error": "bad snapshot"}
    try:
        runs = max(1, int(q.get("runs", ["1"])[0]))
    except (TypeError, ValueError):
        runs = 1
    now = time.time()
    build = {
        "id": q.get("id", [""])[0] or f"{int(now * 1000)}-{snapshot.get('blueprint_id', 0)}",
        "created_at": now,
        "runs": runs,
        "blueprint_id": snapshot.get("blueprint_id"),
        "product_type_id": (snapshot.get("product") or {}).get("type_id"),
        "product_name": (snapshot.get("product") or {}).get("name", "?"),
        "snapshot": snapshot,
    }
    builds = _load_tracked_builds(acct)
    builds = [b for b in builds if b.get("id") != build["id"]]
    builds.insert(0, build)
    del builds[_MAX_TRACKED_BUILDS:]
    _save_tracked_builds(acct, builds)
    return {"ok": True, "build": build}


def do_ind_builds_delete(q):
    acct = current_account()
    if not acct:
        return {"error": "not available"}
    build_id = q.get("id", [""])[0]
    if not build_id:
        return {"error": "missing id"}
    builds = _load_tracked_builds(acct)
    kept = [b for b in builds if b.get("id") != build_id]
    if len(kept) != len(builds):
        _save_tracked_builds(acct, kept)
    return {"ok": True}


def do_ind_builds_link(q):
    """Patch the ESI-job linkage on an existing tracked build without resending
    its (large) frozen snapshot. The client derives status from live jobs and
    calls this only on a transition — first link to a job, or the job finishing —
    so the linkage / completion date survives reloads on any device."""
    acct = current_account()
    if not acct:
        return {"error": "not available"}
    build_id = q.get("id", [""])[0]
    if not build_id:
        return {"error": "missing id"}
    builds = _load_tracked_builds(acct)
    found = False
    for b in builds:
        if b.get("id") != build_id:
            continue
        found = True
        for f in ("job_id", "job_end", "char_name"):
            if f in q:
                v = q.get(f, [None])[0]
                b[f] = None if (v is None or v == "" or v == "null") else v
        if "done_at" in q:
            v = q.get("done_at", [None])[0]
            try:
                b["done_at"] = float(v) if v not in (None, "", "null") else None
            except (TypeError, ValueError):
                b["done_at"] = None
        break
    if found:
        _save_tracked_builds(acct, builds)
    return {"ok": found}


_CHAR_DATA_CACHE = {}  # {cid: (timestamp, result)}
_CHAR_DATA_TTL = 120   # seconds
_CHAR_DATA_SIG = {}    # {cid: signature of the last-seen character-owned state}


class _CharPubSub:
    """Change + schedule notifier backing the /api/char/stream SSE push.

    Two signals share one Condition:

    * A per-account monotonic version. Whenever a freshly-fetched character bundle
      differs from the last one we saw (wallet / LP / SP / skill queue / jobs /
      own orders), we bump that account's version so the account's browsers re-pull
      /api/char/data. Keyed by the Account object's identity so it works in both
      legacy and multi-user mode without depending on account_id.
    * A global sweep counter, bumped once per background-refresh sweep. Every
      stream wakes on it and re-publishes the (server-defined, shared) next-sync
      time, so all connected clients' countdowns stay in lockstep — the UI only
      ever displays the schedule the server hands it."""

    def __init__(self):
        self._cond = threading.Condition()
        self._versions = {}   # id(acct) -> int
        self._sweep = 0       # global background-sweep counter

    def version(self, key):
        with self._cond:
            return self._versions.get(key, 0)

    def state(self, key):
        """(account version, global sweep counter) — the pair a stream waits on."""
        with self._cond:
            return self._versions.get(key, 0), self._sweep

    def bump(self, key):
        with self._cond:
            self._versions[key] = self._versions.get(key, 0) + 1
            self._cond.notify_all()

    def announce_sweep(self):
        """Signal that a background sweep finished; wake every stream so they
        re-publish the next-sync countdown together."""
        with self._cond:
            self._sweep += 1
            self._cond.notify_all()

    def forget(self, key):
        with self._cond:
            self._versions.pop(key, None)

    def wait(self, key, last_version, last_sweep, timeout):
        """Block until this account's version or the global sweep counter differs
        from what the caller last saw, or the timeout elapses. Returns the current
        ``(version, sweep)`` either way."""
        deadline = time.time() + timeout
        with self._cond:
            while True:
                ver = self._versions.get(key, 0)
                sweep = self._sweep
                if ver != last_version or sweep != last_sweep:
                    return ver, sweep
                remaining = deadline - time.time()
                if remaining <= 0:
                    return ver, sweep
                self._cond.wait(remaining)


_CHAR_PUBSUB = _CharPubSub()


def _next_sync_in():
    """Seconds until the next scheduled background refresh — the single, server-
    defined sync cadence every client displays. Falls back to the interval before
    the loop has established a schedule (first 30s after boot)."""
    now = time.time()
    return (_BG_NEXT_SYNC_TS - now if _BG_NEXT_SYNC_TS > now else _BG_REFRESH_INTERVAL)


def _char_data_signature(result):
    """Stable signature of the character-*owned* state that should nudge the UI.

    Deliberately excludes market prices / order ranks so ordinary market movement
    (which changes on every fetch) doesn't spam pushes — only the capsuleer's own
    wallet, loyalty, SP, skill queue, industry jobs and open-order state count."""
    loyalty = sorted((l.get("corp_id"), l.get("loyalty_points"))
                     for l in (result.get("loyalty") or []))
    queue = [(q.get("skill_id"), q.get("finished_level"), q.get("finish_date"))
             for q in (result.get("skillqueue") or [])]
    jobs = sorted((j.get("job_id"), j.get("status"), j.get("end"))
                  for j in (result.get("jobs") or []))
    orders = sorted((o.get("order_id"), o.get("price"), o.get("volume_remain"))
                    for o in (result.get("market_orders") or []))
    return json.dumps([result.get("wallet"), result.get("total_sp"),
                       result.get("unallocated_sp"), loyalty, queue, jobs, orders],
                      sort_keys=True, default=str)


def _fetch_one_char_data(acct, cid):
    """Fetch all ESI data for a single character. Returns a dict bundle.
    Results are cached in-memory for _CHAR_DATA_TTL seconds. When the fetched
    state differs from what we last saw, the account's SSE version is bumped so
    open browsers get nudged to re-pull."""
    cached = _CHAR_DATA_CACHE.get(cid)
    if cached and (time.time() - cached[0]) < _CHAR_DATA_TTL:
        return cached[1]
    result = _fetch_one_char_data_uncached(acct, cid)
    _CHAR_DATA_CACHE[cid] = (time.time(), result)
    _record_wallet_snapshot(acct, cid, result.get("wallet"))
    sig = _char_data_signature(result)
    if _CHAR_DATA_SIG.get(cid) != sig:
        _CHAR_DATA_SIG[cid] = sig
        _CHAR_PUBSUB.bump(id(acct))
    return result


def _fetch_one_char_data_uncached(acct, cid):
    """Actually hit ESI for all character data."""
    token = _access_token(acct, cid)
    char_name = acct.characters[cid]["name"]
    _refresh_skill_profile(acct, cid)
    _refresh_char_blueprints(acct, cid)

    wallet = sso_core.fetch_wallet(token, cid, SESSION)
    skills = sso_core.fetch_skills(token, cid, SESSION)
    queue = sso_core.fetch_skillqueue(token, cid, SESSION)
    loyalty, loyalty_meta = sso_core.fetch_loyalty_points(token, cid, SESSION)
    jobs = sso_core.fetch_industry_jobs(token, cid, SESSION, include_completed=True)
    orders, orders_error, orders_meta = [], None, {}
    try:
        orders, orders_meta = sso_core.fetch_market_orders(token, cid, SESSION)
        orders.sort(key=lambda o: o.get("issued") or "", reverse=True)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        orders_error = (f"Couldn't load market orders ({status}). Make sure "
                        "'esi-markets.read_character_orders.v1' is enabled for your "
                        "EVE application at developers.eveonline.com, then log out and "
                        "back in.")

    name_ids = set()
    for j in jobs:
        name_ids.add(j.get("blueprint_type_id"))
        name_ids.add(j.get("product_type_id"))
    for qd in queue:
        name_ids.add(qd.get("skill_id"))
    for o in orders:
        name_ids.add(o.get("type_id"))
    name_ids.discard(None)
    names = resolve_names(list(name_ids), SESSION, CACHE_DIR) if name_ids else {}

    job_facility_ids = {j.get("facility_id") for j in jobs
                        if j.get("status") in ("active", "paused", "ready")}
    job_facility_ids.discard(None)
    station_names = resolve_station_names(list(job_facility_ids), SESSION, CACHE_DIR,
                                          token=token)

    runs_tracked = _track_delivered_jobs(acct, cid, jobs, names)

    activity_label = {1: "Manufacturing",
                      3: "TE Research",
                      4: "ME Research",
                      5: "Copying",
                      7: "Reverse Engineering",
                      8: "Invention",
                      9: "Reactions",
                      11: "Reactions"}
    out_jobs = []
    for j in jobs:
        if j.get("status") not in ("active", "paused", "ready"):
            continue
        act = j.get("activity_id")
        out_jobs.append({
            "job_id": j.get("job_id"),
            "activity": activity_label.get(act, f"Activity {act}"),
            "activity_id": act,
            "blueprint_type_id": j.get("blueprint_type_id"),
            "blueprint_name": names.get(j.get("blueprint_type_id"), "?"),
            "product_type_id": j.get("product_type_id"),
            "product_name": names.get(j.get("product_type_id"), "?"),
            "runs": j.get("runs"),
            "status": j.get("status"),
            "start": j.get("start_date"),
            "end": j.get("end_date"),
            "location": station_names.get(j.get("facility_id"), "Structure"),
            "character_name": char_name,
            "character_id": cid,
        })
    out_jobs.sort(key=lambda x: x.get("end") or "")

    loyalty_out = []
    for lp in loyalty:
        cid_lp = lp.get("corporation_id")
        loyalty_out.append({
            "corp_id": cid_lp,
            "corp_name": resolve_corp_name(cid_lp, SESSION) if cid_lp else "?",
            "loyalty_points": lp.get("loyalty_points", 0),
        })
    loyalty_out.sort(key=lambda x: -(x.get("loyalty_points") or 0))

    queue_out = []
    for qd in queue:
        queue_out.append({
            "skill_id": qd.get("skill_id"),
            "skill_name": names.get(qd.get("skill_id"), "?"),
            "finished_level": qd.get("finished_level"),
            "finish_date": qd.get("finish_date"),
            "character_name": char_name,
            "character_id": cid,
        })

    order_prices = (fetch_prices({o["type_id"] for o in orders if o.get("type_id")}, SESSION)
                    if orders else {})

    orders_out = []
    for o in orders:
        rank = None
        loc = o.get("location_id")
        region_id = (TRADE_HUBS.get(loc, {}).get("region_id")
                    or resolve_station_region(loc, SESSION, CACHE_DIR)) if loc else None
        if region_id:
            try:
                rank = fetch_order_rank(
                    o.get("type_id"), "buy" if o.get("is_buy_order") else "sell",
                    o.get("order_id"), SESSION, loc, region_id)
            except requests.RequestException:
                rank = None
        orders_out.append({
            "order_id": o.get("order_id"),
            "is_buy_order": bool(o.get("is_buy_order")),
            "type_id": o.get("type_id"),
            "type_name": names.get(o.get("type_id"), "?"),
            "price": o.get("price"),
            "market_sell": order_prices.get(o.get("type_id"), {}).get("sell_min"),
            "volume_remain": o.get("volume_remain"),
            "volume_total": o.get("volume_total"),
            "issued": o.get("issued"),
            "duration": o.get("duration"),
            "is_best": rank["is_best"] if rank else None,
            "queue_rank": rank["rank"] if rank else None,
            "queue_total": rank["total"] if rank else None,
            "character_name": char_name,
            "character_id": cid,
        })

    last_sales = {}
    if not orders_error:
        _, last_sales = _track_order_changes(acct, cid, orders_out, names)
    for o in orders_out:
        sale = last_sales.get(str(o.get("order_id")))
        if sale:
            o["last_sale_ts"] = sale["ts"]
            o["last_sale_qty"] = sale["sold"]

    with acct.lock:
        skill_profile = acct.skill_profiles.get(cid, {})
    accounting_lvl = skill_profile.get(16622, 0)
    broker_rel_lvl = skill_profile.get(3446, 0)

    return {
        "name": char_name,
        "character_id": cid,
        "wallet": wallet,
        "total_sp": skills.get("total_sp"),
        "unallocated_sp": skills.get("unallocated_sp"),
        "skillqueue": queue_out,
        "loyalty": loyalty_out,
        "loyalty_last_modified": loyalty_meta.get("last_modified"),
        "loyalty_expires": loyalty_meta.get("expires"),
        "jobs": out_jobs,
        "runs_tracked": runs_tracked,
        "market_orders": orders_out,
        "market_orders_error": orders_error,
        "market_orders_expires": orders_meta.get("expires"),
        "accounting_level": accounting_lvl,
        "broker_relations_level": broker_rel_lvl,
    }


def do_char_data(q):
    """Fetch data for all linked characters and return a combined bundle."""
    acct = require_account()
    if q.get("refresh"):
        with acct.lock:
            for cid in list(acct.characters.keys()):
                _CHAR_DATA_CACHE.pop(cid, None)
    with acct.lock:
        char_ids = list(acct.characters.keys())
        active_char_id = acct.active_char_id
    results = {}

    if len(char_ids) == 1:
        cid = char_ids[0]
        results[cid] = _fetch_one_char_data(acct, cid)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(char_ids)) as pool:
            futures = {pool.submit(_fetch_one_char_data, acct, cid): cid for cid in char_ids}
            for f in concurrent.futures.as_completed(futures):
                cid = futures[f]
                try:
                    results[cid] = f.result()
                except Exception:
                    pass

    combined_wallet = sum(r.get("wallet") or 0 for r in results.values())
    combined_jobs = []
    combined_orders = []
    combined_queue = []
    combined_runs = {"total_runs": 0, "total_jobs": 0, "by_product": {}}
    combined_orders_expires = None
    for r in results.values():
        combined_jobs.extend(r.get("jobs", []))
        combined_orders.extend(r.get("market_orders", []))
        combined_queue.extend(r.get("skillqueue", []))
        rt = r.get("runs_tracked") or {}
        combined_runs["total_runs"] += rt.get("total_runs", 0)
        combined_runs["total_jobs"] += rt.get("total_jobs", 0)
        exp = r.get("market_orders_expires")
        if exp and (combined_orders_expires is None or exp > combined_orders_expires):
            combined_orders_expires = exp
    combined_jobs.sort(key=lambda x: x.get("end") or "")
    combined_orders.sort(key=lambda o: o.get("issued") or "", reverse=True)

    active_data = results.get(active_char_id) or next(iter(results.values()), {})

    # Time until the next server-side background refresh — the authoritative sync
    # cadence, so every browser's countdown agrees and a page reload shows the
    # real remaining time instead of a fresh 5:00.
    next_sync_in = _next_sync_in()

    return {
        "characters": [results[cid] for cid in char_ids if cid in results],
        "combined_wallet": combined_wallet,
        "combined_jobs": combined_jobs,
        "combined_orders": combined_orders,
        "combined_queue": combined_queue,
        "combined_runs_tracked": combined_runs,
        "active_char_id": active_char_id,
        "name": active_data.get("name"),
        "character_id": active_data.get("character_id"),
        "wallet": active_data.get("wallet"),
        "total_sp": active_data.get("total_sp"),
        "unallocated_sp": active_data.get("unallocated_sp"),
        "skillqueue": active_data.get("skillqueue", []),
        "loyalty": active_data.get("loyalty", []),
        "loyalty_last_modified": active_data.get("loyalty_last_modified"),
        "loyalty_expires": active_data.get("loyalty_expires"),
        "jobs": combined_jobs,
        "runs_tracked": combined_runs,
        "market_orders": combined_orders,
        "market_orders_error": active_data.get("market_orders_error"),
        "market_orders_expires": combined_orders_expires,
        "accounting_level": active_data.get("accounting_level", 0),
        "broker_relations_level": active_data.get("broker_relations_level", 0),
        "order_events": _get_order_events(acct),
        "next_sync_in": next_sync_in,
    }


def _downsample(series, max_points=500):
    """Reduce a [(ts, balance), ...] list to at most max_points via averaging."""
    if len(series) <= max_points:
        return series
    bucket_size = len(series) / max_points
    result = []
    i = 0.0
    while int(i) < len(series):
        end = min(int(i + bucket_size), len(series))
        chunk = series[int(i):end]
        avg_ts = sum(p[0] for p in chunk) / len(chunk)
        avg_bal = sum(p[1] for p in chunk) / len(chunk)
        result.append([avg_ts, avg_bal])
        i += bucket_size
    return result


def do_wallet_history(q):
    """Return wallet balance time-series for all characters."""
    acct = require_account()
    days = min(int(q.get("days", ["30"])[0] or 30), 365)
    since_ts = time.time() - days * 86400

    with acct.lock:
        char_ids = list(acct.characters.keys())
        char_names = {cid: acct.characters[cid].get("name", "?")
                      for cid in char_ids}

    if pg_store.enabled():
        raw = pg_store.wallet_history_query(acct.account_id, since_ts)
    else:
        store = load_json(WALLET_HISTORY_PATH, {})
        raw = {}
        for cid in char_ids:
            pts = store.get(str(cid), [])
            filtered = [(ts, bal) for ts, bal in pts if ts >= since_ts]
            if filtered:
                raw[cid] = filtered

    series = {}
    for cid in char_ids:
        pts = raw.get(cid, [])
        if pts:
            series[str(cid)] = {
                "name": char_names.get(cid, "?"),
                "data": _downsample(pts),
            }

    return {"series": series}


def _all_type_ids(offers):
    ids = set()
    for o in offers:
        ids.add(o["type_id"])
        for req in o.get("required_items", []):
            ids.add(req["type_id"])
    return ids




def do_scan(q):
    corp_arg = (q.get("corp", [""])[0] or "").strip()
    corp_id_arg = q.get("corp_id", [""])[0].strip()
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    max_spread = q.get("max_spread", [""])[0].strip()
    max_spread = float(max_spread) if max_spread else None

    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID

    s = load_settings()
    s.update({
        "corp": corp_arg,
        "lp": str(int(lp)),
        "max_spread": str(max_spread) if max_spread is not None else "",
        "tax": str(tax),
        "broker": str(broker),
        "station": str(station_id),
    })
    save_settings(s)

    if corp_id_arg:
        corp_id = int(corp_id_arg)
        corp_name = resolve_corp_name(corp_id, SESSION)
    elif corp_arg:
        corp_id, corp_name = resolve_corp_id(corp_arg, SESSION)
    else:
        raise LPError("Enter a corporation name (or id).")

    force = q.get("refresh", ["0"])[0] in ("1", "true", "on")
    fresh = force or corp_id not in REFRESHED_CORPS
    if fresh:
        reason = "forced by user" if force else "first scan this session"
        print(f"[LP] Refreshing offers for {corp_name} ({reason})", file=sys.stderr)
    offers = get_offers(corp_id, SESSION, CACHE_DIR, refresh=fresh)
    REFRESHED_CORPS.add(corp_id)
    offers_meta = load_json(CACHE_DIR / f"lpstore_{corp_id}.json", {})
    prices = fetch_prices(_all_type_ids(offers), SESSION, station_id=station_id)
    sellable, unsellable = evaluate(offers, prices, lp, tax, broker)

    names = resolve_names(_all_type_ids(offers), SESSION, CACHE_DIR)
    volumes = resolve_volumes(
        {r["name_id"] for r in sellable} | {r["name_id"] for r in unsellable},
        SESSION, CACHE_DIR)
    rows = []
    for r in sellable:
        sp = r["spread_pct"]
        _vol = volumes.get(r["name_id"])
        rows.append({
            "offer_id": r["offer_id"],
            "name": names.get(r["name_id"], str(r["name_id"])),
            "qty": r["qty"],
            "lp_cost": r["lp_cost"],
            "cost_ea": r["isk_cost"] + r["req_cost"],
            "ask": r["ask"],
            "bid": r["bid"],
            "spread_pct": sp,
            "isk_per_lp_patient": r["isk_per_lp_patient"],
            "isk_per_lp_instant": r["isk_per_lp_instant"],
            "isk_per_lp_best": r["isk_per_lp_best"],
            "max_units": r["max_units"],
            "total_profit_patient": r["total_profit_patient"],
            "total_profit_instant": r["total_profit_instant"],
            "total_profit_best": r["total_profit_best"],
            "buy_volume": r["buy_volume"],
            "output_volume": None if _vol is None else _vol * r["qty"],
            "req_missing": r["req_missing"],
            "ak_cost": r["ak_cost"],
            "illiquid": sp is None or sp >= HIGH_SPREAD_PCT,
            "type_id": r["name_id"],
            "sell_volume": r.get("sell_volume"),
            "daily_vol": None,
            "days_to_clear": None,
            "tradeability": None,
            "list_price": None,
            "floor_age": None,
            "liq_loaded": False,
            "unsellable": False,
        })
    for r in unsellable:
        lp_cost = r["lp_cost"]
        max_units = math.floor(lp / lp_cost) if lp else 0
        _vol = volumes.get(r["name_id"])
        rows.append({
            "offer_id": r["offer_id"],
            "name": names.get(r["name_id"], str(r["name_id"])),
            "qty": r["qty"],
            "lp_cost": lp_cost,
            "cost_ea": None,
            "ask": None,
            "bid": None,
            "spread_pct": None,
            "isk_per_lp_patient": None,
            "isk_per_lp_instant": None,
            "isk_per_lp_best": None,
            "max_units": max_units,
            "total_profit_patient": None,
            "total_profit_instant": None,
            "total_profit_best": None,
            "buy_volume": None,
            "output_volume": None if _vol is None else _vol * r["qty"],
            "req_missing": False,
            "ak_cost": 0,
            "illiquid": True,
            "type_id": r["name_id"],
            "sell_volume": None,
            "daily_vol": None,
            "days_to_clear": None,
            "tradeability": None,
            "list_price": None,
            "floor_age": None,
            "liq_loaded": False,
            "unsellable": True,
        })
    return {
        "corp_id": corp_id,
        "corp_name": corp_name,
        "lp": lp,
        "tax": tax,
        "broker": broker,
        "station_id": station_id,
        "station_name": TRADE_HUBS[station_id]["name"],
        "high_spread_pct": HIGH_SPREAD_PCT,
        "count": len(rows),
        "unsellable": sum(1 for r in rows if r["unsellable"]),
        "rows": rows,
        "scanned_at": time.time(),
        "offers_fetched_at": offers_meta.get("fetched_at"),
    }


def do_liquidity(q):
    """Background fill for the market-saturation columns. Recomputes the same
    sellable rows as /api/scan (so capped figures use the identical LP budget /
    fees), fetches daily traded volume per reward type from region history, and
    returns {offer_id: {daily_vol, days_to_clear, capped_units, capped_profit}}.

    Split out from the scan because it costs one history call per type -- the
    front end fires it after the table is already on screen and patches rows in
    place as the answer arrives."""
    corp_id = int(q["corp_id"][0])
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]

    offers = get_offers(corp_id, SESSION, CACHE_DIR, refresh=False)
    prices = fetch_prices(_all_type_ids(offers), SESSION, station_id=station_id)
    sellable, _ = evaluate(offers, prices, lp, tax, broker)
    reward_ids = {r["name_id"] for r in sellable}
    daily_vols = fetch_history_volumes(reward_ids, region_id, SESSION, CACHE_DIR)
    # Fair-value anchor for the suggested list price -- reuses the same cached
    # history files the volume fetch just wrote, so no extra ESI round-trips.
    fair_prices = fetch_history_prices(reward_ids, region_id, SESSION, CACHE_DIR)
    liq = enrich_liquidity(sellable, daily_vols)
    # Freshness of the current cheapest sell order, deduped per reward type
    # (one live order-book call each -- order books aren't cacheable, so this
    # is the slow part of the fill).
    floor_age_by_type = {}
    for r in sellable:
        tid = r["name_id"]
        if tid not in floor_age_by_type:
            stats = fetch_sell_order_stats(tid, SESSION, station_id=station_id,
                                           region_id=region_id)
            floor_age_by_type[tid] = stats["age_seconds"] if stats else None
        liq[r["offer_id"]]["list_price"] = suggested_list_price(
            r.get("ask"), fair_prices.get(tid))
        liq[r["offer_id"]]["floor_age"] = floor_age_by_type[tid]
    return {"liquidity": liq}


def _resolve_corp_names(ids):
    """POST ids to /universe/names/ → list of corporation entries.

    ESI returns 404 for the *entire* batch if even one id is unresolvable
    (some ids from /npccorps/ are stale). Binary-split on failure so a single
    bad id only drops itself instead of poisoning the whole batch.
    """
    if not ids:
        return []
    nr = SESSION.post(f"{ESI}/universe/names/", json=ids, headers=HEADERS, timeout=30)
    if nr.status_code == 200:
        body = nr.json()
        if isinstance(body, list):
            return [{"id": e["id"], "name": e["name"]}
                    for e in body
                    if isinstance(e, dict) and e.get("category") == "corporation"]
        return []
    if len(ids) == 1:
        print(f"[corps] dropping unresolvable id {ids[0]} "
              f"({nr.status_code})", file=sys.stderr)
        return []
    mid = len(ids) // 2
    return _resolve_corp_names(ids[:mid]) + _resolve_corp_names(ids[mid:])


def _load_npc_corps():
    path = CACHE_DIR / "npc_corps.json"
    cached = load_json(path, None)
    if cached:
        return cached
    print("[corps] fetching NPC corporation list from ESI…", file=sys.stderr)
    r = SESSION.get(f"{ESI}/corporations/npccorps/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    ids = r.json()
    corps = []
    for i in range(0, len(ids), 1000):
        corps.extend(_resolve_corp_names(ids[i:i + 1000]))
    corps.sort(key=lambda c: c["name"])
    print(f"[corps] resolved {len(corps)} of {len(ids)} NPC corporations",
          file=sys.stderr)
    save_json(path, corps)
    return corps


NPC_CORPS = []


def get_npc_corps():
    global NPC_CORPS
    if not NPC_CORPS:
        try:
            NPC_CORPS = _load_npc_corps()
        except Exception as e:  # noqa: BLE001
            print(f"[corps] failed to load NPC corporations: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return []
    return NPC_CORPS


def load_account_settings(acct):
    """The full per-account settings blob (searches/filters/columns/…), or None
    if this account has never synced yet. Server-authoritative and identical
    across every browser/device the user logs in from."""
    if acct is None or not acct.characters:
        return None
    if pg_store.enabled():
        return pg_store.account_settings_get(acct.account_id)
    # Legacy single-user mode keys the blob by the active character.
    return load_user_settings(acct.active_char_id) if acct.active_char_id else None


def save_account_settings(acct, data):
    if acct is None or not acct.characters:
        return
    if pg_store.enabled():
        pg_store.account_settings_set(acct.account_id, data, time.time())
    elif acct.active_char_id:
        save_user_settings(acct.active_char_id, data)


def _profiles_list(blob):
    """The build-location profiles inside a settings blob, as a list (best
    effort). The client stores them under ind.profiles as a JSON string, but be
    lenient about a raw list or missing/garbage values."""
    try:
        raw = ((blob or {}).get("ind") or {}).get("profiles")
    except AttributeError:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _preserve_profiles(incoming, stored):
    """Defend the user's saved build locations against being silently wiped by a
    settings sync. Build-location profiles live only inside the wholesale
    settings blob, which the client snapshots from its live state and pushes to
    the account row. A boot/cold-start race (e.g. the /api/settings fetch failing
    right after a deploy) can leave the client holding its empty default
    (IND.profiles = []) and push that over a durable copy — which is how saved
    build locations kept vanishing after an update.

    So: if the incoming blob has no profiles but the stored one does, keep the
    stored profiles — UNLESS the client explicitly signalled that the user
    cleared them (ind.profiles_cleared == "1", set only by the wizard's delete
    button), in which case an empty list is intentional and honoured."""
    if _profiles_list(incoming):
        return incoming  # client sent real profiles — trust it
    cleared = str(((incoming or {}).get("ind") or {}).get("profiles_cleared", "")) == "1"
    if cleared:
        return incoming  # user genuinely emptied the list
    stored_profiles = _profiles_list(stored)
    if not stored_profiles:
        return incoming  # nothing to protect
    ind = incoming.get("ind")
    if not isinstance(ind, dict):
        ind = {}
        incoming["ind"] = ind
    ind["profiles"] = json.dumps(stored_profiles)
    return incoming


def do_settings_sync(q):
    """Persist the full client-side settings blob for the account, remotely, so
    every browser the user logs in from converges on the same view. No-op when
    unauthenticated."""
    acct = current_account()
    if acct is None or not acct.characters:
        return {"ok": True, "synced": False}
    blob = q.get("blob", ["{}"])[0]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        raise LPError("Invalid settings payload.")
    # Never let a sync blank out saved build locations by accident (see helper).
    data = _preserve_profiles(data, load_account_settings(acct))
    save_account_settings(acct, data)
    return {"ok": True, "synced": True}


def do_prefs(q):
    s = load_settings()
    for k in ("sort_key", "sort_dir", "col_widths", "col_order", "col_layout_v",
              "hide_illiquid", "hide_unaffordable", "active_tab", "trade_weight"):
        if k in q:
            s[k] = q[k][0]
    save_settings(s)
    return {"ok": True}


def do_detail(q):
    corp_id = int(q["corp_id"][0])
    offer_id = int(q["offer_id"][0])
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]

    offers = get_offers(corp_id, SESSION, CACHE_DIR)
    offer = next((o for o in offers if o.get("offer_id") == offer_id), None)
    if offer is None:
        raise LPError(f"Offer {offer_id} not found for corp {corp_id}.")

    tids = {offer["type_id"]} | {r["type_id"] for r in offer.get("required_items", [])}
    prices = fetch_prices(tids, SESSION, station_id=station_id)
    names = resolve_names(tids, SESSION, CACHE_DIR)
    volumes = resolve_volumes(tids, SESSION, CACHE_DIR)
    detail = build_detail(offer, prices, names, volumes, lp, tax, broker)
    detail["high_spread_pct"] = HIGH_SPREAD_PCT

    # Market saturation for the reward item (one cached history call).
    out_tid = offer["type_id"]
    daily_vol = fetch_history_volumes({out_tid}, region_id, SESSION, CACHE_DIR).get(out_tid)
    detail["daily_vol"] = daily_vol
    detail["days_to_clear"] = (
        detail["sell_volume"] / daily_vol if daily_vol and daily_vol > 0 else None)
    # Suggested per-unit sell-order price, anchored to the 30-day fair value
    # (shares the cached history just fetched above -- no extra ESI call).
    fair = fetch_history_prices({out_tid}, region_id, SESSION, CACHE_DIR).get(out_tid)
    detail["fair_price"] = fair
    detail["suggested_list"] = suggested_list_price(detail["ask"], fair)
    # Freshness of the current cheapest sell order (one live order-book call).
    detail["sell_order_stats"] = fetch_sell_order_stats(
        out_tid, SESSION, station_id=station_id, region_id=region_id)

    for it in detail["required_items"]:
        it["book"] = fetch_orderbook_jita(it["type_id"], "sell", SESSION,
                                          station_id=station_id, region_id=region_id)
    # Always fetch the output buy-order book so the instant-sell column can walk
    # it (the patient column values the reward at the lowest sell order / ask).
    detail["output"]["buy_book"] = fetch_orderbook_jita(
        detail["output"]["type_id"], "buy", SESSION,
        station_id=station_id, region_id=region_id)
    return detail


def do_history(q):
    type_id = int(q["type_id"][0])
    region_id = int(q.get("region_id", ["10000002"])[0])
    cache_path = CACHE_DIR / f"mhist_{region_id}_{type_id}.json"
    cached = load_json(cache_path, None)
    if cached and time.time() - cached.get("_ts", 0) < 43200:  # 12-hour cache
        return {"history": cached["data"]}
    r = SESSION.get(
        f"{ESI}/markets/{region_id}/history/",
        params={"type_id": type_id},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = sorted(r.json(), key=lambda x: x["date"])
    save_json(cache_path, {"_ts": time.time(), "data": data})
    return {"history": data}


# ── Arbitrage scanner ───────────────────────────────────────────────────────

def do_arb_prefs(q):
    s = load_arb_settings()
    for k in ("region", "sales_tax", "cross_station", "min_isk", "max_jumps",
              "avoid_lowsec", "route_flag"):
        if k in q:
            s[k] = q[k][0]
    save_arb_settings(s)
    return {"ok": True}


def do_arb_scan(q, emit=None):
    """Run the arb scan, optionally streaming SSE progress via emit(dict)."""
    def _emit(d):
        if emit:
            emit(d)

    region = int(q.get("region", ["10000002"])[0])
    sales_tax = float(q.get("sales_tax", ["0.075"])[0])
    cross_station = q.get("cross_station", ["1"])[0] in ("1", "true", "on")
    min_isk = float(q.get("min_isk", ["0"])[0] or 0)
    max_jumps = int(q.get("max_jumps", ["6"])[0])
    avoid_lowsec = q.get("avoid_lowsec", ["0"])[0] in ("1", "true", "on")
    route_flag = q.get("route_flag", ["shortest"])[0]

    s = load_arb_settings()
    s.update({
        "region": str(region),
        "sales_tax": str(sales_tax),
        "cross_station": "1" if cross_station else "0",
        "min_isk": str(min_isk) if min_isk else "",
        "max_jumps": str(max_jumps),
        "avoid_lowsec": "1" if avoid_lowsec else "0",
        "route_flag": route_flag,
    })
    save_arb_settings(s)

    _ensure_arb_caches()

    # Phase 1 — type list
    def types_progress(stage, **kw):
        if stage == "cache":
            _emit({"type": "progress", "pct": 8,
                   "msg": f"Type list cached ({kw['count']:,} types)", "sub": ""})
        elif stage == "page":
            pages = kw.get("pages", 1)
            pct = max(2, min(8, round(2 + kw["page"] / pages * 6)))
            _emit({"type": "progress", "pct": pct,
                   "msg": f"Fetching type list — page {kw['page']} of {pages}",
                   "sub": f"{kw['count']:,} types found"})

    all_types = arb_core.fetch_region_types(region, SESSION, CACHE_DIR,
                                            progress_cb=types_progress)

    # Phase 2 — Fuzzwork region aggregates → candidate types
    def fuzzwork_progress(stage, **kw):
        pct = 8 + round(kw["chunk"] / kw["total"] * 52)
        _emit({"type": "progress", "pct": pct,
               "msg": f"Price aggregates — batch {kw['chunk']} of {kw['total']}",
               "sub": f"{kw['types_done']:,} of {len(all_types):,} types priced"})

    _emit({"type": "progress", "pct": 8,
           "msg": f"Querying price aggregates for {len(all_types):,} types…", "sub": ""})
    prices = arb_core.fetch_fuzzwork_region(all_types, region, SESSION,
                                            progress_cb=fuzzwork_progress)
    candidates = arb_core.arb_candidates(prices, sales_tax)

    # Phase 3 — per-candidate orders from ESI
    _emit({"type": "progress", "pct": 60,
           "msg": f"Found {len(candidates)} candidate types — fetching orders…", "sub": ""})
    all_orders = []
    for i, type_id in enumerate(candidates):
        all_orders.extend(arb_core.fetch_type_orders(region, type_id, SESSION))
        if i % 10 == 0 or i == len(candidates) - 1:
            pct = 60 + round((i + 1) / max(len(candidates), 1) * 25)
            _emit({"type": "progress", "pct": pct,
                   "msg": f"Fetching orders — {i + 1} of {len(candidates)} types",
                   "sub": f"{len(all_orders):,} orders collected"})

    _emit({"type": "progress", "pct": 85,
           "msg": f"Analyzing {len(all_orders):,} orders…", "sub": "Finding profitable spreads"})

    results = [r for r in arb_core.find_spreads(all_orders, sales_tax, not cross_station)
               if r["isk_opportunity"] >= min_isk]

    if cross_station:
        # Enrich all results (capped) then filter to Jita-leg deals within max_jumps.
        # round_trip=True so jumps counts the haul both ways.
        _emit({"type": "progress", "pct": 87,
               "msg": f"Found {len(results):,} cross-station spreads — resolving stations…",
               "sub": f"Filtering to Jita legs ≤{max_jumps} jumps round-trip"})
        enriched = arb_core.enrich_locations(
            results[:500], round_trip=True, route_flag=route_flag,
            session=SESSION, station_cache=_ARB_STATION_CACHE, route_cache=_ARB_ROUTE_CACHE,
        )
        from_jita = arb_core.filter_from_jita(enriched, max_jumps)
        _emit({"type": "progress", "pct": 92,
               "msg": f"{len(from_jita)} deals within {max_jumps} jumps of Jita — checking security…",
               "sub": ""})
        shown = []
        for r in from_jita:
            arb_core.enrich_security([r], SESSION, _ARB_SYSTEM_CACHE)
            if avoid_lowsec and arb_core.sec_band(arb_core.row_risk_sec(r)) != "high":
                continue
            shown.append(r)
        shown.sort(key=lambda r: r["isk_opportunity"], reverse=True)
    else:
        # Same-station: just take the top 40 by ISK opportunity
        _emit({"type": "progress", "pct": 87,
               "msg": f"Found {len(results):,} same-station spreads — resolving stations…",
               "sub": "Looking up station names and security status"})
        shown = arb_core.build_shown(
            results, 40, False, avoid_lowsec, False, route_flag,
            SESSION, _ARB_STATION_CACHE, _ARB_ROUTE_CACHE, _ARB_SYSTEM_CACHE,
        )

    _emit({"type": "progress", "pct": 90,
           "msg": "Resolving item names & cargo volumes…", "sub": ""})

    if shown:
        names = arb_core.resolve_names({r["type_id"] for r in shown}, SESSION)
    else:
        names = {}

    for r in shown:
        vol = arb_core.resolve_volume(r["type_id"], _ARB_VOLUME_CACHE, SESSION)
        r["total_volume"] = vol * r["flippable_qty"] if vol is not None else None

    arb_core.save_lookup_cache(
        CACHE_DIR, _ARB_STATION_CACHE, _ARB_VOLUME_CACHE,
        _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE,
    )

    _emit({"type": "progress", "pct": 97, "msg": "Formatting results…", "sub": ""})

    rows = []
    for r in shown:
        risk_sec = arb_core.row_risk_sec(r)
        risk_band = arb_core.sec_band(risk_sec)
        from_sec_raw = r.get("from_sec")
        to_sec_raw = r.get("to_sec")
        rows.append({
            "type_id": r["type_id"],
            "name": names.get(r["type_id"], str(r["type_id"])),
            "sell_price": r["sell_price"],
            "buy_price": r["buy_price"],
            "net_per_unit": r["net_per_unit"],
            "margin_pct": r["margin_pct"],
            "flippable_qty": r["flippable_qty"],
            "isk_opportunity": r["isk_opportunity"],
            "total_volume": r["total_volume"],
            "sell_station": r.get("sell_station_name", str(r["sell_location"])),
            "buy_station": r.get("buy_station_name", str(r["buy_location"])),
            "from_sec": arb_core.round_sec(from_sec_raw),
            "from_sec_band": arb_core.sec_band(from_sec_raw),
            "to_sec": arb_core.round_sec(to_sec_raw),
            "to_sec_band": arb_core.sec_band(to_sec_raw),
            "jumps": r.get("jumps_total", 0),
            "risk": arb_core._RISK_LABEL[risk_band],
            "risk_band": risk_band,
        })

    return {
        "region": region,
        "region_name": REGION_NAMES.get(region, f"Region {region}"),
        "cross_station": cross_station,
        "max_jumps": max_jumps,
        "sales_tax": sales_tax,
        "count": len(rows),
        "total_spreads": len(results),
        "total_orders": len(all_orders),
        "snap_expires": None,
        "snap_fetched_at": time.time(),
        "scanned_at": time.time(),
        "rows": rows,
    }


# ── Industry planner ────────────────────────────────────────────────────────

# After ranking by ISK/hour, only the top rows get a (cached, one-call-per-type)
# market-history lookup for the "days to sell" column — bounds the work on a
# broad scan while still covering everything worth looking at.
IND_HISTORY_TOP_N = 80

_IND_PREF_KEYS = ("profiles", "profile", "market_group", "job_rate",
                  "sales_tax", "broker", "station",
                  "buildable_only", "include_unbuildable", "hide_t2",
                  "sort_key", "sort_dir", "min_tradeability", "favorites",
                  "hidden_bps", "col_order", "col_widths", "col_vis")


def do_ind_prefs(q):
    s = load_ind_settings()
    for k in _IND_PREF_KEYS:
        if k in q:
            s[k] = q[k][0]
    save_ind_settings(s)
    return {"ok": True}


def do_ind_groups(q):
    """Top-level market groups for the category dropdown (builds the SDE first
    if needed)."""
    ind_core.load_sde_industry(CACHE_DIR, SESSION)
    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        return {"groups": ind_core.top_market_groups(conn)}
    finally:
        conn.close()


def _ind_params(q):
    """Parse the shared scan/detail knobs. Percentages (job rate, taxes) come
    from the UI as whole numbers and are converted to fractions here.

    me/te/skills_level are NOT user-settable: they're the real (0 = unresearched
    / untrained) baseline, overridden per-blueprint in do_ind_scan/do_ind_detail
    with the logged-in character's actual owned-blueprint ME/TE and trained
    skill levels wherever ESI has that data."""
    return {
        "me": 0,
        "te": 0,
        "job_rate": float(q.get("job_rate", ["6"])[0] or 0) / 100.0,
        "sales_tax": float(q.get("sales_tax", ["4.5"])[0] or 0) / 100.0,
        "broker_fee": float(q.get("broker", ["1.5"])[0] or 0) / 100.0,
        "runs": max(1, int(float(q.get("runs", ["1"])[0] or 1))),
        "skills_level": 0,
    }


def _patch_group_names(rows):
    """Backfill group_name (and market_group_id) for cached rows that predate
    the feature. Older caches lack market_group_id entirely, so we resolve it
    from the product_id via the SDE types table first."""
    missing = [r for r in rows if not r.get("group_name")]
    if not missing:
        return
    try:
        conn = ind_core.connect_sde(CACHE_DIR)
    except Exception:
        return
    try:
        # Backfill market_group_id from SDE for rows that lack it
        need_mgid = [r for r in missing if not r.get("market_group_id")]
        if need_mgid:
            pids = list({r["product_id"] for r in need_mgid if r.get("product_id")})
            if pids:
                marks = ",".join("?" for _ in pids)
                mgid_map = {row[0]: row[1] for row in conn.execute(
                    f"SELECT type_id, market_group_id FROM types WHERE type_id IN ({marks})",
                    pids)}
                for r in need_mgid:
                    mgid = mgid_map.get(r.get("product_id"))
                    if mgid:
                        r["market_group_id"] = mgid
        # Now resolve group names
        gids = {r["market_group_id"] for r in missing if r.get("market_group_id")}
        if gids:
            gnames = ind_core.market_group_names(conn, gids)
            for r in missing:
                r["group_name"] = gnames.get(r.get("market_group_id"), "")
    finally:
        conn.close()


def do_ind_scan(q, emit=None):
    """Rank manufacturable items by profitability. Streams SSE progress."""
    acct = require_account()

    def _emit(d):
        if emit:
            emit(d)

    market_group = q.get("market_group", ["all"])[0]
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]
    refresh_sde = q.get("refresh_sde", ["0"])[0] in ("1", "true", "on")
    buildable_only = q.get("buildable_only", ["0"])[0] in ("1", "true", "on")
    include_unbuildable = q.get("include_unbuildable", ["0"])[0] in ("1", "true", "on")
    hide_t2 = q.get("hide_t2", ["0"])[0] in ("1", "true", "on")
    # A lightweight scan that evaluates ONLY the favorited blueprints, regardless
    # of category — used to show favorites immediately on tab load, before the
    # user runs a real scan. Doesn't touch saved settings.
    favorites_only = q.get("favorites_only", ["0"])[0] in ("1", "true", "on")
    # Like favorites_only but also includes all ESI-owned blueprints — used to
    # show "My Blueprints" + watchlist immediately on tab open.
    owned_only = q.get("owned_only", ["0"])[0] in ("1", "true", "on")
    try:
        fav_ids = set(int(b) for b in json.loads(q.get("favorites", ["[]"])[0]))
    except (ValueError, TypeError):
        fav_ids = set()
    params = _ind_params(q)
    # Refresh owned-blueprint ownership across ALL linked characters so transfers
    # between alts show up here without a re-login. Full (user-initiated) scans
    # always refresh; the tab's auto-fired preview scans share a short throttle so
    # they don't triple-hit ESI on tab open.
    _emit({"type": "progress", "pct": 2, "msg": "Refreshing blueprint ownership…", "sub": ""})
    _refresh_all_blueprints(acct, force=not (favorites_only or owned_only))
    with acct.lock:
        ind_cid = acct.active_char_id
        ind_skill_profile = dict(acct.skill_profiles.get(ind_cid, {})) if ind_cid else {}
        ind_bp_me_te = dict(acct.bp_me_tes.get(ind_cid, {})) if ind_cid else {}
    if ind_skill_profile:
        params["skill_profile"] = ind_skill_profile
    if ind_bp_me_te:
        params["owned_me_te"] = ind_bp_me_te

    if not favorites_only and not owned_only:
        s = load_ind_settings()
        for k in _IND_PREF_KEYS:
            if k in q:
                s[k] = q[k][0]
        save_ind_settings(s)

    _emit({"type": "progress", "pct": 4, "msg": "Loading blueprint database…", "sub": ""})
    ind_core.load_sde_industry(
        CACHE_DIR, SESSION, refresh=refresh_sde,
        emit=lambda m: _emit({"type": "progress", "pct": 6, "msg": m, "sub": ""}))
    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        if favorites_only:
            candidates = ind_core.candidates_for_blueprints(conn, fav_ids)
        elif owned_only:
            bp_ids = set(ind_bp_me_te.keys()) | fav_ids
            candidates = ind_core.candidates_for_blueprints(conn, bp_ids)
        else:
            if market_group and market_group != "all":
                group_ids = ind_core.expand_market_groups(conn, [int(market_group)])
                candidates = ind_core.manufacturing_candidates(conn, group_ids)
            else:
                candidates = ind_core.manufacturing_candidates(conn)
            # Favorited blueprints are always included, even outside the chosen
            # category, so they're "always visible regardless".
            present_bp = {c["blueprint_id"] for c in candidates}
            extra_fav = [b for b in fav_ids if b not in present_bp]
            if extra_fav:
                candidates += ind_core.candidates_for_blueprints(conn, extra_fav)
        _emit({"type": "progress", "pct": 18,
               "msg": f"{len(candidates):,} manufacturable items — loading recipes…", "sub": ""})
        bps = ind_core.assemble_blueprints(conn, candidates)
        ind_core.assemble_invention(conn, bps)

        type_ids = set()
        for bp in bps:
            type_ids.add(bp["product_id"])
            type_ids.add(bp["blueprint_id"])
            type_ids.update(mid for mid, _ in bp["materials"])
            if bp.get("invention"):
                type_ids.update(dc for dc, _ in bp["invention"]["datacores"])

        _emit({"type": "progress", "pct": 30,
               "msg": f"Pricing {len(type_ids):,} item types at "
                      f"{TRADE_HUBS[station_id]['name']}…", "sub": ""})
        prices = fetch_prices(type_ids, SESSION, station_id)
        adjusted = ind_core.fetch_adjusted_prices(SESSION, CACHE_DIR)
        volumes = ind_core.volumes_for(conn, type_ids)

        # Blueprint (BPO) prices for T1 items come from the whole REGION, not the
        # single source station — NPC-seeded BPOs and Jita relists rarely sit at
        # the hub we price materials at. T2 blueprints (BPCs) aren't sold; their
        # cost is invention, handled separately. A BPO with no region sell order
        # is treated as unobtainable (you can neither buy nor own it here).
        t1_bp_ids = {bp["blueprint_id"] for bp in bps if not bp.get("invention")}
        _emit({"type": "progress", "pct": 62,
               "msg": f"Pricing {len(t1_bp_ids):,} blueprints region-wide…", "sub": ""})
        bpo_region = (arb_core.fetch_fuzzwork_region(t1_bp_ids, region_id, SESSION)
                      if t1_bp_ids else {})
        bpo_prices = {bid: v["sell_min"] for bid, v in bpo_region.items()
                      if v.get("sell_min")}

        _emit({"type": "progress", "pct": 78, "msg": "Computing profitability…", "sub": ""})
        params.update({"bpo_prices": bpo_prices, "volumes": volumes})
        rows = ind_core.evaluate_industry(bps, prices, adjusted, params)
        # Training time for unbuildable items (direct skills only, fast).
        train_map = ind_core.bulk_training_time(
            bps, params.get("skill_profile"), conn,
            params.get("skills_level", 0))
        for r in rows:
            r["train_hours"] = train_map.get(r["blueprint_id"])
        # Resolve market group names for display in the table.
        gids = {r["market_group_id"] for r in rows if r.get("market_group_id")}
        gnames = ind_core.market_group_names(conn, gids) if gids else {}
        for r in rows:
            r["group_name"] = gnames.get(r.get("market_group_id"), "")
        # Flag favourites; favourites are exempt from every filter so
        # they're always visible regardless of the current settings.
        for r in rows:
            r["favorite"] = r["blueprint_id"] in fav_ids
        if buildable_only:
            rows = [r for r in rows if r["buildable"] or r["favorite"]]
        if not include_unbuildable:
            rows = [r for r in rows if r["bp_available"] or r["owned_bp_me_te"] or r["favorite"]]
        if hide_t2:
            rows = [r for r in rows
                    if r["favorite"] or not (r["requires_invention"] or r["tech_level"] == 2)]
    finally:
        conn.close()

    # Market depth for the top-ranked rows plus every favourite and owned BP (one
    # cached call per product type), so pinned sections always carry a score.
    scored = rows[:IND_HISTORY_TOP_N] + [r for r in rows[IND_HISTORY_TOP_N:]
                                         if r["favorite"] or r["owned_bp_me_te"]]
    if scored:
        _emit({"type": "progress", "pct": 88,
               "msg": f"Checking market depth for {len(scored)} items…", "sub": ""})
        product_ids = {r["product_id"] for r in scored}
        daily = fetch_history_volumes(product_ids, region_id, SESSION, CACHE_DIR)
        for r in scored:
            dv = daily.get(r["product_id"])
            r["daily_vol"] = dv
            r["days_to_sell"] = ((r["out_qty"] * r["runs"]) / dv) if dv else None
            r["tradeability"] = ind_core.tradeability(dv)
            r["liq_loaded"] = True   # the rest get scored by the background fill

    _emit({"type": "progress", "pct": 97, "msg": "Formatting results…", "sub": ""})
    # Cross-character blueprint ownership annotations (this account's chars only)
    other_owners_map = {}
    with acct.lock:
        bp_snapshot = [(ocid, acct.characters.get(ocid, {}).get("name", "?"),
                        dict(bp_map)) for ocid, bp_map in acct.bp_me_tes.items()]
    for other_cid, other_name, bp_map in bp_snapshot:
        if other_cid == ind_cid:
            continue
        for tid, entry in bp_map.items():
            other_owners_map.setdefault(tid, []).append({
                "character_id": other_cid,
                "name": other_name,
                "me": entry[0], "te": entry[1],
                "is_bpo": entry[2],
            })
    for r in rows:
        r["other_owners"] = other_owners_map.get(r["blueprint_id"], [])
    return {
        "station_id": station_id,
        "station_name": TRADE_HUBS[station_id]["name"],
        "market_group": market_group,
        "runs": params["runs"],
        "count": len(rows),
        "scanned_at": time.time(),
        "favorites_only": favorites_only,
        "owned_only": owned_only,
        "rows": rows,
    }


def do_ind_liquidity(q):
    """Background tradeability fill for the Industry table. The scan scores only
    the top-ranked rows inline (to stay fast); the front end then walks the rest
    of the catalogue here in chunks, so every item eventually gets a real
    tradeability without blocking the initial result. One cached ESI market-
    history call per uncached product type.

    station + comma-separated product type_ids in -> {type_id: {daily_vol,
    tradeability}} out. daily_vol/tradeability are None for a product the market
    has never traded; the caller derives days-to-sell from its own batch size."""
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]
    raw = q.get("type_ids", [""])[0]
    type_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    daily = fetch_history_volumes(set(type_ids), region_id, SESSION, CACHE_DIR)
    out = {}
    for tid in type_ids:
        dv = daily.get(tid)
        out[str(tid)] = {"daily_vol": dv, "tradeability": ind_core.tradeability(dv)}
    return {"liquidity": out}


def do_ind_detail(q):
    """Full breakdown for one blueprint, with accurate (ESI packaged) cargo
    volumes resolved lazily for just this item's inputs and output."""
    acct = require_account()
    blueprint_id = int(q["blueprint_id"][0])
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    params = _ind_params(q)
    with acct.lock:
        ind_cid = acct.active_char_id
        ind_skill_profile = dict(acct.skill_profiles.get(ind_cid, {})) if ind_cid else {}
        ind_bp_me_te = dict(acct.bp_me_tes.get(ind_cid, {})) if ind_cid else {}
    if ind_skill_profile:
        params["skill_profile"] = ind_skill_profile
    owned_me_te = ind_bp_me_te.get(blueprint_id)
    if owned_me_te:
        params["me"], params["te"] = owned_me_te[0], owned_me_te[1]

    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        row = conn.execute(
            "SELECT p.blueprint_id, p.product_id, p.quantity AS out_qty, "
            "t.type_name, t.market_group_id, t.tech_level, t.volume AS out_volume "
            "FROM products p JOIN types t ON t.type_id = p.product_id "
            "WHERE p.blueprint_id = ? AND p.activity_id = ?",
            (blueprint_id, ind_core.ACT_MANUFACTURING)).fetchone()
        if not row:
            raise LPError(f"No manufacturing blueprint {blueprint_id}.")
        bp = ind_core.assemble_blueprints(conn, [dict(row)])[0]
        ind_core.assemble_invention(conn, [bp])

        # Missing skills (needs conn for skill names and training ranks)
        skill_profile = params.get("skill_profile") or {}
        default_level = params.get("skills_level", 0)
        skills_missing = ind_core.missing_skills(
            bp, skill_profile, conn, default_level)
    finally:
        conn.close()

    type_ids = {bp["product_id"], bp["blueprint_id"]}
    type_ids.update(mid for mid, _ in bp["materials"])
    if bp.get("invention"):
        type_ids.update(dc for dc, _ in bp["invention"]["datacores"])
    refresh_prices = q.get("refresh_prices", ["0"])[0] in ("1", "true", "on")
    if refresh_prices:
        region_id = TRADE_HUBS[station_id]["region_id"]
        prices = fetch_prices_esi(type_ids, SESSION, station_id=station_id,
                                  region_id=region_id, cache_dir=CACHE_DIR,
                                  refresh=True)
    else:
        prices = fetch_prices(type_ids, SESSION, station_id)
    params["adjusted"] = ind_core.fetch_adjusted_prices(SESSION, CACHE_DIR)
    # BPO price + where it's sold, region-wide (The Forge). T1 only; T2 is invented.
    params["bpo_prices"] = {}
    bp_market = None
    if not bp.get("invention"):
        region_id = TRADE_HUBS[station_id]["region_id"]
        orders = arb_core.fetch_type_orders(region_id, bp["blueprint_id"], SESSION)
        loc = ind_core.cheapest_sell_location(orders)
        if loc:
            params["bpo_prices"][bp["blueprint_id"]] = loc["price"]
            loc_name = resolve_names([loc["location_id"]], SESSION, CACHE_DIR).get(
                loc["location_id"], f"location {loc['location_id']}")
            bp_market = {"price": loc["price"], "station": loc_name,
                         "orders": loc["orders"],
                         "region": REGION_NAMES.get(region_id, f"region {region_id}")}
    volumes = resolve_volumes(type_ids, SESSION, CACHE_DIR)
    names = resolve_names(type_ids, SESSION, CACHE_DIR)
    detail = ind_core.build_industry_detail(bp, prices, names, volumes, params)
    detail["product"]["tech_level"] = bp.get("tech_level")
    detail["station_name"] = TRADE_HUBS[station_id]["name"]
    detail["region_name"] = REGION_NAMES.get(region_id, f"region {region_id}")
    detail["bp_market"] = bp_market
    detail["missing_skills"] = skills_missing
    detail["owned_me_te"] = ({"me": owned_me_te[0], "te": owned_me_te[1],
                              "is_bpo": owned_me_te[2] if len(owned_me_te) > 2 else True,
                              "max_runs": owned_me_te[3] if len(owned_me_te) > 3 else -1}
                             if owned_me_te else None)
    # Cross-character ownership: show if any other linked char owns this BPO/BPC
    other_owners = []
    with acct.lock:
        for ocid, bp_map in acct.bp_me_tes.items():
            if ocid == ind_cid:
                continue
            entry = bp_map.get(blueprint_id)
            if entry:
                other_owners.append({
                    "name": acct.characters.get(ocid, {}).get("name", "?"),
                    "me": entry[0], "te": entry[1],
                    "is_bpo": entry[2] if len(entry) > 2 else True,
                    "max_runs": entry[3] if len(entry) > 3 else -1,
                })
    detail["other_owners"] = other_owners
    # Tradeability for this product (daily units traded, ~30d median).
    dv = fetch_history_volumes([bp["product_id"]],
                               TRADE_HUBS[station_id]["region_id"],
                               SESSION, CACHE_DIR).get(bp["product_id"])
    detail["daily_units"] = dv
    detail["tradeability"] = ind_core.tradeability(dv)
    detail["esi_prices"] = refresh_prices
    return detail


_BPO_SEARCH_REGIONS = [
    10000002, 10000043, 10000032, 10000042, 10000030,  # main hubs
    10000016, 10000020, 10000028, 10000033, 10000036,  # Lonetrek, Tash-Murkon, Molden Heath, The Citadel, Devoid
    10000037, 10000038, 10000041, 10000044, 10000048,  # Everyshore, The Bleak Lands, Syndicate, Solitude, Placid
    10000049, 10000052, 10000054, 10000064, 10000065,  # Khanid, Kador, Aridia, Essence, Kor-Azor
    10000067, 10000068, 10000001, 10000005, 10000007,  # Genesis, Verge Vendor, Derelik, Detorid, Domain (dup safe)
    10000069, 10000046, 10000010, 10000011, 10000015,  # Black Rise, Fade, Tribute, Vale of the Silent, Venal
]


def do_ind_bpo_search(q):
    """Search all major regions for a BPO that isn't available in the user's
    current region. Returns the cheapest sell with jump distance."""
    _require_login()
    _ensure_arb_caches()
    blueprint_id = int(q["blueprint_id"][0])
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    current_region = TRADE_HUBS[station_id]["region_id"]
    origin_system = HUB_SYSTEM_IDS[station_id]
    regions_to_check = [r for r in _BPO_SEARCH_REGIONS if r != current_region]

    def _search_region(region_id):
        orders = arb_core.fetch_type_orders(region_id, blueprint_id, SESSION)
        return region_id, ind_core.cheapest_sell_location(orders)

    best = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for region_id, loc in pool.map(_search_region, regions_to_check):
            if loc and (best is None or loc["price"] < best["price"]):
                best = {"price": loc["price"], "location_id": loc["location_id"],
                        "system_id": loc.get("system_id"),
                        "orders": loc["orders"], "region_id": region_id}

    if not best:
        return {"bp_market": None}

    loc_name = resolve_names(
        [best["location_id"]], SESSION, CACHE_DIR
    ).get(best["location_id"], f"location {best['location_id']}")
    jumps = None
    if best.get("system_id"):
        jumps, _ = arb_core.route_info(
            origin_system, best["system_id"], "shorter", _ARB_ROUTE_CACHE, SESSION)
    region_name = REGION_NAMES.get(best["region_id"], f"region {best['region_id']}")
    return {"bp_market": {"price": best["price"], "station": loc_name,
                          "orders": best["orders"], "region": region_name,
                          "jumps": jumps}}


# ── Notes API ──────────────────────────────────────────────────────────────

def do_notes_list(q):
    acct = current_account()
    if not acct or not pg_store.enabled():
        return {"notes": []}
    return {"notes": pg_store.notes_list(acct.account_id)}


def do_notes_save(q):
    acct = current_account()
    if not acct or not pg_store.enabled():
        return {"error": "not available"}
    note_id = q.get("id", [""])[0]
    raw_parent = q.get("parent_id", [None])[0]
    parent_id = None if (not raw_parent or raw_parent == "None" or raw_parent == "null") else raw_parent
    kind = q.get("kind", ["note"])[0]
    title = q.get("title", [""])[0]
    body = q.get("body", [""])[0]
    pos = int(q.get("pos", ["0"])[0])
    if not note_id:
        return {"error": "missing id"}
    ts = pg_store.notes_upsert(acct.account_id, note_id, parent_id, kind, title, body, pos)
    return {"ok": True, "updated_at": ts}


def do_notes_delete(q):
    acct = current_account()
    if not acct or not pg_store.enabled():
        return {"error": "not available"}
    note_id = q.get("id", [""])[0]
    if not note_id:
        return {"error": "missing id"}
    pg_store.notes_delete(acct.account_id, note_id)
    return {"ok": True}


# ── HTTP handler ────────────────────────────────────────────────────────────

# Clean URLs the SPA uses for each tab — all serve the app shell so a refresh
# or bookmark on any module reloads straight back into it.
TAB_ROUTES = {"/lp", "/arbitrage", "/arb", "/industry", "/ind",
              "/character", "/char", "/notes", "/exploration", "/exp",
              "/abyss", "/aby"}

_GET_ROUTES = {
    "/api/corps": lambda q: get_npc_corps(),
    "/api/liquidity": do_liquidity,
    "/api/detail": do_detail,
    "/api/history": do_history,
    "/api/ind/groups": do_ind_groups,
    "/api/ind/liquidity": do_ind_liquidity,
    "/api/ind/detail": do_ind_detail,
    "/api/ind/bpo-search": do_ind_bpo_search,
    "/api/ind/builds": do_ind_builds_list,
    "/api/auth/login": do_auth_login,
    "/api/auth/switch": do_auth_switch,
    "/api/char/data": do_char_data,
    "/api/char/wallet-history": do_wallet_history,
    "/api/notes": do_notes_list,
    # /api/auth/status and /api/auth/logout are handled explicitly in do_GET so
    # they can refresh / clear the session cookie.
}

# Uniform POST endpoints: take the merged query/body params, return a JSON dict.
# (Routes needing the raw body or the current account stay inline in do_POST.)
_POST_ROUTES = {
    "/api/prefs": do_prefs,
    "/api/arb/prefs": do_arb_prefs,
    "/api/ind/prefs": do_ind_prefs,
    "/api/settings/sync": do_settings_sync,
    "/api/notes/save": do_notes_save,
    "/api/notes/delete": do_notes_delete,
    "/api/ind/builds/save": do_ind_builds_save,
    "/api/ind/builds/delete": do_ind_builds_delete,
    "/api/ind/builds/link": do_ind_builds_link,
}

# Session cookie + the endpoints reachable without one (multi-user mode). The app
# shell and the login handshake must stay public; everything else needs a session.
_COOKIE_NAME = "emt_sid"
# /api/auth/logout is public so it always clears the browser cookie, even when
# the session is already gone/expired (otherwise a stale cookie could never be
# cleared — the gate would 401 the logout before it ran).
_PUBLIC_PATHS = ({"/", "/favicon.ico", "/callback", "/api/corps",
                  "/api/auth/login", "/api/auth/status", "/api/auth/logout"} | TAB_ROUTES)
_MAX_BODY = 2 * 1024 * 1024  # reject request bodies larger than 2 MiB


def _cookie_header(sid):
    secure = "; Secure" if _callback_url().startswith("https") else ""
    return (f"{_COOKIE_NAME}={sid}; HttpOnly; Path=/; SameSite=Lax; "
            f"Max-Age={_SESSION_TTL}{secure}")


def _expire_cookie_header():
    """A Set-Cookie that clears the session cookie in the browser (logout)."""
    secure = "; Secure" if _callback_url().startswith("https") else ""
    return f"{_COOKIE_NAME}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0{secure}"


# Cap concurrent scans so a burst / crawler on the public URL can't pile up
# unbounded worker threads on the threaded http.server. Excess requests get 503.
_MAX_CONCURRENT_SCANS = 8
_SCAN_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_SCANS)

# Long-lived SSE nudge streams each hold a worker thread; cap them independently
# of scans so a burst of tabs can't exhaust the thread pool.
_MAX_CHAR_STREAMS = 64
_STREAM_SLOTS = threading.BoundedSemaphore(_MAX_CHAR_STREAMS)
_CHAR_STREAM_HEARTBEAT = 25  # seconds between keep-alive comments


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200, set_cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _MIME = {".css": "text/css", ".js": "application/javascript",
             ".json": "application/json", ".svg": "image/svg+xml",
             ".png": "image/png", ".ico": "image/x-icon"}

    def _serve_static(self, rel):
        target = (_STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(_STATIC_DIR.resolve())):
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        ext = target.suffix.lower()
        ct = self._MIME.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, set_sid=None):
        self.send_response(302)
        self.send_header("Location", location)
        if set_sid:
            self.send_header("Set-Cookie", _cookie_header(set_sid))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookies(self):
        jar = {}
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
        return jar

    def _setup_request(self):
        """Resolve the request's Account into the per-thread context. Legacy mode
        always uses the single implicit account; multi-user resolves the cookie."""
        _REQUEST.account = None
        if not pg_store.enabled():
            _REQUEST.account = _LEGACY_ACCOUNT
            return
        try:
            _REQUEST.account = _resolve_session(self._cookies().get(_COOKIE_NAME))
        except Exception:  # noqa: BLE001 — never let a session-store hiccup 500
            _REQUEST.account = None

    def _gate(self, path):
        """In multi-user mode, reject non-public requests without a session.

        Static assets stay public: the app shell served at "/" pulls its own
        CSS/JS from /static/, and without them the login screen can't even
        render (every asset would 401). Path traversal is blocked in
        _serve_static, so exposing the static tree carries no extra risk."""
        if path.startswith("/static/"):
            return True
        if pg_store.enabled() and path not in _PUBLIC_PATHS and current_account() is None:
            self._send_json({"error": "Log in with EVE to continue.",
                             "login_required": True}, 401)
            return False
        return True

    def _handle_callback(self, q):
        """EVE SSO redirect target: validate state, exchange the code for tokens,
        attach the character to an account, mint a session cookie, bounce back."""
        if "error" in q:
            self._send_html(f"<h2>EVE login failed</h2><p>{html.escape(q['error'][0])}</p>"
                            "<p><a href='/'>Back to app</a></p>")
            return
        state = q.get("state", [""])[0]
        code = q.get("code", [""])[0]
        with _PKCE_LOCK:
            handshake = _PKCE.pop(state, None)
        if not handshake or not code:
            self._send_html("<h2>EVE login failed</h2><p>Invalid or expired login "
                            "request. Please try again.</p><p><a href='/'>Back to app</a></p>")
            return
        try:
            client_id = _eve_client_id()
            tok = sso_core.exchange_code(client_id, handshake["redirect_uri"],
                                         code, handshake["verifier"], SESSION)
            claims = sso_core.decode_jwt_payload(tok["access_token"])
            cid = claims["character_id"]
            # Which account does this character join? An existing session's
            # account (adding a character), else the account this character is
            # already known to belong to (re-login), else a brand-new account.
            acct = current_account()
            if acct is None:
                account_id = pg_store.char_account_get(cid) if pg_store.enabled() else None
                acct = _get_account_by_id(account_id) if account_id is not None else None
                if acct is None:
                    acct = Account(cid)
            with acct.lock:
                if acct.account_id is None:
                    acct.account_id = cid
                acct.characters[cid] = {
                    "character_id": cid,
                    "name": claims["name"],
                    "scopes": claims.get("scopes", []),
                    "refresh_token": tok["refresh_token"],
                    "access_token": tok["access_token"],
                    "expires_at": time.time() + int(tok.get("expires_in", 1200)),
                }
                if acct.active_char_id is None:
                    acct.active_char_id = cid
                _persist_account(acct)
            with _REGISTRY_LOCK:
                _ACCOUNTS[acct.account_id] = acct
            _refresh_skill_profile(acct, cid)
            _refresh_char_blueprints(acct, cid)
            set_sid = None
            if pg_store.enabled():
                existing = self._cookies().get(_COOKIE_NAME)
                if not existing or _resolve_session(existing) is not acct:
                    set_sid = _new_session(acct)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._send_html(f"<h2>EVE login failed</h2><p>{html.escape(f'{type(e).__name__}: {e}')}</p>"
                            "<p><a href='/'>Back to app</a></p>")
            return
        self._redirect("/", set_sid)

    def _sse_emit(self, data):
        """Write one SSE ``data:`` frame. Returns False once the socket is gone
        (so streaming loops can stop); scan callers ignore the return value."""
        try:
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_comment(self, text=""):
        """Write an SSE comment line (``: ...``) — used as a keep-alive heartbeat
        and the earliest detection of a browser that has closed the stream."""
        try:
            self.wfile.write(f": {text}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _handle_sse_scan(self, q, scan_fn, tag):
        if not _SCAN_SLOTS.acquire(blocking=False):
            self._send_json({"error": "Server busy — too many scans in progress. "
                             "Try again in a moment."}, 503)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            emit = self._sse_emit
            try:
                result = scan_fn(q, emit=emit)
                emit({"type": "result", **result})
                if tag == "lp":
                    _save_last_scan(current_account(), "lp", result)
                elif tag == "ind" and not result.get("favorites_only") and not result.get("owned_only"):
                    _save_last_scan(current_account(), "ind", result)
            except LPError as e:
                print(f"[{tag}] LPError: {e}", file=sys.stderr)
                emit({"type": "error", "error": str(e)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                emit({"type": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            _SCAN_SLOTS.release()

    def _handle_arb_scan(self, q):
        self._handle_sse_scan(q, do_arb_scan, "arb")

    def _handle_char_stream(self):
        """SSE stream. Pushes a ``sync`` event to the browser (a) whenever the
        background refresh detects new data for this account (``changed:true`` →
        the browser re-pulls /api/char/data) and (b) once per background sweep so
        every client re-publishes the shared, server-defined ``next_sync_in``
        countdown in lockstep. A heartbeat comment every _CHAR_STREAM_HEARTBEAT
        seconds keeps the socket alive and reveals a closed browser."""
        acct = current_account()
        if acct is None or not acct.characters:
            self._send_json({"error": "Not logged in."}, 401)
            return
        if not _STREAM_SLOTS.acquire(blocking=False):
            self._send_json({"error": "Too many open streams — try again shortly."}, 503)
            return
        key = id(acct)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_ver, last_sweep = _CHAR_PUBSUB.state(key)
            if not self._sse_emit({"type": "hello",
                                   "next_sync_in": round(_next_sync_in())}):
                return
            while True:
                ver, sweep = _CHAR_PUBSUB.wait(key, last_ver, last_sweep,
                                               _CHAR_STREAM_HEARTBEAT)
                if ver != last_ver or sweep != last_sweep:
                    changed = ver != last_ver
                    last_ver, last_sweep = ver, sweep
                    if not self._sse_emit({"type": "sync", "changed": changed,
                                           "next_sync_in": round(_next_sync_in())}):
                        return
                elif not self._sse_comment("ping"):
                    return
        finally:
            _STREAM_SLOTS.release()

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            self._setup_request()
            if not self._gate(parsed.path):
                return
            if parsed.path == "/" or parsed.path in TAB_ROUTES:
                self._send_html(INDEX_HTML)
            elif parsed.path.startswith("/static/"):
                self._serve_static(parsed.path[len("/static/"):])
            elif parsed.path == "/favicon.ico":
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(_FAVICON_SVG)))
                self.end_headers()
                self.wfile.write(_FAVICON_SVG)
            elif parsed.path == "/api/settings":
                acct = current_account()
                synced = load_account_settings(acct)
                if synced is not None:
                    synced["_server_synced"] = True
                    self._send_json(synced)
                else:
                    merged = load_settings()
                    merged["arb"] = load_arb_settings()
                    merged["ind"] = load_ind_settings()
                    merged["_server_synced"] = False
                    merged["_logged_in"] = bool(acct and acct.characters)
                    self._send_json(merged)
            elif parsed.path == "/api/last-scan":
                acct = current_account()
                lp_data = _load_last_scan(acct, "lp")
                ind_data = _load_last_scan(acct, "ind")
                if ind_data and ind_data.get("rows"):
                    _patch_group_names(ind_data["rows"])
                self._send_json({"lp": lp_data, "ind": ind_data})
            elif parsed.path == "/api/scan":
                result = do_scan(q)
                _save_last_scan(current_account(), "lp", result)
                self._send_json(result)
            elif parsed.path == "/api/char/stream":
                self._handle_char_stream()
            elif parsed.path == "/api/arb/scan":
                self._handle_arb_scan(q)
            elif parsed.path == "/api/ind/scan":
                self._handle_sse_scan(q, do_ind_scan, "ind")
            elif parsed.path == "/callback":
                self._handle_callback(q)
            elif parsed.path == "/api/auth/status":
                st = do_auth_status(q)
                # Sliding expiration: re-issue the cookie with a fresh Max-Age on
                # each app load so an actively-used session doesn't hit the hard
                # 30-day cap. (last_seen is also refreshed server-side.)
                cookie = None
                if pg_store.enabled() and current_account() is not None:
                    sid = self._cookies().get(_COOKIE_NAME)
                    if sid:
                        cookie = _cookie_header(sid)
                self._send_json(st, set_cookie=cookie)
            elif parsed.path == "/api/auth/logout":
                result = do_auth_logout(q)
                self._send_json(result,
                                set_cookie=_expire_cookie_header() if pg_store.enabled() else None)
            elif parsed.path in _GET_ROUTES:
                self._send_json(_GET_ROUTES[parsed.path](q))
            else:
                self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except LPError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def do_POST(self):
        parsed = urlparse(self.path)
        q = parse_qs(urlparse(self.path).query)
        try:
            self._setup_request()
            if not self._gate(parsed.path):
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > _MAX_BODY:
                self._send_json({"error": "request too large"}, 413)
                return
            body = self.rfile.read(length) if length else b""
            if body:
                try:
                    body_params = json.loads(body)
                    if isinstance(body_params, dict):
                        for k, v in body_params.items():
                            q.setdefault(k, []).append(str(v) if not isinstance(v, str) else v)
                except json.JSONDecodeError:
                    pass
            if parsed.path == "/api/save-scan":
                data = json.loads(body) if body else {}
                tab = data.get("tab", "")
                blob = data.get("blob")
                if tab in ("lp", "ind") and blob:
                    _save_last_scan(current_account(), tab, blob)
                self._send_json({"ok": True})
            elif parsed.path == "/api/orders/dismiss":
                event_id = q.get("id", [""])[0]
                if event_id:
                    _dismiss_order_event(current_account(), event_id)
                self._send_json({"ok": True})
            elif parsed.path in _POST_ROUTES:
                self._send_json(_POST_ROUTES[parsed.path](q))
            else:
                self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


# ── Front-end ───────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = (_STATIC_DIR / "index.html").read_text().replace(
    "__VERSION__", __version__).replace("__FAVICON__", _FAVICON_B64)

# Full concatenation of all frontend source for test assertions.
_JS_DIR = _STATIC_DIR / "js"
FRONTEND_SOURCE = INDEX_HTML + "\n" + (
    (_STATIC_DIR / "style.css").read_text() + "\n" +
    "\n".join((_JS_DIR / f).read_text() for f in sorted(_JS_DIR.iterdir()) if f.suffix == ".js")
) if _JS_DIR.is_dir() else INDEX_HTML


def main():
    ap = argparse.ArgumentParser(description="EVE Market Tools web UI.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    global _SERVER_PORT
    _SERVER_PORT = args.port
    _warn_if_multi_replica()
    _startup_restore()

    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=get_npc_corps, daemon=True).start()
    threading.Thread(target=_sweep_loop, daemon=True).start()
    threading.Thread(target=_bg_char_refresh_loop, daemon=True).start()
    print(f"EVE Market Tools running at {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
