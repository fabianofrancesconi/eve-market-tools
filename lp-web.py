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
__version__ = "1.75.0"

import argparse
import base64
import concurrent.futures
import html
import json
import math
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
from lp_core import (
    ESI, HEADERS, HIGH_SPREAD_PCT, JITA_STATION_ID, LPError, build_detail, default_cache_dir,
    TRADE_HUBS, enrich_liquidity, evaluate, fetch_history_prices,
    fetch_history_volumes,
    fetch_orderbook_jita, fetch_order_rank, fetch_prices, fetch_prices_esi,
    fetch_sell_order_stats, get_offers,
    load_json, resolve_corp_id, resolve_corp_name, resolve_names, resolve_station_region,
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
AUTH_SETTINGS_PATH = CACHE_DIR / "auth_settings.json"  # client_id + callback_url
JOBS_TRACK_PATH = CACHE_DIR / "ind_jobs_delivered.json"  # cumulative delivered-run counter
ORDER_EVENTS_PATH = CACHE_DIR / "order_events.json"  # market order sale/fill events
USER_SETTINGS_DB_PATH = CACHE_DIR / "user_settings.sqlite"  # per-character synced settings
LP_LAST_SCAN_PATH = CACHE_DIR / "lp_last_scan.json"
IND_LAST_SCAN_PATH = CACHE_DIR / "ind_last_scan.json"
REFRESHED_CORPS = set()

# ── EVE SSO state (single process, multi-character) ──────────────────────────
# Pending PKCE handshakes keyed by `state` (verifier + redirect_uri), set in
# /api/auth/login and consumed once in /callback.
_PKCE: dict = {}
# All linked characters, keyed by character_id (int).
# Each entry: {character_id, name, scopes, refresh_token, access_token, expires_at}
_CHARACTERS: dict = {}
_CHARACTERS_LOCK = threading.RLock()
# The "active" character. Selected from the header dropdown, it drives every
# per-character view: LP-tab budget, Industry skills/BP calculations, and the
# wallet shown in the header chip.
_ACTIVE_CHAR_ID: int | None = None
# Per-character skill profiles: {cid: {skill_id: trained_level}}
_CHAR_SKILL_PROFILES: dict = {}
# Per-character owned blueprints: {cid: {type_id: (me, te, is_bpo, runs)}}
_CHAR_BP_ME_TES: dict = {}
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

def load_settings():
    return load_json(SETTINGS_PATH, {})


def save_settings(d):
    save_json(SETTINGS_PATH, d)


def load_arb_settings():
    return load_json(ARB_SETTINGS_PATH, {})


def save_arb_settings(d):
    save_json(ARB_SETTINGS_PATH, d)


def load_ind_settings():
    return load_json(IND_SETTINGS_PATH, {})


def save_ind_settings(d):
    save_json(IND_SETTINGS_PATH, d)


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

def load_auth_settings():
    return load_json(AUTH_SETTINGS_PATH, {})


def save_auth_settings(d):
    save_json(AUTH_SETTINGS_PATH, d)


def _suggested_callback():
    return f"http://localhost:{_SERVER_PORT}/callback"


def _callback_url():
    """The redirect_uri to use — the explicit one the user saved, else the
    suggested localhost callback for the bound port."""
    return (load_auth_settings().get("callback_url") or "").strip() or _suggested_callback()


def _persist_auth():
    """Write all linked characters + the active selection to disk."""
    data = {
        "version": 2,
        "active_char_id": _ACTIVE_CHAR_ID,
        "characters": [
            {"character_id": c["character_id"], "name": c["name"],
             "scopes": c["scopes"], "refresh_token": c["refresh_token"]}
            for c in _CHARACTERS.values()
        ],
    }
    sso_core.save_tokens(CACHE_DIR, data)


def _restore_auth():
    """Load persisted characters on startup. Migrates v1 (single char) to v2
    automatically so existing users don't need to re-login."""
    global _ACTIVE_CHAR_ID
    saved = sso_core.load_tokens(CACHE_DIR)
    if saved.get("version") == 2:
        for c in saved.get("characters", []):
            cid = c["character_id"]
            _CHARACTERS[cid] = {
                "character_id": cid,
                "name": c["name"],
                "scopes": c.get("scopes", []),
                "refresh_token": c["refresh_token"],
                "access_token": None,
                "expires_at": 0,
            }
        _ACTIVE_CHAR_ID = saved.get("active_char_id")
        if _ACTIVE_CHAR_ID not in _CHARACTERS:
            _ACTIVE_CHAR_ID = next(iter(_CHARACTERS), None)
    elif saved.get("refresh_token") and saved.get("character_id"):
        cid = saved["character_id"]
        _CHARACTERS[cid] = {
            "character_id": cid,
            "name": saved.get("name"),
            "scopes": saved.get("scopes", []),
            "refresh_token": saved["refresh_token"],
            "access_token": None,
            "expires_at": 0,
        }
        _ACTIVE_CHAR_ID = cid
        _persist_auth()


def _require_login():
    """Raise LPError unless at least one character is logged in."""
    if not _CHARACTERS:
        raise LPError("Log in with EVE to use the Industry planner.")


def _access_token(cid=None):
    """A valid bearer token for the given character (defaults to active).
    Refreshes transparently when expired."""
    with _CHARACTERS_LOCK:
        if cid is None:
            cid = _ACTIVE_CHAR_ID
        char = _CHARACTERS.get(cid)
        if not char or not char.get("refresh_token"):
            raise LPError("Not logged in to EVE.")
        if char.get("access_token") and not sso_core.access_token_expired(char.get("expires_at")):
            return char["access_token"]
        client_id = (load_auth_settings().get("client_id") or "").strip()
        if not client_id:
            raise LPError("No EVE application CLIENT_ID configured.")
        tok = sso_core.refresh_access_token(client_id, char["refresh_token"], SESSION)
        claims = sso_core.decode_jwt_payload(tok["access_token"])
        char.update({
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", char["refresh_token"]),
            "expires_at": time.time() + int(tok.get("expires_in", 1200)),
            "name": claims.get("name") or char["name"],
            "scopes": claims.get("scopes") or char["scopes"],
        })
        _persist_auth()
        return char["access_token"]


def _refresh_skill_profile(cid):
    """Pull the character's skills and cache {skill_id: level} for Industry."""
    try:
        skills = sso_core.fetch_skills(_access_token(cid), cid, SESSION)
        _CHAR_SKILL_PROFILES[cid] = sso_core.skill_profile_from_skills(skills)
    except (LPError, requests.RequestException):
        _CHAR_SKILL_PROFILES.setdefault(cid, {})


def _refresh_char_blueprints(cid):
    """Pull the character's owned blueprints and cache each type's best ME/TE.
    Supplements from active industry jobs (a running manufacturing job proves
    ownership even if the blueprints endpoint hasn't caught up)."""
    try:
        token = _access_token(cid)
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
        _CHAR_BP_ME_TES[cid] = bp_map
    except (LPError, requests.RequestException):
        _CHAR_BP_ME_TES.setdefault(cid, {})


def do_auth_config(q):
    """GET returns the saved CLIENT_ID + callback; with params, saves them."""
    s = load_auth_settings()
    changed = False
    if "client_id" in q:
        s["client_id"] = q["client_id"][0].strip()
        changed = True
    if "callback_url" in q:
        s["callback_url"] = q["callback_url"][0].strip()
        changed = True
    if changed:
        save_auth_settings(s)
    return {
        "client_id": s.get("client_id", ""),
        "callback_url": s.get("callback_url", ""),
        "suggested_callback": _suggested_callback(),
        "scopes": sso_core.SCOPES,
    }


def do_auth_login(q):
    """Begin the PKCE handshake — returns the authorize URL to send the browser to."""
    client_id = (load_auth_settings().get("client_id") or "").strip()
    if not client_id:
        raise LPError("Enter your EVE application CLIENT_ID first (Login settings).")
    verifier, challenge = sso_core.make_pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = _callback_url()
    _PKCE[state] = {"verifier": verifier, "redirect_uri": redirect_uri}
    url = sso_core.build_authorize_url(client_id, redirect_uri, sso_core.SCOPES, state, challenge)
    return {"url": url}


def do_auth_status(q):
    chars = [{"character_id": c["character_id"], "name": c["name"]}
             for c in _CHARACTERS.values()]
    active = _CHARACTERS.get(_ACTIVE_CHAR_ID)
    return {
        "logged_in": bool(_CHARACTERS),
        "characters": chars,
        "active_char_id": _ACTIVE_CHAR_ID,
        "character_id": _ACTIVE_CHAR_ID,
        "name": active["name"] if active else None,
        "scopes": active["scopes"] if active else [],
    }


def do_auth_switch(q):
    """Switch the active character. It drives the LP budget, the Industry
    skills/BP calculations and the header wallet, so refresh that character's
    skill profile and blueprints while we're here."""
    global _ACTIVE_CHAR_ID
    with _CHARACTERS_LOCK:
        if "active_char_id" in q:
            cid = int(q["active_char_id"][0])
            if cid in _CHARACTERS:
                _ACTIVE_CHAR_ID = cid
                _refresh_skill_profile(cid)
                _refresh_char_blueprints(cid)
        _persist_auth()
    return do_auth_status({})


def do_auth_logout(q):
    char_id = q.get("char_id", [None])[0]
    global _ACTIVE_CHAR_ID
    with _CHARACTERS_LOCK:
        if char_id:
            cid = int(char_id)
            _CHARACTERS.pop(cid, None)
            _CHAR_SKILL_PROFILES.pop(cid, None)
            _CHAR_BP_ME_TES.pop(cid, None)
            if _ACTIVE_CHAR_ID == cid:
                _ACTIVE_CHAR_ID = next(iter(_CHARACTERS), None)
        else:
            _CHARACTERS.clear()
            _CHAR_SKILL_PROFILES.clear()
            _CHAR_BP_ME_TES.clear()
            _ACTIVE_CHAR_ID = None
            _PKCE.clear()
        _persist_auth()
    return {"ok": True}


_JOBS_TRACK_LOCK = threading.Lock()


def _track_delivered_jobs(cid, jobs, names):
    """Cumulative counter of runs/jobs the character has *delivered*, persisted
    across restarts. Only counts jobs newly seen as "delivered" — the first time
    a character is observed, its already-delivered jobs (ESI's 90-day completed
    window) are recorded as a baseline but not counted, so the counter only grows
    from the moment this feature started watching, as advertised to the user."""
    with _JOBS_TRACK_LOCK:
        store = load_json(JOBS_TRACK_PATH, {})
        key = str(cid)
        entry = store.get(key)
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
            prod = entry["by_product"].setdefault(pid, {"name": names.get(j.get("product_type_id"), "?"), "runs": 0, "jobs": 0})
            prod["runs"] += runs
            prod["jobs"] += 1
        entry["seen_job_ids"] = list(seen)
        if changed:
            store[key] = entry
            save_json(JOBS_TRACK_PATH, store)
        return {"total_runs": entry["total_runs"], "total_jobs": entry["total_jobs"],
                "since": entry["since"], "by_product": entry["by_product"]}


ORDER_EVENT_EXPIRY = 7 * 24 * 3600  # auto-expire after 1 week


def _track_order_changes(cid, current_orders, names):
    """Compare current market orders with previously seen ones. Record sale/fill
    events when volume_remain decreases or an order disappears entirely.
    Also maintains per-order last_sale metadata for active orders."""
    store = load_json(ORDER_EVENTS_PATH, {})
    char_key = str(cid)
    prev_key = f"_prev_{char_key}"
    sales_key = f"_sales_{char_key}"
    prev_orders = store.get(prev_key, {})
    events = store.get(char_key, [])
    last_sales = store.get(sales_key, {})

    now = time.time()
    events = [e for e in events if now - e["ts"] < ORDER_EVENT_EXPIRY and not e.get("dismissed")]

    current_by_id = {}
    for o in current_orders:
        oid = o.get("order_id")
        if oid:
            current_by_id[str(oid)] = o

    for oid_str, prev in prev_orders.items():
        cur = current_by_id.get(oid_str)
        prev_remain = prev.get("volume_remain", 0)
        if cur is None:
            sold = prev_remain
        else:
            sold = prev_remain - cur.get("volume_remain", 0)
        if sold > 0:
            events.append({
                "id": f"{oid_str}_{int(now)}",
                "ts": now,
                "order_id": int(oid_str),
                "type_name": prev.get("type_name") or names.get(prev.get("type_id"), "?"),
                "sold": sold,
                "price": prev.get("price", 0),
                "is_buy_order": prev.get("is_buy_order", False),
                "filled": cur is None,
                "character_name": _CHARACTERS.get(cid, {}).get("name", ""),
                "dismissed": False,
            })
            if cur is not None:
                last_sales[oid_str] = {"ts": now, "sold": sold}

    # Clean up last_sales for orders that no longer exist
    last_sales = {k: v for k, v in last_sales.items() if k in current_by_id}

    new_prev = {}
    for o in current_orders:
        oid = o.get("order_id")
        if oid:
            new_prev[str(oid)] = {
                "volume_remain": o.get("volume_remain"),
                "type_id": o.get("type_id"),
                "type_name": o.get("type_name"),
                "price": o.get("price"),
                "is_buy_order": o.get("is_buy_order"),
            }

    store[prev_key] = new_prev
    store[char_key] = events
    store[sales_key] = last_sales
    save_json(ORDER_EVENTS_PATH, store)
    return events, last_sales


def _get_order_events():
    """Return all non-dismissed, non-expired events across all characters."""
    store = load_json(ORDER_EVENTS_PATH, {})
    now = time.time()
    all_events = []
    for key, val in store.items():
        if key.startswith("_prev_"):
            continue
        if isinstance(val, list):
            all_events.extend(
                e for e in val
                if not e.get("dismissed") and now - e["ts"] < ORDER_EVENT_EXPIRY
            )
    all_events.sort(key=lambda e: e["ts"], reverse=True)
    return all_events


def _dismiss_order_event(event_id):
    """Mark a single event as dismissed, or dismiss all if event_id is 'all'."""
    store = load_json(ORDER_EVENTS_PATH, {})
    for key, val in store.items():
        if key.startswith("_prev_"):
            continue
        if isinstance(val, list):
            for e in val:
                if event_id == "all" or e.get("id") == event_id:
                    e["dismissed"] = True
    save_json(ORDER_EVENTS_PATH, store)


def _fetch_one_char_data(cid):
    """Fetch all ESI data for a single character. Returns a dict bundle."""
    token = _access_token(cid)
    char_name = _CHARACTERS[cid]["name"]
    _refresh_skill_profile(cid)
    _refresh_char_blueprints(cid)

    wallet = sso_core.fetch_wallet(token, cid, SESSION)
    skills = sso_core.fetch_skills(token, cid, SESSION)
    queue = sso_core.fetch_skillqueue(token, cid, SESSION)
    loyalty = sso_core.fetch_loyalty_points(token, cid, SESSION)
    jobs = sso_core.fetch_industry_jobs(token, cid, SESSION, include_completed=True)
    orders, orders_error = [], None
    try:
        orders = sso_core.fetch_market_orders(token, cid, SESSION)
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

    runs_tracked = _track_delivered_jobs(cid, jobs, names)

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

    _, last_sales = _track_order_changes(cid, orders_out, names)
    for o in orders_out:
        sale = last_sales.get(str(o.get("order_id")))
        if sale:
            o["last_sale_ts"] = sale["ts"]
            o["last_sale_qty"] = sale["sold"]

    skill_profile = _CHAR_SKILL_PROFILES.get(cid, {})
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
        "jobs": out_jobs,
        "runs_tracked": runs_tracked,
        "market_orders": orders_out,
        "market_orders_error": orders_error,
        "accounting_level": accounting_lvl,
        "broker_relations_level": broker_rel_lvl,
    }


