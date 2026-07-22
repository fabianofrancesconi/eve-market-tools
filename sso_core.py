#!/usr/bin/env python3
"""
EVE Online SSO (OAuth2 PKCE) + authenticated ESI helpers.

This is the "native application" SSO flow — no client secret. Each user registers
their own application at https://developers.eveonline.com, pastes the CLIENT_ID into
the UI, and registers the callback URL the app prints. The flow:

  1. make_pkce()              -> (verifier, challenge)
  2. build_authorize_url(...) -> send the browser there
  3. EVE redirects back to the callback with ?code=...&state=...
  4. exchange_code(...)       -> {access_token, refresh_token, expires_in, ...}
  5. decode_jwt_payload(...)  -> {character_id, name, scopes, exp}
  6. authenticated ESI calls with the bearer token; refresh_access_token(...) when it
     expires (access tokens live ~20 min, refresh tokens are long-lived).

The access-token JWT is base64url-decoded directly without signature verification.
That keeps `requests` the only runtime dependency; it is acceptable here because the
token is delivered straight from EVE over TLS to a local single-user tool.
"""
import base64
import hashlib
import json
import secrets
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from lp_core import ESI, HEADERS, USER_AGENT, load_json, save_json

# ── SSO endpoints / scopes ────────────────────────────────────────────────────

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize/"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

SCOPES = [
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-characters.read_loyalty.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-markets.read_character_orders.v1",
    "esi-characters.read_blueprints.v1",
    "esi-universe.read_structures.v1",
    "esi-location.read_location.v1",
    "esi-location.read_online.v1",
    "esi-location.read_ship_type.v1",
    "esi-assets.read_assets.v1",
]

AUTH_FILE = "eve_auth.json"


# ── PKCE + authorize URL ──────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    """base64url with no padding, per RFC 7636."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce():
    """Return (code_verifier, code_challenge). The challenge is
    base64url(sha256(verifier)), no padding."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(client_id, redirect_uri, scopes, state, challenge):
    """Full https://login.eveonline.com/v2/oauth/authorize URL for the PKCE flow."""
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


# ── Token exchange / refresh ──────────────────────────────────────────────────

_TOKEN_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENT,
}


