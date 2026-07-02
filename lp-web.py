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
__version__ = "1.66.1"

import argparse
import base64
import concurrent.futures
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
USER_SETTINGS_DB_PATH = CACHE_DIR / "user_settings.sqlite"  # per-character synced settings
LP_LAST_SCAN_PATH = CACHE_DIR / "lp_last_scan.json"
IND_LAST_SCAN_PATH = CACHE_DIR / "ind_last_scan.json"
REFRESHED_CORPS = set()

# ── EVE SSO state (single process, single user) ───────────────────────────────
# Pending PKCE handshakes keyed by `state` (verifier + redirect_uri), set in
# /api/auth/login and consumed once in /callback.
_PKCE: dict = {}
# Live session: access_token + expiry + character identity. Refresh token is
# persisted via sso_core; the access token is held only in memory.
_AUTH: dict = {}
# Logged-in character's {skill_id: trained_level}, used to auto-fill the planner.
_CHAR_SKILL_PROFILE: dict = {}
# Logged-in character's owned blueprints, as {type_id: (material_efficiency,
# time_efficiency)} of the best-researched copy — used to override the
# Industry planner's uniform ME/TE assumption per blueprint.
_CHAR_BP_ME_TE: dict = {}
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


def _ensure_arb_caches():
    global _ARB_STATION_CACHE, _ARB_VOLUME_CACHE, _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE, _ARB_CACHES_LOADED
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


def _restore_auth():
    """Load a persisted session on startup so a previous login survives a restart.
    The access token is refreshed lazily on first use."""
    saved = sso_core.load_tokens(CACHE_DIR)
    if saved.get("refresh_token") and saved.get("character_id"):
        _AUTH.update({
            "refresh_token": saved["refresh_token"],
            "character_id": saved["character_id"],
            "name": saved.get("name"),
            "scopes": saved.get("scopes", []),
            "access_token": None,
            "expires_at": 0,
        })


def _require_login():
    """Raise LPError unless a character is logged in. The Industry planner has
    no manual ME/TE/skill inputs — it needs a real character's owned blueprints
    and trained skills to mean anything, so login isn't optional for it."""
    if not _AUTH.get("refresh_token"):
        raise LPError("Log in with EVE to use the Industry planner.")


def _access_token():
    """A valid bearer token, refreshing transparently when expired. Raises LPError
    if nobody is logged in or no CLIENT_ID is configured."""
    if not _AUTH.get("refresh_token"):
        raise LPError("Not logged in to EVE.")
    if _AUTH.get("access_token") and not sso_core.access_token_expired(_AUTH.get("expires_at")):
        return _AUTH["access_token"]
    client_id = (load_auth_settings().get("client_id") or "").strip()
    if not client_id:
        raise LPError("No EVE application CLIENT_ID configured.")
    tok = sso_core.refresh_access_token(client_id, _AUTH["refresh_token"], SESSION)
    _apply_token(tok)
    return _AUTH["access_token"]


def _apply_token(tok):
    """Store a token response into _AUTH and persist the (possibly rotated)
    refresh token + identity."""
    claims = sso_core.decode_jwt_payload(tok["access_token"])
    _AUTH.update({
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", _AUTH.get("refresh_token")),
        "expires_at": time.time() + int(tok.get("expires_in", 1200)),
        "character_id": claims["character_id"],
        "name": claims.get("name"),
        "scopes": claims.get("scopes", []),
    })
    sso_core.save_tokens(CACHE_DIR, {
        "refresh_token": _AUTH["refresh_token"],
        "character_id": _AUTH["character_id"],
        "name": _AUTH["name"],
        "scopes": _AUTH["scopes"],
    })


def _refresh_skill_profile():
    """Pull the character's skills and cache the {skill_id: level} profile so the
    Industry planner can use real per-skill levels. Best-effort."""
    global _CHAR_SKILL_PROFILE
    try:
        skills = sso_core.fetch_skills(_access_token(), _AUTH["character_id"], SESSION)
        _CHAR_SKILL_PROFILE = sso_core.skill_profile_from_skills(skills)
    except (LPError, requests.RequestException):
        _CHAR_SKILL_PROFILE = {}


def _refresh_char_blueprints():
    """Pull the character's owned blueprints and cache each type's best ME/TE,
    so the Industry planner's "My blueprints" override can use them. Best-
    effort — a 403 (scope granted before this feature existed) just leaves the
    cache empty and the planner falls back to the uniform ME/TE assumption."""
    global _CHAR_BP_ME_TE
    try:
        bps = sso_core.fetch_character_blueprints(_access_token(), _AUTH["character_id"], SESSION)
        _CHAR_BP_ME_TE = sso_core.owned_blueprint_lookup(bps)
    except (LPError, requests.RequestException):
        _CHAR_BP_ME_TE = {}


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
    return {
        "logged_in": bool(_AUTH.get("refresh_token")),
        "character_id": _AUTH.get("character_id"),
        "name": _AUTH.get("name"),
        "scopes": _AUTH.get("scopes", []),
    }


def do_auth_logout(q):
    sso_core.clear_tokens(CACHE_DIR)
    _AUTH.clear()
    _PKCE.clear()
    global _CHAR_SKILL_PROFILE, _CHAR_BP_ME_TE
    _CHAR_SKILL_PROFILE = {}
    _CHAR_BP_ME_TE = {}
    return {"ok": True}


def _track_delivered_jobs(cid, jobs, names):
    """Cumulative counter of runs/jobs the character has *delivered*, persisted
    across restarts. Only counts jobs newly seen as "delivered" — the first time
    a character is observed, its already-delivered jobs (ESI's 90-day completed
    window) are recorded as a baseline but not counted, so the counter only grows
    from the moment this feature started watching, as advertised to the user."""
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
            continue   # baseline — don't count history from before tracking started
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