def do_char_data(q):
    """Fetch data for all linked characters and return a combined bundle."""
    if not _CHARACTERS:
        raise LPError("Not logged in to EVE.")

    char_ids = list(_CHARACTERS.keys())
    results = {}

    if len(char_ids) == 1:
        cid = char_ids[0]
        results[cid] = _fetch_one_char_data(cid)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(char_ids)) as pool:
            futures = {pool.submit(_fetch_one_char_data, cid): cid for cid in char_ids}
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
    for r in results.values():
        combined_jobs.extend(r.get("jobs", []))
        combined_orders.extend(r.get("market_orders", []))
        combined_queue.extend(r.get("skillqueue", []))
        rt = r.get("runs_tracked") or {}
        combined_runs["total_runs"] += rt.get("total_runs", 0)
        combined_runs["total_jobs"] += rt.get("total_jobs", 0)
    combined_jobs.sort(key=lambda x: x.get("end") or "")
    combined_orders.sort(key=lambda o: o.get("issued") or "", reverse=True)

    active_data = results.get(_ACTIVE_CHAR_ID) or next(iter(results.values()), {})

    return {
        "characters": [results[cid] for cid in char_ids if cid in results],
        "combined_wallet": combined_wallet,
        "combined_jobs": combined_jobs,
        "combined_orders": combined_orders,
        "combined_queue": combined_queue,
        "combined_runs_tracked": combined_runs,
        "active_char_id": _ACTIVE_CHAR_ID,
        "name": active_data.get("name"),
        "character_id": active_data.get("character_id"),
        "wallet": active_data.get("wallet"),
        "total_sp": active_data.get("total_sp"),
        "unallocated_sp": active_data.get("unallocated_sp"),
        "skillqueue": active_data.get("skillqueue", []),
        "loyalty": active_data.get("loyalty", []),
        "jobs": combined_jobs,
        "runs_tracked": combined_runs,
        "market_orders": combined_orders,
        "market_orders_error": active_data.get("market_orders_error"),
        "accounting_level": active_data.get("accounting_level", 0),
        "broker_relations_level": active_data.get("broker_relations_level", 0),
        "order_events": _get_order_events(),
    }


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