def exchange_code(client_id, redirect_uri, code, verifier, session):
    """Trade an authorization code for tokens. Returns the raw token dict
    (access_token, refresh_token, expires_in, token_type)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    r = session.post(TOKEN_URL, data=data, headers=_TOKEN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def refresh_access_token(client_id, refresh_token, session):
    """Exchange a refresh token for a fresh access token. Returns the raw token
    dict (note: EVE may rotate the refresh_token, so persist whatever comes back)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    r = session.post(TOKEN_URL, data=data, headers=_TOKEN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def decode_jwt_payload(access_token):
    """Decode the JWT payload (no signature check) into
    {character_id, name, scopes, exp}. Character id comes from
    sub='CHARACTER:EVE:<id>'."""
    parts = access_token.split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    sub = claims.get("sub", "")
    character_id = int(sub.split(":")[-1]) if sub.startswith("CHARACTER:EVE:") else None
    scp = claims.get("scp") or []
    if isinstance(scp, str):
        scp = [scp]
    return {
        "character_id": character_id,
        "name": claims.get("name"),
        "scopes": scp,
        "exp": claims.get("exp"),
    }


# ── Token persistence (refresh token lives server-side, in the cache dir) ──────

def _auth_path(cache_dir):
    return Path(cache_dir) / AUTH_FILE


def load_tokens(cache_dir):
    """Load the persisted auth blob ({refresh_token, character_id, name, scopes})
    or {} if none."""
    return load_json(_auth_path(cache_dir), {})


def save_tokens(cache_dir, data):
    save_json(_auth_path(cache_dir), data)


def clear_tokens(cache_dir):
    p = _auth_path(cache_dir)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


# ── Authenticated ESI fetchers ────────────────────────────────────────────────

def _auth_headers(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def fetch_public_char(character_id, session):
    """Public character sheet (name, corporation_id, …). No scope required."""
    r = session.get(f"{ESI}/characters/{character_id}/", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_skills(token, character_id, session):
    """{skills:[{skill_id, active_skill_level, trained_skill_level, ...}],
    total_sp, unallocated_sp}."""
    r = session.get(f"{ESI}/characters/{character_id}/skills/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_skillqueue(token, character_id, session):
    """List of queued skills with finish_date / level."""
    r = session.get(f"{ESI}/characters/{character_id}/skillqueue/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_wallet(token, character_id, session):
    """Current ISK balance (a bare number)."""
    r = session.get(f"{ESI}/characters/{character_id}/wallet/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_wallet_transactions(token, character_id, session):
    """([{transaction_id, date, type_id, quantity, unit_price, is_buy, client_id,
    location_id, journal_ref_id, is_personal}, …], meta) — the character's most
    recent market transactions (up to 2500, newest first), plus ESI cache headers.
    Requires esi-wallet.read_character_wallet.v1. NOTE: transactions carry NO
    order_id, so a sell transaction can't be tied back to a specific sell order —
    it only tells you an item of that type_id sold at that price. ESI caches this
    ~1h. Not paginated here (from_id walks older pages); the newest page is enough
    for incremental accrual since callers dedup by transaction_id."""
    r = session.get(f"{ESI}/characters/{character_id}/wallet/transactions/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    meta = {"last_modified": r.headers.get("Last-Modified"),
            "expires": r.headers.get("Expires")}
    return r.json(), meta


def fetch_loyalty_points(token, character_id, session):
    """([{corporation_id, loyalty_points}, …], meta) where meta carries ESI's
    cache headers — {last_modified, expires}. ESI caches loyalty points for ~1h,
    so the value only changes hourly no matter how often we poll; surfacing
    Last-Modified/Expires lets the UI show an honest "LP as of …" timestamp."""
    r = session.get(f"{ESI}/characters/{character_id}/loyalty/points/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    meta = {"last_modified": r.headers.get("Last-Modified"),
            "expires": r.headers.get("Expires")}
    return r.json(), meta


def fetch_market_orders(token, character_id, session):
    """([{order_id, type_id, is_buy_order, price, volume_remain, volume_total,
    issued, duration, location_id, …}, …], meta) — the character's currently open
    sell/buy orders plus ESI cache headers. Requires esi-markets.read_character_orders.v1."""
    r = session.get(f"{ESI}/characters/{character_id}/orders/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    meta = {"last_modified": r.headers.get("Last-Modified"),
            "expires": r.headers.get("Expires")}
    return r.json(), meta


def fetch_location(token, character_id, session):
    """{solar_system_id, station_id?, structure_id?} — the character's current
    location. Requires esi-location.read_location.v1. ESI caches this ~5s, so
    polling faster than that just returns the same cached position. Note: a
    logged-out character keeps returning its last-known system, so pair this with
    fetch_online() to tell "still here" from "logged off in this system"."""
    r = session.get(f"{ESI}/characters/{character_id}/location/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_online(token, character_id, session):
    """{online: bool, last_login, last_logout, logins} — whether the character is
    currently logged in. Requires esi-location.read_online.v1. ESI caches this
    ~60s, so it lags a real logout/login by up to a minute."""
    r = session.get(f"{ESI}/characters/{character_id}/online/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_ship(token, character_id, session):
    """{ship_item_id, ship_type_id, ship_name} — the character's active ship.
    Requires esi-location.read_ship_type.v1. ESI caches this ~5s. ship_item_id is
    the unique item id of the hull the pilot is flying, which is the location_id of
    everything currently in that ship's holds (see fetch_assets)."""
    r = session.get(f"{ESI}/characters/{character_id}/ship/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def _http_date_to_epoch(value):
    """RFC-7231 HTTP date (e.g. a Last-Modified header) -> epoch seconds, or None."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


def fetch_assets(token, character_id, session):
    """([{item_id, type_id, quantity, location_id, location_flag, is_singleton, ...},
    …], last_modified, expires) — every asset the character owns, across all
    locations, plus two epochs from ESI's cache headers: last_modified (when CCP last
    refreshed the asset data, NOT when we fetched it) and expires (when ESI's cache
    entry lapses, i.e. the earliest a re-fetch could return newer data). Both are
    taken from the first page. Requires esi-assets.read_assets.v1. Paginated
    (X-Pages). Note: ESI caches assets ~1h and only refreshes on server-side asset
    changes, so freshly looted items lag — last_modified reflects that real data age,
    and expires lets a caller schedule its next poll for when fresh data is due."""
    out, page, last_modified, expires = [], 1, None, None
    while page <= 100:
        r = session.get(f"{ESI}/characters/{character_id}/assets/",
                        params={"page": page},
                        headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        if page == 1:
            last_modified = _http_date_to_epoch(r.headers.get("Last-Modified"))
            expires = _http_date_to_epoch(r.headers.get("Expires"))
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
    return out, last_modified, expires


# type_ids that are NEVER real ship haulage even when ESI reports them at the
# ship's location under a cargo flag. PLEX (44992) lives in the account-wide PLEX
# Vault — a secure hold reachable from anywhere, not the ship's cargo — which is
# exactly why the in-game client omits it from a ship's cargo manifest. Counting it
# inflated the cargo value by the whole vault (e.g. 560 PLEX ≈ 3.46B ISK).
CARGO_EXCLUDED_TYPE_IDS = frozenset({44992})


# location_flags that count as "cargo" the pilot is hauling. Fitted modules,
# rigs and the like are excluded — this is the loot/haul in the holds, not the
# fit. Kept broad so ore/gas/mineral/fuel/ammo holds all count.
CARGO_FLAGS = frozenset({
    "Cargo", "DroneBay", "FighterBay", "FleetHangar", "ShipHangar",
    "SpecializedOreHold", "SpecializedGasHold", "SpecializedMineralHold",
    "SpecializedSalvageHold", "SpecializedAmmoHold", "SpecializedFuelBay",
    "SpecializedCommandCenterHold", "SpecializedPlanetaryCommoditiesHold",
    "SpecializedIndustrialShipHold", "SpecializedMaterialBay", "SpecializedAsteroidHold",
    "SpecializedIceHold", "SpecializedSmallShipHold", "SpecializedMediumShipHold",
    "SpecializedLargeShipHold", "SpecializedShipHold",
})


def cargo_items_in_ship(assets, ship_item_id):
    """From a raw assets list, return {type_id: quantity} for everything sitting in
    the given ship's cargo/hold flags (see CARGO_FLAGS). Excludes fitted modules,
    rigs, the ship itself, and account-vault items like PLEX that ESI reports at the
    ship but that aren't real haulage (see CARGO_EXCLUDED_TYPE_IDS). Stacks of the
    same type are summed."""
    out = {}
    for a in assets or []:
        if a.get("location_id") != ship_item_id:
            continue
        if a.get("location_flag") not in CARGO_FLAGS:
            continue
        tid = a.get("type_id")
        if tid is None:
            continue
        if int(tid) in CARGO_EXCLUDED_TYPE_IDS:
            continue
        out[int(tid)] = out.get(int(tid), 0) + int(a.get("quantity") or 0)
    return out


def fetch_industry_jobs(token, character_id, session, include_completed=False):
    """[{job_id, activity_id, blueprint_type_id, product_type_id, runs, status,
    start_date, end_date, …}, …]."""
    r = session.get(f"{ESI}/characters/{character_id}/industry/jobs/",
                    params={"include_completed": str(include_completed).lower()},
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_character_blueprints(token, character_id, session):
    """[{item_id, location_id, location_flag, type_id, quantity, runs,
    material_efficiency, time_efficiency}, …] — every blueprint (BPO and BPC)
    the character owns, across all its assets. quantity is -1 for a BPO, -2 for
    a single BPC, or the stack size for multiple identical BPCs. Requires
    esi-characters.read_blueprints.v1. Paginated (X-Pages)."""
    out, page = [], 1
    while page <= 50:
        r = session.get(f"{ESI}/characters/{character_id}/blueprints/",
                        params={"page": page},
                        headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
    return out


# ── Mapping helpers ───────────────────────────────────────────────────────────

def skill_profile_from_skills(skills_resp):
    """ESI skills response -> {skill_id: trained_level} for the Industry planner."""
    out = {}
    for s in (skills_resp or {}).get("skills", []):
        sid = s.get("skill_id")
        lvl = s.get("trained_skill_level", s.get("active_skill_level", 0))
        if sid is not None:
            out[int(sid)] = int(lvl or 0)
    return out


def owned_blueprint_lookup(blueprints_resp):
    """ESI blueprints response -> {type_id: (me, te, is_bpo, max_runs)} for the
    Industry planner. When a character owns several copies of the same blueprint
    type, prefers BPOs over BPCs (infinite runs), then highest ME/TE. max_runs
    is -1 for BPOs (unlimited), or the highest remaining runs across BPCs."""
    best = {}
    for b in blueprints_resp or []:
        tid = b.get("type_id")
        if tid is None:
            continue
        me = int(b.get("material_efficiency") or 0)
        te = int(b.get("time_efficiency") or 0)
        qty = b.get("quantity", -1)
        is_bpo = (qty == -1)
        runs = -1 if is_bpo else int(b.get("runs") or 0)
        prev = best.get(tid)
        if prev is None:
            best[tid] = (me, te, is_bpo, runs)
        elif is_bpo and not prev[2]:
            best[tid] = (me, te, is_bpo, -1)
        elif is_bpo == prev[2] and (me, te) > (prev[0], prev[1]):
            best[tid] = (me, te, is_bpo, max(runs, prev[3]) if not is_bpo else -1)
        elif not is_bpo and not prev[2] and runs > prev[3]:
            best[tid] = (prev[0], prev[1], False, runs)
    return best


def access_token_expired(expires_at, skew=60):
    """True if an access token whose epoch expiry is `expires_at` should be
    refreshed now (with a safety skew)."""
    return not expires_at or time.time() >= (expires_at - skew)