def do_char_data(q):
    """Bundle of the logged-in character's wallet, SP, skill queue, loyalty points
    and running industry jobs (with resolved names + timers)."""
    token = _access_token()
    cid = _AUTH["character_id"]
    _refresh_skill_profile()
    _refresh_char_blueprints()

    wallet = sso_core.fetch_wallet(token, cid, SESSION)
    skills = sso_core.fetch_skills(token, cid, SESSION)
    queue = sso_core.fetch_skillqueue(token, cid, SESSION)
    loyalty = sso_core.fetch_loyalty_points(token, cid, SESSION)
    # include_completed so delivered jobs (last 90 days) surface for the runs-
    # delivered counter; the active-jobs table below filters them back out.
    jobs = sso_core.fetch_industry_jobs(token, cid, SESSION, include_completed=True)
    # Orders needs a scope (esi-markets.read_character_orders.v1) added after
    # earlier logins, and that scope also has to be enabled for the user's own
    # registered EVE app on developers.eveonline.com — if either is missing
    # ESI 403s. Isolate that failure so the rest of the character tab (wallet,
    # skills, jobs, LP) still loads instead of the whole bundle 500ing.
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

    # Resolve every type/skill name referenced by jobs, the skill queue and
    # open orders in one call.
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
            continue   # delivered/cancelled/reverted — not a running job any more
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
    now = time.time()
    for qd in queue:
        fin = qd.get("finish_date")
        queue_out.append({
            "skill_id": qd.get("skill_id"),
            "skill_name": names.get(qd.get("skill_id"), "?"),
            "finished_level": qd.get("finished_level"),
            "finish_date": fin,
        })

    # Jita best-sell, just as a comparison reference for your own listings —
    # not necessarily the station/region the order is actually sitting in.
    order_prices = (fetch_prices({o["type_id"] for o in orders if o.get("type_id")}, SESSION)
                    if orders else {})

    orders_out = []
    for o in orders:
        # Your exact place in the order-matching queue: needs the live order
        # book at this order's own station, not just the Jita aggregate above.
        # Best-effort -- a player structure or a transient ESI hiccup just
        # leaves this unknown rather than failing the whole bundle.
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
        })

    # Trade skills for auto-calculating tax/broker
    accounting_lvl = _CHAR_SKILL_PROFILE.get(16622, 0)
    broker_rel_lvl = _CHAR_SKILL_PROFILE.get(3446, 0)

    return {
        "name": _AUTH.get("name"),
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
    min_profit = q.get("min_profit", [""])[0].strip()
    min_profit = float(min_profit) if min_profit else None

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
    if min_profit is not None:
        sellable = [r for r in sellable
                    if r["profit_best"] is not None and r["profit_best"] >= min_profit]
    if max_spread is not None:
        sellable = [r for r in sellable
                    if r["spread_pct"] is not None and r["spread_pct"] <= max_spread]

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
    character_id = _AUTH.get("character_id")
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
    # Real per-character data always wins over the uniform assumption below,
    # for whatever it actually covers — no opt-in needed. It falls back to the
    # uniform ME/TE/skill assumption for blueprints you don't own / skills you
    # haven't trained, or when logged out entirely.
    if _CHAR_SKILL_PROFILE:
        params["skill_profile"] = _CHAR_SKILL_PROFILE
    if _CHAR_BP_ME_TE:
        params["owned_me_te"] = _CHAR_BP_ME_TE

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
            bp_ids = set(_CHAR_BP_ME_TE.keys()) | fav_ids
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
    # Real per-character data always wins over the uniform assumption, when
    # it's available — see do_ind_scan.
    if _CHAR_SKILL_PROFILE:
        params["skill_profile"] = _CHAR_SKILL_PROFILE
    owned_me_te = _CHAR_BP_ME_TE.get(blueprint_id)
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
        load the skill profile, then bounce back to the app."""
        if "error" in q:
            self._send_html(f"<h2>EVE login failed</h2><p>{q['error'][0]}</p>"
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
            _apply_token(tok)
            _refresh_skill_profile()
            _refresh_char_blueprints()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._send_html(f"<h2>EVE login failed</h2><p>{type(e).__name__}: {e}</p>"
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
                # The SPA renders the right module client-side from the path, so
                # every tab URL (and a deep-link refresh) must serve the shell.
                self._send_html(INDEX_HTML)
            elif parsed.path == "/favicon.ico":
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(_FAVICON_SVG)))
                self.end_headers()
                self.wfile.write(_FAVICON_SVG)
            elif parsed.path == "/api/corps":
                self._send_json(get_npc_corps())
            elif parsed.path == "/api/settings":
                character_id = _AUTH.get("character_id")
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
            elif parsed.path == "/api/settings/sync":
                self._send_json(do_settings_sync(q))
            elif parsed.path == "/api/prefs":
                self._send_json(do_prefs(q))
            elif parsed.path == "/api/last-scan":
                lp_data = load_json(LP_LAST_SCAN_PATH, None)
                ind_data = load_json(IND_LAST_SCAN_PATH, None)
                self._send_json({"lp": lp_data, "ind": ind_data})
            elif parsed.path == "/api/scan":
                self._send_json(do_scan(q))
            elif parsed.path == "/api/liquidity":
                self._send_json(do_liquidity(q))
            elif parsed.path == "/api/detail":
                self._send_json(do_detail(q))
            elif parsed.path == "/api/history":
                self._send_json(do_history(q))
            elif parsed.path == "/api/arb/prefs":
                self._send_json(do_arb_prefs(q))
            elif parsed.path == "/api/arb/scan":
                self._handle_arb_scan(q)
            elif parsed.path == "/api/ind/prefs":
                self._send_json(do_ind_prefs(q))
            elif parsed.path == "/api/ind/groups":
                self._send_json(do_ind_groups(q))
            elif parsed.path == "/api/ind/scan":
                self._handle_sse_scan(q, do_ind_scan, "ind")
            elif parsed.path == "/api/ind/liquidity":
                self._send_json(do_ind_liquidity(q))
            elif parsed.path == "/api/ind/detail":
                self._send_json(do_ind_detail(q))
            elif parsed.path == "/api/ind/bpo-search":
                self._send_json(do_ind_bpo_search(q))
            elif parsed.path == "/api/auth/config":
                self._send_json(do_auth_config(q))
            elif parsed.path == "/api/auth/login":
                self._send_json(do_auth_login(q))
            elif parsed.path == "/api/auth/status":
                self._send_json(do_auth_status(q))
            elif parsed.path == "/api/auth/logout":
                self._send_json(do_auth_logout(q))
            elif parsed.path == "/api/char/data":
                self._send_json(do_char_data(q))
            elif parsed.path == "/callback":
                self._handle_callback(q)
            else:
                self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass   # client navigated away / closed the tab mid-request — nothing to send
        except LPError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass   # client is already gone — the error response has nowhere to go

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if parsed.path == "/api/save-scan":
                data = json.loads(body) if body else {}
                tab = data.get("tab", "")
                blob = data.get("blob")
                if tab == "lp" and blob:
                    save_json(LP_LAST_SCAN_PATH, blob)
                elif tab == "ind" and blob:
                    save_json(IND_LAST_SCAN_PATH, blob)
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

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EVE Market Tools</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,__FAVICON__">
<style>
  :root {
    --bg:#080d11; --panel:#0f1923; --panel2:#162130; --panel3:#1c2a3a;
    --line:#1f3044; --line2:#2a3f55;
    --fg:#c8d8e8; --dim:#5a7a95; --dim2:#3d5a70;
    --cyan:#4fc3f7; --cyan2:#29b6f6; --green:#4caf76; --green2:#66bb6a;
    --yellow:#f0c040; --red:#e05555; --accent:#1e5799;
    --accent2:#2471c8; --gold:#c8a040;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg);
    font:15px/1.5 "Segoe UI",system-ui,sans-serif; height:100vh; overflow:hidden; }
  /* Dark themed scrollbars everywhere, not just the default OS chrome. */
  * { scrollbar-width:thin; scrollbar-color:var(--line2) transparent; }
  *::-webkit-scrollbar { width:9px; height:9px; }
  *::-webkit-scrollbar-track { background:transparent; }
  *::-webkit-scrollbar-thumb { background:var(--line2); border-radius:5px; }
  *::-webkit-scrollbar-thumb:hover { background:var(--cyan2); }
  a { color:var(--cyan); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .hidden { display:none !important; }
  /* Inline spinner shown in saturation cells while the background fetch runs. */
  .spin { display:inline-block; width:11px; height:11px; vertical-align:-1px;
    border:2px solid var(--dim2); border-top-color:var(--cyan);
    border-radius:50%; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* ── Initial loading placeholder ─────────────────────────────────── */
  .init-loading {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    gap:12px; padding-top:min(18vh, 140px); color:var(--dim); font-size:13px;
    animation:initFadeIn .3s ease .15s both;
  }
  @keyframes initFadeIn { from { opacity:0; } to { opacity:1; } }
  .init-loading .init-spinner {
    width:22px; height:22px;
    border:2.5px solid var(--line2); border-top-color:var(--cyan);
    border-radius:50%; animation:spin .8s linear infinite;
  }

  /* ── Custom tooltip (replaces native title=) ─────────────────────── */
  #tooltip {
    position:fixed; z-index:9999; max-width:280px;
    padding:8px 11px;
    background:linear-gradient(180deg, var(--panel3) 0%, var(--panel2) 100%);
    border:1px solid var(--line2); border-radius:7px;
    color:var(--fg); font-size:12.5px; line-height:1.45; letter-spacing:.1px;
    box-shadow:0 8px 26px rgba(0,0,0,.55);
    pointer-events:none; opacity:0; transform:translateY(3px);
    transition:opacity .11s ease, transform .11s ease;
  }
  #tooltip.show { opacity:1; transform:translateY(0); }
  #tooltip b, #tooltip .k { color:var(--cyan); font-weight:600; }
  [data-tip] { cursor:help; }
  th[data-tip], button[data-tip], label[data-tip] { cursor:pointer; }

  /* ── Top bar ─────────────────────────────────────────────────────── */
  header {
    padding:0 18px;
    height:46px;
    border-bottom:1px solid var(--line);
    display:flex; gap:0; align-items:center;
    background:linear-gradient(180deg, #0f1f30 0%, var(--panel) 100%);
    box-shadow:0 2px 12px rgba(0,0,0,.5);
    flex-shrink:0;
  }
  .logo {
    font-size:17px; font-weight:700; color:var(--cyan); letter-spacing:.5px;
    white-space:nowrap; text-shadow:0 0 18px rgba(79,195,247,.35);
    padding-right:16px; margin-right:8px;
    border-right:1px solid var(--line2);
  }
  .logo span { color:var(--gold); }
  .logo .ver { font-size:10px; font-weight:400; color:var(--dim2);
    letter-spacing:.5px; margin-left:6px; vertical-align:middle; }
  .tabs { display:flex; gap:0; }
  .tab {
    background:transparent; border:none; border-bottom:2px solid transparent;
    color:var(--dim); font:inherit; font-size:14px; font-weight:600;
    padding:0 18px; height:46px; cursor:pointer;
    transition:color .12s, border-color .12s;
  }
  .tab:hover { color:var(--fg); }
  .tab.active { color:var(--cyan); border-bottom-color:var(--cyan2); }

  /* ── EVE login (header right) ─────────────────────────────────────── */
  #auth-region { margin-left:auto; display:flex; align-items:center; gap:8px; position:relative; }
  .auth-btn {
    background:var(--panel3); border:1px solid var(--line2); color:var(--fg);
    font:inherit; font-size:13px; font-weight:600; border-radius:4px;
    padding:5px 12px; cursor:pointer; white-space:nowrap;
  }
  .auth-btn:hover { border-color:var(--cyan2); color:var(--cyan); }
  .auth-btn.primary-btn { background:var(--accent); border-color:var(--accent2); color:#fff; }
  .auth-btn.primary-btn:hover { background:var(--accent2); color:#fff; }
  .auth-cog {
    background:transparent; border:1px solid var(--line2); color:var(--dim);
    font-size:13px; line-height:1; border-radius:4px; width:26px; height:26px;
    cursor:pointer; flex-shrink:0;
  }
  .auth-cog:hover { color:var(--cyan); border-color:var(--cyan2); }
  #char-chip { display:flex; align-items:center; gap:8px;
    background:var(--panel3); border:1px solid var(--line2); border-radius:4px;
    padding:3px 4px 3px 11px; cursor:pointer; }
  #char-chip:hover { border-color:var(--cyan2); }
  #chip-name { color:var(--cyan); font-weight:700; font-size:13px; white-space:nowrap; }
  .chip-wallet { color:var(--gold); font-size:12px; font-variant-numeric:tabular-nums; }
  #char-refresh-timer { position:absolute; top:100%; right:0; margin-top:4px;
    font-size:11px; color:var(--dim2); font-variant-numeric:tabular-nums; white-space:nowrap; }
  #auth-cfg-pop {
    position:absolute; top:50px; right:16px; z-index:60; width:380px;
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:14px 16px 16px; box-shadow:0 8px 30px rgba(0,0,0,.6);
  }
  #auth-cfg-pop h4 { font-size:13px; color:var(--cyan); margin-bottom:8px; }
  #auth-cfg-pop .cfg-hint { font-size:12px; color:var(--dim); line-height:1.5; margin-bottom:12px; }
  #auth-cfg-pop input { width:100%; background:var(--panel); border:1px solid var(--line2);
    color:var(--fg); font:inherit; font-size:13px; border-radius:4px; padding:6px 8px; }
  #auth-cfg-pop .field-row input { width:auto; flex:1; }
  .cfg-l { font-size:10px; text-transform:uppercase; letter-spacing:.7px;
    color:var(--dim); font-weight:700; margin:10px 0 3px; }
  .cfg-sub { text-transform:none; letter-spacing:0; font-weight:400; color:var(--dim2); }
  .cfg-scopes { font-size:11px; color:var(--dim); font-family:ui-monospace,monospace; line-height:1.6; }
  .lp-mylp { text-transform:none; letter-spacing:0; font-weight:600; color:var(--gold);
    font-size:10px; margin-left:6px; }
  #lp.locked { color:var(--gold); border-color:var(--line2); opacity:.85; cursor:not-allowed; }
  #char-jobs-tbl .timer-cell { color:var(--cyan); font-weight:600; font-variant-numeric:tabular-nums; }
  #char-jobs-tbl .timer-cell.done { color:var(--green2); }

  /* ── Control bar ─────────────────────────────────────────────────── */
  .ctrlbar {
    padding:0 18px 7px; height:56px; flex-shrink:0;
    border-bottom:1px solid var(--line);
    background:var(--panel);
    display:flex; gap:10px; align-items:flex-end; flex-wrap:nowrap; overflow:hidden;
  }
  .field { display:flex; flex-direction:column; gap:1px; }
  .field label { font-size:10px; text-transform:uppercase; letter-spacing:.7px;
    color:var(--dim); font-weight:600; }
  /* An input paired with an inline control (a button / preset group) on one row. */
  .field-row { display:flex; gap:4px; align-items:center; }
  /* The Industry bar has many controls — let it wrap to multiple rows instead of
     being clipped (the shared .ctrlbar is a fixed-height, no-wrap, overflow:hidden).
     Controls are organised into labelled groups separated by a divider. */
  #ind-controls { height:auto; min-height:56px; flex-wrap:wrap; overflow:visible;
    align-items:stretch; row-gap:10px; padding-top:6px; padding-bottom:10px; }
  #ind-controls .ctrl-group {
    display:flex; flex-direction:column; gap:5px;
    padding-right:14px; margin-right:2px; border-right:1px solid var(--line2);
  }
  #ind-controls .ctrl-group:last-child { border-right:none; padding-right:0; }
  .ctrl-cap { font-size:9px; text-transform:uppercase; letter-spacing:1.2px;
    color:var(--cyan); font-weight:700; opacity:.65; }
  .ctrl-fields { display:flex; gap:10px; align-items:flex-end; flex:1; }
  .ctrl-actions .ctrl-fields { gap:8px; }
  input, select {
    background:var(--panel2); border:1px solid var(--line2); color:var(--fg);
    border-radius:4px; padding:4px 8px; font:inherit; font-size:14px;
    transition:border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus {
    outline:none; border-color:var(--cyan2);
    box-shadow:0 0 0 2px rgba(41,182,246,.15);
  }
  input[type=number] { width:90px; }
  input#corp { width:210px; }
  input#arb-minisk { width:110px; }
  .corp-wrap { position:relative; }
  .corp-wrap input { padding-left:28px; width:100%; }
  .corp-icon {
    position:absolute; left:8px; top:50%; transform:translateY(-50%);
    color:var(--dim); font-size:13px; pointer-events:none; user-select:none;
  }
  .search-wrap { position:relative; }
  .search-wrap input { padding-right:22px; }
  .search-clear {
    position:absolute; right:3px; top:50%; transform:translateY(-50%);
    width:16px; height:16px; padding:0; line-height:1;
    background:none; border:none; color:var(--dim); font-size:12px;
    cursor:pointer; border-radius:3px;
  }
  .search-clear:hover { color:var(--fg); background:var(--panel3); }
  .search-clear.hidden { display:none; }
  .corp-drop {
    position:fixed; z-index:200;
    background:var(--panel2); border:1px solid var(--cyan2);
    border-radius:4px;
    box-shadow:0 8px 28px rgba(0,0,0,.6);
    max-height:240px; overflow-y:auto;
  }
  .corp-drop-item {
    padding:7px 12px; cursor:pointer; font-size:14px; color:var(--fg);
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    transition:background .08s;
  }
  .corp-drop-item:hover, .corp-drop-item.hi {
    background:var(--accent); color:#fff;
  }
  .corp-drop-empty {
    padding:8px 12px; font-size:13px; color:var(--dim); font-style:italic;
  }
  .btn-group { display:flex; gap:6px; align-self:flex-end; align-items:center; }
  .check-field {
    display:inline-flex; align-items:center; gap:5px;
    font-size:13px; color:var(--dim); cursor:pointer; white-space:nowrap; user-select:none;
  }
  .check-field:hover { color:var(--fg); }
  .check-field input[type=checkbox] {
    accent-color:var(--cyan2); width:14px; height:14px; cursor:pointer; flex-shrink:0;
  }
  button {
    border:none; border-radius:4px; cursor:pointer; font:inherit; font-size:14px;
    font-weight:600; padding:5px 14px; transition:filter .12s, background .12s;
    white-space:nowrap;
  }
  button.primary {
    background:linear-gradient(180deg,#2080d0 0%,#1560a8 100%);
    color:#fff; box-shadow:0 1px 4px rgba(0,0,0,.4);
  }
  button.primary:hover { filter:brightness(1.15); }
  button.primary:disabled { filter:brightness(.6); cursor:default; }
  button.secondary {
    background:var(--panel2); border:1px solid var(--line2);
    color:var(--dim); font-weight:500;
  }
  button.secondary:hover { border-color:var(--cyan2); color:var(--fg); }
  /* Tradeability balance presets (segmented control). */
  .balance-group { display:inline-flex; align-items:center; gap:0; white-space:nowrap; }
  .balance-label { font-size:13px; color:var(--dim); margin-right:7px; }
  .balance-btn {
    background:var(--panel2); border:1px solid var(--line2); border-left-width:0;
    color:var(--dim); font-weight:500; font-size:13px; padding:5px 11px; border-radius:0;
  }
  .balance-btn:first-of-type { border-left-width:1px; border-radius:4px 0 0 4px; }
  .balance-btn:last-of-type { border-radius:0 4px 4px 0; }
  .balance-btn:hover { color:var(--fg); }
  .balance-btn.on { background:var(--accent); color:#fff; border-color:var(--accent2); }
  .ind-balance-btn {
    background:var(--panel2); border:1px solid var(--line2); border-left-width:0;
    color:var(--dim); font-weight:500; font-size:13px; padding:5px 11px; border-radius:0; cursor:pointer;
  }
  .ind-balance-btn:first-of-type { border-left-width:1px; border-radius:4px 0 0 4px; }
  .ind-balance-btn:last-of-type { border-radius:0 4px 4px 0; }
  .ind-balance-btn:hover { color:var(--fg); }
  .ind-balance-btn.on { background:var(--accent); color:#fff; border-color:var(--accent2); }
  .ind-group-sub { display:block; font-size:10px; color:var(--dim2); line-height:1.2; margin-top:1px; }

  .global-costs {
    height:auto; min-height:0; padding:5px 18px; gap:14px;
    align-items:center; flex-wrap:nowrap; border-bottom:1px solid var(--line);
    background:var(--bg);
  }
  .global-costs .field { flex-direction:row; align-items:center; gap:6px; }
  .global-costs .field label { font-size:10px; margin:0; }
  .global-costs input { width:55px; font-size:12px; padding:3px 6px;
    background:var(--panel); border:1px solid var(--line2); border-radius:4px; color:var(--fg); }
  .global-costs input[readonly] { color:var(--dim); background:var(--bg); cursor:default; }
  #ind-jobrate[readonly] { color:var(--dim); background:var(--bg); cursor:default; border-style:dashed; }

  /* ── Status bar ──────────────────────────────────────────────────── */
  #statusbar {
    padding:4px 18px; font-size:13px; min-height:27px; flex-shrink:0;
    background:var(--panel); border-bottom:1px solid var(--line);
    display:flex; align-items:center; gap:8px; color:var(--fg);
  }
  #statusbar.err { color:var(--red); }
  #statusbar .ts { color:var(--dim); font-size:11px; margin-left:4px; }
  #statusbar .pill {
    display:inline-flex; align-items:center; gap:5px;
    background:var(--panel3); border:1px solid var(--line2);
    border-radius:20px; padding:1px 10px; font-size:12px; color:var(--dim);
  }
  #statusbar .pill b { color:var(--fg); font-weight:600; }

  /* ── Layout ──────────────────────────────────────────────────────── */
  main { display:flex; height:calc(100vh - 163px); overflow:hidden; }
  .tablewrap { flex:1; overflow:auto; min-width:0; }

  /* ── Tables ──────────────────────────────────────────────────────── */
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:14px; }
  th, td { padding:7px 12px; text-align:right; white-space:nowrap;
    border-bottom:1px solid var(--line); }
  th:first-child, td:first-child { text-align:left; padding-left:16px; }
  td:last-child, th:last-child { padding-right:16px; }
  th {
    position:sticky; top:0; z-index:2;
    background:linear-gradient(180deg,#132030 0%,#0f1923 100%);
    color:var(--dim); font-size:11px; text-transform:uppercase;
    letter-spacing:.6px; font-weight:700; cursor:pointer; user-select:none;
    border-bottom:2px solid var(--line2);
  }
  th:hover { color:var(--cyan); }
  th.sorted { color:var(--cyan2); }
  .resizer {
    position:absolute; top:0; right:0; width:12px; height:100%;
    cursor:col-resize; z-index:3;
  }
  .resizer::after {
    content:""; position:absolute;
    top:18%; right:3px; width:2px; height:64%;
    background:var(--line2); border-radius:1px; pointer-events:none;
    transition:background .12s, width .12s;
  }
  .resizer:hover::after, .resizer.active::after { background:var(--cyan); width:3px; }
  body.col-resizing { cursor:col-resize; user-select:none; }
  /* Column drag-to-reorder. box-shadow markers avoid any layout shift. */
  th[draggable=true] { cursor:grab; }
  th.col-dragging { opacity:.45; cursor:grabbing; }
  th.drop-before { box-shadow: inset 3px 0 0 var(--cyan2); }
  th.drop-after  { box-shadow: inset -3px 0 0 var(--cyan2); }
  body.col-dragging-active { cursor:grabbing; }

  /* LP table */
  #tbl th, #tbl td { overflow:hidden; text-overflow:ellipsis; }
  #tbl td:first-child, #tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  #tbl tbody tr { cursor:pointer; transition:background .08s; }
  #tbl tbody tr:hover { background:var(--panel2); }
  #tbl tbody tr.sel { background:rgba(32,113,196,.18); border-left:3px solid var(--cyan2); }
  #tbl tbody tr.sel td:first-child { padding-left:13px; }
  #tbl tbody tr.illiquid { opacity:.75; }
  #tbl tbody tr.illiquid td.spread { color:var(--red); }
  #tbl tbody tr.unsellable { opacity:.55; font-style:italic; }
  #tbl tbody tr.unaffordable td { color:var(--dim2); }

  /* ARB table */
  #arb-tbl th { position:sticky; }
  #arb-tbl th, #arb-tbl td { overflow:hidden; text-overflow:ellipsis; }
  #arb-tbl td:first-child, #arb-tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  #arb-tbl tbody tr { transition:background .08s; }
  #arb-tbl tbody tr:hover { background:var(--panel2); }
  td.sec-high  { color:var(--green2); font-weight:500; }
  td.sec-low   { color:var(--yellow); font-weight:500; }
  td.sec-null  { color:var(--red);    font-weight:500; }
  td.sec-unknown { color:var(--dim); }
  td.risk-high  { color:var(--green2); font-weight:600; }
  td.risk-low   { color:var(--yellow); font-weight:600; }
  td.risk-null  { color:var(--red);    font-weight:600; }
  td.risk-unknown { color:var(--dim); }

  td.pos { color:var(--green2); font-weight:500; }
  td.neg { color:var(--red); }
  /* The better of the two sell-mode columns (list vs instant sell). */
  td.win { background:rgba(79,195,247,.10); box-shadow:inset 2px 0 0 var(--cyan2); font-weight:700; }
  td.spread.tight { color:var(--green); }
  td.spread.mid { color:var(--yellow); }
  .flag { color:var(--red); font-weight:700; font-size:12px; margin-left:2px; }

  /* ── Dedicated Recipe List Section ───────────────────────────────── */
  .recipe-list {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:4px 14px; margin-bottom:14px;
  }
  .recipe-list-item {
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 0; border-bottom:1px solid var(--line); font-size:13px;
  }
  .recipe-list-item:last-child { border-bottom:none; }
  .recipe-list-item .name { color:var(--dim); }
  .recipe-list-item .val { color:var(--fg); font-weight:600; }
  .recipe-list-item .val.lp { color:#81d4fa; }
  .recipe-list-item .val.isk { color:#a5d6a7; }
  #detail {
    flex-shrink:0; width:0; overflow:hidden;
    transition:width .18s cubic-bezier(.4,0,.2,1);
    background:var(--panel);
  }
  #detail.open { width:580px; border-left:1px solid var(--line2);
    box-shadow:-16px 0 40px rgba(0,0,0,.6); }
  #detail .inner { width:580px; max-width:96vw; padding:20px 22px;
    overflow-y:auto; overflow-x:hidden; height:100%; }
  #detail .dheader { display:flex; align-items:flex-start; justify-content:space-between;
    margin-bottom:4px; }
  #detail h2 { font-size:20px; color:var(--cyan); font-weight:700; line-height:1.2;
    text-shadow:0 0 20px rgba(79,195,247,.2); }
  .lp-copy { margin-left:8px; padding:1px 8px; font-size:11px; cursor:pointer;
    background:var(--panel); border:1px solid var(--line2); border-radius:4px; color:var(--cyan);
    vertical-align:middle; }
  .lp-copy:hover { border-color:var(--cyan2); }
  #detail .sub { color:var(--dim); font-size:12px; margin-bottom:14px; }
  .close { cursor:pointer; color:var(--dim); font-size:20px; line-height:1;
    padding:2px 4px; border-radius:3px; flex-shrink:0; }
  .close:hover { color:var(--fg); background:var(--panel3); }
  .redrow { display:flex; align-items:center; gap:10px; margin:14px 0 4px;
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:8px 12px; }
  .redrow label { color:var(--dim); font-size:13px; white-space:nowrap; }
  .redrow input { width:90px; font-size:15px; font-weight:600; }
  .redrow .maxlink { font-size:12px; color:var(--dim); }
  .redrow .maxlink + .maxlink::before { content:"·"; margin-right:10px; color:var(--dim2); }
  .kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin:12px 0; }
  .kpi {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:7px 10px; position:relative; overflow:hidden;
  }
  .kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:var(--line2); }
  .kpi.accent::before { background:linear-gradient(90deg,var(--cyan2),transparent); }
  .kpi .l { font-size:9px; text-transform:uppercase; letter-spacing:.5px;
    color:var(--dim); font-weight:700; }
  .kpi .v { font-size:16px; font-weight:700; margin-top:2px; }
  .v.pos { color:var(--green2); } .v.neg { color:var(--red); }
  h3 {
    font-size:11px; text-transform:uppercase; letter-spacing:.7px; font-weight:700;
    color:var(--dim); border-bottom:1px solid var(--line); padding-bottom:5px;
    margin:18px 0 8px;
  }

  /* ── Character tab ───────────────────────────────────────────────── */
  #char-tablewrap { padding:18px; }
  .char-empty { max-width:480px; margin:60px auto; text-align:center; color:var(--dim);
    line-height:1.6; display:flex; flex-direction:column; gap:16px; align-items:center; }
  .char-kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
    gap:12px; margin-bottom:18px; }
  .char-kpi { background:var(--panel2); border:1px solid var(--line); border-radius:6px;
    padding:10px 14px; position:relative; overflow:hidden; }
  .char-kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg,var(--cyan2),transparent); }
  .char-kpi .l { font-size:9px; text-transform:uppercase; letter-spacing:.5px;
    color:var(--dim); font-weight:700; }
  .char-kpi .v { font-size:20px; font-weight:700; margin-top:3px; }
  .char-kpi .v.gold { color:var(--gold); font-variant-numeric:tabular-nums; }
  .char-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; }
  .char-card { background:var(--panel2); border:1px solid var(--line); border-radius:6px;
    padding:4px 16px 14px; }
  .char-card h3 { margin-top:14px; }
  .char-card-wide { grid-column:1/-1; }
  .char-card-sub { color:var(--dim); font-size:11px; font-weight:400; margin-left:6px; }
  /* Caps each card's table at a fixed height so one long list (e.g. a deep
     skill queue) can't stretch the whole grid row and bury the cards next to
     it — scroll inside the card instead. */
  .char-card-scroll { max-height:260px; overflow-y:auto; }
  .char-card-scroll table.mini thead th { position:sticky; top:0; background:var(--panel2); z-index:1; }
  .char-none { color:var(--dim); font-size:13px; padding:10px 4px; }
  .char-none.char-none-warn { color:var(--gold); }
  #char-orders-tbl td.tx-buy { color:var(--red); font-weight:600; }
  #char-orders-tbl td.tx-sell { color:var(--green2); font-weight:600; }
  #char-jobs-tbl td.tl, #char-jobs-tbl th:last-child { text-align:right;
    font-variant-numeric:tabular-nums; }
  /* Give the product name a real minimum so auto layout can't crush it down
     to one character per line — word-break:break-word otherwise lets the
     browser treat its minimum width as a single glyph while "Manufacturing"
     and the timer take all the space. The numeric/status columns hug their
     content; the product column keeps the rest. */
  #char-jobs-tbl th:first-child, #char-jobs-tbl td:first-child { min-width:8em; }
  #char-jobs-tbl th:nth-child(2), #char-jobs-tbl td:nth-child(2),
  #char-jobs-tbl th:nth-child(3), #char-jobs-tbl td:nth-child(3),
  #char-jobs-tbl th:nth-child(4), #char-jobs-tbl td:nth-child(4) { white-space:nowrap; }
  table.mini { font-size:13px; width:100%; border-collapse:collapse; }
  table.mini th { position:static; background:none; color:var(--dim);
    font-size:10px; letter-spacing:.5px; border-bottom:1px solid var(--line); padding:4px 8px; }
  table.mini td { padding:6px 8px; border-bottom:1px solid var(--line);
    color:var(--fg); vertical-align:top; }
  table.mini th:first-child, table.mini td:first-child { text-align:left;
    white-space:normal; word-break:break-word; }
  table.mini tr:last-child td { border-bottom:none; }
  table.mini tr:hover td { background:var(--panel2); }
  table.mini .total td { font-weight:700; border-top:1px solid var(--line2);
    background:var(--panel3); }
  table.mini .subtotal td { font-weight:600; border-top:1px solid var(--line);
    color:var(--fg); }
  .note {
    display:flex; align-items:flex-start; gap:7px;
    background:rgba(240,192,64,.07); border:1px solid rgba(240,192,64,.25);
    border-radius:5px; padding:8px 10px; color:var(--yellow); font-size:13px;
    margin:6px 0;
  }
  .note::before { content:"⚠"; flex-shrink:0; }
  .note.bad { background:rgba(224,85,85,.08); border-color:rgba(224,85,85,.3);
    color:var(--red); }
  .note.bad::before { content:"✕"; }
  .muted { color:var(--dim); font-size:12px; line-height:1.5; margin-top:10px; }

  /* ── Arb progress overlay ────────────────────────────────────────── */
  #arb-progress {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    height:100%; gap:10px; padding:24px;
  }
  .prog-label { font-size:15px; font-weight:600; color:var(--fg); text-align:center; }
  .prog-track {
    width:340px; max-width:90vw; height:6px;
    background:var(--line2); border-radius:3px; overflow:hidden;
  }
  .prog-fill {
    height:100%; width:0%;
    background:linear-gradient(90deg, var(--accent2), var(--cyan2));
    border-radius:3px; transition:width .35s ease;
  }
  .prog-sub { font-size:12px; color:var(--dim); text-align:center; min-height:16px; }

  /* ── Lot tracker ─────────────────────────────────────────────────── */
  .lot-tracker { display:flex; flex-direction:column; gap:5px; }
  .lot-row {
    background:var(--panel2); border:1px solid var(--line2); border-radius:5px;
    padding:7px 10px;
  }
  .lot-label { font-size:12px; color:var(--dim); margin-bottom:5px; }
  .lot-label .lot-need { color:var(--fg); font-weight:600; }
  .lot-controls { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .lot-tags { display:flex; flex-wrap:wrap; gap:3px; }
  .lot-tag {
    background:var(--panel3); border:1px solid var(--line2); border-radius:3px;
    padding:1px 7px; font-size:12px; display:inline-flex; align-items:center; gap:5px;
  }
  .lot-tag .rm { cursor:pointer; color:var(--dim); font-size:10px; line-height:1; }
  .lot-tag .rm:hover { color:var(--red); }
  .lot-num { width:70px; font-size:13px; padding:3px 6px; }
  .lot-sum { font-size:13px; font-weight:600; }

  /* ── Column picker ───────────────────────────────────────────────── */
  .col-picker {
    position:fixed; z-index:300;
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:6px 0; box-shadow:0 6px 24px rgba(0,0,0,.55); min-width:170px;
  }
  .col-picker.hidden { display:none; }
  .col-picker label {
    display:flex; align-items:center; gap:8px;
    padding:5px 14px; cursor:pointer; font-size:13px; user-select:none;
  }
  .col-picker label:hover { background:var(--panel3); }
  .col-picker input[type=checkbox] { margin:0; accent-color:var(--cyan2); }

  /* ── Price history chart ─────────────────────────────────────────── */
  .chart-wrap { position:relative; width:100%; height:160px; margin:8px 0 4px; }
  .chart-canvas { width:100%; height:100%; display:block; border-radius:4px;
    cursor:crosshair; background:var(--panel2); }
  .chart-tip {
    position:absolute; pointer-events:none; display:none;
    background:rgba(8,13,17,.96); border:1px solid var(--line2);
    border-radius:4px; padding:5px 9px; font-size:11px; white-space:nowrap;
    z-index:10; color:var(--fg);
  }
  .chart-stats {
    font-size:11px; color:var(--dim); margin-bottom:6px;
    display:flex; flex-wrap:wrap; gap:5px;
  }
  .chart-stats span { background:var(--panel3); border:1px solid var(--line2);
    border-radius:4px; padding:2px 8px; display:inline-flex; align-items:baseline;
    gap:5px; cursor:help; }
  .chart-stats .k { text-transform:uppercase; font-size:9px; letter-spacing:.4px;
    color:var(--dim); }
  .chart-stats .v { color:var(--fg); font-weight:600; }
  .chart-stats .d { font-weight:600; }
  .chart-cross {
    position:absolute; top:0; bottom:20px; width:1px;
    background:rgba(200,216,232,.3); pointer-events:none; display:none;
  }
  .chart-expand-btn {
    position:absolute; top:4px; right:4px; z-index:5;
    background:rgba(8,13,17,.78); border:1px solid var(--line2);
    color:var(--dim); font-size:13px; padding:1px 6px; line-height:1.5;
    border-radius:3px; cursor:pointer;
  }
  .chart-expand-btn:hover { color:var(--fg); border-color:var(--cyan2); }
  /* Expand chart modal */
  #chartExpandModal {
    position:fixed; inset:0; z-index:600; background:rgba(0,0,0,.78);
    display:flex; align-items:center; justify-content:center;
  }
  #chartExpandModal.hidden { display:none; }
  .chart-expand-box {
    background:var(--panel2); border:1px solid var(--line2); border-radius:8px;
    padding:20px 22px; width:880px; max-width:97vw;
    box-shadow:0 20px 60px rgba(0,0,0,.7);
  }
  .chart-expand-head {
    display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
  }
  .chart-expand-head h3 { font-size:16px; font-weight:700; color:var(--cyan); margin:0; }
  /* ARB chart modal */
  #arbChartModal {
    position:fixed; inset:0; z-index:500; background:rgba(0,0,0,.72);
    display:flex; align-items:center; justify-content:center;
  }
  #arbChartModal.hidden { display:none; }
  .arb-chart-box {
    background:var(--panel2); border:1px solid var(--line2); border-radius:8px;
    padding:20px 22px; width:620px; max-width:95vw;
    box-shadow:0 20px 60px rgba(0,0,0,.7);
  }
  .arb-chart-head {
    display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
  }
  .arb-chart-head h3 { font-size:16px; font-weight:700; color:var(--cyan); margin:0; }

  /* ── Industry ────────────────────────────────────────────────────── */
  .ind-d-runs-wrap { display:inline-flex; align-items:center; gap:4px; font-size:12px; }
  .ind-d-runs-wrap input { font-size:12px; border:1px solid var(--line2); background:var(--panel); color:var(--fg); border-radius:4px; padding:3px 6px; }
  .ind-d-runs-wrap button { font-size:11px; padding:3px 8px; cursor:pointer; border:1px solid var(--line2); background:var(--panel2); color:var(--dim); border-radius:4px; transition:background .12s, color .12s; }
  .ind-d-runs-wrap button:hover { background:var(--cyan2); color:var(--fg); border-color:var(--cyan2); }
  .ind-bpc-warn { background:#3a2800; border:1px solid #b8860b; border-radius:6px; padding:8px 12px; margin-bottom:10px; color:#ffd080; font-size:13px; line-height:1.5; width:100%; }
  .ind-bpc-warn b { color:#ffe4a0; }
  .ind-bpc-warn .ind-bpc-buy { display:block; margin-top:6px; color:#fff; font-weight:700; font-size:14px; }
  .ind-bpo-expand { background:#4a3000; border:1px solid #b8860b; border-radius:4px; color:#ffd080; cursor:pointer; padding:2px 10px; font-size:12px; margin-left:6px; }
  .ind-bpo-expand:hover { background:#5a3a00; border-color:#daa520; }
  #ind-detail {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:12px 14px; margin-bottom:12px;
  }
  .ind-d-head { font-size:14px; color:var(--fg); margin-bottom:10px; position:relative;
    cursor:pointer; }
  .ind-d-head:hover { color:var(--cyan); }
  .ind-d-head button { cursor:pointer; }
  .ind-d-close { position:absolute; right:0; top:0; cursor:pointer; color:var(--dim); padding:0 4px; }
  .ind-d-close:hover { color:var(--fg); }
  .ind-copy, .ind-own, .ind-pull-prices { margin:0 6px; padding:1px 8px; font-size:11px; cursor:pointer;
    background:var(--panel); border:1px solid var(--line2); border-radius:4px; color:var(--cyan); }
  .ind-copy:hover, .ind-own:hover, .ind-pull-prices:hover { border-color:var(--cyan2); }
  .ind-pull-prices.on { color:#5fb85f; border-color:#5fb85f; }
  /* Body splits the stats grid (left) from the crafting timer (right) so the
     wide empty space beside the two-column grid is put to use. Wraps on narrow. */
  .ind-d-body { display:flex; gap:28px; align-items:flex-start; flex-wrap:wrap;
    margin-bottom:12px; }
  .ind-d-grid {
    display:grid; grid-template-columns:auto auto; gap:3px 18px;
    font-size:12px; max-width:560px;
  }
  .ind-d-side { flex:2 1 480px; min-width:320px; display:flex; flex-direction:column; gap:4px; }
  .ind-d-section { display:flex; flex-direction:column; gap:8px; }
  .ind-d-timer-card { background:var(--panel); border:1px solid var(--line2);
    border-radius:6px; padding:10px 12px; }
  .ind-d-cards { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }
  .ind-d-card { background:var(--panel); border:1px solid var(--line2);
    border-radius:6px; padding:8px 10px; font-size:12px; }
  .ind-d-card-label { color:var(--dim); font-size:10px; font-weight:700;
    text-transform:uppercase; letter-spacing:.6px; margin-bottom:3px; }
  .ind-d-card-val { font-size:15px; font-weight:700; color:var(--fg); }
  .ind-d-card-val.pos { color:var(--green2,#4caf76); }
  .ind-d-card-val.neg { color:var(--red); }
  .ind-d-card-sub { color:var(--dim); font-size:11px; margin-top:1px; }
  .ind-d-card-sub.ind-d-card-warn { color:var(--gold); font-weight:600; }
  .ind-d-card-grid { display:grid; grid-template-columns:auto auto; gap:2px 10px; }
  .ind-d-card-grid span { color:var(--dim); }
  .ind-d-card-grid b { text-align:right; color:var(--fg); font-weight:600; }
  .ind-d-grid span { color:var(--dim); }
  .ind-d-grid b { text-align:right; color:var(--fg); }
  .ind-d-sub { grid-column:1/-1; margin-top:8px; padding-bottom:2px;
    border-bottom:1px solid var(--line2); font-size:10px; font-weight:700;
    text-transform:uppercase; letter-spacing:.8px; color:var(--cyan); opacity:.7; }
  .ind-d-grid .ind-d-sub:first-child { margin-top:0; }
  .ind-d-mats { width:100%; border-collapse:collapse; font-size:12px; }
  .ind-d-mats th, .ind-d-mats td { padding:3px 8px; border-bottom:1px solid var(--line2); }
  .ind-d-mats th { color:var(--dim); text-align:left; font-weight:600; }
  .ind-d-mats td.num, .ind-d-mats th.num { text-align:right; }
  .ind-d-mats tr.ind-d-total td { border-top:2px solid var(--line2); font-weight:700; color:var(--fg); }
  .ind-skills-warn { color:#e06050; }
  .ind-d-skills td { color:#e8b050; }
  .ind-d-skills tr.ind-d-total td { color:#e06050; }
  .ind-prereq { font-size:10px; color:var(--dim); font-style:italic; }
  #ind-tbl th { cursor:pointer; user-select:none; }
  #ind-tbl th[data-nosort] { cursor:default; }
  #ind-tbl th, #ind-tbl td { overflow:hidden; text-overflow:ellipsis; }
  #ind-tbl td:first-child, #ind-tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  /* Highlight the blueprint buy-in price (the thing you must purchase). */
  #ind-tbl td.bp-buy { color:var(--c8a040, #c8a040); font-weight:600; }
  .ind-d-grid b.bp-buy { color:#c8a040; }
  /* Build-location wizard modal */
  .ind-modal { position:fixed; inset:0; background:rgba(0,0,0,.6);
    display:flex; align-items:center; justify-content:center; z-index:50; }
  .ind-modal-box { background:var(--panel); border:1px solid var(--line2);
    border-radius:8px; padding:18px 20px; width:380px; max-width:92vw;
    box-shadow:0 20px 60px rgba(0,0,0,.7); }
  .ind-modal-box h3 { margin:0 0 4px; font-size:16px; color:var(--cyan); }
  .sw-hint { font-size:11px; color:var(--dim); margin:0 0 12px; line-height:1.4; }
  .sw-field { display:flex; align-items:center; justify-content:space-between;
    gap:10px; margin-bottom:8px; font-size:13px; color:var(--fg); }
  .sw-field span small { color:var(--dim); font-weight:400; }
  .sw-field input { width:110px; }
  .sw-eff { margin:12px 0; padding:8px 10px; background:var(--panel2);
    border-radius:5px; font-size:13px; }
  .sw-eff b { color:var(--c8a040,#c8a040); font-size:15px; }
  .sw-formula { display:block; font-size:10px; color:var(--dim); margin-top:2px; }
  .sw-actions { display:flex; gap:8px; align-items:center; margin-top:6px; }
  #ind-tbl td.fav-cell { text-align:center; }
  .fav-star { cursor:pointer; color:var(--dim); font-size:15px; user-select:none; }
  .fav-star:hover { color:var(--c8a040,#c8a040); }
  .fav-star.on { color:var(--c8a040,#c8a040); }
  .ind-fav-btn { margin:0 6px; padding:1px 8px; font-size:11px; cursor:pointer;
    background:var(--panel); border:1px solid var(--line2); border-radius:4px; color:var(--dim); }
  .ind-fav-btn.on { color:var(--c8a040,#c8a040); border-color:var(--c8a040,#c8a040); }
  .ind-timer { display:flex; align-items:center; gap:8px; margin:4px 0 6px; font-size:13px; flex-wrap:wrap; }
  .ind-timer input { width:54px; }
  .ind-timer-remaining { font-weight:700; color:var(--cyan); font-variant-numeric:tabular-nums; }
  .ind-timer.done .ind-timer-remaining { color:var(--green2,#4caf76); }
  .ind-timer-eta { color:var(--dim); font-size:12px; }
  .ind-timer-none { color:var(--dim); font-size:12px; line-height:1.4; }
  #ind-tbl td.timer-cell { text-align:center; font-variant-numeric:tabular-nums;
    color:var(--cyan); font-weight:600; }
  #ind-tbl td.timer-cell.done { color:var(--green2,#4caf76); }
  #ind-tbl tr.ind-section td {
    background:var(--panel2); color:var(--dim);
    font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em;
    padding:6px 8px; border-top:1px solid var(--line2); cursor:pointer; user-select:none;
  }
  #ind-tbl tr.ind-section td:hover { color:var(--fg); }
  #ind-tbl tr.ind-section .sect-arrow { display:inline-block; transition:transform .15s; margin-right:4px; }
  #ind-tbl tr.ind-section.collapsed .sect-arrow { transform:rotate(-90deg); }
  .ind-chips { display:flex; gap:18px; flex-wrap:wrap; margin:4px 0 6px; align-items:baseline;
    padding:0 2px; border-bottom:1px solid var(--line); }
  .ind-chip { font-size:12px; padding:6px 2px 5px; cursor:pointer;
    border:none; border-bottom:2px solid transparent; background:none; color:var(--dim);
    transition:color .12s, border-color .12s; user-select:none;
    display:inline-flex; align-items:center; gap:4px; }
  .ind-chip:hover { color:var(--fg); }
  .ind-chip.active { color:var(--cyan); border-bottom-color:var(--cyan); font-weight:600; }
  .ind-chip .chip-count { opacity:.55; font-weight:400; font-size:11px; }
  .ind-hide-btn { cursor:pointer; color:var(--dim); font-size:11px; padding:0 3px; opacity:.5; transition:opacity .12s; }
  .ind-hide-btn:hover { opacity:1; color:var(--orange,#e8a040); }
</style>
</head>
<body>

<header>
  <div class="logo">EVE <span>MARKET TOOLS</span><span class="ver">v__VERSION__</span></div>
  <nav class="tabs">
    <button class="tab active" data-tab="lp">LP Store</button>
    <button class="tab" data-tab="arb">Arbitrage</button>
    <button class="tab" data-tab="ind">Industry</button>
    <button class="tab hidden" id="char-tab-btn" data-tab="char">Character</button>
  </nav>
  <div id="auth-region">
    <button id="login-eve" class="auth-btn" data-tip="Log in with your EVE Online account to load your skills, wallet, loyalty points and running industry jobs.">Log in with EVE</button>
    <button id="login-cfg" class="auth-cog" title="EVE login settings" aria-label="EVE login settings">&#9881;</button>
    <div id="char-chip" class="hidden" data-tip="Click to open the Character tab. Use the gear to log out.">
      <span id="chip-name">&bull;&bull;&bull;</span>
      <span id="chip-wallet" class="chip-wallet"></span>
      <button id="logout-eve" class="auth-cog" title="Log out of EVE" aria-label="Log out">&#10005;</button>
    </div>
    <div id="char-refresh-timer" class="hidden">Next sync in <span id="char-refresh-secs">5:00</span></div>
  </div>
</header>

<!-- EVE login settings popover -->
<div id="auth-cfg-pop" class="hidden">
  <h4>EVE Online login</h4>
  <p class="cfg-hint">Register an application at
    <a href="https://developers.eveonline.com/applications" target="_blank" rel="noopener">developers.eveonline.com</a>
    (Authentication only). Set its <b>Callback URL</b> to the value below, then paste the <b>Client ID</b> here.</p>
  <label class="cfg-l">Client ID</label>
  <input id="cfg-client-id" type="text" placeholder="your application Client ID" autocomplete="off">
  <label class="cfg-l">Callback URL <span class="cfg-sub">(register this exact value)</span></label>
  <div class="field-row">
    <input id="cfg-callback" type="text" readonly>
    <button id="cfg-copy" class="auth-btn">Copy</button>
  </div>
  <div class="cfg-l" style="margin-top:10px">Scopes requested</div>
  <div id="cfg-scopes" class="cfg-scopes"></div>
  <div class="field-row" style="margin-top:12px; justify-content:flex-end">
    <button id="cfg-close" class="auth-btn">Close</button>
    <button id="cfg-save" class="auth-btn primary-btn">Save</button>
  </div>
</div>

<!-- Global cost settings (shared by LP + Industry) -->
<div id="global-costs" class="ctrlbar global-costs">
  <div class="field" data-tip="Sales tax on sell orders. Based on your Accounting skill when logged in (editable).">
    <label>Sales tax %</label><input id="g-tax" type="number" step="0.1" value="4.5" style="width:65px">
  </div>
  <div class="field" data-tip="Broker fee for placing sell orders. Depends on Broker Relations skill + standings (~1.5% typical at Jita). Edit manually.">
    <label>Broker fee %</label><input id="g-broker" type="number" step="0.1" value="1.5" style="width:65px">
  </div>
</div>

<!-- LP controls -->
<div id="lp-controls" class="ctrlbar">
  <div class="field"><label>Corporation <span id="lp-mylp" class="lp-mylp hidden"></span></label>
    <div class="corp-wrap">
      <span class="corp-icon">⌕</span>
      <input id="corp" placeholder="Search corporation…" autocomplete="off" spellcheck="false">
    </div>
  </div>
  <div class="field"><label>LP budget</label><input id="lp" type="number" value="500000"></div>
  <div class="field"><label>Max spread %</label><input id="maxspread" type="number" placeholder="off" value="20"></div>
  <div class="field"><label>Market</label>
    <select id="market">
      <option value="60003760">Jita 4-4</option>
      <option value="60008494">Amarr 8-20</option>
      <option value="60004588">Rens 6-8</option>
      <option value="60011866">Dodixie 9-20</option>
      <option value="60005686">Hek 8-12</option>
    </select>
  </div>
  <div class="field"><label>Search</label>
    <div class="search-wrap">
      <input id="lp-search" type="text" placeholder="item name…" style="width:140px">
      <button id="lp-search-clear" class="search-clear hidden" title="Clear search" type="button">✕</button>
    </div>
  </div>
  <div class="btn-group">
    <button id="go" class="primary">Scan</button>
    <button id="refresh" class="secondary" data-tip="Re-fetch offers and prices from ESI">⟳ Refresh</button>
    <label class="check-field" data-tip="Show or hide illiquid rows"><input type="checkbox" id="toggleIlliquid"> Hide illiquid !</label>
    <label class="check-field" data-tip="Hide offers you can't afford"><input type="checkbox" id="toggleAffordable"> Hide unaffordable</label>
    <span class="balance-group" data-tip="How the Tradeability score weights liquidity vs competition">
      <span class="balance-label">Tradeability:</span>
      <button class="balance-btn" data-w="0.5">Balanced</button>
      <button class="balance-btn" data-w="0.75">Favor liquidity</button>
      <button class="balance-btn" data-w="0.25">Favor quiet markets</button>
    </span>
    <button id="colPickerBtn" class="secondary" data-tip="Choose visible columns">Columns ▾</button>
  </div>
</div>
<div id="colPicker" class="col-picker hidden"></div>

<!-- ARB controls -->
<div id="arb-controls" class="ctrlbar hidden">
  <div class="field"><label>Region</label>
    <select id="arb-region">
      <option value="10000002">The Forge (Jita)</option>
      <option value="10000043">Domain (Amarr)</option>
      <option value="10000032">Sinq Laison (Dodixie)</option>
      <option value="10000042">Metropolis (Hek)</option>
      <option value="10000030">Heimatar (Rens)</option>
    </select>
  </div>
  <div class="field"><label>Mode</label>
    <select id="arb-cross">
      <option value="1" selected>Cross-station (haul)</option>
      <option value="0">Same-station (instant flip)</option>
    </select>
  </div>
  <div class="field"><label>Min ISK opp</label>
    <input id="arb-minisk" type="number" placeholder="0">
  </div>
  <div class="field" id="arb-maxjumps-field"><label>Max jumps (RT)</label>
    <input id="arb-maxjumps" type="number" value="6" min="1" max="50" style="width:70px">
  </div>
  <div class="field"><label>Route</label>
    <select id="arb-route">
      <option value="shortest">Shortest</option>
      <option value="secure">Secure (highsec only)</option>
      <option value="insecure">Insecure</option>
    </select>
  </div>
  <div class="btn-group">
    <button id="arb-go" class="primary">Scan</button>
    <button id="arb-toggleLowsec" class="secondary toggle" data-tip="Hide deals touching lowsec/nullsec">Highsec only</button>
  </div>
</div>

<!-- Industry controls -->
<div id="ind-controls" class="ctrlbar hidden">
  <!-- What & where -->
  <div class="ctrl-group">
    <span class="ctrl-cap">Scope</span>
    <div class="ctrl-fields">
      <div class="field" data-tip="Limit the scan to one market group (e.g. Ammunition & Charges). 'All' ranks every blueprint — much slower.">
        <label>Category</label>
        <select id="ind-group"><option value="all">All (slow)</option></select>
      </div>
      <div class="field" data-tip="Trade hub where you BUY the materials and SELL the finished item — all prices in the scan come from here.">
        <label>Source hub</label>
        <select id="ind-station">
          <option value="60003760">Jita 4-4</option>
          <option value="60008494">Amarr 8-20</option>
          <option value="60004588">Rens 6-8</option>
          <option value="60011866">Dodixie 9-20</option>
          <option value="60005686">Hek 8-12</option>
        </select>
      </div>
    </div>
  </div>
  <!-- Costs & fees -->
  <div class="ctrl-group">
    <span class="ctrl-cap">Costs &amp; fees</span>
    <div class="ctrl-fields">
      <div class="field" data-tip="Saved build locations (station / structure), each with its own system cost index, structure bonus, facility tax and SCC surcharge. ＋ adds one via a wizard, ✎ edits the selected one.">
        <label>Build location</label>
        <div class="field-row">
          <select id="ind-profile"></select>
          <button id="ind-struct-new" class="secondary" style="padding:4px 8px" title="Add a build location">＋</button>
          <button id="ind-struct-edit" class="secondary" style="padding:4px 8px" title="Edit selected build location">✎</button>
        </div>
      </div>
      <div class="field" data-tip="Effective job installation cost as a % of EIV. Set by the chosen build location [ system index × (1 − bonus) + facility tax + SCC surcharge ].">
        <label>Job cost %</label>
        <input id="ind-jobrate" type="number" step="0.01" value="0" style="width:70px" readonly>
      </div>
    </div>
  </div>
  <!-- Filters -->
  <div class="ctrl-group">
    <span class="ctrl-cap">Filter</span>
    <div class="ctrl-fields">
      <div class="field" data-tip="Hide items whose tradeability is below this (0–100). 0 = no filter. Tradeability is scored for the top-ranked items; items further down the list (not yet scored) are kept, so this trims the illiquid top picks without wiping out a big scan.">
        <label>Min trade</label><input id="ind-mintrade" type="number" min="0" max="100" value="0" style="width:60px">
      </div>
      <span class="balance-group ind-balance" data-tip="How the Tradeability score weights liquidity (daily volume) vs competition (days to sell). Ranked within this scan.">
        <span class="balance-label">Balance:</span>
        <button class="ind-balance-btn" data-w="0.5">Balanced</button>
        <button class="ind-balance-btn" data-w="0.75">Favor liquidity</button>
        <button class="ind-balance-btn" data-w="0.25">Favor quiet</button>
      </span>
      <div class="field" data-tip="Search by item name. Overrides every other display filter (min trade, etc.) while typing — matches against all scanned items.">
        <label>Search</label>
        <div class="search-wrap">
          <input id="ind-search" type="text" placeholder="item name…" style="width:140px">
          <button id="ind-search-clear" class="search-clear hidden" title="Clear search" type="button">✕</button>
        </div>
      </div>
    </div>
  </div>
  <!-- Actions & display filters -->
  <div class="ctrl-group ctrl-actions">
    <span class="ctrl-cap">Actions</span>
    <div class="ctrl-fields">
      <button id="ind-go" class="primary">Scan</button>
      <label class="check-field" data-tip="Only show items every required skill your character has actually trained to the needed level can build."><input type="checkbox" id="ind-buildable"> Buildable only</label>
      <label class="check-field" data-tip="Also show items whose blueprint you don't own and isn't on sale (a T1 BPO with no market order). Off = only craftable things."><input type="checkbox" id="ind-unobtainable"> Include unobtainable</label>
      <label class="check-field" data-tip="Hide T2 / invention items — show only directly-built T1 items."><input type="checkbox" id="ind-hidet2"> Hide T2</label>
      <button id="ind-refresh" class="secondary" data-tip="Re-download the blueprint database (SDE) from Fuzzwork. Only needed after a game patch.">⟳ Refresh SDE</button>
      <button id="indColPickerBtn" class="secondary" data-tip="Choose visible columns">Columns ▾</button>
    </div>
  </div>
</div>
<div id="indColPicker" class="col-picker hidden"></div>

<div id="statusbar"></div>

<main>
  <!-- LP tab -->
  <div id="lp-tablewrap" class="tablewrap">
    <div id="init-loading" class="init-loading">
      <div class="init-spinner"></div>
      <span>Loading…</span>
    </div>
    <table id="tbl"><colgroup id="cg"></colgroup><thead></thead><tbody></tbody></table>
  </div>
  <!-- ARB tab -->
  <div id="arb-tablewrap" class="tablewrap hidden">
    <div id="arb-progress" class="hidden">
      <div class="prog-label" id="arb-prog-label">Initializing…</div>
      <div class="prog-track"><div class="prog-fill" id="arb-prog-fill"></div></div>
      <div class="prog-sub" id="arb-prog-sub"></div>
    </div>
    <table id="arb-tbl"><colgroup id="arb-cg"></colgroup><thead></thead><tbody></tbody></table>
  </div>
  <!-- Industry tab -->
  <div id="ind-empty" class="char-empty hidden">
    <p>Log in with your EVE Online account to use the Industry planner.</p>
    <button id="ind-login-btn" class="auth-btn primary-btn">Log in with EVE</button>
  </div>
  <div id="ind-tablewrap" class="tablewrap hidden">
    <div id="ind-progress" class="hidden">
      <div class="prog-label" id="ind-prog-label">Initializing…</div>
      <div class="prog-track"><div class="prog-fill" id="ind-prog-fill"></div></div>
      <div class="prog-sub" id="ind-prog-sub"></div>
    </div>
    <div id="ind-chips" class="ind-chips"></div>
    <div id="ind-detail" class="hidden"></div>
    <table id="ind-tbl"><colgroup id="ind-cg"></colgroup><thead></thead><tbody></tbody></table>
  </div>
  <div id="char-tablewrap" class="tablewrap hidden">
    <div id="char-empty" class="char-empty">
      <p>Log in with your EVE Online account to see your wallet, skills, loyalty
      points, running industry jobs and active market orders here.</p>
      <button id="char-login-btn" class="auth-btn primary-btn">Log in with EVE</button>
    </div>
    <div id="char-body" class="hidden">
      <div class="char-kpis">
        <div class="char-kpi"><div class="l">Character</div><div class="v" id="cv-name">—</div></div>
        <div class="char-kpi"><div class="l">Wallet</div><div class="v gold" id="cv-wallet">—</div></div>
        <div class="char-kpi"><div class="l">Total SP</div><div class="v" id="cv-sp">—</div></div>
        <div class="char-kpi"><div class="l">Running jobs</div><div class="v" id="cv-jobs">—</div></div>
        <div class="char-kpi"><div class="l">Open orders</div><div class="v" id="cv-orders">—</div></div>
        <div class="char-kpi" id="cv-runs-kpi" data-tip="Cumulative runs delivered since this app started tracking — it can't see deliveries from before that.">
          <div class="l">Runs delivered</div><div class="v" id="cv-runs">—</div>
        </div>
      </div>
      <div class="char-grid">
        <section class="char-card char-card-wide">
          <h3>Running industry jobs</h3>
          <div class="char-card-scroll">
            <table class="mini" id="char-jobs-tbl"><thead><tr>
              <th>Product</th><th>Activity</th><th>Runs</th><th>Status</th><th style="text-align:right">Time left</th>
            </tr></thead><tbody></tbody></table>
          </div>
          <div id="char-jobs-empty" class="char-none hidden">No active jobs.</div>
        </section>
        <section class="char-card">
          <h3>Skill queue</h3>
          <div class="char-card-scroll">
            <table class="mini" id="char-queue-tbl"><thead><tr>
              <th>Skill</th><th>Lvl</th><th style="text-align:right">Finishes</th>
            </tr></thead><tbody></tbody></table>
          </div>
          <div id="char-queue-empty" class="char-none hidden">Skill queue is empty.</div>
        </section>
        <section class="char-card">
          <h3>Loyalty points</h3>
          <div class="char-card-scroll">
            <table class="mini" id="char-lp-tbl"><thead><tr>
              <th>Corporation</th><th style="text-align:right">LP</th>
            </tr></thead><tbody></tbody></table>
          </div>
          <div id="char-lp-empty" class="char-none hidden">No loyalty points.</div>
        </section>
        <section class="char-card char-card-wide">
          <h3>Active market orders <span id="char-orders-total" class="char-card-sub"></span></h3>
          <div class="char-card-scroll">
            <table class="mini" id="char-orders-tbl"><thead><tr>
              <th>Item</th><th>Side</th><th style="text-align:right">Remaining</th>
              <th style="text-align:right">Price</th>
              <th style="text-align:right" data-tip="Remaining units × your listed price — what's left to fill on this order at its current price.">Total value</th>
              <th style="text-align:right" data-tip="Current best sell price at Jita 4-4 — a quick reference, not necessarily the same station as your order.">Jita sell</th>
              <th style="text-align:right" data-tip="Your position in this item's order-matching queue at your order's own station — #1 means you're the best price, ties broken by who listed first. Blank if the station can't be resolved (e.g. a player structure).">Queue</th>
              <th style="text-align:right">Posted</th>
              <th style="text-align:right">Expires</th>
            </tr></thead><tbody></tbody></table>
          </div>
          <div id="char-orders-empty" class="char-none hidden">No open orders.</div>
        </section>
      </div>
    </div>
  </div>
  <!-- LP detail panel -->
  <div id="detail"><div class="inner"></div></div>
  <!-- Price history modal (ARB rows) -->
  <div id="arbChartModal" class="hidden">
    <div class="arb-chart-box">
      <div class="arb-chart-head">
        <h3 id="arbChartTitle"></h3>
        <span class="close" id="arbChartClose">✕</span>
      </div>
      <div class="chart-wrap" style="height:200px">
        <canvas class="chart-canvas" id="arbChartCanvas"></canvas>
        <div class="chart-tip" id="arbChartTip"></div>
        <div class="chart-cross"></div>
        <button class="chart-expand-btn" data-tip="Expand chart">⤢</button>
      </div>
      <div class="chart-stats" id="arbChartStats" style="margin-top:6px"></div>
    </div>
  </div>
  <!-- Expanded chart modal (LP + ARB) -->
  <div id="chartExpandModal" class="hidden">
    <div class="chart-expand-box">
      <div class="chart-expand-head">
        <h3 id="chartExpandTitle"></h3>
        <span class="close" id="chartExpandClose">✕</span>
      </div>
      <div class="chart-wrap" style="height:320px">
        <canvas class="chart-canvas" id="chartExpandCanvas"></canvas>
        <div class="chart-tip" id="chartExpandTip"></div>
        <div class="chart-cross"></div>
      </div>
      <div class="chart-stats" id="chartExpandStats" style="margin-top:6px"></div>
    </div>
  </div>
</main>

<!-- Build-location wizard (Industry) -->
<div id="indStructModal" class="ind-modal hidden">
  <div class="ind-modal-box">
    <h3 id="sw-title">New build location</h3>
    <p class="sw-hint">Read these off the in-game Industry window for your station/structure (the same panel that shows "Total job cost").</p>
    <label class="sw-field"><span>Name</span>
      <input id="sw-name" placeholder="e.g. Low Tax Magic House" autocomplete="off"></label>
    <label class="sw-field"><span>System cost index %</span>
      <input id="sw-index" type="number" step="0.01" value="0"></label>
    <label class="sw-field"><span>Structure bonus % <small>(role + rig cost reduction)</small></span>
      <input id="sw-bonus" type="number" step="0.1" value="0"></label>
    <label class="sw-field"><span>Facility tax %</span>
      <input id="sw-facility" type="number" step="0.01" value="0"></label>
    <label class="sw-field"><span>SCC surcharge %</span>
      <input id="sw-scc" type="number" step="0.01" value="4"></label>
    <div class="sw-eff">Effective job cost: <b id="sw-eff">—</b> of EIV
      <span class="sw-formula">= index × (1 − bonus) + facility tax + SCC</span></div>
    <div class="sw-actions">
      <button id="sw-delete" class="secondary">Delete</button>
      <span style="flex:1"></span>
      <button id="sw-cancel" class="secondary">Cancel</button>
      <button id="sw-save" class="primary">Save</button>
    </div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const COL_LAYOUT_VERSION = 6;

// Tax / broker are shown to the user as percent (4.5) but stored & sent to the
// backend as fractions (0.045). Convert at the input boundary only.
function pctToFrac(v){ const n=parseFloat(v); return isNaN(n)?"":String(n/100); }
function fracToPct(v){ const n=parseFloat(v); return isNaN(n)?"":String(+(n*100).toFixed(4)); }

// ── Shared utils ─────────────────────────────────────────────────────────
function fmtISK(n){
  if(n===null||n===undefined) return "-";
  const a=Math.abs(n);
  if(a>=1e9) return (n/1e9).toFixed(2)+"B";
  if(a>=1e6) return (n/1e6).toFixed(2)+"M";
  if(a>=1e3) return (n/1e3).toFixed(1)+"K";
  return Math.round(n).toLocaleString();
}
function fmtNum(n){ return (n===null||n===undefined)? "-" : Math.round(n).toLocaleString(); }
function fmtVol(n){ return (n===null||n===undefined)? "?" : n.toLocaleString(undefined,{maximumFractionDigits:1})+" m³"; }
function fmtSpread(s){ return s===null? "no bid" : Math.round(s)+"%"; }
// Days-to-clear. capped_profit===null is the "not fetched yet" sentinel (the
// background /api/liquidity call hasn't landed); daily_vol distinguishes "never
// traded" (null) from "history exists but no recent volume" (0).
const _SPIN = "<span class='spin'></span>";
function fmtDays(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(r.daily_vol===null) return "no data";
  if(r.daily_vol===0) return "∞";
  return v<1 ? "<1 d" : Math.round(v)+" d";
}
function fmtVolPerDay(v,r){
  if(!r.liq_loaded) return _SPIN;
  return v===null ? "no data" : fmtNum(v)+"/d";
}
// Suggested per-unit list price — needs market history, so it rides the same
// background /api/liquidity fill (spinner until it lands).
function fmtListPrice(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no data" : fmtISK(v);
}
// Age of the current cheapest sell order ("8h ago"). Also from the background
// fill (one live order-book call per type), so spinner until it lands.
function fmtFloorAge(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no orders" : fmtAgo(v);
}
// Tradeability: 0–100 blend of liquidity + low-competition, color-graded red→green.
function fmtTrade(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(v===null||v===undefined) return "—";
  return `<span style="color:hsl(${Math.round(v*1.2)},70%,58%);font-weight:600">${Math.round(v)}</span>`;
}
function fmtTs(epoch){
  if(!epoch) return "unknown";
  return fmtAgo(Math.round((Date.now()/1000)-epoch));
}
// A raw age in seconds → "8h ago" / "3d ago".
function fmtAgo(sec){
  if(sec===null||sec===undefined) return "unknown";
  sec=Math.round(sec);
  if(sec<5) return "just now";
  if(sec<60) return `${sec}s ago`;
  if(sec<3600) return `${Math.floor(sec/60)}m ago`;
  if(sec<86400) return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}
function setStatus(html,err){
  const s=$("#statusbar"); s.innerHTML=html; s.className=err?"err":"";
}
function persistScan(tab, blob){
  if(!blob) return;
  navigator.sendBeacon("/api/save-scan", new Blob(
    [JSON.stringify({tab, blob})], {type:"application/json"}));
}
function persistAllScans(){
  if(STATE.lastScanData && STATE.rows.length)
    persistScan("lp", {...STATE.lastScanData, rows:STATE.rows});
  if(IND.lastData && IND.rows.length && !IND.lastData.favorites_only && !IND.lastData.owned_only)
    persistScan("ind", {...IND.lastData, rows:IND.rows});
}
document.addEventListener("visibilitychange",()=>{ if(document.visibilityState==="hidden") persistAllScans(); });
window.addEventListener("beforeunload", persistAllScans);

// ── localStorage + server-synced persistence ────────────────────────────────
const LS_KEY='eve-scanner';
function settingsBlob(){
  return {
    corp:$("#corp").value,lp:$("#lp").value,
    maxspread:$("#maxspread").value,tax:pctToFrac($("#g-tax").value),broker:pctToFrac($("#g-broker").value),
    market:$("#market").value,
    sort_key:STATE.sort.key,sort_dir:STATE.sort.dir,
    col_widths:STATE.colw,col_order:STATE.colOrder,col_layout_v:COL_LAYOUT_VERSION,col_vis:STATE.colVis,
    hide_illiquid:STATE.hideIlliquid?'1':'0',
    hide_unaffordable:STATE.hideUnaffordable?'1':'0',
    trade_weight:STATE.tradeWeight,
    active_tab:ACTIVE_TAB,
    arb:{region:$("#arb-region").value,cross_station:$("#arb-cross").value,
      sales_tax:pctToFrac($("#g-tax").value),min_isk:$("#arb-minisk").value,
      max_jumps:$("#arb-maxjumps").value,route_flag:$("#arb-route").value,
      avoid_lowsec:ARB.avoidLowsec?'1':'0'},
    ind:{market_group:$("#ind-group").value,station:$("#ind-station").value,
      job_rate:$("#ind-jobrate").value,
      sales_tax:$("#g-tax").value,broker:$("#g-broker").value,
      buildable_only:$("#ind-buildable").checked?'1':'0',
      include_unbuildable:$("#ind-unobtainable").checked?'1':'0',
      hide_t2:$("#ind-hidet2").checked?'1':'0',
      min_tradeability:$("#ind-mintrade").value,
      profiles:JSON.stringify(IND.profiles),profile:$("#ind-profile").value,
      favorites:JSON.stringify([...IND.favorites]),
      sort_key:IND.sort.key,sort_dir:IND.sort.dir,
      col_order:JSON.stringify(IND.colOrder),col_widths:JSON.stringify(IND.colw),
      col_vis:JSON.stringify(IND.colVis),
      sections:JSON.stringify(IND.sections),
      ind_trade_weight:String(IND.tradeWeight)}
  };
}
// Debounced push of the full settings blob to the server so every device the
// logged-in character uses converges on the same columns/filters/etc. Cheap
// no-op server-side when nobody is logged in.
let _settingsSyncTimer=null;
function syncSettingsToServer(blob){
  clearTimeout(_settingsSyncTimer);
  _settingsSyncTimer=setTimeout(()=>{
    fetch(`/api/settings/sync?blob=${encodeURIComponent(JSON.stringify(blob))}`).catch(()=>{});
  }, 800);
}
function saveLS(){
  const blob=settingsBlob();
  try{ localStorage.setItem(LS_KEY,JSON.stringify(blob)); }catch(e){}
  syncSettingsToServer(blob);
}

// ── Tab switching ─────────────────────────────────────────────────────────
let ACTIVE_TAB = "lp";
// Each tab has a clean URL so a refresh/bookmark reopens the same module.
const TAB_PATH = { lp:"/", arb:"/arbitrage", ind:"/industry", char:"/character" };
const PATH_TAB = { "/":"lp", "/lp":"lp", "/arbitrage":"arb", "/arb":"arb",
                   "/industry":"ind", "/ind":"ind", "/character":"char", "/char":"char" };
function switchTab(tab, opts){
  opts = opts || {};
  ACTIVE_TAB = tab;
  // Reflect the tab in the URL (skip when we're reacting to a URL change, e.g.
  // back/forward, so we don't fight the browser's own history).
  if(opts.url!==false){
    const p = TAB_PATH[tab] || "/";
    if(location.pathname !== p) history.pushState({tab}, "", p);
  }
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active", t.dataset.tab===tab));
  $("#lp-controls").classList.toggle("hidden", tab!=="lp");
  $("#arb-controls").classList.toggle("hidden", tab!=="arb");
  $("#lp-tablewrap").classList.toggle("hidden", tab!=="lp");
  $("#arb-tablewrap").classList.toggle("hidden", tab!=="arb");
  $("#char-tablewrap").classList.toggle("hidden", tab!=="char");
  updateIndGate();
  if(tab!=="lp") closeDetail();
  setStatus("");
  document.title = tab==="lp" ? "EVE LP Store Scanner"
                : tab==="arb" ? "EVE Arbitrage Scanner"
                : tab==="char" ? "EVE Character" : "EVE Industry Planner";
  fetch(`/api/prefs?active_tab=${tab}`).catch(()=>{}); saveLS();
  if(tab==="ind" && AUTH.loggedIn){
    if(!IND.groupsLoaded) loadIndGroups();
    renderIndTable(); renderIndStatus();   // show whatever's loaded (e.g. a favourites preview) immediately
  }
  if(tab==="char" && AUTH.loggedIn) refreshCharData();
}
// The Industry planner has no manual ME/TE/skill inputs — it needs a real
// character's owned blueprints and trained skills, so it's gated behind login
// exactly like the Character tab, just without hiding the nav entry (so a
// logged-out visitor can discover why it needs EVE login).
function updateIndGate(){
  const show = ACTIVE_TAB==="ind" && AUTH.loggedIn;
  $("#ind-controls").classList.toggle("hidden", !show);
  $("#ind-tablewrap").classList.toggle("hidden", !show);
  $("#ind-empty").classList.toggle("hidden", !(ACTIVE_TAB==="ind" && !AUTH.loggedIn));
}
document.querySelectorAll(".tab").forEach(t=>{
  t.onclick = ()=>switchTab(t.dataset.tab);
});
// Back/forward between tab URLs — switch without re-pushing history. The
// Character tab needs a login; fall back to LP if the URL points there logged out.
window.addEventListener("popstate", ()=>{
  let tab = PATH_TAB[location.pathname] || "lp";
  if(tab==="char" && !AUTH.loggedIn) tab="lp";
  switchTab(tab, {url:false});
});

// ══════════════════════════════════════════════════════════════════════════
// LP TAB
// ══════════════════════════════════════════════════════════════════════════
let STATE = {rows:[], sort:{key:"isk_per_lp_best", dir:-1}, ctx:{}, selOffer:null,
             colw:{}, colVis:{}, hideIlliquid:false, hideUnaffordable:false, lastScanData:null,
             tradeWeight:0.5,  // liquidity↔competition blend: 0=all competition, 1=all liquidity
             lotTrackerOpen:false, recipeOpen:false,
             shoppingOpen:true, costOpen:false, cargoOpen:false, saleOpen:false};

// Tradeability = a 0–100 blend of two raw signals, each scored by its rank
// against the other offers in this store (so there's no invented "good volume"
// constant): liquidity (higher daily_vol = better) and low competition (lower
// days_to_clear = better). STATE.tradeWeight sets the proportion. Recomputed
// here on every render and whenever the user changes the balance preset.
function computeTradeability(){
  const loaded=STATE.rows.filter(r=>r.liq_loaded && r.daily_vol!==null);
  if(!loaded.length){ STATE.rows.forEach(r=>r.tradeability=null); return; }
  const vols=loaded.map(r=>r.daily_vol);
  const days=loaded.map(r=> r.days_to_clear===null ? Infinity : r.days_to_clear);
  const w=STATE.tradeWeight;
  const pctRank=(arr,v,higherBetter)=>{
    const n=arr.length; if(n<=1) return 100;
    let beats=0;
    for(const x of arr){ if(x===v) continue; if(higherBetter? v>x : v<x) beats++; }
    return beats/(n-1)*100;
  };
  for(const r of STATE.rows){
    if(!r.liq_loaded || r.daily_vol===null){ r.tradeability=null; continue; }
    const liq=pctRank(vols, r.daily_vol, true);
    const comp=pctRank(days, r.days_to_clear===null?Infinity:r.days_to_clear, false);
    r.tradeability=w*liq + (1-w)*comp;
  }
}
let LP_RESIZING = false;

const fmtIpl = v => (v===null||v===undefined) ? "—" : v.toLocaleString(undefined,{maximumFractionDigits:1});
const COLS = [
  {k:"name",               t:"Reward Item",     w:220, defvis:true,  tip:"The item this LP offer gives you.  * = a required input has no Jita price  ·  ^ = costs Analysis Kredits  ·  ! = illiquid (spread ≥25%)"},
  {k:"isk_per_lp_patient", t:"List ISK/LP",        w:100, defvis:true,  tip:"Profit per Loyalty Point if you LIST a sell order at the ask and wait (pay sales tax + broker fee).", f:fmtIpl, pn:true},
  {k:"isk_per_lp_instant", t:"Instant-sell ISK/LP",w:120, defvis:true,  tip:"Profit per Loyalty Point if you INSTANT-SELL into a buy order at the bid (pay sales tax only).", f:fmtIpl, pn:true},
  {k:"total_profit_patient",t:"List profit",       w:105, defvis:true,  tip:"Total profit across your whole LP budget, listing sell orders at the ask.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"total_profit_instant",t:"Instant-sell profit",w:120, defvis:true,  tip:"Total profit across your whole LP budget, instant-selling into buy orders.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"tradeability", t:"Tradeability",  w: 95, defvis:true,  tip:"0–100: how realistically you can sell at your price. Blends liquidity (Daily Vol) and low competition (Days to Clear), weighted by the Balance buttons. Higher is better; ranked within this store.", f:fmtTrade, rowCtx:true, cls:"spread"},
  {k:"daily_vol",    t:"Daily Vol",     w: 90, defvis:true,  tip:"Units traded per day at the hub (30-day median). High = deep market you can sell into; low = thin and hard to offload.", f:fmtVolPerDay, rowCtx:true},
  {k:"days_to_clear",t:"Days to Clear", w: 95, defvis:true,  tip:"Sell-side backlog: units listed ÷ units sold per day. “5 d” = 5 days of stock ahead of you. <1 d sells fast; ∞ = barely trades.", f:fmtDays, rowCtx:true, cls:"spread"},
  {k:"spread_pct",   t:"Spread",        w: 70, defvis:true,  tip:"Ask vs bid gap. ≥25% (!) means the ask isn't backed by real buyers — the patient (sell) figure is unreliable, prefer the buy column.", f:fmtSpread, cls:"spread"},
  {k:"max_units",    t:"Max Runs",      w: 80, defvis:true,  tip:"Redemptions your LP budget affords (budget ÷ LP per run). Affordability only — it doesn't check whether the market can absorb them.", f:v=>v===0?"—":fmtNum(v)},
  {k:"lp_cost",      t:"LP / Run",      w: 80, defvis:true,  tip:"Loyalty Points per redemption.", f:fmtNum},
  {k:"cost_ea",      t:"ISK / Run",     w: 95, defvis:true,  tip:"ISK + required input costs per redemption.", f:fmtISK},
  {k:"list_price",   t:"List @",        w:100, defvis:true,  tip:"Suggested per-unit price to put on your sell order: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value. Per unit of the reward item.", f:fmtListPrice, rowCtx:true},
  {k:"floor_age",    t:"Floor age",     w: 95, defvis:true,  tip:"How long ago the current cheapest sell order at the hub was posted (from its issued timestamp). A fresh floor in a thin market means the price is actively moving. “no orders” = nothing listed.", f:fmtFloorAge, rowCtx:true, cls:"spread"},
  {k:"ask",          t:"Ask (sell)",    w: 95, defvis:false, tip:"Lowest sell order price at the hub — what the patient column lists at.", f:fmtISK},
  {k:"bid",          t:"Bid (buy)",     w: 95, defvis:false, tip:"Highest buy order price at the hub — what the instant column dumps into.", f:fmtISK},
  {k:"buy_volume",   t:"Buy Demand",    w: 95, defvis:false, tip:"Units on hub buy orders — how many you could sell instantly.", f:fmtNum},
  {k:"qty",          t:"Units",         w: 55, defvis:false, tip:"Units per redemption.", f:fmtNum},
  {k:"output_volume",t:"Vol m³",        w:140, defvis:false, tip:"Packaged m³ per redemption, and total for all runs in parentheses.", f:(v,r)=>{ if(v===null) return "?"; const per=fmtVol(v); return r.max_units>0?`${per} (${fmtVol(v*r.max_units)})`:per; }, rowCtx:true},
];
COLS.forEach(c=>{ STATE.colVis[c.k]=c.defvis; STATE.colw[c.k]=c.w; });
const COL_BY_KEY=Object.fromEntries(COLS.map(c=>[c.k,c]));
STATE.colOrder=COLS.map(c=>c.k);  // user-reorderable; persisted with col widths
// Resolve STATE.colOrder to column objects, dropping unknown keys and appending
// any columns that aren't listed yet (so a saved order survives COLS additions).
function orderedCols(){
  const seen=new Set(), out=[];
  for(const k of STATE.colOrder){ const c=COL_BY_KEY[k]; if(c&&!seen.has(k)){ out.push(c); seen.add(k); } }
  for(const c of COLS) if(!seen.has(c.k)){ out.push(c); seen.add(c.k); }
  return out;
}
function visCols(){ return orderedCols().filter(c=>STATE.colVis[c.k]!==false); }

function lpSetColgroup(){
  $("#cg").innerHTML=visCols().map(c=>`<col style="width:${STATE.colw[c.k]||c.w}px">`).join("");
}

function startLPResize(e, key){
  e.preventDefault(); e.stopPropagation();
  LP_RESIZING=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#tbl").style.tableLayout="fixed";
  const startX=e.clientX, startW=STATE.colw[key]||80;
  function mm(ev){ STATE.colw[key]=Math.max(40,startW+(ev.clientX-startX)); lpSetColgroup(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    saveLPColWidths();
    setTimeout(()=>{ LP_RESIZING=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

// ── Column drag-to-reorder ────────────────────────────────────────────────
// HTML5 drag-and-drop on the <th>s. The resizer's mousedown preventDefault()
// suppresses a drag starting from the resize grip, and a sort-click never fires
// after a real drag, so the three header interactions stay independent.
let LP_DRAG_KEY=null;
function clearLPDropMarks(){
  document.querySelectorAll("#tbl thead th").forEach(th=>th.classList.remove("drop-before","drop-after"));
}
function lpDropAfter(th,clientX){
  const r=th.getBoundingClientRect();
  return clientX > r.left + r.width/2;
}
function reorderLPCols(srcKey,dstKey,after){
  if(!srcKey||srcKey===dstKey) return;
  const order=orderedCols().map(c=>c.k);   // full order, hidden cols included
  order.splice(order.indexOf(srcKey),1);
  let to=order.indexOf(dstKey);
  if(after) to+=1;
  order.splice(to,0,srcKey);
  STATE.colOrder=order;
  saveLPColWidths();   // col_order rides along with widths under the same version
  renderTable();
}
function wireLPColDrag(th){
  th.addEventListener("dragstart",e=>{
    LP_DRAG_KEY=th.dataset.k;
    e.dataTransfer.effectAllowed="move";
    try{ e.dataTransfer.setData("text/plain",LP_DRAG_KEY); }catch(_){}
    th.classList.add("col-dragging");
    document.body.classList.add("col-dragging-active");
  });
  th.addEventListener("dragend",()=>{
    th.classList.remove("col-dragging");
    document.body.classList.remove("col-dragging-active");
    clearLPDropMarks();
    setTimeout(()=>{ LP_DRAG_KEY=null; },0);
  });
  th.addEventListener("dragover",e=>{
    if(!LP_DRAG_KEY) return;
    e.preventDefault();
    e.dataTransfer.dropEffect="move";
    clearLPDropMarks();
    if(th.dataset.k!==LP_DRAG_KEY)
      th.classList.add(lpDropAfter(th,e.clientX)?"drop-after":"drop-before");
  });
  th.addEventListener("dragleave",()=>th.classList.remove("drop-before","drop-after"));
  th.addEventListener("drop",e=>{
    e.preventDefault();
    const after=lpDropAfter(th,e.clientX);
    clearLPDropMarks();
    reorderLPCols(LP_DRAG_KEY, th.dataset.k, after);
  });
}

function renderTable(){
  const _il=$("#init-loading"); if(_il) _il.remove();
  computeTradeability();
  const thead=$("#tbl thead"), tbody=$("#tbl tbody");
  const vc=visCols();
  $("#tbl").style.tableLayout="fixed";
  lpSetColgroup();
  thead.innerHTML="<tr>"+vc.map(c=>{
    const active=STATE.sort.key===c.k;
    const arrow=active?(STATE.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th draggable="true" data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(LP_RESIZING){ LP_RESIZING=false; return; }
      if(LP_DRAG_KEY){ return; }  // tail end of a reorder, not a sort click
      const k=th.dataset.k;
      if(STATE.sort.key===k) STATE.sort.dir*=-1;
      else STATE.sort={key:k, dir:k==="name"?1:-1};
      saveLPSort(); renderTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startLPResize(e,vc[i].k));
    wireLPColDrag(th);
  });
  const _lpSearch=($("#lp-search").value||"").trim().toLowerCase();
  const rows=[...STATE.rows]
    .filter(r=>!_lpSearch||r.name.toLowerCase().includes(_lpSearch))
    .filter(r=>!STATE.hideIlliquid||!r.illiquid||r.unsellable)
    .filter(r=>!STATE.hideUnaffordable||r.max_units>0)
    .sort((a,b)=>{
      const k=STATE.sort.key, d=STATE.sort.dir;
      let x=a[k], y=b[k];
      if(typeof x==="string") return x.localeCompare(y)*d;
      if(x===null) x=-Infinity; if(y===null) y=-Infinity;
      return (x-y)*d;
    });
  tbody.innerHTML=rows.map(r=>{
    const tds=vc.map(c=>{
      let v=r[c.k], txt=c.f?(c.rowCtx?c.f(v,r):c.f(v)):v;
      let cls=c.cls||"";
      if(c.k==="spread_pct"&&v!==null) cls+=v<10?" tight":v<25?" mid":"";
      if(c.k==="name"){
        let flag=""; if(r.req_missing) flag+="*"; if(r.ak_cost) flag+="^"; if(r.illiquid) flag+="!";
        txt=txt+(flag?` <span class="flag">${flag}</span>`:"");
      }
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      // Mark the better of the two sell-mode cells so the comparison reads at a glance.
      if((c.k==="isk_per_lp_patient"||c.k==="isk_per_lp_instant")
         && r.isk_per_lp_best!==null && v!==null && v===r.isk_per_lp_best) cls+=" win";
      if((c.k==="total_profit_patient"||c.k==="total_profit_instant")
         && r.total_profit_best!==null && v!==null && v===r.total_profit_best && r.max_units>0) cls+=" win";
      return `<td class="${cls}">${txt}</td>`;
    }).join("");
    return `<tr class="${r.illiquid?'illiquid':''} ${r.unsellable?'unsellable':''} ${r.offer_id===STATE.selOffer?'sel':''}" data-id="${r.offer_id}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr=>tr.onclick=()=>openDetail(+tr.dataset.id));
}

async function scan(forceRefresh=false){
  const _il=$("#init-loading"); if(_il) _il.remove();
  const corp=$("#corp").value.trim();
  if(!corp){ setStatus("Enter a corporation name.",true); return; }
  const btn=$("#refresh");
  if(forceRefresh){ btn.disabled=true; btn.textContent="⟳ Fetching…"; }
  setStatus("Scanning "+corp+(forceRefresh?" (refreshing from ESI)":"")+" …");
  STATE.ctx={lp:$("#lp").value, tax:pctToFrac($("#g-tax").value), broker:pctToFrac($("#g-broker").value), station:$("#market").value};
  const p=new URLSearchParams({corp, ...STATE.ctx});
  const ms=$("#maxspread").value.trim(); if(ms) p.set("max_spread",ms);
  if(forceRefresh) p.set("refresh","1");
  try{
    const res=await fetch("/api/scan?"+p);
    const data=await res.json();
    if(data.error){ setStatus(data.error,true); return; }
    STATE.rows=data.rows; STATE.ctx.corp_id=data.corp_id; STATE.selOffer=null;
    STATE.lastScanData=data; closeDetail(); renderLPStatus(); renderTable();
    fillLiquidity();
  }catch(e){ setStatus("Request failed: "+e,true); }
  finally{ btn.disabled=false; btn.textContent="⟳ Refresh"; }
}

// Background-fill the market-saturation columns (Days to Clear / Capped Profit)
// after the table is already on screen. One history call per type server-side,
// so this can take a few seconds on a fresh corp; rows show "…" until it lands.
async function fillLiquidity(){
  const corpId=STATE.ctx.corp_id; if(!corpId) return;
  const p=new URLSearchParams({corp_id:corpId, lp:STATE.ctx.lp,
    tax:STATE.ctx.tax, broker:STATE.ctx.broker, station:STATE.ctx.station});
  try{
    const d=await (await fetch("/api/liquidity?"+p)).json();
    if(d.error||!d.liquidity) return;
    if(STATE.ctx.corp_id!==corpId) return;  // user re-scanned; drop stale fill
    const liq=d.liquidity;
    for(const r of STATE.rows){
      const e=liq[r.offer_id];
      if(e){ r.daily_vol=e.daily_vol; r.days_to_clear=e.days_to_clear; r.list_price=e.list_price; r.floor_age=e.floor_age; r.liq_loaded=true; }
    }
    renderTable();
    if(STATE.detail&&STATE.selOffer) renderDetail();
    persistScan("lp", STATE.lastScanData ? {...STATE.lastScanData, rows:STATE.rows} : null);
  }catch(e){ /* leave the "…" placeholders; non-fatal */ }
}

function renderLPStatus(){
  const d=STATE.lastScanData; if(!d||ACTIVE_TAB!=="lp") return;
  setStatus(
    `<span class="pill"><b>${d.corp_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> offers</span>`
    +`<span class="pill"><b>${Number(d.lp).toLocaleString()}</b> LP · list vs instant sell</span>`
    +`<span class="ts">offers ${fmtTs(d.offers_fetched_at)} · prices ${fmtTs(d.scanned_at)}</span>`);
}

function saveLPSort(){
  const s=STATE.sort;
  fetch(`/api/prefs?sort_key=${encodeURIComponent(s.key)}&sort_dir=${s.dir}`).catch(()=>{}); saveLS();
}
function saveLPColWidths(){
  fetch(`/api/prefs?col_widths=${encodeURIComponent(JSON.stringify(STATE.colw))}`
    +`&col_order=${encodeURIComponent(JSON.stringify(STATE.colOrder))}`
    +`&col_layout_v=${COL_LAYOUT_VERSION}`).catch(()=>{}); saveLS();
}

// ── Column picker ─────────────────────────────────────────────────────────
(function(){
  const btn=document.getElementById("colPickerBtn");
  const picker=document.getElementById("colPicker");
  function renderPicker(){
    picker.innerHTML=COLS.map(c=>`<label><input type="checkbox" data-k="${c.k}"${STATE.colVis[c.k]!==false?' checked':''}> ${c.t}</label>`).join("");
    picker.querySelectorAll("input").forEach(cb=>{
      cb.onchange=()=>{ STATE.colVis[cb.dataset.k]=cb.checked; renderTable(); saveLS(); };
    });
  }
  btn.onclick=e=>{
    e.stopPropagation();
    if(!picker.classList.contains("hidden")){ picker.classList.add("hidden"); return; }
    renderPicker();
    const r=btn.getBoundingClientRect();
    picker.style.top=(r.bottom+4)+"px";
    picker.style.left=r.left+"px";
    picker.classList.remove("hidden");
  };
  document.addEventListener("click",()=>picker.classList.add("hidden"));
  picker.addEventListener("click",e=>e.stopPropagation());
})();

// ── LP detail panel ───────────────────────────────────────────────────────
async function openDetail(offerId){
  STATE.selOffer=offerId; STATE.recipeOpen=false; renderTable();
  const p=new URLSearchParams({corp_id:STATE.ctx.corp_id, offer_id:offerId,
    lp:STATE.ctx.lp, tax:STATE.ctx.tax, broker:STATE.ctx.broker,
    station:STATE.ctx.station});
  const inner=$("#detail .inner");
  inner.innerHTML="<div class='muted'>Loading volumes…</div>";
  $("#detail").classList.add("open");
  try{
    const d=await (await fetch("/api/detail?"+p)).json();
    if(d.error){ inner.innerHTML=`<span style='color:var(--red)'>${d.error}</span>`; return; }
    STATE.detail=d; renderDetail();
  }catch(e){ inner.innerHTML=`<span style='color:var(--red)'>${e}</span>`; }
}
function closeDetail(){ $("#detail").classList.remove("open"); STATE.selOffer=null; }

function renderDetail(){
  const d=STATE.detail;
  const def=Math.max(d.max_units||0,1);
  const inner=$("#detail .inner");
  inner.innerHTML=`
    <div class="dheader">
      <div><h2>${d.output.name} <button class="lp-copy" title="Copy item name to clipboard">⧉ Copy</button></h2>
        <div class="sub">${d.output.quantity}× per redemption · offer #${d.offer_id} ·
          list vs instant sell</div>
      </div>
      <span class="close" id="closeBtn">✕</span>
    </div>
    <div class="chart-wrap"><canvas class="chart-canvas" id="detailChart"></canvas><div class="chart-tip" id="detailChartTip"></div><div class="chart-cross"></div><button class="chart-expand-btn" data-tip="Expand chart">⤢</button></div>
    <div class="chart-stats" id="detailChartStats"></div>
    <div class="redrow">
      <label>Redemptions</label>
      <input id="reds" type="number" min="1" value="${def}">
      <span class="maxlink">max LP affords: <a href="#" id="maxLink">${fmtNum(d.max_units)}</a></span>
      ${AUTH.loggedIn&&AUTH.data&&AUTH.data.wallet!=null&&d.total_cost>0
        ?`<span class="maxlink">max ISK affords: <a href="#" id="maxIskLink">${fmtNum(Math.floor(AUTH.data.wallet/d.total_cost))}</a></span>`:''}
    </div>
    <div id="dbody"></div>`;
  $("#closeBtn").onclick=closeDetail;
  const lpCopyBtn=inner.querySelector(".lp-copy");
  lpCopyBtn.onclick=e=>{
    e.stopPropagation();
    const done=()=>{ lpCopyBtn.textContent="✓ Copied"; setTimeout(()=>{lpCopyBtn.textContent="⧉ Copy";},1200); };
    if(navigator.clipboard&&navigator.clipboard.writeText)
      navigator.clipboard.writeText(d.output.name).then(done).catch(()=>fallbackCopy(d.output.name,done));
    else fallbackCopy(d.output.name, done);
  };
  $("#reds").oninput=renderBody;
  const ml=$("#maxLink");
  if(ml) ml.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(d.max_units,1); renderBody(); };
  const mil=document.getElementById("maxIskLink");
  if(mil) mil.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(Math.floor(AUTH.data.wallet/d.total_cost),1); renderBody(); };
  renderBody();
  const regionId=_STATION_TO_REGION[parseInt(STATE.ctx.station)]||10000002;
  requestAnimationFrame(()=>{
    const c=document.getElementById('detailChart');
    if(c) _attachChart(c,document.getElementById('detailChartTip'),document.getElementById('detailChartStats'),d.output.type_id,regionId,d.ask||d.bid||null,d.output.name);
  });
}

function walkBook(book, qty){
  let need=qty, cost=0, filled=0, last=null;
  for(const lvl of (book||[])){
    if(need<=0) break;
    const take=Math.min(need,lvl[1]);
    cost+=take*lvl[0]; filled+=take; need-=take; last=lvl[0];
  }
  return {cost, filled, avg:filled>0?cost/filled:null, shortBy:Math.max(0,qty-filled), lastPrice:last};
}

function bindLotCalcs(savedLots){
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{
    const tid=row.dataset.tid;
    const need=parseInt(row.dataset.need)||0;
    const tagsEl=row.querySelector(".lot-tags");
    const numEl=row.querySelector(".lot-num");
    const sumEl=row.querySelector(".lot-sum");
    row._lotNums=(savedLots&&savedLots[tid])?[...savedLots[tid]]:[];

    function renderChips(){
      tagsEl.innerHTML=row._lotNums.map((v,i)=>
        `<span class="lot-tag">${fmtNum(v)}<span class="rm" data-i="${i}">×</span></span>`
      ).join("");
      tagsEl.querySelectorAll(".rm").forEach(rm=>{
        rm.onclick=()=>{ row._lotNums.splice(+rm.dataset.i,1); renderChips(); };
      });
      const tot=row._lotNums.reduce((a,b)=>a+b,0);
      if(!row._lotNums.length){ sumEl.textContent=""; return; }
      const rem=need-tot;
      if(rem<=0){ sumEl.textContent=`${fmtNum(tot)} ✓`; sumEl.style.color="var(--green2)"; }
      else { sumEl.textContent=`${fmtNum(tot)} · ${fmtNum(rem)} more`; sumEl.style.color="var(--yellow)"; }
    }

    numEl.addEventListener("keydown",e=>{
      if(e.key==="Enter"||e.key===" "){
        e.preventDefault();
        const v=parseInt(numEl.value);
        if(v>0){ row._lotNums.push(v); numEl.value=""; renderChips(); }
      }
    });
    renderChips();
  });
  const toggle=document.getElementById("lotTrackerToggle");
  if(toggle) toggle.onclick=()=>{
    STATE.lotTrackerOpen=!STATE.lotTrackerOpen;
    toggle.textContent=(STATE.lotTrackerOpen?"▼":"▶")+" Lot tracker";
    document.querySelector(".lot-tracker").style.display=STATE.lotTrackerOpen?"":"none";
  };
  const recipeToggle=document.getElementById("recipeToggle");
  if(recipeToggle) recipeToggle.onclick=()=>{
    STATE.recipeOpen=!STATE.recipeOpen;
    recipeToggle.textContent=(STATE.recipeOpen?"▼":"▶")+" Base Recipe (1× redemption)";
    document.querySelector(".recipe-list").style.display=STATE.recipeOpen?"":"none";
  };
  ["shoppingToggle","costToggle","cargoToggle","saleToggle"].forEach((id,i)=>{
    const keys=["shoppingOpen","costOpen","cargoOpen","saleOpen"];
    const el=document.getElementById(id);
    if(!el) return;
    const labelText=el.textContent.replace(/^[▼▶] /,"");
    el.onclick=()=>{
      const key=keys[i];
      STATE[key]=!STATE[key];
      el.textContent=(STATE[key]?"▼":"▶")+" "+labelText;
      document.querySelector(`[data-sec="${id}"]`).style.display=STATE[key]?"":"none";
    };
  });
}

function renderBody(){
  const d=STATE.detail;
  const n=Math.max(1,parseInt($("#reds").value||"1"));
  const tax=parseFloat(STATE.ctx.tax)||0.045, broker=parseFloat(STATE.ctx.broker)||0.015;
  const hub=(STATE.lastScanData&&STATE.lastScanData.station_name)||"the selected hub";
  const pn=v=>v>0?"pos":(v<0?"neg":"");
  const savedLots={};
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{ if(row._lotNums&&row._lotNums.length) savedLots[row.dataset.tid]=[...row._lotNums]; });
  let reqCost=0, anyShort=false, reqVol=0, reqVolMissing=false;
  const reqRows=d.required_items.map(it=>{
    const need=it.quantity*n;
    const w=walkBook(it.book,need);
    const remPrice=w.lastPrice||it.unit_price||0;
    const line=w.cost+w.shortBy*remPrice;
    const noPrice=(it.unit_price===null&&w.filled===0);
    if(!noPrice) reqCost+=line;
    const short=w.shortBy>0; if(short) anyShort=true;
    if(it.line_volume===null) reqVolMissing=true; else reqVol+=it.line_volume*n;
    const vol=it.line_volume===null?'?':fmtVol(it.line_volume*n);
    return `<tr><td>${it.name}${short?' <span class="flag" data-tip="Not enough on market">!</span>':''}</td>
      <td>${fmtNum(need)}</td>
      <td>${w.avg===null?(it.unit_price===null?'<span class="flag">*</span>':fmtISK(it.unit_price)):fmtISK(w.avg)}</td>
      <td>${noPrice?'<span class="flag">?</span>':fmtISK(line)}</td>
      <td>${vol}</td></tr>`;
  }).join("");
  // Patient: list the whole reward quantity at the ask, pay sales tax + broker fee.
  const soldQtyP=d.output.quantity*n;
  const grossP=d.ask?soldQtyP*d.ask:null;
  const taxP=grossP===null?0:grossP*tax, brokerP=grossP===null?0:grossP*broker;
  const revenueP=grossP===null?null:grossP-taxP-brokerP;
  // Instant: walk down the live buy orders, pay sales tax only.
  const wI=walkBook(d.output.buy_book,d.output.quantity*n);
  const soldQtyI=wI.filled, sellShort=wI.shortBy>0;
  const grossI=(d.bid!==null&&soldQtyI>0)?wI.cost:null;
  const taxI=grossI===null?0:grossI*tax;
  const revenueI=grossI===null?null:grossI-taxI;

  const lpTot=d.lp_cost*n, isk_fee=d.isk_fee*n, cost=isk_fee+reqCost;
  const profitP=revenueP===null?null:revenueP-cost;
  const profitI=revenueI===null?null:revenueI-cost;
  const inVol=d.input_volume_per_redemption*n, outVol=(d.output_volume_per_redemption||0)*n;
  const pcls=v=>v===null?'':v>=0?'pos':'neg';
  let warn="";
  if(anyShort) warn+=`<div class="note">! Not enough sell orders at ${hub} for some required items.</div>`;
  if(sellShort) warn+=`<div class="note">Instant sell: only ${fmtNum(soldQtyI)} of ${fmtNum(d.output.quantity*n)} fit the current ${hub} buy orders.</div>`;
  if(d.spread_pct===null) warn+=`<div class="note bad">No buy orders exist — instant-sell can't fill and a listed sell order may never clear.</div>`;
  else if(d.spread_pct>=d.high_spread_pct) warn+=`<div class="note">${Math.round(d.spread_pct)}% spread — the ask isn't backed by real demand; the list figure is optimistic.</div>`;
  if(d.req_missing_price) warn+=`<div class="note">* A required item has no ${hub} price — true cost is higher.</div>`;

  const recipeItems=[];
  recipeItems.push(`
    <div class="recipe-list-item">
      <span class="name">Loyalty Points (LP)</span>
      <span class="val lp">${fmtNum(d.lp_cost)} LP</span>
    </div>`);
  if(d.isk_fee>0) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">Redemption ISK</span>
        <span class="val isk">${fmtISK(d.isk_fee)} ISK</span>
      </div>`);
  }
  for(const it of d.required_items) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">${it.name}</span>
        <span class="val">× ${fmtNum(it.quantity)}</span>
      </div>`);
  }
  const recipeHTML = `
    <h3 id="recipeToggle" style="cursor:pointer;user-select:none">${STATE.recipeOpen?'▼':'▶'} Base Recipe (1× redemption)</h3>
    <div class="recipe-list" style="${STATE.recipeOpen?'':'display:none'}">
      ${recipeItems.join("")}
    </div>`;

  const sec=(id, stateKey, label, content)=>`
    <h3 id="${id}" style="cursor:pointer;user-select:none">${STATE[stateKey]?'▼':'▶'} ${label}</h3>
    <div class="detail-section" data-sec="${id}" style="${STATE[stateKey]?'':'display:none'}">${content}</div>`;

  // Freshness of the current cheapest sell order — how recently the floor was
  // set and how thin the sell side is (fresh floor + few sellers = price moving).
  let freshHTML="";
  const sos=d.sell_order_stats;
  if(sos){
    const sellers=sos.sell_orders_total;
    const tie=sos.orders_at_best>1?` · ${sos.orders_at_best} orders tied at the floor`:"";
    freshHTML=`<p class="muted" style="margin:-4px 0 12px" data-tip="From each order's issued timestamp. The cheapest price has held for at least this long; later sellers undercut to match it.">Cheapest sell listed <b style="color:var(--fg)">${fmtAgo(sos.age_seconds)}</b>${tie} · ${fmtNum(sellers)} sell order${sellers===1?'':'s'} at ${hub}.</p>`;
  }

  $("#dbody").innerHTML=`
    <div class="kpis">
      <div class="kpi accent"><div class="l">List profit</div><div class="v ${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</div></div>
      <div class="kpi accent"><div class="l">Instant-sell profit</div><div class="v ${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</div></div>
      <div class="kpi" data-tip="Item cost + redemption ISK per ${n}× run${n>1?'s':''} (the LP cost is shown separately).">
        <div class="l">Item + ISK cost</div><div class="v">${fmtISK(cost)}</div></div>
      <div class="kpi"><div class="l">LP cost</div><div class="v">${fmtNum(lpTot)} LP</div></div>
      <div class="kpi" data-tip="Suggested per-unit sell-order price: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">
        <div class="l">Suggested list / unit</div><div class="v">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</div></div>
      <div class="kpi"><div class="l">Volume</div><div class="v">${fmtVol(Math.max(inVol||0,outVol||0))}</div></div>
    </div>
    ${warn}
    ${sec("shoppingToggle","shoppingOpen",`Shopping list — ${n}× redemption${n>1?'s':''}`,
      d.required_items.length?`<table class="mini"><thead><tr>
          <th style="text-align:left">Required item</th><th>Total qty</th><th>Avg unit</th><th>Line cost</th><th>Volume</th></tr></thead>
          <tbody>${reqRows}
          <tr class="total"><td>Total</td><td></td><td></td><td>${fmtISK(reqCost)}</td><td>${reqVolMissing?'?':fmtVol(reqVol)}</td></tr></tbody></table>
      <h3 id="lotTrackerToggle" style="cursor:pointer;user-select:none">${STATE.lotTrackerOpen?'▼':'▶'} Lot tracker</h3>
      <div class="lot-tracker" style="${STATE.lotTrackerOpen?'':'display:none'}">${d.required_items.map(it=>`
        <div class="lot-row" data-tid="${it.type_id}" data-need="${it.quantity*n}">
          <div class="lot-label">${it.name} <span class="lot-need">× ${fmtNum(it.quantity*n)} needed</span></div>
          <div class="lot-controls">
            <input type="number" class="lot-num" min="1" placeholder="qty" data-tip="Type a quantity, then press Enter or Space to add">
            <div class="lot-tags"></div>
            <span class="lot-sum"></span>
          </div>
        </div>`).join("")}
      </div>`
        :`<div class="muted">No required items — just LP + ISK.</div>`)}
    ${recipeHTML}
    ${sec("costToggle","costOpen","Cost breakdown",`
      <table class="mini"><tbody>
        <tr><td>Required items total</td><td>${fmtISK(reqCost)}</td></tr>
        <tr><td>Redemption ISK</td><td>${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td>Total acquisition cost</td><td>${fmtISK(cost)}</td></tr>
      </tbody></table>`)}
    ${sec("cargoToggle","cargoOpen","Cargo volume",`
      <table class="mini"><tbody>
        <tr><td style="text-align:left">Required items → LP corp station</td><td>${fmtVol(inVol)}</td></tr>
        <tr><td style="text-align:left">Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → ${hub}</td><td>${fmtVol(outVol)}</td></tr>
        <tr class="total"><td style="text-align:left">Ship cargo needed (larger leg)</td><td>${fmtVol(Math.max(inVol||0,outVol||0))}</td></tr>
      </tbody></table>`)}
    ${sec("saleToggle","saleOpen","Profit breakdown",`
      <div class="recipe-list-item" style="border:1px solid var(--line2);border-radius:6px;padding:9px 12px;margin-bottom:12px">
        <span class="name" data-tip="Per-unit price to put on your sell order. The lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">Suggested list price <span style="color:var(--dim2)">/ unit</span></span>
        <span class="val isk">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</span>
      </div>
      ${d.suggested_list===null?'':`<p class="muted" style="margin:-4px 0 6px">Lowest sell ${d.ask===null?'—':fmtISK(d.ask)} · 30-day fair value ${d.fair_price===null?'—':fmtISK(d.fair_price)}.</p>`}
      ${freshHTML}
      <table class="mini"><thead><tr>
        <th style="text-align:left"></th>
        <th data-tip="Sell value (listed at ask) — list the reward at the lowest sell order and pay sales tax + broker fee.">List<br><span style="color:var(--dim);font-weight:400">sell order</span></th>
        <th data-tip="Sell value (walking buy orders) — instant-sell the reward into the highest buy orders and pay sales tax only.">Instant sell<br><span style="color:var(--dim);font-weight:400">buy order</span></th>
      </tr></thead><tbody>
        <tr><td style="text-align:left">Sell value</td>
          <td>${grossP===null?'—':fmtISK(grossP)}</td>
          <td>${grossI===null?'—':fmtISK(grossI)}</td></tr>
        <tr><td style="text-align:left">− Sales tax (${(tax*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(taxP)}</td>
          <td class="neg">${grossI===null?'—':'−'+fmtISK(taxI)}</td></tr>
        <tr><td style="text-align:left">− Broker fee (${(broker*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(brokerP)}</td>
          <td style="color:var(--dim)">n/a</td></tr>
        <tr class="subtotal"><td style="text-align:left">Net revenue</td>
          <td>${revenueP===null?'—':fmtISK(revenueP)}</td>
          <td>${revenueI===null?'—':fmtISK(revenueI)}</td></tr>
        <tr><td style="text-align:left">− Items cost</td>
          <td class="neg">−${fmtISK(reqCost)}</td><td class="neg">−${fmtISK(reqCost)}</td></tr>
        <tr><td style="text-align:left">− Redemption ISK</td>
          <td class="neg">−${fmtISK(isk_fee)}</td><td class="neg">−${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td style="text-align:left">Profit</td>
          <td class="${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</td>
          <td class="${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</td></tr>
      </tbody></table>
      <p class="muted" style="margin-top:14px">Costs use the live ${hub} order book.
        List values the reward at the lowest sell order (sales tax + broker fee);
        instant-sell walks down the buy orders (sales tax only).</p>`)}`;
  bindLotCalcs(savedLots);
}

// LP control wiring
$("#go").onclick = ()=>scan(false);
$("#refresh").onclick = ()=>scan(true);
let ALL_CORPS=[], _corpsLoading=false, _corpsRetry=0;
async function _fetchCorps(){
  if(_corpsLoading||_corpsRetry>8) return;
  _corpsLoading=true;
  try{
    const r=await (await fetch("/api/corps")).json();
    if(Array.isArray(r)&&r.length){
      ALL_CORPS=r; _corpsRetry=0;
      if(document.activeElement===_corpInput&&_corpInput.value.length>=2)
        _corpOpen(_corpInput.value);
    } else {
      _corpsRetry++;
      setTimeout(_fetchCorps, 3000);
    }
  }catch(e){ _corpsRetry++; setTimeout(_fetchCorps,3000); }
  _corpsLoading=false;
}
_fetchCorps();

// ── Corp search dropdown ──────────────────────────────────────────────────
// Appended to <body> so no parent CSS interferes.
const _corpInput=$("#corp");
let _corpHi=-1;
const _corpDrop=document.createElement("div");
_corpDrop.className="corp-drop";
_corpDrop.style.display="none";
document.body.appendChild(_corpDrop);

function _corpClose(){ _corpDrop.style.display="none"; _corpHi=-1; }
function _corpItems(){ return _corpDrop.querySelectorAll(".corp-drop-item"); }

function _corpSelect(name){
  _corpInput.value=name; _corpClose();
  if(typeof updateMyLpBadge==="function") updateMyLpBadge();  // lock LP budget to this corp's character LP
  saveLS(); clearTimeout(lpScanTimer); scan(false);
}

function _corpOpen(q){
  if(!q||q.length<2){ _corpClose(); return; }
  if(!ALL_CORPS.length){ _fetchCorps(); }
  const lower=q.toLowerCase();
  const hits=ALL_CORPS.filter(c=>c.name.toLowerCase().includes(lower)).slice(0,20);
  _corpDrop.innerHTML = hits.length
    ? hits.map(c=>`<div class="corp-drop-item">${c.name.replace(/</g,"&lt;")}</div>`).join("")
    : `<div class="corp-drop-empty">${ALL_CORPS.length?'No match':'Loading corp list — retrying…'}</div>`;
  _corpDrop.querySelectorAll(".corp-drop-item").forEach(el=>{
    el.addEventListener("mousedown",e=>{ e.preventDefault(); _corpSelect(el.textContent); });
  });
  _corpHi=-1;
  const r=_corpInput.getBoundingClientRect();
  Object.assign(_corpDrop.style,{
    top:(r.bottom+3)+"px",
    left:r.left+"px",
    width:Math.max(240,r.width)+"px",
    display:"block"
  });
}

function _corpHighlight(idx){
  const items=_corpItems();
  items.forEach(el=>el.classList.remove("hi"));
  _corpHi=Math.max(-1,Math.min(idx,items.length-1));
  if(_corpHi>=0){ items[_corpHi].classList.add("hi"); items[_corpHi].scrollIntoView({block:"nearest"}); }
}

_corpInput.addEventListener("input",e=>_corpOpen(e.target.value));
_corpInput.addEventListener("blur",()=>setTimeout(_corpClose,150));
_corpInput.addEventListener("keydown",e=>{
  const items=_corpItems();
  if(e.key==="ArrowDown"){ e.preventDefault(); _corpHighlight(_corpHi+1); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); _corpHighlight(_corpHi-1); }
  else if(e.key==="Enter"){
    if(_corpHi>=0&&items[_corpHi]){ _corpSelect(items[_corpHi].textContent); }
    else{ clearTimeout(lpScanTimer); scan(false); }
  }
  else if(e.key==="Escape"){ _corpClose(); }
});
document.addEventListener("click",e=>{ if(!_corpInput.contains(e.target)&&!_corpDrop.contains(e.target)) _corpClose(); });
let lpScanTimer;
function scheduleScan(delay=800){ clearTimeout(lpScanTimer); lpScanTimer=setTimeout(()=>scan(false),delay); }
["#lp","#maxspread","#market"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change",()=>{ saveLS(); scheduleScan(800); });
  if(sel!=="#market") el.addEventListener("input",()=>{ saveLS(); scheduleScan(800); });
});
// Global tax/broker affects LP (rescan) + Industry (recalc) + Arb
["#g-tax","#g-broker"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change",()=>{ saveLS(); scheduleScan(800); recalcIndProfits(); });
  el.addEventListener("input",()=>{ saveLS(); scheduleScan(800); recalcIndProfits(); });
});
$("#toggleIlliquid").onchange=()=>{
  STATE.hideIlliquid=$("#toggleIlliquid").checked;
  fetch(`/api/prefs?hide_illiquid=${STATE.hideIlliquid?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
$("#toggleAffordable").onchange=()=>{
  STATE.hideUnaffordable=$("#toggleAffordable").checked;
  fetch(`/api/prefs?hide_unaffordable=${STATE.hideUnaffordable?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
$("#lp-search").addEventListener("input", ()=>{
  $("#lp-search-clear").classList.toggle("hidden", !$("#lp-search").value);
  renderTable();
});
$("#lp-search-clear").addEventListener("click", ()=>{
  $("#lp-search").value="";
  $("#lp-search-clear").classList.add("hidden");
  renderTable();
  $("#lp-search").focus();
});
// Tradeability balance presets — set the liquidity↔competition weight, re-rank.
function syncBalanceButtons(){
  document.querySelectorAll(".balance-btn").forEach(b=>
    b.classList.toggle("on", parseFloat(b.dataset.w)===STATE.tradeWeight));
}
document.querySelectorAll(".balance-btn").forEach(b=>{
  b.onclick=()=>{
    STATE.tradeWeight=parseFloat(b.dataset.w);
    syncBalanceButtons();
    fetch(`/api/prefs?trade_weight=${STATE.tradeWeight}`).catch(()=>{}); saveLS();
    renderTable();
  };
});
syncBalanceButtons();
setInterval(renderLPStatus, 30000);

// ══════════════════════════════════════════════════════════════════════════
// PRICE HISTORY CHART
// ══════════════════════════════════════════════════════════════════════════
const _STATION_TO_REGION = {
  60003760:10000002, 60008494:10000043,
  60004588:10000030, 60011866:10000032, 60005686:10000042,
};
const _histCache = {};
const _CHART_PAD = {t:18,r:76,b:20,l:6};

function _sma(vals, n){
  return vals.map((_,i)=>i<n-1?null:vals.slice(i-n+1,i+1).reduce((s,v)=>s+v,0)/n);
}

function _drawChart(canvas, hist, currentPrice){
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||560, H=canvas.offsetHeight||160;
  canvas.width=W*dpr; canvas.height=H*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,W,H);

  if(!hist.length){
    ctx.fillStyle='#5a7a95'; ctx.font='12px system-ui'; ctx.textAlign='center';
    ctx.fillText('No market history for this region',W/2,H/2); return;
  }

  const PAD=_CHART_PAD;
  const volH=Math.floor(H*.22);
  const priceH=H-PAD.t-PAD.b-volH-2;
  const cW=W-PAD.l-PAD.r;
  const n=hist.length;

  const avgs=hist.map(d=>d.average);
  const vols=hist.map(d=>d.volume);
  const maArr=_sma(avgs,30);
  const ath=Math.max(...avgs);
  const allP=[...avgs,...hist.map(d=>d.highest),...hist.map(d=>d.lowest)].filter(Boolean);
  if(currentPrice) allP.push(currentPrice);
  const pMin=Math.min(...allP)*.99, pMax=Math.max(...allP)*1.01;
  const vMax=Math.max(...vols)||1;

  const px=i=>PAD.l+(i/Math.max(n-1,1))*cW;
  const py=v=>PAD.t+priceH*(1-(v-pMin)/(pMax-pMin));
  const vy=v=>H-PAD.b-(v/vMax)*volH;

  // Grid
  ctx.strokeStyle='rgba(31,48,68,.9)'; ctx.lineWidth=.5;
  for(let i=0;i<=3;i++){
    const y=PAD.t+(priceH/3)*i;
    ctx.beginPath(); ctx.moveTo(PAD.l,y); ctx.lineTo(W-PAD.r,y); ctx.stroke();
  }

  // Reference lines (ATH and current price)
  ctx.save(); ctx.lineWidth=1;
  ctx.setLineDash([3,3]);
  ctx.strokeStyle='rgba(224,85,85,.55)';
  ctx.beginPath(); ctx.moveTo(PAD.l,py(ath)); ctx.lineTo(W-PAD.r,py(ath)); ctx.stroke();
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.strokeStyle='rgba(76,175,118,.55)';
    ctx.beginPath(); ctx.moveTo(PAD.l,py(currentPrice)); ctx.lineTo(W-PAD.r,py(currentPrice)); ctx.stroke();
  }
  ctx.restore();

  // Volume bars (green above MA, red below)
  const bw=Math.max(1,cW/n*.7);
  hist.forEach((d,i)=>{
    const above=maArr[i]===null||d.average>=maArr[i];
    ctx.fillStyle=above?'rgba(76,175,118,.28)':'rgba(224,85,85,.18)';
    const yTop=vy(d.volume);
    ctx.fillRect(px(i)-bw/2,yTop,bw,H-PAD.b-yTop);
  });

  // 30-day MA line
  ctx.save(); ctx.strokeStyle='#f0c040'; ctx.lineWidth=1.2;
  ctx.beginPath(); let maFirst=true;
  maArr.forEach((v,i)=>{
    if(v===null) return;
    if(maFirst){ctx.moveTo(px(i),py(v));maFirst=false;}
    else ctx.lineTo(px(i),py(v));
  });
  ctx.stroke(); ctx.restore();

  // Price area gradient fill
  const grad=ctx.createLinearGradient(0,PAD.t,0,PAD.t+priceH);
  grad.addColorStop(0,'rgba(79,195,247,.18)');
  grad.addColorStop(1,'rgba(79,195,247,.01)');
  ctx.beginPath();
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.lineTo(px(n-1),PAD.t+priceH); ctx.lineTo(px(0),PAD.t+priceH);
  ctx.closePath(); ctx.fillStyle=grad; ctx.fill();

  // Price line
  ctx.beginPath(); ctx.strokeStyle='#4fc3f7'; ctx.lineWidth=1.5;
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.stroke();

  // Right-side labels
  ctx.font='9px system-ui'; ctx.textAlign='left';
  ctx.fillStyle='#e05555';
  ctx.fillText('ATH '+fmtISK(ath),W-PAD.r+3,py(ath)+3);
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.fillStyle='#4caf76';
    ctx.fillText(fmtISK(currentPrice),W-PAD.r+3,py(currentPrice)+3);
  }
  const lastMA=maArr[n-1];
  if(lastMA){ ctx.fillStyle='#f0c040'; ctx.fillText('MA '+fmtISK(lastMA),W-PAD.r+3,py(lastMA)+3); }

  // X-axis date labels
  ctx.fillStyle='#3d5a70'; ctx.font='8px system-ui'; ctx.textAlign='center';
  const step=Math.ceil(n/5);
  for(let i=0;i<n;i+=step) ctx.fillText(hist[i].date.slice(5),px(i),H-PAD.b+10);
  if((n-1)%step!==0) ctx.fillText(hist[n-1].date.slice(5),px(n-1),H-PAD.b+10);
}

function _chartStats(hist, currentPrice){
  if(!hist.length) return '';
  const avgs=hist.map(d=>d.average);
  const ath=Math.max(...avgs);
  const lastMA=_sma(avgs,30).at(-1);
  const price=currentPrice||avgs.at(-1);
  const pctAth=ath>0?((price-ath)/ath*100):null;
  const pctMA=lastMA?((price-lastMA)/lastMA*100):null;
  let s=`<span data-tip="Latest sell price — the figure used for profit calculations.">`
    +`<span class="k">Current</span><span class="v" style="color:var(--cyan)">${fmtISK(price)}</span></span>`;
  if(pctAth!==null){
    const col=pctAth>=-3?'var(--red)':pctAth>=-15?'var(--yellow)':'var(--dim)';
    s+=`<span data-tip="All-time high daily average over the chart window, and how far current price sits below it.">`
      +`<span class="k">ATH</span><span class="v">${fmtISK(ath)}</span>`
      +`<span class="d" style="color:${col}">${pctAth.toFixed(1)}%</span></span>`;
  }
  if(pctMA!==null){
    const col=pctMA>=0?'var(--green2)':'var(--red)';
    s+=`<span data-tip="Current price vs the 30-day moving average. Positive means trading above trend.">`
      +`<span class="k">vs 30d MA</span><span class="v">${fmtISK(lastMA)}</span>`
      +`<span class="d" style="color:${col}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}% ${pctMA>=0?'▲':'▼'}</span></span>`;
  }
  return s;
}

async function _loadHistory(typeId, regionId){
  const k=`${typeId}_${regionId}`;
  if(!_histCache[k]){
    try{
      const d=await (await fetch(`/api/history?type_id=${typeId}&region_id=${regionId}`)).json();
      _histCache[k]=(d.history||[]).slice(-90);
    }catch{ _histCache[k]=[]; }
  }
  return _histCache[k];
}

async function _attachChart(canvas, tipEl, statsEl, typeId, regionId, currentPrice, title=''){
  canvas.style.opacity='.4';
  const hist=await _loadHistory(typeId, regionId);
  canvas.style.opacity='1';
  _drawChart(canvas, hist, currentPrice);
  if(statsEl) statsEl.innerHTML=_chartStats(hist, currentPrice);
  // Wire expand button if the parent wrap has one
  const expandBtn=canvas.parentElement&&canvas.parentElement.querySelector('.chart-expand-btn');
  if(expandBtn) expandBtn.onclick=()=>openExpandChart(typeId,regionId,currentPrice,title);
  if(!tipEl) return;
  const crossEl=canvas.parentElement&&canvas.parentElement.querySelector('.chart-cross');
  canvas.onmousemove=e=>{
    if(!hist.length) return;
    const r=canvas.getBoundingClientRect();
    const W=canvas.offsetWidth||r.width;
    // Map mouse X into the data drawing area (accounts for left/right padding)
    const drawW=W-_CHART_PAD.l-_CHART_PAD.r;
    const xInDraw=Math.max(0,Math.min(drawW,(e.clientX-r.left)-_CHART_PAD.l));
    const idx=Math.round(xInDraw/Math.max(drawW,1)*(hist.length-1));
    // Snap crosshair to the exact data-point x
    const crossX=_CHART_PAD.l+idx/Math.max(hist.length-1,1)*drawW;
    if(crossEl){crossEl.style.left=crossX+'px';crossEl.style.display='block';}
    const d=hist[idx];
    const ma=_sma(hist.map(h=>h.average),30)[idx];
    const pctMA=ma?((d.average-ma)/ma*100):null;
    const tx=Math.min(crossX+12,W-158);
    const ty=Math.max(2,e.clientY-r.top-75);
    tipEl.style.cssText=`display:block;left:${tx}px;top:${ty}px`;
    tipEl.innerHTML=`<div style="color:var(--dim);margin-bottom:2px">${d.date}</div>`
      +`<div>Avg <b style="color:var(--cyan)">${fmtISK(d.average)}</b></div>`
      +`<div>H/L ${fmtISK(d.highest)} / ${fmtISK(d.lowest)}</div>`
      +(ma?`<div>MA30 ${fmtISK(ma)} <span style="color:${pctMA>=0?'var(--green2)':'var(--red)'}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}%</span></div>`:'')
      +`<div style="color:var(--dim)">Vol ${fmtNum(d.volume)}</div>`;
  };
  canvas.onmouseleave=()=>{
    tipEl.style.display='none';
    if(crossEl) crossEl.style.display='none';
  };
}

// ══════════════════════════════════════════════════════════════════════════
// ARB TAB
// ══════════════════════════════════════════════════════════════════════════
let ARB = {rows:[], sort:{key:"isk_opportunity", dir:-1}, colw:{}, lastData:null, avoidLowsec:false, es:null};
let ARB_RESIZING = false;

const ARB_COLS = [
  {k:"name",           t:"Item",        w:240, tip:"Item to flip."},
  {k:"sell_price",     t:"Ask",         w:120, tip:"Lowest sell order — what you pay to buy the item.", f:fmtISK},
  {k:"buy_price",      t:"Bid",         w:120, tip:"Highest buy order — what you receive when you sell instantly.", f:fmtISK},
  {k:"net_per_unit",   t:"Net/u",       w:105, tip:"Profit per unit after sales tax.", f:fmtISK, pn:true},
  {k:"margin_pct",     t:"Margin %",    w: 80, tip:"Net profit as % of ask price.", f:v=>v.toFixed(1)+"%", pn:true},
  {k:"flippable_qty",  t:"Qty",         w: 75, tip:"Units available (min of sell vol and buy vol).", f:fmtNum},
  {k:"isk_opportunity",t:"ISK Opp",     w:115, tip:"Total ISK profit if you flip all available units.", f:fmtISK, pn:true},
  {k:"total_volume",   t:"Vol m³",      w: 90, tip:"Total cargo volume for the flippable quantity.", f:v=>v===null?"?":fmtVol(v)},
  {k:"sell_station",   t:"From",        w:220, tip:"Station where you buy (sell order location)."},
  {k:"from_sec",       t:"Sec",         w: 52, tip:"Security status of From station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"from_sec_band"},
  {k:"buy_station",    t:"To",          w:220, tip:"Station where you deliver and sell.", cls:""},
  {k:"to_sec",         t:"Sec",         w: 52, tip:"Security status of To station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"to_sec_band"},
  {k:"jumps",          t:"Jumps",       w: 65, tip:"Jump count From→To (0 = same station).", f:fmtNum},
  {k:"risk",           t:"Risk",        w: 80, tip:"SAFE = all highsec. LOWSEC/NULLSEC = route touches lower security.", riskBand:"risk_band"},
];

function arbSetColgroup(){
  $("#arb-cg").innerHTML=ARB_COLS.map(c=>{
    const w=ARB.colw[c.k]; return `<col${w?` style="width:${w}px"`:""}>`;
  }).join("");
}

function startArbResize(e, key){
  e.preventDefault(); e.stopPropagation();
  ARB_RESIZING=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#arb-tbl").style.tableLayout="fixed";
  const startX=e.clientX, startW=ARB.colw[key]||80;
  function mm(ev){ ARB.colw[key]=Math.max(40,startW+(ev.clientX-startX)); arbSetColgroup(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    setTimeout(()=>{ ARB_RESIZING=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

function renderArbTable(){
  const thead=$("#arb-tbl thead"), tbody=$("#arb-tbl tbody");
  const haveW=ARB_COLS.every(c=>ARB.colw[c.k]);
  $("#arb-tbl").style.tableLayout=haveW?"fixed":"auto";
  arbSetColgroup();
  thead.innerHTML="<tr>"+ARB_COLS.map(c=>{
    const active=ARB.sort.key===c.k;
    const arrow=active?(ARB.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(ARB_RESIZING){ ARB_RESIZING=false; return; }
      const k=th.dataset.k;
      if(ARB.sort.key===k) ARB.sort.dir*=-1;
      else ARB.sort={key:k, dir:k==="name"||k==="sell_station"||k==="buy_station"?1:-1};
      renderArbTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startArbResize(e,ARB_COLS[i].k));
  });
  if(!haveW){
    requestAnimationFrame(()=>{
      thead.querySelectorAll("th").forEach((th,i)=>{
        const c=ARB_COLS[i];
        ARB.colw[c.k]=ARB.colw[c.k]||c.w||Math.ceil(th.getBoundingClientRect().width);
      });
      $("#arb-tbl").style.tableLayout="fixed"; arbSetColgroup();
    });
  }
  const rows=[...ARB.rows].sort((a,b)=>{
    const k=ARB.sort.key, d=ARB.sort.dir;
    let x=a[k], y=b[k];
    if(typeof x==="string") return x.localeCompare(y)*d;
    if(x===null) x=-Infinity; if(y===null) y=-Infinity;
    return (x-y)*d;
  });
  tbody.innerHTML=rows.map((r,i)=>{
    const tds=ARB_COLS.map(c=>{
      let v=r[c.k], txt=c.f?c.f(v):(v===null||v===undefined?"-":v);
      let cls=c.cls||"";
      if(c.secBand) cls+=" sec-"+r[c.secBand];
      if(c.riskBand) cls+=" risk-"+r[c.riskBand];
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      const titleAttr=(c.k==="sell_station"||c.k==="buy_station")&&v?` data-tip="${String(v).replace(/"/g,'&quot;')}"` :"";
      return `<td class="${cls.trim()}"${titleAttr}>${txt}</td>`;
    }).join("");
    return `<tr style="cursor:pointer" data-ridx="${i}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach((tr,i)=>{
    tr.onclick=()=>{
      if(ARB_RESIZING){ARB_RESIZING=false;return;}
      openArbChart(rows[i]);
    };
  });
}

function renderArbStatus(){
  const d=ARB.lastData; if(!d||ACTIVE_TAB!=="arb") return;
  const mode=d.cross_station?`Cross-station ≤${d.max_jumps}J RT`:"Same-station";
  const stale = d.snap_expires && (Date.now()/1000) > d.snap_expires;
  const staleNote = stale
    ? ` <span style="color:var(--yellow);font-size:12px">· order book expired — click ⟳ Refresh for latest prices</span>`
    : "";
  setStatus(
    `<span class="pill"><b>${d.region_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> deals · <b>${d.total_spreads}</b> spreads · ${mode}</span>`
    +`<span class="ts">book ${fmtTs(d.snap_fetched_at)} · scan ${fmtTs(d.scanned_at)}</span>`
    +staleNote);
}

function showArbProgress(msg, sub, pct){
  $("#arb-tbl").classList.add("hidden");
  $("#arb-progress").classList.remove("hidden");
  $("#arb-prog-label").textContent = msg;
  $("#arb-prog-sub").textContent = sub || "";
  $("#arb-prog-fill").style.width = (pct || 0) + "%";
}
function hideArbProgress(){
  $("#arb-progress").classList.add("hidden");
  $("#arb-tbl").classList.remove("hidden");
}

function scanArb(){
  // Close any in-flight scan.
  if(ARB.es){ ARB.es.close(); ARB.es=null; }

  const btn=$("#arb-go");
  btn.disabled=true; btn.textContent="Scanning…";

  const arbTax=pctToFrac($("#g-tax").value)+pctToFrac($("#g-broker").value);
  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    String(arbTax),
    min_isk:      $("#arb-minisk").value||"0",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });

  showArbProgress("Connecting to ESI…", "", 1);
  setStatus("Scanning…");

  const es = new EventSource("/api/arb/scan?"+p);
  ARB.es = es;

  es.onmessage = e => {
    let data;
    try{ data=JSON.parse(e.data); }catch(err){ return; }

    if(data.type==="progress"){
      showArbProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg + (data.sub ? " — "+data.sub : ""));

    } else if(data.type==="result"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      ARB.rows=data.rows; ARB.lastData=data;
      hideArbProgress();
      renderArbStatus(); renderArbTable();

    } else if(data.type==="error"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      hideArbProgress();
      setStatus(data.error, true);
    }
  };

  es.onerror = () => {
    es.close(); ARB.es=null;
    btn.disabled=false; btn.textContent="Scan";
    hideArbProgress();
    setStatus("Connection error — server may have stopped.", true);
  };
}

function saveArbPrefs(){
  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    pctToFrac($("#g-tax").value),
    min_isk:      $("#arb-minisk").value||"",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });
  fetch("/api/arb/prefs?"+p).catch(()=>{}); saveLS();
}
function updateArbJumpsVisibility(){
  const cross=$("#arb-cross").value==="1";
  $("#arb-maxjumps-field").style.display=cross?"":"none";
}
$("#arb-cross").addEventListener("change",()=>{ updateArbJumpsVisibility(); saveArbPrefs(); });
["#arb-region","#arb-minisk","#arb-maxjumps","#arb-route"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveArbPrefs);
  el.addEventListener("input", saveArbPrefs);
});
$("#arb-go").onclick=()=>scanArb();
$("#arb-toggleLowsec").onclick=()=>{
  ARB.avoidLowsec=!ARB.avoidLowsec;
  $("#arb-toggleLowsec").classList.toggle("active",ARB.avoidLowsec);
  saveArbPrefs();
  if(ARB.rows.length) scanArb(false);
};
setInterval(renderArbStatus, 30000);

function openExpandChart(typeId, regionId, currentPrice, title){
  document.getElementById('arbChartModal').classList.add('hidden');
  document.getElementById('chartExpandTitle').textContent=title||'';
  document.getElementById('chartExpandStats').textContent='';
  document.getElementById('chartExpandModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('chartExpandCanvas');
    if(c) _attachChart(c,document.getElementById('chartExpandTip'),document.getElementById('chartExpandStats'),typeId,regionId,currentPrice,title);
  });
}

function openArbChart(row){
  const regionId=parseInt($("#arb-region").value)||10000002;
  document.getElementById('arbChartTitle').textContent=row.name;
  document.getElementById('arbChartStats').textContent='';
  document.getElementById('arbChartModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('arbChartCanvas');
    if(c) _attachChart(c,document.getElementById('arbChartTip'),document.getElementById('arbChartStats'),row.type_id,regionId,row.sell_price||null,row.name);
  });
}
(()=>{
  const arbModal=document.getElementById('arbChartModal');
  const expModal=document.getElementById('chartExpandModal');
  document.getElementById('arbChartClose').onclick=()=>arbModal.classList.add('hidden');
  document.getElementById('chartExpandClose').onclick=()=>expModal.classList.add('hidden');
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){arbModal.classList.add('hidden');expModal.classList.add('hidden');}
  });
  arbModal.onclick=e=>{if(e.target===arbModal) arbModal.classList.add('hidden');};
  expModal.onclick=e=>{if(e.target===expModal) expModal.classList.add('hidden');};
})();

// ══════════════════════════════════════════════════════════════════════════
// INDUSTRY TAB
// ══════════════════════════════════════════════════════════════════════════
let IND = {rows:[], sort:{key:"isk_per_hour_patient", dir:-1}, lastData:null, es:null,
           groupsLoaded:false, profiles:[], favorites:new Set(), hidden:new Set(),
           timers:{}, savedGroup:null, openDetail:null, colOrder:null,
           colw:{}, colVis:{}, detailRuns:1,
           fillTotal:0, fillDone:0, tradeWeight:0.5,
           sections:{fav:true, owned:true, hidden:false, all:true}};
// Bumped whenever a scan starts or a new fill begins, so an in-flight background
// tradeability fill from a previous scan knows to abandon itself.
let IND_FILL_TOKEN = 0;

const fmtDur = s => {
  if(s===null||s===undefined) return "—";
  const d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.round((s%3600)/60);
  if(d>0) return `${d}d ${h}h`;
  return h>0 ? `${h}h ${m}m` : `${m}m`;
};
const fmtPct1 = v => (v===null||v===undefined) ? "—" : (v*100).toFixed(1)+"%";
const fmtDaysSell = v => (v===null||v===undefined) ? "—" : (v<1 ? "<1 d" : v.toFixed(1)+" d");

function computeIndTradeability(){
  const loaded=IND.rows.filter(r=>r.liq_loaded && r.daily_vol!==null);
  if(!loaded.length){ IND.rows.forEach(r=>r.tradeability=null); return; }
  const vols=loaded.map(r=>r.daily_vol);
  const days=loaded.map(r=> r.days_to_sell===null ? Infinity : r.days_to_sell);
  const w=IND.tradeWeight;
  const pctRank=(arr,v,higherBetter)=>{
    const n=arr.length; if(n<=1) return 100;
    let beats=0;
    for(const x of arr){ if(x===v) continue; if(higherBetter? v>x : v<x) beats++; }
    return beats/(n-1)*100;
  };
  for(const r of IND.rows){
    if(!r.liq_loaded || r.daily_vol===null){ r.tradeability=null; continue; }
    const liq=pctRank(vols, r.daily_vol, true);
    const comp=pctRank(days, r.days_to_sell===null?Infinity:r.days_to_sell, false);
    r.tradeability=Math.round(w*liq + (1-w)*comp);
  }
}

const IND_COLS = [
  {k:"_fav",               t:"★",              w: 30, tip:"Add to Watchlist — track blueprints you don't own. Your owned blueprints appear in 'My Blueprints' automatically.", raw:true},
  {k:"product_name",       t:"Item",           w:210, tip:"The manufactured item. * = an input has no sell price at the source hub."},
  {k:"tech_level",         t:"Tech",           w: 46, tip:"Tech level.", f:v=>v?("T"+v):"—"},
  {k:"_timer",             t:"⏱ Timer",        w: 84, tip:"Live countdown for your running manufacturing job on this blueprint, pulled from EVE (refreshed every 5 min). Log in with EVE to populate.", raw:true},
  {k:"isk_per_hour_patient",t:"ISK/hr list",   w:110, tip:"Profit per hour when selling at the lowest ask (patient list order).", f:fmtISK, pn:true},
  {k:"isk_per_hour_instant",t:"ISK/hr instant",w:110, tip:"Profit per hour when selling instantly at the highest bid.", f:fmtISK, pn:true},
  {k:"profit_patient",     t:"Profit list",    w:105, tip:"Profit per run selling at the lowest ask (patient list order).", f:fmtISK, pn:true},
  {k:"profit_instant",     t:"Profit instant", w:105, tip:"Profit per run selling instantly at the highest bid.", f:fmtISK, pn:true},
  {k:"margin_patient",     t:"Margin list",    w: 75, tip:"Profit as % of cost when selling at the lowest ask.", f:fmtPct1, pn:true},
  {k:"margin_instant",     t:"Margin instant", w: 75, tip:"Profit as % of cost when selling instantly at the highest bid.", f:fmtPct1, pn:true},
  {k:"build_time",         t:"Build time",     w: 72, tip:"Time for one run after TE + skills.", f:fmtDur},
  {k:"total_cost",         t:"Cost/run",       w: 98, tip:"Materials + job install + blueprint, per run.", f:fmtISK},
  {k:"bp_price",           t:"BP price",       w:108, tip:"Cheapest BPO sell price in The Forge (open an item to see WHERE it's sold). 'invent' = T2, obtained by invention. 'BPO' = you own the original. 'BPC (N)' = you have a limited-run copy.", f:(v,r)=> r.owned_bp_me_te?(r.owned_is_bpo?"BPO":`BPC (${r.owned_max_runs})`):(v!=null?fmtISK(v):(r.bp_source==="invention"?"invent":"—")), cls:"bp-buy"},
  {k:"payback_runs",       t:"Payback",        w: 88, tip:"Runs of profit needed to recoup the BPO purchase (T1 you don't own).", f:(v,r)=> r.owned_bp_me_te?"—":(v==null?"—":fmtNum(v)+" runs")},
  {k:"ask",                t:"Sell price",     w: 98, tip:"Item's lowest sell order at the source hub.", f:v=>v===null?"—":fmtISK(v)},
  {k:"in_vol_run",         t:"Cargo in",       w: 85, tip:"m³ of materials to haul in per run.", f:v=>v?fmtVol(v):"—"},
  {k:"out_vol_run",        t:"Cargo out",      w: 85, tip:"m³ of finished items to haul out per run.", f:v=>v?fmtVol(v):"—"},
  {k:"days_to_sell",       t:"Days to sell",   w: 88, tip:"How many days to sell one run's output (output qty ÷ daily volume). Spins while the market history loads in the background.", f:(v,r)=> !r.liq_loaded ? _SPIN : fmtDaysSell(v)},
  {k:"tradeability",       t:"Tradeability",   w: 98, tip:"0–100: how realistically you can sell at your price. Blends liquidity (daily volume) and low competition (days to sell), weighted by the Balance buttons. Higher is better; ranked within this scan.", f:(v,r)=> !r.liq_loaded ? _SPIN : (v==null?"—":`<span style="color:${v>=70?'#4caf76':v>=40?'#c8a040':'#e0655a'};font-weight:600">${v}</span>`)},
  {k:"buildable",          t:"Buildable?",     w: 58, tip:"Can every required skill (at the Skills level) make it?", f:v=>v?"✓":"✗"},
];

const IND_COL_BY_KEY=Object.fromEntries(IND_COLS.map(c=>[c.k,c]));
IND.colOrder=IND_COLS.map(c=>c.k);   // user-reorderable; persisted with the rest of the IND prefs
IND_COLS.forEach(c=>{ IND.colVis[c.k]=true; IND.colw[c.k]=c.w; });
// Resolve IND.colOrder to column objects, dropping unknown keys and appending any
// columns not yet listed (so a saved order survives IND_COLS additions/removals).
function indOrderedCols(){
  const seen=new Set(), out=[];
  for(const k of IND.colOrder){ const c=IND_COL_BY_KEY[k]; if(c&&!seen.has(k)){ out.push(c); seen.add(k); } }
  for(const c of IND_COLS) if(!seen.has(c.k)){ out.push(c); seen.add(c.k); }
  return out;
}
function indVisCols(){ return indOrderedCols().filter(c=>IND.colVis[c.k]!==false); }
function indSetColgroup(){
  $("#ind-cg").innerHTML=indVisCols().map(c=>`<col style="width:${IND.colw[c.k]||c.w}px">`).join("");
}

let IND_RESIZING=false;
function startIndResize(e, key){
  e.preventDefault(); e.stopPropagation();
  IND_RESIZING=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#ind-tbl").style.tableLayout="fixed";
  const startX=e.clientX, startW=IND.colw[key]||80;
  function mm(ev){ IND.colw[key]=Math.max(40,startW+(ev.clientX-startX)); indSetColgroup(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    saveIndPrefs();
    setTimeout(()=>{ IND_RESIZING=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

// ── Industry column drag-to-reorder (mirrors the LP store) ─────────────────
let IND_DRAG_KEY=null;
function clearIndDropMarks(){
  document.querySelectorAll("#ind-tbl thead th").forEach(th=>th.classList.remove("drop-before","drop-after"));
}
function indDropAfter(th,clientX){
  const r=th.getBoundingClientRect();
  return clientX > r.left + r.width/2;
}
function reorderIndCols(srcKey,dstKey,after){
  if(!srcKey||srcKey===dstKey) return;
  const order=indOrderedCols().map(c=>c.k);
  order.splice(order.indexOf(srcKey),1);
  let to=order.indexOf(dstKey);
  if(after) to+=1;
  order.splice(to,0,srcKey);
  IND.colOrder=order;
  saveIndPrefs();
  renderIndTable();
}
function wireIndColDrag(th){
  th.addEventListener("dragstart",e=>{
    IND_DRAG_KEY=th.dataset.k;
    e.dataTransfer.effectAllowed="move";
    try{ e.dataTransfer.setData("text/plain",IND_DRAG_KEY); }catch(_){}
    th.classList.add("col-dragging");
    document.body.classList.add("col-dragging-active");
  });
  th.addEventListener("dragend",()=>{
    th.classList.remove("col-dragging");
    document.body.classList.remove("col-dragging-active");
    clearIndDropMarks();
    setTimeout(()=>{ IND_DRAG_KEY=null; },0);
  });
  th.addEventListener("dragover",e=>{
    if(!IND_DRAG_KEY) return;
    e.preventDefault();
    e.dataTransfer.dropEffect="move";
    clearIndDropMarks();
    if(th.dataset.k!==IND_DRAG_KEY)
      th.classList.add(indDropAfter(th,e.clientX)?"drop-after":"drop-before");
  });
  th.addEventListener("dragleave",()=>th.classList.remove("drop-before","drop-after"));
  th.addEventListener("drop",e=>{
    e.preventDefault();
    const after=indDropAfter(th,e.clientX);
    clearIndDropMarks();
    reorderIndCols(IND_DRAG_KEY, th.dataset.k, after);
  });
}

// ── Industry column picker (mirrors the LP store) ───────────────────────────
(function(){
  const btn=document.getElementById("indColPickerBtn");
  const picker=document.getElementById("indColPicker");
  function renderPicker(){
    picker.innerHTML=IND_COLS.map(c=>`<label><input type="checkbox" data-k="${c.k}"${IND.colVis[c.k]!==false?' checked':''}> ${c.t}</label>`).join("");
    picker.querySelectorAll("input").forEach(cb=>{
      cb.onchange=()=>{ IND.colVis[cb.dataset.k]=cb.checked; renderIndTable(); saveIndPrefs(); };
    });
  }
  btn.onclick=e=>{
    e.stopPropagation();
    if(!picker.classList.contains("hidden")){ picker.classList.add("hidden"); return; }
    renderPicker();
    const r=btn.getBoundingClientRect();
    picker.style.top=(r.bottom+4)+"px";
    picker.style.left=r.left+"px";
    picker.classList.remove("hidden");
  };
  document.addEventListener("click",()=>picker.classList.add("hidden"));
  picker.addEventListener("click",e=>e.stopPropagation());
})();

function indSortRows(rows){
  const k=IND.sort.key, d=IND.sort.dir;
  return [...rows].sort((a,b)=>{
    let x=a[k], y=b[k];
    if(typeof x==="string") return String(x).localeCompare(String(y))*d;
    if(x===null||x===undefined) x=-Infinity;
    if(y===null||y===undefined) y=-Infinity;
    return (x-y)*d;
  });
}

function indRowHtml(r, idx){
  const fav=IND.favorites.has(r.blueprint_id);
  const hid=IND.hidden.has(r.blueprint_id);
  const canHide=r.owned_bp_me_te||fav;
  const tds=indVisCols().map(c=>{
    if(c.k==="_fav"){
      const hideBtn=canHide?`<span class="ind-hide-btn" data-bp="${r.blueprint_id}" title="${hid?"Unhide":"Hide"}">${hid?"👁":"⊘"}</span>`:"";
      return `<td class="fav-cell"><span class="fav-star${fav?" on":""}" data-bp="${r.blueprint_id}" title="${fav?"Remove from Watchlist":"Add to Watchlist"}">${fav?"★":"☆"}</span>${hideBtn}</td>`;
    }
    if(c.k==="_timer"){
      const end=IND.timers[r.blueprint_id];
      if(!end) return `<td class="timer-cell">—</td>`;
      const rem=end-Date.now();
      if(rem<=0) return `<td class="timer-cell done" title="Ready">✓ Ready</td>`;
      return `<td class="timer-cell ind-live-timer" data-end="${end}" title="Crafting timer — click the row to view/edit">${fmtCountdownShort(rem)}</td>`;
    }
    let v=r[c.k], txt=c.f?c.f(v,r):(v===null||v===undefined?"—":v);
    if(c.k==="product_name"){
      if(r.missing_price) txt+=" *";
      if(r.group_name) txt+=`<span class="ind-group-sub">${r.group_name}</span>`;
    }
    let cls=c.cls||"";
    if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
    if(c.k==="buildable") cls+=v?" pos":" neg";
    return `<td class="${cls.trim()}">${txt}</td>`;
  }).join("");
  return `<tr style="cursor:pointer" data-ridx="${idx}">${tds}</tr>`;
}

function renderIndTable(){
  const thead=$("#ind-tbl thead"), tbody=$("#ind-tbl tbody");
  const vc=indVisCols();
  $("#ind-tbl").style.tableLayout="fixed";
  indSetColgroup();
  thead.innerHTML="<tr>"+vc.map(c=>{
    const active=IND.sort.key===c.k;
    const arrow=active?(IND.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`:"";
    const nosort=c.raw?' data-nosort="1"':"";
    return `<th draggable="true" data-k="${c.k}"${tip}${nosort}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    wireIndColDrag(th);   // every column can be dragged to reorder
    th.querySelector(".resizer").addEventListener("mousedown",e=>startIndResize(e,vc[i].k));
    if(th.dataset.nosort) return;
    th.onclick=()=>{
      if(IND_RESIZING){ IND_RESIZING=false; return; }
      if(IND_DRAG_KEY) return;   // tail end of a reorder, not a sort click
      const k=th.dataset.k;
      if(IND.sort.key===k) IND.sort.dir*=-1;
      else IND.sort={key:k, dir:k==="product_name"?1:-1};
      saveIndPrefs();
      renderIndTable();
    };
  });

  // Split into four sections: Favorites, My Blueprints (owned, visible),
  // Hidden (owned, explicitly hidden), All items (the rest).
  const search=($("#ind-search").value||"").trim().toLowerCase();
  const isFav=r=>IND.favorites.has(r.blueprint_id);
  const isOwned=r=>!!r.owned_bp_me_te;
  const isHidden=r=>IND.hidden.has(r.blueprint_id);
  let favs=IND.rows.filter(r=>isFav(r) && !isHidden(r));
  let myBps=IND.rows.filter(r=>isOwned(r) && !isHidden(r) && !isFav(r));
  let hiddenBps=IND.rows.filter(r=>isHidden(r));
  let rest=IND.rows.filter(r=>!isFav(r) && !isOwned(r) && !isHidden(r));
  if(search){
    const matches=r=>(r.product_name||"").toLowerCase().includes(search);
    favs=favs.filter(matches); myBps=myBps.filter(matches);
    hiddenBps=hiddenBps.filter(matches); rest=rest.filter(matches);
  } else {
    const minTrade=parseInt($("#ind-mintrade").value)||0;
    if(minTrade>0) rest=rest.filter(r=> !r.liq_loaded || (r.tradeability!=null && r.tradeability>=minTrade));
  }
  favs=indSortRows(favs); myBps=indSortRows(myBps);
  hiddenBps=indSortRows(hiddenBps); rest=indSortRows(rest);

  // Render filter chips
  const chips=$("#ind-chips");
  const hasSections=favs.length||myBps.length||hiddenBps.length;
  if(hasSections && IND.rows.length){
    const chip=(key,label,n)=>{
      const on=IND.sections[key];
      return `<span class="ind-chip${on?" active":""}" data-sect="${key}">${label} <span class="chip-count">(${n})</span></span>`;
    };
    let ch="";
    if(favs.length||IND.favorites.size) ch+=chip("fav","★ Favorites",favs.length);
    if(myBps.length||IND.rows.some(isOwned)) ch+=chip("owned","My Blueprints",myBps.length);
    if(hiddenBps.length||IND.hidden.size) ch+=chip("hidden","Hidden",hiddenBps.length);
    if(rest.length) ch+=chip("all","All Items",rest.length);
    chips.innerHTML=ch;
    chips.querySelectorAll(".ind-chip").forEach(el=>{
      el.onclick=()=>{ const k=el.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); saveLS(); };
    });
  } else { chips.innerHTML=""; }

  const ncol=vc.length;
  const sect=(key,label,n)=>{
    const col=IND.sections[key]?"":" collapsed";
    return `<tr class="ind-section${col}" data-sect="${key}"><td colspan="${ncol}"><span class="sect-arrow">▾</span>${label} — ${n}</td></tr>`;
  };

  const ordered=[];
  let html="";
  if(favs.length){
    html+=sect("fav","★ Favorites", favs.length);
    if(IND.sections.fav) favs.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  if(myBps.length){
    html+=sect("owned","My Blueprints", myBps.length);
    if(IND.sections.owned) myBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  if(hiddenBps.length){
    html+=sect("hidden","Hidden", hiddenBps.length);
    if(IND.sections.hidden) hiddenBps.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
  }
  const IND_LAZY_BATCH=60;
  let lazyRest=null, lazyIdx=0;
  if(rest.length){
    if(hasSections) html+=sect("all","All Items", rest.length);
    if(!hasSections || IND.sections.all){
      const show=Math.max(IND_LAZY_BATCH, IND._lazyRendered||0);
      const initial=rest.slice(0, Math.min(show, rest.length));
      initial.forEach(r=>{ html+=indRowHtml(r, ordered.length); ordered.push(r); });
      IND._lazyRendered=initial.length;
      if(rest.length>initial.length){ lazyRest=rest; lazyIdx=initial.length; }
    }
  }
  tbody.innerHTML=html;

  // Lazy-load remaining "All Items" rows on scroll
  if(lazyRest){
    const sentinel=document.createElement("tr");
    sentinel.className="ind-sentinel";
    sentinel.innerHTML=`<td colspan="${ncol}"></td>`;
    tbody.appendChild(sentinel);
    const wrap=$("#ind-tablewrap");
    const obs=new IntersectionObserver(entries=>{
      if(!entries[0].isIntersecting) return;
      const batch=lazyRest.slice(lazyIdx, lazyIdx+IND_LAZY_BATCH);
      if(!batch.length){ obs.disconnect(); sentinel.remove(); return; }
      let bhtml="";
      batch.forEach(r=>{ bhtml+=indRowHtml(r, ordered.length); ordered.push(r); });
      sentinel.insertAdjacentHTML("beforebegin", bhtml);
      wireIndRows(tbody, ordered);
      lazyIdx+=IND_LAZY_BATCH;
      IND._lazyRendered=lazyIdx;
      if(lazyIdx>=lazyRest.length){ obs.disconnect(); sentinel.remove(); IND._lazyRendered=lazyRest.length; }
    }, {root:wrap, rootMargin:"200px"});
    obs.observe(sentinel);
  }

  wireIndRows(tbody, ordered);
}
function wireIndRows(tbody, ordered){
  // Section header click toggles collapse
  tbody.querySelectorAll("tr.ind-section").forEach(tr=>{
    if(tr._wired) return; tr._wired=true;
    tr.onclick=()=>{ const k=tr.dataset.sect; IND.sections[k]=!IND.sections[k]; renderIndTable(); saveLS(); };
  });
  tbody.querySelectorAll("tr[data-ridx]").forEach(tr=>{
    if(tr._wired) return; tr._wired=true;
    const r=ordered[+tr.dataset.ridx];
    tr.onclick=ev=>{
      if(ev.target.classList.contains("fav-star")) return;
      if(ev.target.classList.contains("ind-hide-btn")) return;
      const box=$("#ind-detail");
      if(IND.openDetail && IND.openDetail.blueprint_id===r.blueprint_id && !box.classList.contains("hidden")){
        box.classList.add("hidden"); IND.openDetail=null;
      } else openIndDetail(r);
    };
  });
  tbody.querySelectorAll(".fav-star").forEach(star=>{
    if(star._wired) return; star._wired=true;
    star.onclick=ev=>{ ev.stopPropagation(); toggleFavorite(+star.dataset.bp); };
  });
  tbody.querySelectorAll(".ind-hide-btn").forEach(btn=>{
    if(btn._wired) return; btn._wired=true;
    btn.onclick=ev=>{ ev.stopPropagation(); toggleHidden(+btn.dataset.bp); };
  });
}

function toggleFavorite(bp){
  if(IND.favorites.has(bp)) IND.favorites.delete(bp); else IND.favorites.add(bp);
  saveIndPrefs();
  renderIndTable();
  if(IND.openDetail && IND.openDetail.blueprint_id===bp) renderIndDetail(IND.openDetail);
}
function toggleHidden(bp){
  if(IND.hidden.has(bp)) IND.hidden.delete(bp); else IND.hidden.add(bp);
  saveIndPrefs();
  renderIndTable();
}

function renderIndStatus(){
  const d=IND.lastData; if(!d||ACTIVE_TAB!=="ind") return;
  if(d.favorites_only || d.owned_only){
    setStatus(`<span class="pill"><b>${d.count.toLocaleString()}</b> blueprint${d.count===1?"":"s"} loaded</span>`
      +`<span class="ts">press Scan for full catalogue</span>`);
    return;
  }
  const fillPill = IND.fillTotal>0
    ? `<span class="pill">${_SPIN} scoring tradeability <b>${IND.fillDone.toLocaleString()}</b> / ${IND.fillTotal.toLocaleString()}</span>`
    : "";
  setStatus(
    `<span class="pill"><b>${d.count.toLocaleString()}</b> items · source <b>${d.station_name}</b></span>`
    +fillPill
    +`<span class="ts">prices ${fmtTs(d.scanned_at)}</span>`);
}

function showIndProgress(msg, sub, pct){
  $("#ind-tbl").classList.add("hidden");
  $("#ind-detail").classList.add("hidden");
  $("#ind-progress").classList.remove("hidden");
  $("#ind-prog-label").textContent=msg;
  $("#ind-prog-sub").textContent=sub||"";
  $("#ind-prog-fill").style.width=(pct||0)+"%";
}
function hideIndProgress(){
  $("#ind-progress").classList.add("hidden");
  $("#ind-tbl").classList.remove("hidden");
}

function indParams(extra){
  const p={
    market_group: $("#ind-group").value,
    station:      $("#ind-station").value,
    job_rate:     $("#ind-jobrate").value||"0",
    sales_tax:    $("#g-tax").value||"0",
    broker:       $("#g-broker").value||"0",
    runs:         "1",
    buildable_only:$("#ind-buildable").checked?"1":"0",
    include_unbuildable:$("#ind-unobtainable").checked?"1":"0",
    hide_t2:      $("#ind-hidet2").checked?"1":"0",
    min_tradeability: $("#ind-mintrade").value||"0",
    favorites:    JSON.stringify([...IND.favorites]),
  };
  return new URLSearchParams(Object.assign(p, extra||{}));
}

function scanInd(refreshSde){
  if(IND.es){ IND.es.close(); IND.es=null; }
  IND_FILL_TOKEN++; IND.fillTotal=0; IND._lazyRendered=0;
  const btn=$("#ind-go"); btn.disabled=true; btn.textContent="Scanning…";
  const p=indParams(refreshSde?{refresh_sde:"1"}:null);
  showIndProgress("Loading blueprint database…","",1);
  setStatus("Scanning…");
  const es=new EventSource("/api/ind/scan?"+p); IND.es=es;
  es.onmessage=e=>{
    let data; try{ data=JSON.parse(e.data); }catch(err){ return; }
    if(data.type==="progress"){
      showIndProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg+(data.sub?" — "+data.sub:""));
    } else if(data.type==="result"){
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      IND.rows=data.rows; IND.lastData=data;
      computeIndTradeability();
      persistScan("ind", {...IND.lastData, rows:IND.rows});
      hideIndProgress(); renderIndStatus(); renderIndTable();
      fillIndTradeability();   // score the long tail in the background
    } else if(data.type==="error"){
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      hideIndProgress(); setStatus(data.error, true);
    }
  };
  es.onerror=()=>{
    es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
    hideIndProgress(); setStatus("Connection error — server may have stopped.", true);
  };
}

// The scan scores only the top-ranked rows inline (to return fast). This walks
// the rest of the catalogue afterwards in chunks, fetching market history per
// product so EVERY item ends up with a real tradeability — gracefully: pending
// rows spin, a status pill counts progress, and the table fills in as it lands.
// A newer scan/fill cancels this one via IND_FILL_TOKEN.
async function fillIndTradeability(){
  const token=++IND_FILL_TOKEN;
  const station=(IND.lastData && IND.lastData.station_id) || $("#ind-station").value;
  // Group still-pending rows by product type so one history lookup updates every
  // blueprint that builds the same item.
  const byProduct=new Map();
  for(const r of IND.rows){
    if(r.liq_loaded) continue;
    if(!byProduct.has(r.product_id)) byProduct.set(r.product_id, []);
    byProduct.get(r.product_id).push(r);
  }
  const ids=[...byProduct.keys()];
  if(!ids.length){ IND.fillTotal=0; renderIndStatus(); return; }
  IND.fillTotal=ids.length; IND.fillDone=0; renderIndStatus();
  const CHUNK=60;
  for(let i=0;i<ids.length;i+=CHUNK){
    if(token!==IND_FILL_TOKEN) return;   // superseded by a newer scan
    const chunk=ids.slice(i,i+CHUNK);
    let liq=null;
    try{
      const p=new URLSearchParams({station:station, type_ids:chunk.join(",")});
      const d=await (await fetch("/api/ind/liquidity?"+p)).json();
      liq=d.liquidity||null;
    }catch(e){ liq=null; }
    if(token!==IND_FILL_TOKEN) return;
    for(const pid of chunk){
      const e=liq && liq[pid];
      for(const r of (byProduct.get(pid)||[])){
        if(e){
          r.daily_vol=e.daily_vol;
          r.days_to_sell=(e.daily_vol>0)?((r.out_qty*r.runs)/e.daily_vol):null;
        }
        r.liq_loaded=true;   // clear the spinner even on a failed/empty fetch
      }
    }
    IND.fillDone=Math.min(i+chunk.length, ids.length);
    computeIndTradeability();
    renderIndStatus(); renderIndTable();
  }
  IND.fillTotal=0; renderIndStatus();
  if(IND.lastData && !IND.lastData.favorites_only && !IND.lastData.owned_only)
    persistScan("ind", {...IND.lastData, rows:IND.rows});
}

// Loads all ESI-owned blueprints + favourites silently and without touching
// saved settings, so "My Blueprints" and the watchlist are visible the moment
// the Industry tab opens — before the user ever presses Scan. A later real
// Scan replaces these rows with the full category results.
function loadOwnedPreview(){
  if(IND.rows.length>0 || IND.es) return;
  const p=indParams({owned_only:"1"});
  const es=new EventSource("/api/ind/scan?"+p);
  IND.es=es;   // shares the slot scanInd() checks/clears, so a real Scan cancels this
  es.onmessage=e=>{
    let data; try{ data=JSON.parse(e.data); }catch(err){ return; }
    if(data.type==="result"){
      es.close(); IND.es=null;
      IND.rows=data.rows; IND.lastData=data;
      computeIndTradeability();
      if(ACTIVE_TAB==="ind"){ renderIndStatus(); renderIndTable(); }
      fillIndTradeability();
    } else if(data.type==="error"){
      es.close(); IND.es=null;
    }
  };
  es.onerror=()=>{ es.close(); IND.es=null; };
}

function openIndDetail(row){
  const box=$("#ind-detail");
  box.classList.remove("hidden");
  box.innerHTML=`<div class="ind-d-head">Loading ${row.product_name}…</div>`;
  box.scrollIntoView({block:"nearest"});
  const p=indParams({blueprint_id:row.blueprint_id});
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(d=>{
    if(d.error){ box.innerHTML=`<div class="ind-d-head">${d.error}</div>`; return; }
    renderIndDetail(d);
  }).catch(()=>{ box.innerHTML=`<div class="ind-d-head">Failed to load detail.</div>`; });
}

function renderIndDetail(d){
  IND.openDetail=d;   // remembered so a batch-size change can re-render this panel
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const n=Math.max(1, IND.detailRuns||1);
  // Batch figures are derived from per-run values × current run count, so they
  // track the Batch (runs) field live (no re-fetch needed).
  // Materials table = the shopping list for the whole batch: every column scales
  // with the run count (qty, cost and m3 you actually buy for N runs), with a
  // totals row so the cargo required is summed and obvious.
  const mvol=v=> v==null?"—":(v.toLocaleString(undefined,{maximumFractionDigits:v<10?2:1})+" m³");
  let matTotCost=0, matTotVol=0, matHasVol=false;
  const sortedItems=[...d.required_items].sort((a,b)=>a.name.localeCompare(b.name));
  const mats=sortedItems.map(m=>{
    const qtyBatch = m.eff_qty*n;
    const costBatch = m.line_cost==null?null:m.line_cost*n;
    const volBatch = (m.volume_each!=null)? m.eff_qty*m.volume_each*n : null;
    if(costBatch!=null) matTotCost+=costBatch;
    if(volBatch!=null){ matTotVol+=volBatch; matHasVol=true; }
    return `<tr><td>${m.name}</td><td class="num">${qtyBatch.toLocaleString()}</td>`
      +`<td class="num">${isk(m.unit_price)}</td><td class="num">${isk(costBatch)}</td>`
      +`<td class="num">${mvol(volBatch)}</td></tr>`;
  }).join("");
  const matTotal=`<tr class="ind-d-total"><td>Total — ${d.required_items.length} material${d.required_items.length===1?"":"s"}</td>`
    +`<td class="num"></td><td class="num"></td><td class="num">${isk(matTotCost)}</td>`
    +`<td class="num">${matHasVol?mvol(matTotVol):"—"}</td></tr>`;
  const inVolRun=d.required_items.reduce((s,m)=>s+((m.volume_each!=null)?m.eff_qty*m.volume_each:0),0);
  const outVolRun=(d.product.volume_each!=null)?d.product.quantity*d.product.volume_each:null;
  const inputBatch=inVolRun*n, outputBatch=outVolRun!=null?outVolRun*n:null;
  const batchCost=d.total_cost!=null?d.total_cost*n:null;
  const batchProfitL=d.profit_patient!=null?d.profit_patient*n:null;
  const batchProfitI=d.profit_instant!=null?d.profit_instant*n:null;
  const batchTime=d.build_time?d.build_time*n:null;
  const pn=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));
  // Fee/tax breakdown — re-derives the ISK amounts folded into revenue_patient
  // / revenue_instant (qty × price × rate) so they can surface as their own card.
  const qty=d.product.quantity, qtyBatchTot=qty*n;
  const brokerIsk=(d.ask!=null && d.broker_fee)?qty*d.ask*d.broker_fee*n:null;
  const taxListIsk=(d.ask!=null && d.sales_tax)?qty*d.ask*d.sales_tax*n:null;
  const taxInstantIsk=(d.bid!=null && d.sales_tax)?qty*d.bid*d.sales_tax*n:null;
  const jobCostBatch=d.job_cost!=null?d.job_cost*n:null;
  const inventionCostBatch=d.invention?d.invention_cost*n:0;
  // Cumulative runs delivered for this exact item, from the same tracker
  // backing the Character tab KPI — broken out per product there.
  const prodTrack=(AUTH.loggedIn && AUTH.data && AUTH.data.runs_tracked)
    ? AUTH.data.runs_tracked.by_product[String(d.product.type_id)] : null;
  // Break-even sell price: instant sale only pays sales tax (no broker fee), so
  // qty*price*(1-sales_tax) = total_cost solved for price. Surfaced only when
  // the instant sale is currently unprofitable.
  const minPriceInstant=(d.profit_instant!=null && d.profit_instant<0
      && d.total_cost!=null && qty>0 && d.sales_tax!=null && d.sales_tax<1)
    ? d.total_cost/(qty*(1-d.sales_tax)) : null;
  const tier=d.product.tech_level?("T"+d.product.tech_level):"";
  const esiOwned = !!d.owned_me_te;
  const isBpo = esiOwned && d.owned_me_te.is_bpo;
  const bpcRuns = esiOwned && !isBpo ? d.owned_me_te.max_runs : null;
  const ownedLabel = isBpo
    ? `BPO (ME ${d.owned_me_te.me} / TE ${d.owned_me_te.te})`
    : esiOwned ? `BPC · ${bpcRuns} run${bpcRuns===1?"":"s"} left (ME ${d.owned_me_te.me} / TE ${d.owned_me_te.te})`
    : null;
  let bpSrc;
  if(esiOwned && !isBpo && d.bp_market){
    bpSrc = `${ownedLabel} — <b>buy BPO ${isk(d.bp_market.price)}</b> at ${d.bp_market.station}`;
  } else if(esiOwned && d.bp_market){
    bpSrc = `${ownedLabel} · market ${isk(d.bp_market.price)} at ${d.bp_market.station}`;
  } else if(esiOwned){
    bpSrc = ownedLabel;
  } else if(d.bp_market){
    bpSrc = `Buy BPO ${isk(d.bp_market.price)} at ${d.bp_market.station}`
          + ` · ${fmtNum(d.bp_market.orders)} on sale in ${d.bp_market.region}`;
  } else if(d.bp_source==="invention"){
    bpSrc = "Invent (T2) — no BPO on the market; datacore cost is in Cost/run";
  } else {
    bpSrc = "Not obtainable (no BPO for sale in The Forge)";
  }
  // Payback shown regardless of ownership: how many runs of profit recoup the
  // BPO's market price (informational even if you already own it).
  let payback;
  if(d.payback_runs_patient!=null || d.payback_runs_instant!=null){
    const pl=d.payback_runs_patient!=null ? `${fmtNum(d.payback_runs_patient)} list` : "never (list)";
    const pi=d.payback_runs_instant!=null ? `${fmtNum(d.payback_runs_instant)} instant` : "never (instant)";
    payback=`${pl} / ${pi}`+(d.bp_market?` (BPO ${isk(d.bp_market.price)})`:"");
  } else if(d.bp_source==="invention") payback="n/a — invented per run";
  else if(d.bp_market) payback="never at current profit";
  else payback="—";
  // Industry job timer — read-only, driven by the character's running jobs (ESI).
  const tEnd=IND.timers[d.blueprint_id], nowMs=Date.now();
  const job=(AUTH.loggedIn && AUTH.data && AUTH.data.jobs)
    ? AUTH.data.jobs.find(j=>j.blueprint_type_id===d.blueprint_id && j.activity_id===1) : null;
  const jobRuns=job&&job.runs?` · ${job.runs} run(s)`:"";
  let timerHtml;
  if(tEnd && tEnd>nowMs){
    timerHtml=`<div class="ind-timer">
        <span class="ind-timer-remaining ind-live-timer" data-end="${tEnd}">${fmtCountdown(tEnd-nowMs)}</span>
        <span class="ind-timer-eta">ETA ${new Date(tEnd).toLocaleString([],{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'})}${jobRuns}</span>
      </div>`;
  } else if(tEnd){
    timerHtml=`<div class="ind-timer done">
        <span class="ind-timer-remaining">✓ Ready — finished ${new Date(tEnd).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</span>
      </div>`;
  } else {
    timerHtml=`<div class="ind-timer-none">${AUTH.loggedIn
        ? "No active manufacturing job for this blueprint."
        : "Log in with EVE to see your running industry jobs here."}</div>`;
  }
  let invHtml="";
  if(d.invention){
    const iv=d.invention;
    const dcs=iv.datacores.map(c=>
      `<tr><td>${c.name}</td><td class="num">${fmtNum(c.quantity)}</td>`
      +`<td class="num">${isk(c.unit_price)}</td><td class="num">${isk(c.line_cost)}</td></tr>`).join("");
    invHtml=`
      <div class="ind-d-head" style="margin-top:10px">Invention (T2)</div>
      <div class="ind-d-grid">
        <span>Success probability</span><b>${(iv.probability*100).toFixed(1)}% (base ${(iv.base_probability*100).toFixed(1)}%)</b>
        <span>Runs per invented BPC</span><b>${fmtNum(iv.runs_per_bpc)}</b>
        <span>Invention cost / T2 run</span><b>${isk(iv.cost_per_run)}</b>
      </div>
      <table class="ind-d-mats"><thead><tr><th>Datacore</th><th class="num">Qty</th>
        <th class="num">Unit</th><th class="num">Line</th></tr></thead><tbody>${dcs}</tbody></table>`;
  }
  $("#ind-detail").innerHTML=`
    <div class="ind-d-head">
      <b>${d.product.name}</b>
      <button class="ind-fav-btn${IND.favorites.has(d.blueprint_id)?" on":""}" title="${esiOwned?"Owned blueprints appear in My Blueprints automatically":"Add to Watchlist — track blueprints you don't own yet"}">${IND.favorites.has(d.blueprint_id)?"★ Watchlist":"☆ Watchlist"}</button>
      <button class="ind-copy" title="Copy item name to clipboard">⧉ Copy</button>
      <button class="ind-pull-prices${d.esi_prices?" on":""}" title="Fetch live prices directly from ESI (more accurate than Fuzzwork aggregate)">${d.esi_prices?"✓ ESI prices":"⟳ Pull live prices"}</button>
      ${tier} · <span class="ind-d-runs-wrap">Runs <input class="ind-d-runs" type="number" min="1" value="${n}" style="width:68px"><button class="ind-d-runs-pre" data-n="1">1</button><button class="ind-d-runs-pre" data-n="10">10</button><button class="ind-d-runs-pre" data-n="100">100</button><button class="ind-d-runs-pre" data-n="10000">10k</button><button class="ind-d-runs-mul" data-m="10">×10</button></span> · source ${d.station_name}
      <span class="ind-d-close" title="Close">✕</span>
    </div>
    <div class="ind-d-body">
    ${esiOwned && !isBpo ? `<div class="ind-bpc-warn">
      ⚠ You only have a <b>Blueprint Copy</b> with <b>${bpcRuns} run${bpcRuns===1?"":"s"}</b> remaining — it will be consumed.
      ${d.bp_market
        ? `<span class="ind-bpc-buy">Buy permanent BPO: ${isk(d.bp_market.price)} at ${d.bp_market.station} (${fmtNum(d.bp_market.orders)} on sale in ${d.bp_market.region})</span>`
        : `<span class="ind-bpc-buy">No BPO on the market in ${d.region_name}. <button class="ind-bpo-expand" data-bp="${d.blueprint_id}">Search other regions</button></span>`}
    </div>` : ""}
    <div class="ind-d-grid">
      <div class="ind-d-sub">Per unit (sell price)</div>
      <span>Sell @ ask — list</span><b>${isk(d.ask)}</b>
      <span>Sell @ bid — instant</span><b>${isk(d.bid)}</b>

      <div class="ind-d-sub">Per run — ${fmtNum(d.product.quantity)}× ${d.product.name}</div>
      <span>Material cost</span><b>${isk(d.material_cost)}</b>
      <span>Job install (EIV ${isk(d.eiv)} × ${(d.job_rate*100).toFixed(1)}%)</span><b>${isk(d.job_cost)}</b>
      ${d.invention?`<span>Invention cost</span><b>${isk(d.invention_cost)}</b>`:""}
      <span>Total cost</span><b>${isk(d.total_cost)}</b>
      <span>Profit — list</span><b class="${pn(d.profit_patient)}">${isk(d.profit_patient)}</b>
      <span>Profit — instant</span><b class="${pn(d.profit_instant)}">${isk(d.profit_instant)}</b>
      <span>Build time</span><b>${fmtDur(d.build_time)}</b>

      <div class="ind-d-sub">Batch — ${n.toLocaleString()} run(s)</div>
      <span>Total cost</span><b>${isk(batchCost)}</b>
      <span>Profit — list</span><b class="${pn(batchProfitL)}">${isk(batchProfitL)}</b>
      <span>Profit — instant</span><b class="${pn(batchProfitI)}">${isk(batchProfitI)}</b>
      <span>Build time</span><b>${fmtDur(batchTime)}</b>
      <span>Cargo in / out</span><b>${inputBatch?fmtVol(inputBatch):"—"} / ${outputBatch?fmtVol(outputBatch):"—"}</b>

      <div class="ind-d-sub">Blueprint &amp; market</div>
      <span>Blueprint</span><b class="bp-buy">${bpSrc}</b>
      <span>ME / TE used</span><b>${d.owned_me_te
          ? `${d.me_used} / ${d.te_used} (your blueprint)`
          : `${d.me_used} / ${d.te_used} (unresearched — not in your blueprints)`}</b>
      <span>Blueprint payback</span><b>${payback}</b>
      <span>Tradeability</span><b>${d.tradeability==null?"—":d.tradeability+" / 100"}${d.daily_units!=null?` (${fmtNum(d.daily_units)} units/day)`:""}</b>
    </div>
    ${d.missing_skills&&d.missing_skills.length?`
    <div class="ind-d-sub ind-skills-warn">Missing skills — ${d.missing_skills.length} needed</div>
    <table class="ind-d-mats ind-d-skills"><thead><tr><th>Skill</th><th class="num">Have</th><th class="num">Need</th><th class="num">Train time</th></tr></thead><tbody>${d.missing_skills.map(s=>`<tr><td>${s.name}${s.prereq?' <span class="ind-prereq">(prereq)</span>':''}</td><td class="num">${s.current}</td><td class="num">${s.required}</td><td class="num">${s.train_hours<1?(Math.round(s.train_hours*60)+"m"):(s.train_hours<24?s.train_hours.toFixed(1)+"h":(s.train_hours/24).toFixed(1)+"d")}</td></tr>`).join("")}</tbody>
    <tfoot><tr class="ind-d-total"><td>Total training</td><td></td><td></td><td class="num">${(()=>{const h=d.missing_skills.reduce((s,sk)=>s+sk.train_hours,0);return h<1?(Math.round(h*60)+"m"):(h<24?h.toFixed(1)+"h":(h/24).toFixed(1)+"d");})()}</td></tr></tfoot></table>`:""}
    <aside class="ind-d-side">
      <div class="ind-d-section">
        <div class="ind-d-sub">Craft</div>
        <div class="ind-d-timer-card">${timerHtml}</div>
        <div class="ind-d-cards">
          <div class="ind-d-card">
            <div class="ind-d-card-label">Job duration</div>
            <div class="ind-d-card-val">${fmtDur(batchTime)}</div>
            <div class="ind-d-card-sub">${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Build cost</div>
            <div class="ind-d-card-val">${isk(batchCost)}</div>
            <div class="ind-d-card-sub">mats ${isk(matTotCost)} + job ${isk(jobCostBatch)}${d.invention?` + invent ${isk(inventionCostBatch)}`:""}</div>
          </div>
          <div class="ind-d-card" data-tip="Job installation fee charged by the station/structure when you start the manufacturing job. Calculated as EIV × job cost % (system index × bonuses + facility tax + SCC surcharge).">
            <div class="ind-d-card-label">Job install fee</div>
            <div class="ind-d-card-val">${isk(jobCostBatch)}</div>
            <div class="ind-d-card-sub">EIV ${isk(d.eiv)} × ${(d.job_rate*100).toFixed(2)}% × ${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Cargo in</div>
            <div class="ind-d-card-val">${inputBatch?fmtVol(inputBatch):"—"}</div>
            <div class="ind-d-card-sub">${n.toLocaleString()} run(s)</div>
          </div>
          <div class="ind-d-card" data-tip="Cumulative runs you've delivered for this item, tracked since the app started watching — it can't see deliveries from before that. Log in with EVE to track.">
            <div class="ind-d-card-label">Runs delivered</div>
            <div class="ind-d-card-val">${prodTrack?prodTrack.runs.toLocaleString():(AUTH.loggedIn?"0":"—")}</div>
            <div class="ind-d-card-sub">${prodTrack?prodTrack.jobs.toLocaleString()+" job(s)":(AUTH.loggedIn?"none yet":"log in to track")}</div>
          </div>
        </div>
      </div>
      <div class="ind-d-section">
        <div class="ind-d-sub">Resell</div>
        <div class="ind-d-cards">
          <div class="ind-d-card">
            <div class="ind-d-card-label">Profit — instant</div>
            <div class="ind-d-card-val ${pn(batchProfitI)}">${isk(batchProfitI)}</div>
            <div class="ind-d-card-sub">${qtyBatchTot.toLocaleString()}× @ bid ${isk(d.bid)} − tax ${fmtPct1(d.sales_tax)} − cost ${isk(batchCost)} = ${isk(batchProfitI)}</div>
            ${minPriceInstant!=null?`<div class="ind-d-card-sub ind-d-card-warn">Break-even sell: ${isk(minPriceInstant)}/unit</div>`:""}
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Profit — sell (list)</div>
            <div class="ind-d-card-val ${pn(batchProfitL)}">${isk(batchProfitL)}</div>
            <div class="ind-d-card-sub">${qtyBatchTot.toLocaleString()}× @ ask ${isk(d.ask)} − tax ${fmtPct1(d.sales_tax)} − broker ${fmtPct1(d.broker_fee)} − cost ${isk(batchCost)} = ${isk(batchProfitL)}</div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Fees &amp; taxes</div>
            <div class="ind-d-card-grid">
              <span>Broker fee (list)</span><b>${isk(brokerIsk)}</b>
              <span>Sales tax (list)</span><b>${isk(taxListIsk)}</b>
              <span>Sales tax (instant)</span><b>${isk(taxInstantIsk)}</b>
            </div>
          </div>
          <div class="ind-d-card">
            <div class="ind-d-card-label">Cargo out</div>
            <div class="ind-d-card-val">${outputBatch?fmtVol(outputBatch):"—"}</div>
            <div class="ind-d-card-sub">batch of ${n.toLocaleString()} run(s)</div>
          </div>
        </div>
      </div>
    </aside>
    </div>
    <div class="ind-d-sub">Materials to buy — ${n.toLocaleString()} run(s)</div>
    <table class="ind-d-mats"><thead><tr><th>Material</th><th class="num">Qty needed</th>
      <th class="num">Unit price</th><th class="num">Total cost</th>
      <th class="num">Cargo m³</th></tr></thead><tbody>${mats}${matTotal}</tbody></table>
    ${invHtml}`;
  // Wire copy + close + ownership via listeners (inline onclick can't see $).
  const box=$("#ind-detail");
  const closeDetail=()=>{ box.classList.add("hidden"); IND.openDetail=null; };
  box.querySelector(".ind-d-close").onclick=closeDetail;
  // Clicking the header bar itself (not its buttons) collapses the detail view.
  // Track mousedown origin so drag-selecting inside the runs input doesn't close.
  const head=box.querySelector(".ind-d-head");
  let headDownInInteractive=false;
  head.onmousedown=ev=>{ headDownInInteractive=!!ev.target.closest("button,input,.ind-d-runs-wrap"); };
  head.onclick=ev=>{ if(!ev.target.closest("button,input,.ind-d-runs-wrap") && !headDownInInteractive) closeDetail(); };
  box.querySelector(".ind-fav-btn").onclick=()=>toggleFavorite(d.blueprint_id);
  const copyBtn=box.querySelector(".ind-copy");
  copyBtn.onclick=()=>{
    const done=()=>{ copyBtn.textContent="✓ Copied"; setTimeout(()=>{copyBtn.textContent="⧉ Copy";},1200); };
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(d.product.name).then(done).catch(()=>fallbackCopy(d.product.name,done));
    } else fallbackCopy(d.product.name, done);
  };
  const pullBtn=box.querySelector(".ind-pull-prices");
  pullBtn.onclick=()=>{
    pullBtn.disabled=true; pullBtn.textContent="Fetching…";
    const p=indParams({blueprint_id:d.blueprint_id, refresh_prices:"1"});
    fetch("/api/ind/detail?"+p).then(r=>r.json()).then(fresh=>{
      if(fresh.error){ pullBtn.textContent="⚠ "+fresh.error; return; }
      renderIndDetail(fresh);
    }).catch(()=>{ pullBtn.disabled=false; pullBtn.textContent="⟳ Pull live prices"; });
  };
  const bpoExpBtn=box.querySelector(".ind-bpo-expand");
  if(bpoExpBtn) bpoExpBtn.onclick=()=>{
    bpoExpBtn.disabled=true; bpoExpBtn.textContent="Searching…";
    const p=new URLSearchParams({blueprint_id:bpoExpBtn.dataset.bp, station:$("#ind-station").value});
    fetch("/api/ind/bpo-search?"+p).then(r=>r.json()).then(res=>{
      if(res.bp_market){
        const m=res.bp_market;
        const jmp=m.jumps!=null?` · ${m.jumps} jump${m.jumps===1?"":"s"}`:"";
        bpoExpBtn.parentElement.innerHTML=`Buy permanent BPO: ${isk(m.price)} at ${m.station} (${m.region}${jmp})`;
      } else {
        bpoExpBtn.textContent="Not sold anywhere — LP store / event only";
      }
    }).catch(()=>{ bpoExpBtn.disabled=false; bpoExpBtn.textContent="Search other regions"; });
  };
  const runsInput=box.querySelector(".ind-d-runs");
  const setRuns=v=>{ IND.detailRuns=Math.max(1,v); renderIndDetail(d); };
  runsInput.addEventListener("input", ()=>setRuns(parseInt(runsInput.value)||1));
  box.querySelectorAll(".ind-d-runs-pre").forEach(b=>{
    b.onclick=()=>setRuns(+b.dataset.n);
  });
  box.querySelectorAll(".ind-d-runs-mul").forEach(b=>{
    b.onclick=()=>setRuns(IND.detailRuns*(+b.dataset.m));
  });
}

function fmtCountdown(ms){
  let s=Math.max(0,Math.floor(ms/1000));
  const d=Math.floor(s/86400); s-=d*86400;
  const h=Math.floor(s/3600); s-=h*3600;
  const m=Math.floor(s/60); s-=m*60;
  if(d>0) return `${d}d ${h}h left`;
  return (h?h+"h ":"")+(h||m?m+"m ":"")+s+"s left";
}
// Compact H:MM:SS / M:SS form for the narrow table column (Dd Hh past 24h).
function fmtCountdownShort(ms){
  let s=Math.max(0,Math.floor(ms/1000));
  const d=Math.floor(s/86400); s-=d*86400;
  const h=Math.floor(s/3600); s-=h*3600;
  const m=Math.floor(s/60); s-=m*60;
  if(d>0) return `${d}d ${h}h`;
  return h>0 ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`
             : `${m}:${String(s).padStart(2,"0")}`;
}
// Tick every live countdown once a second — the open detail panel's span and
// any "_timer" cells in the main table — without a full table re-render.
setInterval(()=>{
  document.querySelectorAll(".ind-live-timer[data-end]").forEach(el=>{
    const rem=(+el.dataset.end)-Date.now();
    const isCell=el.classList.contains("timer-cell");
    if(rem<=0){
      if(isCell){ el.textContent="✓ Ready"; el.classList.add("done"); el.removeAttribute("data-end"); }
      else if(IND.openDetail) renderIndDetail(IND.openDetail);
    } else {
      el.textContent=isCell?fmtCountdownShort(rem):fmtCountdown(rem);
    }
  });
  tickCharRefreshTimer();
}, 1000);

// ══════════════════════════════════════════════════════════════════════════
// EVE SSO / CHARACTER
// ══════════════════════════════════════════════════════════════════════════
const AUTH = { loggedIn:false, name:null, charId:null, data:null,
               cfg:{client_id:"",callback_url:"",suggested_callback:"",scopes:[]} };
const CHAR_REFRESH_MS = 300000;  // ESI caches character industry jobs for 5 min
let charRefreshDeadline = 0;
function tickCharRefreshTimer(){
  const el=$("#char-refresh-timer");
  if(!AUTH.loggedIn || !charRefreshDeadline){ el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  $("#char-refresh-secs").textContent=fmtCountdownShort(charRefreshDeadline-Date.now());
}
const ROMAN=["0","I","II","III","IV","V"];
function authEsc(s){ return String(s==null?"":s).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function romanLvl(n){ return ROMAN[n]||String(n||""); }

async function loadAuthConfig(){
  try{ AUTH.cfg=await (await fetch("/api/auth/config")).json(); }catch(e){ return; }
  $("#cfg-client-id").value=AUTH.cfg.client_id||"";
  $("#cfg-callback").value=AUTH.cfg.callback_url||AUTH.cfg.suggested_callback||"";
  $("#cfg-scopes").innerHTML=(AUTH.cfg.scopes||[]).map(authEsc).join("<br>");
}
function openAuthCfg(){ loadAuthConfig(); $("#auth-cfg-pop").classList.remove("hidden"); }
function closeAuthCfg(){ $("#auth-cfg-pop").classList.add("hidden"); }

function renderAuthChip(){
  $("#login-eve").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-chip").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-tab-btn").classList.toggle("hidden", !AUTH.loggedIn);
  $("#char-empty").classList.toggle("hidden", AUTH.loggedIn);
  $("#char-body").classList.toggle("hidden", !AUTH.loggedIn);
  if(AUTH.loggedIn) $("#chip-name").textContent=AUTH.name||"Capsuleer";
  if(ACTIVE_TAB==="char" && !AUTH.loggedIn) switchTab("ind");
  else updateIndGate();
}

async function checkAuth(){
  let st; try{ st=await (await fetch("/api/auth/status")).json(); }catch(e){ return; }
  AUTH.loggedIn=!!st.logged_in; AUTH.name=st.name; AUTH.charId=st.character_id;
  renderAuthChip();
  if(AUTH.loggedIn){
    refreshCharData();
    // A /character deep link couldn't open before auth resolved (it runs
    // concurrently with loadSettings) — open it now that we know we're in.
    if(location.pathname==="/character" || location.pathname==="/char") switchTab("char", {url:false});
  }
}

async function doLogin(){
  let r; try{ r=await (await fetch("/api/auth/login")).json(); }catch(e){ openAuthCfg(); return; }
  if(r.url){ window.location.href=r.url; }
  else { openAuthCfg(); if(r.error) setStatus(authEsc(r.error), true); }
}
async function doLogout(){
  await fetch("/api/auth/logout").catch(()=>{});
  AUTH.loggedIn=false; AUTH.name=null; AUTH.charId=null; AUTH.data=null;
  IND.timers={};   // timers come from the API only — nothing to keep
  charRefreshDeadline=0; tickCharRefreshTimer();
  renderAuthChip(); updateMyLpBadge(); renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}

let charRetryCount = 0;
function _retryCharDataSoon(){
  // Covers both a network-level failure (fetch/json throws) and a transient
  // backend error surfaced as a normal {"error":...} 500/400 (e.g. a stale
  // pooled ESI/Fuzzwork connection) — retry after a short delay instead of
  // leaving the error on screen for a full CHAR_REFRESH_MS cycle. Capped so a
  // *persistent* error (e.g. a revoked EVE session) doesn't hammer the
  // backend/ESI every 10s forever — after a few tries fall back to the normal
  // slow cadence.
  charRetryCount++;
  const delay = charRetryCount <= 3 ? 10000 : CHAR_REFRESH_MS;
  setTimeout(()=>{ if(AUTH.loggedIn) refreshCharData(); }, delay);
  charRefreshDeadline=Date.now()+delay; tickCharRefreshTimer();
}
async function refreshCharData(){
  let d;
  try{ d=await (await fetch("/api/char/data")).json(); }
  catch(e){ _retryCharDataSoon(); return; }
  if(d.error){ setStatus(authEsc(d.error), true); _retryCharDataSoon(); return; }
  charRetryCount = 0;
  AUTH.data=d;
  // Auto-fill sales tax from Accounting skill: base 7.5% × (1 − 0.11 × level)
  if(d.accounting_level!=null){
    const tax=7.5*(1-0.11*d.accounting_level);
    $("#g-tax").value=tax.toFixed(2);
  }
  // Auto-fill broker fee from Broker Relations: base 3% − 0.3% × level (no standings)
  if(d.broker_relations_level!=null){
    const fee=3.0-0.3*d.broker_relations_level;
    $("#g-broker").value=fee.toFixed(2);
  }
  charRefreshDeadline=Date.now()+CHAR_REFRESH_MS; tickCharRefreshTimer();
  const prevLp=$("#lp").value;
  renderCharData(); syncJobTimers(); updateMyLpBadge();
  // If the character LP just changed the locked budget for the corp on screen,
  // re-run the LP scan so results reflect the real budget (e.g. on first load,
  // when char data arrives after the initial scan).
  if($("#lp").value!==prevLp && ACTIVE_TAB==="lp" && ($("#corp").value||"").trim()){
    clearTimeout(lpScanTimer); scan(false);
  }
}

function renderCharData(){
  const d=AUTH.data; if(!d) return;
  $("#cv-name").textContent=d.name||"—";
  $("#cv-wallet").textContent=d.wallet!=null?fmtISK(d.wallet):"—";
  $("#cv-sp").textContent=d.total_sp!=null?Math.round(d.total_sp).toLocaleString():"—";
  $("#chip-wallet").textContent=d.wallet!=null?fmtISK(d.wallet)+" ISK":"";

  const rt=d.runs_tracked;
  if(rt){
    $("#cv-runs").textContent=rt.total_runs.toLocaleString()+" runs / "+rt.total_jobs.toLocaleString()+" jobs";
    const since=new Date(rt.since*1000).toLocaleString([],
      {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
    $("#cv-runs-kpi").setAttribute("data-tip",
      "Cumulative runs delivered since this app started tracking ("+since+") — it can't see deliveries from before that.");
  }

  const jobs=d.jobs||[];
  $("#cv-jobs").textContent=jobs.length;
  $("#char-jobs-empty").classList.toggle("hidden", jobs.length>0);
  $("#char-jobs-tbl").classList.toggle("hidden", jobs.length===0);
  $("#char-jobs-tbl tbody").innerHTML=jobs.map(j=>{
    const end=Date.parse(j.end), rem=end-Date.now();
    let tcell="—";
    if(isFinite(end)) tcell = rem>0
      ? `<span class="ind-live-timer timer-cell" data-end="${end}">${fmtCountdownShort(rem)}</span>`
      : `<span class="timer-cell done">✓ Ready</span>`;
    return `<tr><td>${authEsc(j.product_name)}</td><td>${authEsc(j.activity)}</td>`
         + `<td>${j.runs??""}</td><td>${authEsc(j.status||"")}</td>`
         + `<td class="tl">${tcell}</td></tr>`;
  }).join("");

  const q=d.skillqueue||[];
  $("#char-queue-empty").classList.toggle("hidden", q.length>0);
  $("#char-queue-tbl").classList.toggle("hidden", q.length===0);
  $("#char-queue-tbl tbody").innerHTML=q.map(s=>{
    const fin=s.finish_date?new Date(s.finish_date).toLocaleString([],
      {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"—";
    return `<tr><td>${authEsc(s.skill_name)}</td><td>${romanLvl(s.finished_level)}</td>`
         + `<td style="text-align:right">${fin}</td></tr>`;
  }).join("");

  const lp=d.loyalty||[];
  $("#char-lp-empty").classList.toggle("hidden", lp.length>0);
  $("#char-lp-tbl").classList.toggle("hidden", lp.length===0);
  $("#char-lp-tbl tbody").innerHTML=lp.map(l=>
    `<tr><td>${authEsc(l.corp_name)}</td>`
   + `<td style="text-align:right">${(l.loyalty_points||0).toLocaleString()}</td></tr>`).join("");

  const orders=d.market_orders||[];
  $("#cv-orders").textContent=orders.length;
  const ordersTotal=orders.reduce((s,o)=>s+(o.volume_remain??0)*(o.price||0), 0);
  $("#char-orders-total").textContent=orders.length?`(${orders.length} · ${fmtISK(ordersTotal)} ISK)`:"";
  $("#char-orders-empty").classList.toggle("hidden", orders.length>0);
  $("#char-orders-empty").classList.toggle("char-none-warn", !!d.market_orders_error);
  $("#char-orders-empty").textContent=d.market_orders_error||"No open orders.";
  $("#char-orders-tbl").classList.toggle("hidden", orders.length===0);
  $("#char-orders-tbl tbody").innerHTML=orders.map(o=>{
    const issuedMs=o.issued?Date.parse(o.issued):NaN;
    const posted=isFinite(issuedMs)?fmtDur((Date.now()-issuedMs)/1000)+" ago":"—";
    const postedTip=isFinite(issuedMs)?` title="${new Date(issuedMs).toLocaleString()}"`:"";
    const expiresMs=isFinite(issuedMs)&&o.duration!=null?issuedMs+o.duration*86400000:NaN;
    const expires=isFinite(expiresMs)?new Date(expiresMs).toLocaleString([],
      {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):"—";
    const queue=o.is_best==null?`<span style="color:var(--dim)">—</span>`
      :o.is_best?`<span class="tx-sell">Best ✓</span>`
      :`<span class="tx-buy">#${o.queue_rank} / ${o.queue_total}</span>`;
    return `<tr><td>${authEsc(o.type_name)}</td>`
         + `<td class="${o.is_buy_order?"tx-buy":"tx-sell"}">${o.is_buy_order?"Buy":"Sell"}</td>`
         + `<td style="text-align:right">${(o.volume_remain??0).toLocaleString()} / ${(o.volume_total??0).toLocaleString()}</td>`
         + `<td style="text-align:right">${fmtISK(o.price)}</td>`
         + `<td style="text-align:right">${fmtISK((o.volume_remain??0)*o.price)}</td>`
         + `<td style="text-align:right">${o.market_sell!=null?fmtISK(o.market_sell):"—"}</td>`
         + `<td style="text-align:right">${queue}</td>`
         + `<td style="text-align:right"${postedTip}>${posted}</td>`
         + `<td style="text-align:right">${expires}</td></tr>`;
  }).join("");
}

// Rebuild the Industry-table timers from the character's real manufacturing jobs,
// keyed by blueprint type id (== the planner's blueprint_id). This is the only
// source of timers — there is no manual timer any more.
function syncJobTimers(){
  IND.timers={};
  (AUTH.data&&AUTH.data.jobs||[]).forEach(j=>{
    if(j.activity_id!==1) return;          // manufacturing only
    const end=Date.parse(j.end), bp=j.blueprint_type_id;
    if(isFinite(end) && bp) IND.timers[bp]=end;
  });
  if(ACTIVE_TAB==="ind") renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}

// When logged in, drive the LP budget from the character's loyalty points for
// the selected corp and lock the field. Falls back to a normal editable input
// when not logged in or the corp isn't one the character holds LP with.
function updateMyLpBadge(){
  const badge=$("#lp-mylp"), inp=$("#lp");
  const corp=($("#corp").value||"").trim().toLowerCase();
  const lp=(AUTH.data&&AUTH.data.loyalty)||[];
  const m=(AUTH.loggedIn&&corp)?lp.find(l=>(l.corp_name||"").toLowerCase()===corp):null;
  if(m){
    inp.value=m.loyalty_points||0;
    inp.readOnly=true; inp.classList.add("locked");
    inp.title="Locked to your character's loyalty points for this corporation.";
    badge.textContent="🔒 from character";
    badge.classList.remove("hidden");
  } else {
    inp.readOnly=false; inp.classList.remove("locked");
    inp.title="";
    badge.classList.add("hidden");
  }
}

$("#login-eve").onclick=doLogin;
$("#char-login-btn").onclick=doLogin;
$("#ind-login-btn").onclick=doLogin;
$("#login-cfg").onclick=()=>{ const p=$("#auth-cfg-pop"); p.classList.contains("hidden")?openAuthCfg():closeAuthCfg(); };
$("#logout-eve").onclick=e=>{ e.stopPropagation(); doLogout(); };
$("#char-chip").onclick=()=>switchTab("char");
$("#cfg-close").onclick=closeAuthCfg;
$("#cfg-copy").onclick=()=>{
  const t=$("#cfg-callback").value;
  const done=()=>{ const b=$("#cfg-copy"); b.textContent="Copied"; setTimeout(()=>b.textContent="Copy",1200); };
  if(navigator.clipboard&&navigator.clipboard.writeText)
    navigator.clipboard.writeText(t).then(done).catch(()=>fallbackCopy(t,done));
  else fallbackCopy(t,done);
};
$("#cfg-save").onclick=async()=>{
  const cid=encodeURIComponent($("#cfg-client-id").value.trim());
  const cb=encodeURIComponent($("#cfg-callback").value.trim());
  await fetch(`/api/auth/config?client_id=${cid}&callback_url=${cb}`).catch(()=>{});
  closeAuthCfg();
  setStatus('<span class="pill">EVE login settings saved</span>');
};

// Re-pull character data (wallet, jobs, skill queue, LP) on EVE's cache cadence
// so the job timers stay current. The per-second ticker handles the countdown
// itself; this just refreshes the underlying job list every 5 minutes.
setInterval(()=>{ if(AUTH.loggedIn) refreshCharData(); }, CHAR_REFRESH_MS);

// When the tab returns from background (sleep, alt-tab, phone lock), the
// setInterval may have drifted far past the deadline. Refresh immediately.
document.addEventListener("visibilitychange", ()=>{
  if(document.hidden || !AUTH.loggedIn) return;
  if(Date.now() >= charRefreshDeadline) refreshCharData();
});

function fallbackCopy(text, done){
  // execCommand path for non-secure contexts where navigator.clipboard is absent.
  try{
    const ta=document.createElement("textarea");
    ta.value=text; ta.style.position="fixed"; ta.style.opacity="0";
    document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta);
    if(done) done();
  }catch(e){}
}

function loadIndGroups(){
  fetch("/api/ind/groups").then(r=>r.json()).then(d=>{
    if(!d.groups) return;
    const sel=$("#ind-group");
    // The saved category can't be applied until the option list exists (it's
    // fetched async), so honour IND.savedGroup here once the options are in.
    const want=(sel.value && sel.value!=="all") ? sel.value : (IND.savedGroup||"all");
    sel.innerHTML='<option value="all">All (slow)</option>'
      +d.groups.map(g=>`<option value="${g.id}">${g.name}</option>`).join("");
    sel.value=[...sel.options].some(o=>o.value===want)?want:"all";
    IND.groupsLoaded=true;
  }).catch(()=>{});
}

// ── Build locations (station/structure job-cost profiles) ───────────
// A profile is {name, system_index, role_bonus, facility_tax, scc_surcharge};
// its effective Job cost % = system_index×(1−role_bonus/100) + facility_tax + SCC,
// matching the in-game Industry job-cost breakdown. (Legacy profiles may carry a
// flat job_rate instead.)
function structEffectiveRate(p){
  if(p && p.system_index!==undefined && p.system_index!==null){
    return (+p.system_index||0)*(1-(+p.role_bonus||0)/100)
         + (+p.facility_tax||0) + (+p.scc_surcharge||0);
  }
  return parseFloat(p&&p.job_rate)||0;
}
function renderIndProfiles(){
  const sel=$("#ind-profile");
  sel.innerHTML='<option value="">— custom —</option>'
    +IND.profiles.map((p,i)=>`<option value="${i}">${p.name}</option>`).join("");
}
function applyIndProfile(){
  const i=$("#ind-profile").value;
  if(i!==""&&IND.profiles[i]){
    $("#ind-jobrate").value=structEffectiveRate(IND.profiles[i]).toFixed(2);
    saveIndPrefs();
    recalcIndProfits();
  }
}

// Wizard ----------------------------------------------------------------
let IND_EDIT_IDX=null;
function swPreview(){
  const eff=(+$("#sw-index").value||0)*(1-(+$("#sw-bonus").value||0)/100)
          +(+$("#sw-facility").value||0)+(+$("#sw-scc").value||0);
  $("#sw-eff").textContent=eff.toFixed(2)+"%";
}
function openStructWizard(idx){
  IND_EDIT_IDX = (idx==null||idx==="")?null:+idx;
  const p = IND_EDIT_IDX!=null ? IND.profiles[IND_EDIT_IDX] : null;
  $("#sw-title").textContent = p ? "Edit build location" : "New build location";
  $("#sw-name").value     = p ? (p.name||"") : "";
  $("#sw-index").value    = p && p.system_index!=null ? p.system_index : "0";
  $("#sw-bonus").value    = p && p.role_bonus!=null ? p.role_bonus : "0";
  $("#sw-facility").value = p && p.facility_tax!=null ? p.facility_tax : "0";
  $("#sw-scc").value      = p && p.scc_surcharge!=null ? p.scc_surcharge : "4";
  $("#sw-delete").style.display = p ? "" : "none";
  swPreview();
  $("#indStructModal").classList.remove("hidden");
  $("#sw-name").focus();
}
function closeStructWizard(){ $("#indStructModal").classList.add("hidden"); }
function saveStructWizard(){
  const name=$("#sw-name").value.trim();
  if(!name){ $("#sw-name").focus(); return; }
  const p={ name,
    system_index:+$("#sw-index").value||0,
    role_bonus:+$("#sw-bonus").value||0,
    facility_tax:+$("#sw-facility").value||0,
    scc_surcharge:+$("#sw-scc").value||0 };
  let idx;
  if(IND_EDIT_IDX!=null){ IND.profiles[IND_EDIT_IDX]=p; idx=IND_EDIT_IDX; }
  else { IND.profiles.push(p); idx=IND.profiles.length-1; }
  renderIndProfiles();
  $("#ind-profile").value=String(idx);
  $("#ind-jobrate").value=structEffectiveRate(p).toFixed(2);
  saveIndPrefs();
  closeStructWizard();
}
function deleteStruct(){
  if(IND_EDIT_IDX==null) return;
  IND.profiles.splice(IND_EDIT_IDX,1);
  renderIndProfiles();
  $("#ind-profile").value="";
  saveIndPrefs();
  closeStructWizard();
}

function saveIndPrefs(){
  const p=indParams({
    profiles: JSON.stringify(IND.profiles),
    profile:  $("#ind-profile").value,
    sort_key: IND.sort.key,
    sort_dir: String(IND.sort.dir),
    hidden_bps: JSON.stringify([...IND.hidden]),
    col_order: JSON.stringify(IND.colOrder),
    col_widths: JSON.stringify(IND.colw),
    col_vis: JSON.stringify(IND.colVis),
    ind_trade_weight: String(IND.tradeWeight),
  });
  fetch("/api/ind/prefs?"+p).catch(()=>{}); saveLS();
}

// wiring
$("#ind-go").onclick=()=>scanInd(false);
$("#ind-refresh").onclick=()=>scanInd(true);

$("#ind-profile").addEventListener("change", applyIndProfile);
// Build-location wizard wiring
$("#ind-struct-new").onclick=()=>openStructWizard(null);
$("#ind-struct-edit").onclick=()=>{
  const i=$("#ind-profile").value;
  if(i==="") openStructWizard(null); else openStructWizard(i);
};
["#sw-index","#sw-bonus","#sw-facility","#sw-scc"].forEach(s=>$(s).addEventListener("input", swPreview));
$("#sw-save").onclick=saveStructWizard;
$("#sw-cancel").onclick=closeStructWizard;
$("#sw-delete").onclick=deleteStruct;
$("#indStructModal").addEventListener("click", e=>{ if(e.target.id==="indStructModal") closeStructWizard(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && !$("#indStructModal").classList.contains("hidden")) closeStructWizard(); });
function recalcIndProfits(){
  if(!IND.rows.length) return;
  const jobRate=parseFloat($("#ind-jobrate").value||"0")/100;
  const salesTax=parseFloat($("#g-tax").value||"0")/100;
  const broker=parseFloat($("#g-broker").value||"0")/100;
  const patientFactor=1-salesTax-broker;
  const instantFactor=1-salesTax;
  const n=Math.max(1,IND.detailRuns||1);
  for(const r of IND.rows){
    const jc=r.eiv*jobRate;
    const opCost=r.material_cost+jc+r.invention_cost;
    r.job_cost=jc; r.total_cost=opCost;
    const revP=r.ask!=null?(r.out_qty*r.ask*patientFactor):null;
    const revI=r.bid!=null?(r.out_qty*r.bid*instantFactor):null;
    r.profit_patient=revP!=null?(revP-opCost):null;
    r.profit_instant=revI!=null?(revI-opCost):null;
    r.profit_best=r.profit_patient!=null&&r.profit_instant!=null?Math.max(r.profit_patient,r.profit_instant):(r.profit_patient??r.profit_instant);
    const margin=pr=>(pr!=null&&opCost>0)?pr/opCost:null;
    r.margin_patient=margin(r.profit_patient);
    r.margin_instant=margin(r.profit_instant);
    r.margin_best=margin(r.profit_best);
    const hrs=r.build_time?r.build_time/3600:null;
    const iph=pr=>(pr!=null&&hrs)?pr/hrs:null;
    r.isk_per_hour_patient=iph(r.profit_patient);
    r.isk_per_hour_instant=iph(r.profit_instant);
    r.isk_per_hour_best=iph(r.profit_best);
    r.total_profit_patient=r.profit_patient!=null?r.profit_patient*r.runs:null;
    r.total_profit_instant=r.profit_instant!=null?r.profit_instant*r.runs:null;
  }
  renderIndTable();
  if(IND.openDetail) renderIndDetail(IND.openDetail);
}
["#ind-group","#ind-station"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveIndPrefs);
});
["#ind-jobrate"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", ()=>{ saveIndPrefs(); recalcIndProfits(); });
});
["#ind-buildable","#ind-unobtainable","#ind-hidet2"].forEach(sel=>$(sel).addEventListener("change", saveIndPrefs));
// Min-tradeability is a client-side filter — re-render immediately (no rescan).
$("#ind-mintrade").addEventListener("input", ()=>{ saveIndPrefs(); renderIndTable(); });
// Industry tradeability balance presets
function syncIndBalanceButtons(){
  document.querySelectorAll(".ind-balance-btn").forEach(b=>
    b.classList.toggle("on", parseFloat(b.dataset.w)===IND.tradeWeight));
}
document.querySelectorAll(".ind-balance-btn").forEach(b=>{
  b.onclick=()=>{
    IND.tradeWeight=parseFloat(b.dataset.w);
    syncIndBalanceButtons();
    computeIndTradeability();
    renderIndTable();
    saveIndPrefs();
  };
});
syncIndBalanceButtons();
function updateIndSearchClear(){
  $("#ind-search-clear").classList.toggle("hidden", !$("#ind-search").value);
}
$("#ind-search").addEventListener("input", ()=>{ updateIndSearchClear(); renderIndTable(); });
$("#ind-search-clear").addEventListener("click", ()=>{
  $("#ind-search").value="";
  updateIndSearchClear();
  renderIndTable();
  $("#ind-search").focus();
});

// ══════════════════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════════════════
async function restoreLastScans(){
  const restored={lp:false, ind:false};
  try{
    const resp=await fetch("/api/last-scan");
    const cached=await resp.json();
    if(cached.lp && cached.lp.rows && cached.lp.rows.length){
      const _il=$("#init-loading"); if(_il) _il.remove();
      STATE.rows=cached.lp.rows;
      STATE.ctx={corp_id:cached.lp.corp_id, lp:cached.lp.lp,
        tax:cached.lp.tax, broker:cached.lp.broker, station:String(cached.lp.station_id)};
      STATE.lastScanData=cached.lp;
      if(ACTIVE_TAB==="lp"){ renderLPStatus(); renderTable(); }
      restored.lp=true;
    }
    if(cached.ind && cached.ind.rows && cached.ind.rows.length){
      IND.rows=cached.ind.rows; IND.lastData=cached.ind;
      computeIndTradeability();
      if(ACTIVE_TAB==="ind"){ renderIndStatus(); renderIndTable(); }
      restored.ind=true;
    }
  }catch(e){}
  return restored;
}

updateArbJumpsVisibility();  // reflect default cross-station selection before settings load
async function loadSettings(){
  let server=null;
  try{ server=await (await fetch("/api/settings")).json(); }catch(e){}
  let s=null;
  if(server && server._server_synced){
    // This character has synced settings from some device before — that's
    // the cross-device source of truth, takes priority over this browser's
    // local copy.
    s=server;
  } else {
    try{ s=JSON.parse(localStorage.getItem(LS_KEY)); }catch(e){}
    if(!s) s=server;
    // First login on this character, before any device has synced yet — seed
    // the server row now so other devices see something right away.
    if(s && server && server._logged_in) syncSettingsToServer(s);
  }
  if(s && Object.keys(s).length){
      if(s.corp) $("#corp").value=s.corp;
      if(s.lp)   $("#lp").value=s.lp;
      if(s.market) $("#market").value=s.market;
      const _ms=s.maxspread??s.max_spread; if(_ms!=null) $("#maxspread").value=_ms;
      if(s.tax)   $("#g-tax").value=fracToPct(s.tax);
      if(s.broker) $("#g-broker").value=fracToPct(s.broker);
      if(s.sort_key && COLS.some(c=>c.k===s.sort_key))
        STATE.sort={key:s.sort_key, dir:Number(s.sort_dir)===1?1:-1};
      if(s.col_widths && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          STATE.colw=(typeof s.col_widths==="string"?JSON.parse(s.col_widths):s.col_widths)||{};
        }catch(e){}
      }
      if(s.col_order && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          const ord=typeof s.col_order==="string"?JSON.parse(s.col_order):s.col_order;
          if(Array.isArray(ord)){
            const known=ord.filter(k=>COL_BY_KEY[k]);
            if(known.length) STATE.colOrder=known;  // orderedCols() appends any missing
          }
        }catch(e){}
      }
      if(s.hide_illiquid==="1"){ STATE.hideIlliquid=true; $("#toggleIlliquid").checked=true; }
      if(s.hide_unaffordable==="1"){ STATE.hideUnaffordable=true; $("#toggleAffordable").checked=true; }
      if(s.trade_weight!==undefined && s.trade_weight!==""){
        const tw=parseFloat(s.trade_weight);
        if([0.25,0.5,0.75].includes(tw)){ STATE.tradeWeight=tw; syncBalanceButtons(); }
      }
      if(s.col_vis && typeof s.col_vis==="object")
        COLS.forEach(c=>{ if(c.k in s.col_vis) STATE.colVis[c.k]=!!s.col_vis[c.k]; });
      // Arb settings
      const a=s.arb||{};
      if(a.region) $("#arb-region").value=a.region;
      if(a.cross_station==="0"||a.cross_station==="1") $("#arb-cross").value=a.cross_station;
      if(a.min_isk)   $("#arb-minisk").value=a.min_isk;
      if(a.max_jumps) $("#arb-maxjumps").value=a.max_jumps;
      if(a.route_flag) $("#arb-route").value=a.route_flag;
      if(a.avoid_lowsec==="1"){
        ARB.avoidLowsec=true;
        $("#arb-toggleLowsec").classList.add("active");
      }
      updateArbJumpsVisibility();
      // Industry settings
      const ind=s.ind||{};
      // Category options load async; stash the saved one so loadIndGroups applies
      // it once the list exists (and set it now in case the list is already there).
      if(ind.market_group){ IND.savedGroup=ind.market_group; $("#ind-group").value=ind.market_group; }
      if(ind.sort_key && IND_COLS.some(c=>c.k===ind.sort_key))
        IND.sort={key:ind.sort_key, dir:Number(ind.sort_dir)===1?1:-1};
      if(ind.col_order){ try{
        const ord=typeof ind.col_order==="string"?JSON.parse(ind.col_order):ind.col_order;
        if(Array.isArray(ord)&&ord.length) IND.colOrder=ord;  // indOrderedCols() drops unknown / appends new
      }catch(e){} }
      if(ind.col_widths){ try{
        const cw=typeof ind.col_widths==="string"?JSON.parse(ind.col_widths):ind.col_widths;
        if(cw&&typeof cw==="object") Object.assign(IND.colw,cw);
      }catch(e){} }
      if(ind.col_vis){ try{
        const cv=typeof ind.col_vis==="string"?JSON.parse(ind.col_vis):ind.col_vis;
        if(cv&&typeof cv==="object") IND_COLS.forEach(c=>{ if(c.k in cv) IND.colVis[c.k]=!!cv[c.k]; });
      }catch(e){} }
      if(ind.station) $("#ind-station").value=ind.station;
      if(ind.job_rate) $("#ind-jobrate").value=ind.job_rate;
      if(ind.buildable_only==="1") $("#ind-buildable").checked=true;
      if(ind.include_unbuildable==="1") $("#ind-unobtainable").checked=true;
      if(ind.hide_t2==="1") $("#ind-hidet2").checked=true;
      if(ind.min_tradeability!==undefined&&ind.min_tradeability!=="") $("#ind-mintrade").value=ind.min_tradeability;
      if(ind.ind_trade_weight!==undefined){ IND.tradeWeight=parseFloat(ind.ind_trade_weight)||0.5; syncIndBalanceButtons(); }
      if(ind.profiles){ try{ IND.profiles=JSON.parse(ind.profiles)||[]; }catch(e){} }
      renderIndProfiles();
      if(ind.profile) $("#ind-profile").value=ind.profile;
      if(ind.favorites){ try{ IND.favorites=new Set(JSON.parse(ind.favorites)||[]); }catch(e){} }
      if(ind.hidden_bps){ try{ IND.hidden=new Set(JSON.parse(ind.hidden_bps)||[]); }catch(e){} }
      if(ind.sections){ try{
        const sec=typeof ind.sections==="string"?JSON.parse(ind.sections):ind.sections;
        if(sec&&typeof sec==="object") Object.assign(IND.sections, sec);
      }catch(e){} }
      // Restore the last-used tab saved server-side. A tab URL overrides this
      // just below; don't re-push history for either.
      if(s.active_tab==="arb") switchTab("arb", {url:false});
      else if(s.active_tab==="ind") switchTab("ind", {url:false});
  }
  // A deep link / refresh on a tab URL wins over the saved pref. "/" is not an
  // explicit choice, so it defers to the pref restored above.
  const urlTab = location.pathname==="/" ? null : PATH_TAB[location.pathname];
  if(urlTab && urlTab!==ACTIVE_TAB && (urlTab!=="char" || AUTH.loggedIn))
    switchTab(urlTab, {url:false});
  // Restore last scan results from server cache, then auto-scan if the LP tab
  // is active and a corp is set.
  restoreLastScans().then(restored=>{
    if(ACTIVE_TAB==="lp" && $("#corp").value.trim() && !restored.lp) scan(false);
    if(!restored.ind) loadOwnedPreview();
  });
}
// ── Custom tooltip engine ──────────────────────────────────────────
// Reads data-tip on any element and shows a themed, cursor-following
// tooltip instead of the browser's default title= popup.
(function(){
  const tip=document.createElement("div");
  tip.id="tooltip"; document.body.appendChild(tip);
  let cur=null;
  document.addEventListener("mousemove",e=>{
    const el=e.target.closest?e.target.closest("[data-tip]"):null;
    if(el){
      if(el!==cur){ cur=el; tip.textContent=el.getAttribute("data-tip"); tip.classList.add("show"); }
      const pad=14, w=tip.offsetWidth, h=tip.offsetHeight;
      let x=e.clientX+pad, y=e.clientY+pad;
      if(x+w>innerWidth-8)  x=Math.max(8, e.clientX-w-pad);
      if(y+h>innerHeight-8) y=Math.max(8, e.clientY-h-pad);
      tip.style.left=x+"px"; tip.style.top=y+"px";
    } else if(cur){ cur=null; tip.classList.remove("show"); }
  },{passive:true});
  document.addEventListener("mouseleave",()=>{ cur=null; tip.classList.remove("show"); });
  // Hide while scrolling/clicking so it never lingers in a stale spot.
  document.addEventListener("scroll",()=>{ if(cur){ cur=null; tip.classList.remove("show"); } }, true);
})();

loadSettings();
checkAuth();
_corpInput.addEventListener("input", updateMyLpBadge);
</script>
</body>
</html>""".replace("__VERSION__", __version__).replace("__FAVICON__", _FAVICON_B64)


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