def do_settings_sync(q):
    """Push the full client-side settings blob for the logged-in character to
    the server, so other devices see the same columns/filters/etc. No-op when
    no character is logged in (unauthenticated use keeps the per-field
    /api/prefs, /api/arb/prefs, /api/ind/prefs endpoints as its only store)."""
    character_id = _ACTIVE_CHAR_ID
    if not character_id:
        return {"ok": True, "synced": False}
    blob = q.get("blob", ["{}"])[0]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        raise LPError("Invalid settings payload.")
    save_user_settings(character_id, data)
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
    _require_login()

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
    ind_cid = _ACTIVE_CHAR_ID
    ind_skill_profile = _CHAR_SKILL_PROFILES.get(ind_cid, {}) if ind_cid else {}
    ind_bp_me_te = _CHAR_BP_ME_TES.get(ind_cid, {}) if ind_cid else {}
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
    # Cross-character blueprint ownership annotations
    other_owners_map = {}
    for other_cid, bp_map in _CHAR_BP_ME_TES.items():
        if other_cid == ind_cid:
            continue
        other_name = _CHARACTERS.get(other_cid, {}).get("name", "?")
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
    _require_login()
    blueprint_id = int(q["blueprint_id"][0])
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    params = _ind_params(q)
    ind_cid = _ACTIVE_CHAR_ID
    ind_skill_profile = _CHAR_SKILL_PROFILES.get(ind_cid, {}) if ind_cid else {}
    ind_bp_me_te = _CHAR_BP_ME_TES.get(ind_cid, {}) if ind_cid else {}
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


# ── HTTP handler ────────────────────────────────────────────────────────────

# Clean URLs the SPA uses for each tab — all serve the app shell so a refresh
# or bookmark on any module reloads straight back into it.
TAB_ROUTES = {"/lp", "/arbitrage", "/arb", "/industry", "/ind",
              "/character", "/char"}

_GET_ROUTES = {
    "/api/corps": lambda q: get_npc_corps(),
    "/api/liquidity": do_liquidity,
    "/api/detail": do_detail,
    "/api/history": do_history,
    "/api/ind/groups": do_ind_groups,
    "/api/ind/liquidity": do_ind_liquidity,
    "/api/ind/detail": do_ind_detail,
    "/api/ind/bpo-search": do_ind_bpo_search,
    "/api/auth/config": do_auth_config,
    "/api/auth/login": do_auth_login,
    "/api/auth/status": do_auth_status,
    "/api/auth/logout": do_auth_logout,
    "/api/auth/switch": do_auth_switch,
    "/api/char/data": do_char_data,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_callback(self, q):
        """EVE SSO redirect target: validate state, exchange the code for tokens,
        add the character to _CHARACTERS, then bounce back to the app."""
        if "error" in q:
            self._send_html(f"<h2>EVE login failed</h2><p>{html.escape(q['error'][0])}</p>"
                            "<p><a href='/'>Back to app</a></p>")
            return
        state = q.get("state", [""])[0]
        code = q.get("code", [""])[0]
        handshake = _PKCE.pop(state, None)
        if not handshake or not code:
            self._send_html("<h2>EVE login failed</h2><p>Invalid or expired login "
                            "request. Please try again.</p><p><a href='/'>Back to app</a></p>")
            return
        try:
            client_id = (load_auth_settings().get("client_id") or "").strip()
            tok = sso_core.exchange_code(client_id, handshake["redirect_uri"],
                                         code, handshake["verifier"], SESSION)
            claims = sso_core.decode_jwt_payload(tok["access_token"])
            cid = claims["character_id"]
            global _ACTIVE_CHAR_ID
            with _CHARACTERS_LOCK:
                _CHARACTERS[cid] = {
                    "character_id": cid,
                    "name": claims["name"],
                    "scopes": claims.get("scopes", []),
                    "refresh_token": tok["refresh_token"],
                    "access_token": tok["access_token"],
                    "expires_at": time.time() + int(tok.get("expires_in", 1200)),
                }
                if _ACTIVE_CHAR_ID is None:
                    _ACTIVE_CHAR_ID = cid
                _persist_auth()
            _refresh_skill_profile(cid)
            _refresh_char_blueprints(cid)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._send_html(f"<h2>EVE login failed</h2><p>{html.escape(f'{type(e).__name__}: {e}')}</p>"
                            "<p><a href='/'>Back to app</a></p>")
            return
        self._redirect("/")

    def _sse_emit(self, data):
        try:
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_sse_scan(self, q, scan_fn, tag):
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
                save_json(LP_LAST_SCAN_PATH, result)
            elif tag == "ind" and not result.get("favorites_only") and not result.get("owned_only"):
                save_json(IND_LAST_SCAN_PATH, result)
        except LPError as e:
            print(f"[{tag}] LPError: {e}", file=sys.stderr)
            emit({"type": "error", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            emit({"type": "error", "error": f"{type(e).__name__}: {e}"})

    def _handle_arb_scan(self, q):
        self._handle_sse_scan(q, do_arb_scan, "arb")

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/" or parsed.path in TAB_ROUTES:
                self._send_html(INDEX_HTML)
            elif parsed.path == "/favicon.ico":
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(_FAVICON_SVG)))
                self.end_headers()
                self.wfile.write(_FAVICON_SVG)
            elif parsed.path == "/api/settings":
                character_id = _ACTIVE_CHAR_ID
                synced = load_user_settings(character_id) if character_id else None
                if synced is not None:
                    synced["_server_synced"] = True
                    self._send_json(synced)
                else:
                    merged = load_settings()
                    merged["arb"] = load_arb_settings()
                    merged["ind"] = load_ind_settings()
                    merged["_server_synced"] = False
                    merged["_logged_in"] = bool(character_id)
                    self._send_json(merged)
            elif parsed.path == "/api/last-scan":
                lp_data = load_json(LP_LAST_SCAN_PATH, None)
                ind_data = load_json(IND_LAST_SCAN_PATH, None)
                if ind_data and ind_data.get("rows"):
                    _patch_group_names(ind_data["rows"])
                self._send_json({"lp": lp_data, "ind": ind_data})
            elif parsed.path == "/api/scan":
                result = do_scan(q)
                save_json(LP_LAST_SCAN_PATH, result)
                self._send_json(result)
            elif parsed.path == "/api/arb/scan":
                self._handle_arb_scan(q)
            elif parsed.path == "/api/ind/scan":
                self._handle_sse_scan(q, do_ind_scan, "ind")
            elif parsed.path == "/callback":
                self._handle_callback(q)
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
            length = int(self.headers.get("Content-Length", 0))
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
                if tab == "lp" and blob:
                    save_json(LP_LAST_SCAN_PATH, blob)
                elif tab == "ind" and blob:
                    save_json(IND_LAST_SCAN_PATH, blob)
                self._send_json({"ok": True})
            elif parsed.path == "/api/prefs":
                self._send_json(do_prefs(q))
            elif parsed.path == "/api/arb/prefs":
                self._send_json(do_arb_prefs(q))
            elif parsed.path == "/api/ind/prefs":
                self._send_json(do_ind_prefs(q))
            elif parsed.path == "/api/settings/sync":
                self._send_json(do_settings_sync(q))
            elif parsed.path == "/api/orders/dismiss":
                event_id = q.get("id", [""])[0]
                if event_id:
                    _dismiss_order_event(event_id)
                self._send_json({"ok": True})
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


def main():
    ap = argparse.ArgumentParser(description="EVE Market Tools web UI.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    global _SERVER_PORT
    _SERVER_PORT = args.port
    _restore_auth()

    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=get_npc_corps, daemon=True).start()
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
