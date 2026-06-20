#!/usr/bin/env python3
"""
EVE Online negative-spread / arbitrage scanner.
Pulls the full public order book for a region from CCP's official ESI API and
flags type_ids where the best (lowest) SELL order sits below the best (highest)
BUY order *after sales tax* -- i.e. you could buy from a sell order and resell
to a buy order at a profit.
  Same-station mode (default): both legs at one station = "instant flip".
      Mostly catches fat-finger mispricings (gone fast) or illiquid junk.
  Cross-station mode (--cross-station): legs at different stations in the region.
      Real arbitrage, but you have to haul the goods (time + cargo + gank risk).
Data source : https://esi.evetech.net   (official, public, no auth for region orders)
Caveats     : ESI region orders EXCLUDE most player-structure (Upwell) markets, so
              prices can differ from the in-game view. For scanning every region,
              prefer Fuzzwork's bulk dumps over hammering ESI.
Usage:
  pip install requests
  python eve_spread_scanner.py                          # Jita / The Forge, instant flips
  python eve_spread_scanner.py --cross-station          # allow hauling within the region
  python eve_spread_scanner.py --region 10000043        # Domain (Amarr)
  python eve_spread_scanner.py --sales-tax 0.034 --top 60
Caching     : The order book is cached locally (./.eve_scanner_cache/) so you can re-run with
              different --max-jumps / --round-trip / --top / etc. without re-hitting ESI.
              The cache is "smart": it follows ESI's own freshness metadata rather than a
              blind timer. Every orders response carries Expires (when ESI will have a new
              snapshot -- market orders refresh on a ~5 min cycle), Last-Modified (when the
              current snapshot was built) and an ETag (a version id). So:
                * Before the cached snapshot's Expires time, we KNOW upstream hasn't changed
                  -- the cache is served with zero network calls.
                * Once expired, we send the stored ETag as If-None-Match. ESI answers 304
                  Not Modified (a few bytes) when the market hasn't actually moved yet, and
                  we just bump the local expiry; only a real new version triggers a full
                  re-download of the order book.
              If ESI is unreachable, the last cached book is reused (stale) instead of
              failing. --refresh forces a full re-fetch regardless (rarely needed).
              Station/volume/route lookups are cached indefinitely across runs since that
              data is essentially static.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import requests
ESI = "https://esi.evetech.net"
# Jita IV - Moon 4 - Caldari Navy Assembly Plant, and its solar system.
JITA_STATION_ID = 60003760
JITA_SYSTEM_ID = 30000142
# Tells ESI which behaviour version to serve. Bump this as you review the spec.
COMPAT_DATE = "2025-08-26"
# Be a good citizen: ESI asks for a real contact so they ping you instead of banning you.
USER_AGENT = "negative-spread-scanner/1.0 (your-email@example.com)"
HEADERS = {
    "X-Compatibility-Date": COMPAT_DATE,
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}
# ---------------------------------------------------------------------------
# Terminal colour + security-status helpers.
# EVE space is split by solar-system security status:
#   high  >= 0.5  (CONCORD protects you; ganks are punished)
#   low   0.1-0.4 (no CONCORD; gate/station guns only)
#   null  <= 0.0  (no rules at all)
# The scanner now resolves the sec status of the From station, the To station,
# and every system the haul route passes through, so a "Jita flip" that
# actually drags goods through nullsec is flagged instead of looking safe.
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
# Flipped on/off in main() based on --no-color and whether stdout is a TTY.
USE_COLOR = True
# ---------------------------------------------------------------------------
# Progress bar (stderr, no external deps).
# ---------------------------------------------------------------------------
class ProgressBar:
    """Simple in-terminal progress bar that rewrites the current line.
    On a TTY it redraws in place; when stderr is redirected it stays quiet
    and emits a single completion line from done() so log files stay clean."""
    def __init__(self, total, prefix="", width=36, file=None):
        self._file = file or sys.stderr
        self.total = max(total, 1)
        self.prefix = prefix
        self.width = width
        self.current = 0
        self._tty = self._file.isatty()

    def update(self, n=1):
        self.current = min(self.current + n, self.total)
        self._draw()

    def set(self, n):
        self.current = min(n, self.total)
        self._draw()

    def _draw(self):
        if not self._tty:  # Non-TTY: stay silent until done() summarises.
            return
        pct = self.current / self.total
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        print(f"\r  {self.prefix} [{bar}] {self.current}/{self.total}",
              end="", file=self._file, flush=True)

    def done(self, msg=""):
        self.current = self.total
        if self._tty:
            self._draw()
            print(f"  {msg}" if msg else "", file=self._file)
        else:
            tail = f" -- {msg}" if msg else ""
            print(f"  {self.prefix}: {self.total} done{tail}", file=self._file)
def c(text, code):
    """Wrap text in an ANSI colour code (no-op when colour is disabled)."""
    if not USE_COLOR or not code:
        return text
    return f"{code}{text}{RESET}"
def pad(text, width, align="<"):
    """Truncate/pad PLAIN text to a fixed width. Always do this before adding
    colour codes -- ANSI escapes count as characters in f-string padding and
    would otherwise wreck column alignment."""
    text = str(text)
    if len(text) > width:
        text = text[:width]
    return f"{text:{align}{width}}"
def round_sec(sec):
    """Round a raw security_status the way EVE displays it: tiny positives are
    shown as 0.1 (never 0.0), everything else to one decimal."""
    if sec is None:
        return None
    if 0.0 < sec < 0.05:
        return 0.1
    return round(sec, 1)
def sec_band(sec):
    """Classify a raw security_status into 'high' / 'low' / 'null' / 'unknown'."""
    if sec is None:
        return "unknown"
    r = round_sec(sec)
    if r >= 0.5:
        return "high"
    if r >= 0.1:
        return "low"
    return "null"
_BAND_COLOR = {"high": GREEN, "low": YELLOW, "null": RED, "unknown": DIM}
def sec_color(sec):
    return _BAND_COLOR[sec_band(sec)]
def sec_str(sec):
    """Right-aligned one-decimal sec value, or '?' when unknown."""
    r = round_sec(sec)
    return "  ?" if r is None else f"{r:>4.1f}"
def row_risk_sec(r):
    """Worst (lowest) security status anywhere on the deal: the From system,
    the To system, or any system the route passes through. None if unknown."""
    secs = [r.get("from_sec"), r.get("to_sec"), r.get("route_min_sec")]
    secs = [s for s in secs if s is not None]
    return min(secs) if secs else None
# Labels for the Risk column. They describe the WORST security band touched on
# the deal (both stations + every route hop), phrased as danger to the hauler --
# so a fully-highsec deal reads "SAFE", not "HIGH" (the old label was confusing,
# since *high* security is actually the safe end).
_RISK_LABEL = {"high": "SAFE", "low": "LOWSEC", "null": "NULLSEC", "unknown": "?"}
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default
def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
def load_lookup_cache(cache_dir):
    """Load the persistent station/volume/route lookup cache. This data is
    essentially static EVE universe data, so it's kept indefinitely across
    runs rather than expiring like the order book does."""
    raw = load_json(cache_dir / "lookups.json", {})
    stations = {int(k): v for k, v in raw.get("stations", {}).items()}
    volumes = {int(k): v for k, v in raw.get("volumes", {}).items()}
    systems = {int(k): v for k, v in raw.get("systems", {}).items()}
    routes = {}
    for k, v in raw.get("routes", {}).items():
        parts = k.split(":")
        # New keys are "origin:dest:flag" and store the full route (list of
        # system_ids). Legacy keys were "origin:dest" and stored just a jump
        # count (int); we read those back under the default 'shortest' flag.
        if len(parts) == 3:
            a, b, flag = parts
        else:
            a, b, flag = parts[0], parts[1], "shortest"
        routes[(int(a), int(b), flag)] = v
    return stations, volumes, systems, routes
def save_lookup_cache(cache_dir, stations, volumes, systems, routes):
    save_json(cache_dir / "lookups.json", {
        "stations": {str(k): v for k, v in stations.items()},
        "volumes": {str(k): v for k, v in volumes.items()},
        "systems": {str(k): v for k, v in systems.items()},
        "routes": {f"{a}:{b}:{flag}": v for (a, b, flag), v in routes.items()},
    })
def _http_date_to_epoch(value):
    """Parse an HTTP date header (e.g. Expires/Last-Modified) into a Unix
    timestamp. Returns None if absent or unparseable."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
def _meta_from_headers(headers):
    """Pull ESI's freshness metadata off a response. ETag = version id,
    Expires = when a new snapshot is due, Last-Modified = when this one was built."""
    return {
        "etag": headers.get("ETag"),
        "expires": _http_date_to_epoch(headers.get("Expires")),
        "last_modified": headers.get("Last-Modified"),
    }
def _normalize_cache(cached, path):
    """Accept both the new {orders, etag, expires, ...} cache shape and the
    legacy bare-list format, so old caches keep working after the upgrade."""
    if cached is None:
        return None
    if isinstance(cached, list):
        return {"orders": cached, "etag": None, "expires": None,
                "last_modified": None, "fetched_at": path.stat().st_mtime}
    return cached
def _expiry_str(expires, now):
    """Concise 'when does this cached data expire' note, e.g.
    'expires 13:25:00 UTC (~4 min)'."""
    if not expires:
        return "expiry unknown"
    clock = datetime.fromtimestamp(expires, timezone.utc).strftime("%H:%M:%S UTC")
    return f"expires {clock} (~{(expires - now) / 60:.0f} min)"
def _store_orders(path, orders, meta, now):
    save_json(path, {
        "orders": orders,
        "etag": meta.get("etag"),
        "expires": meta.get("expires"),
        "last_modified": meta.get("last_modified"),
        "fetched_at": now,
    })
def get_orders(region_id, session, cache_dir, refresh):
    """Order book for a region, served from a local JSON cache when ESI's own
    freshness metadata says it's still current, so repeated runs -- e.g. trying
    different --max-jumps values -- don't have to re-paginate ESI's
    /markets/{region}/orders/ endpoint every time.
    Freshness is driven by the response headers, not a fixed timer:
      * If the cached snapshot's Expires time is still in the future, upstream
        hasn't changed -> serve the cache with no network call at all.
      * Once expired, revalidate cheaply with If-None-Match: <etag>. A 304 means
        the market hasn't refreshed yet -> reuse the cache and bump its expiry.
        A 200 means a genuinely new snapshot -> store it.
    If ESI can't be reached, a previously cached book is reused (stale) rather
    than failing outright. --refresh forces a full re-fetch regardless."""
    path = cache_dir / f"orders_region_{region_id}.json"
    cached = _normalize_cache(load_json(path, None), path)
    now = time.time()
    if not refresh and cached and cached.get("orders"):
        orders = cached["orders"]
        expires = cached.get("expires")
        snap_meta = {k: cached.get(k) for k in ("etag", "expires", "last_modified", "fetched_at")}
        # 1) Still inside ESI's published freshness window -> definitely no new data.
        if expires and now < expires:
            print(f"Using cached order book for region {region_id} "
                  f"({len(orders)} orders); {_expiry_str(expires, now)} -- no ESI calls made.",
                  file=sys.stderr)
            return orders, snap_meta
        # 2) Expired -> revalidate. With an ETag this is a cheap conditional request.
        etag = cached.get("etag")
        print(f"Cached order book for region {region_id} expired; "
              "checking ESI for a newer snapshot ...", file=sys.stderr)
        try:
            new_orders, meta = fetch_region_orders(region_id, session, etag=etag)
        except requests.RequestException as e:
            print(f"  ESI unreachable ({e}); reusing {len(orders)} cached orders.",
                  file=sys.stderr)
            return orders, snap_meta
        if new_orders is None:  # 304 Not Modified -- market hasn't refreshed yet.
            for k, v in meta.items():
                if v is not None:
                    cached[k] = v
            cached["fetched_at"] = now
            save_json(path, cached)
            print(f"  ESI: 304 Not Modified -- reusing {len(orders)} cached orders; "
                  f"{_expiry_str(meta.get('expires'), now)}.", file=sys.stderr)
            snap_meta = {k: cached.get(k) for k in ("etag", "expires", "last_modified", "fetched_at")}
            return orders, snap_meta
        print(f"  ESI served a new snapshot -- {len(new_orders)} orders.", file=sys.stderr)
        _store_orders(path, new_orders, meta, now)
        snap_meta = {**meta, "fetched_at": now}
        return new_orders, snap_meta
    print(f"Fetching order book for region {region_id} ...", file=sys.stderr)
    orders, meta = fetch_region_orders(region_id, session)
    _store_orders(path, orders, meta, now)
    snap_meta = {**meta, "fetched_at": now}
    return orders, snap_meta
def fetch_region_orders(region_id, session, etag=None):
    """Fetch every page of buy + sell orders for a region.
    Returns (orders, meta) where meta holds ESI's etag/expires/last_modified from
    the first page (ESI builds the whole multi-page snapshot together, so page 1's
    metadata describes the dataset). If etag is given it's sent as If-None-Match on
    page 1; when ESI replies 304 Not Modified, returns (None, meta) without paging."""
    orders, page = [], 1
    meta = {}
    bar = None
    while True:
        req_headers = dict(HEADERS)
        if page == 1 and etag:
            req_headers["If-None-Match"] = etag
        r = session.get(
            f"{ESI}/markets/{region_id}/orders/",
            params={"order_type": "all", "page": page},
            headers=req_headers,
            timeout=30,
        )
        if page == 1 and r.status_code == 304:
            return None, _meta_from_headers(r.headers)
        r.raise_for_status()
        if page == 1:
            meta = _meta_from_headers(r.headers)  # ETag/Expires for the whole snapshot
        batch = r.json()
        if not batch:
            break
        orders.extend(batch)
        total_pages = int(r.headers.get("X-Pages", 1))
        if bar is None:
            bar = ProgressBar(total_pages, prefix="Fetching pages")
        bar.set(page)
        if page >= total_pages:
            break
        page += 1
    if bar:
        bar.done(f"{len(orders)} orders received")
    return orders, meta
def resolve_names(type_ids, session):
    """Map type_id -> name via the bulk /universe/names/ endpoint (<=1000 per call)."""
    names, ids = {}, list(type_ids)
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        r = session.post(f"{ESI}/universe/names/", json=chunk, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for entry in r.json():
            names[entry["id"]] = entry["name"]
    return names
def resolve_volume(type_id, cache, session):
    """m3 per unit for a type_id, via /universe/types/{id}/ (no bulk endpoint).
    Prefers packaged_volume (what actually fits in cargo/freighter holds) over
    the unpackaged 'volume' field. Returns None if the lookup fails."""
    if type_id in cache:
        return cache[type_id]
    r = session.get(f"{ESI}/universe/types/{type_id}/", headers=HEADERS, timeout=30)
    vol = None
    if r.status_code == 200:
        data = r.json()
        vol = data.get("packaged_volume", data.get("volume"))
    cache[type_id] = vol
    return vol
def find_spreads(orders, sales_tax, same_station_only):
    """
    Bucket orders, keep the best sell (min price) and best buy (max price) per bucket,
    and return those where buy*(1-tax) > sell. Bucket is (type_id, location_id) for
    same-station mode, else (type_id,).
    """
    best_sell, best_buy = {}, {}
    for o in orders:
        key = (o["type_id"], o["location_id"]) if same_station_only else (o["type_id"],)
        book = best_buy if o["is_buy_order"] else best_sell
        cur = book.get(key)
        if cur is None or (o["price"] > cur["price"] if o["is_buy_order"]
                           else o["price"] < cur["price"]):
            book[key] = o
    results = []
    for key, sell in best_sell.items():
        buy = best_buy.get(key)
        if buy is None:
            continue
        net_proceeds = buy["price"] * (1.0 - sales_tax)   # selling INTO a buy order: tax only
        if net_proceeds > sell["price"]:
            qty = min(sell["volume_remain"], buy["volume_remain"])
            min_vol = max(sell.get("min_volume", 1), buy.get("min_volume", 1))
            if qty < min_vol:
                # Can't fill either order's minimum-quantity requirement in one
                # match -- the flip isn't actually executable, so skip it.
                continue
            net_per_unit = net_proceeds - sell["price"]
            results.append({
                "type_id": key[0],
                "sell_price": sell["price"],
                "buy_price": buy["price"],
                "sell_location": sell["location_id"],
                "buy_location": buy["location_id"],
                "net_per_unit": net_per_unit,
                "flippable_qty": qty,
                "isk_opportunity": net_per_unit * qty,
                "margin_pct": net_per_unit / sell["price"] * 100.0,
            })
    results.sort(key=lambda x: x["isk_opportunity"], reverse=True)
    return results
def resolve_station(station_id, cache, session):
    """Station name + solar system_id via /universe/stations/{id}/ (NPC
    stations only; Upwell structures need an extra auth scope and are
    already excluded upstream). Cached; returns None on lookup failure."""
    if station_id in cache:
        return cache[station_id]
    r = session.get(f"{ESI}/universe/stations/{station_id}/", headers=HEADERS, timeout=30)
    info = None
    if r.status_code == 200:
        data = r.json()
        info = {"name": data.get("name", str(station_id)), "system_id": data.get("system_id")}
    cache[station_id] = info
    return info
def resolve_system(system_id, cache, session):
    """Solar-system name + security_status via /universe/systems/{id}/.
    Cached indefinitely (static universe data); returns None on failure."""
    if system_id in cache:
        return cache[system_id]
    r = session.get(f"{ESI}/universe/systems/{system_id}/", headers=HEADERS, timeout=30)
    info = None
    if r.status_code == 200:
        data = r.json()
        info = {"name": data.get("name", str(system_id)),
                "sec": data.get("security_status")}
    cache[system_id] = info
    return info
def route_info(origin_system, dest_system, flag, cache, session):
    """Return (jumps, route_systems) between two systems via ESI /route/.
    route_systems is the ordered list of system_ids the route passes through
    (so callers can inspect the security of every hop, not just the endpoints).
    flag is ESI's routing preference: 'shortest', 'secure' (avoids low/null),
    or 'insecure'. Cached per (origin, dest, flag). Legacy cache entries stored
    only a jump count, so route_systems comes back None for those until the
    route is re-resolved."""
    if origin_system == dest_system:
        return 0, [origin_system]
    key = (origin_system, dest_system, flag)
    if key in cache:
        v = cache[key]
        if isinstance(v, list):
            return len(v) - 1, v
        return v, None  # legacy: bare jump count, no per-hop systems
    r = session.get(f"{ESI}/route/{origin_system}/{dest_system}/",
                     params={"flag": flag}, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        cache[key] = None
        return None, None
    route = r.json()
    cache[key] = route
    return len(route) - 1, route
def enrich_locations(results, round_trip, route_flag, session, station_cache, route_cache):
    """Resolve sell/buy station names + systems and the jump distance between
    them for every row (the haul leg: buy at sell_location, deliver/sell at
    buy_location). Tags rows with sell_station_name, buy_station_name,
    sell_system_id, buy_system_id, jumps_one_way, jumps_total (one-way
    doubled when round_trip is set, to cover going there AND back) and
    route_systems (the system_ids the haul passes through, used later to flag
    low/null exposure). Drops rows whose station/route can't be resolved.
    route_flag picks ESI's routing preference (shortest/secure/insecure).
    station_cache/route_cache are passed in (rather than created here) so they
    can be persisted across runs."""
    enriched = []
    for r in results:
        sell_info = resolve_station(r["sell_location"], station_cache, session)
        buy_info = resolve_station(r["buy_location"], station_cache, session)
        if sell_info is None or buy_info is None:
            continue
        one_way, route_systems = route_info(
            sell_info["system_id"], buy_info["system_id"],
            route_flag, route_cache, session)
        if one_way is None:
            continue
        r["sell_station_name"] = sell_info["name"]
        r["buy_station_name"] = buy_info["name"]
        r["sell_system_id"] = sell_info["system_id"]
        r["buy_system_id"] = buy_info["system_id"]
        r["jumps_one_way"] = one_way
        r["jumps_total"] = one_way * 2 if round_trip else one_way
        r["route_systems"] = route_systems
        enriched.append(r)
    return enriched
def enrich_security(rows, session, system_cache):
    """Resolve security status for the From system, the To system, and the
    lowest-sec system anywhere on the haul route, for each row. Only called on
    the handful of rows actually printed, so the extra /universe/systems/
    lookups stay bounded. Tags from_sec, to_sec, route_min_sec."""
    for r in rows:
        from_info = resolve_system(r["sell_system_id"], system_cache, session)
        to_info = resolve_system(r["buy_system_id"], system_cache, session)
        r["from_sec"] = from_info["sec"] if from_info else None
        r["to_sec"] = to_info["sec"] if to_info else None
        route_systems = r.get("route_systems")
        if route_systems:
            secs = []
            for sys_id in route_systems:
                info = resolve_system(sys_id, system_cache, session)
                if info and info["sec"] is not None:
                    secs.append(info["sec"])
            r["route_min_sec"] = min(secs) if secs else None
        else:
            # Endpoints are known; without the hop list the best we can say is
            # the worse of the two ends.
            ends = [s for s in (r["from_sec"], r["to_sec"]) if s is not None]
            r["route_min_sec"] = min(ends) if ends else None
    return rows
def filter_avoid_lowsec(rows):
    """Drop rows whose From, To, or any route system is lowsec/nullsec."""
    return [r for r in rows if sec_band(row_risk_sec(r)) == "high"]
def build_shown(results, top, already_enriched, avoid_lowsec, round_trip, route_flag,
                session, station_cache, route_cache, system_cache):
    """Walk results in profit order, resolving stations/routes/security lazily,
    and collect up to `top` rows to display. When avoid_lowsec is set, rows that
    touch low/null (at either station or anywhere on the route) are skipped.
    Doing this lazily keeps the extra ESI lookups bounded to roughly `top` rows
    even when --avoid-lowsec has to skip past many low/null deals."""
    shown = []
    for r in results:
        if not already_enriched:
            if not enrich_locations([r], round_trip, route_flag,
                                    session, station_cache, route_cache):
                continue  # station/route couldn't be resolved
        enrich_security([r], session, system_cache)
        if avoid_lowsec and sec_band(row_risk_sec(r)) != "high":
            continue
        shown.append(r)
        if len(shown) >= top:
            break
    return shown
def filter_from_jita(results, max_jumps):
    """Keep only already-enriched rows where one leg is in the Jita system
    and jumps_total is within max_jumps."""
    return [r for r in results
            if (r["sell_system_id"] == JITA_SYSTEM_ID or r["buy_system_id"] == JITA_SYSTEM_ID)
            and r["jumps_total"] <= max_jumps]
def print_grouped_by_from(shown):
    """Second view: aggregate the displayed deals by their 'From' station (where
    you buy) and show cumulative ISK opportunity per source, so you can see which
    single pickup location concentrates the most profit. Only printed when the
    deals span more than one source station."""
    groups = {}
    for r in shown:
        g = groups.setdefault(r["sell_station_name"],
                              {"deals": 0, "isk": 0.0, "vol": 0.0, "vol_known": True,
                               "sec": r.get("from_sec")})
        g["deals"] += 1
        g["isk"] += r["isk_opportunity"]
        if r.get("total_volume") is not None:
            g["vol"] += r["total_volume"]
        else:
            g["vol_known"] = False
    if len(groups) < 2:
        return
    rows = sorted(groups.items(), key=lambda kv: kv[1]["isk"], reverse=True)
    print(f"\nBy source station (From) -- cumulative opportunity across {len(shown)} shown deals:")
    header = (pad("From (buy here)", 42) + pad("Sec", 5, ">") + pad("Deals", 7, ">")
              + pad("Cumulative ISK", 20, ">") + pad("Total Vol m3", 18, ">"))
    print(c(header, BOLD + CYAN))
    print(c("-" * len(header), DIM))
    for name, g in rows:
        vol_str = (f"{g['vol']:,.1f}" if g["vol_known"]
                   else "~" + format(g["vol"], ",.1f"))
        sc = sec_color(g["sec"])
        print(c(pad(name, 42), sc) + c(pad(sec_str(g["sec"]), 5, ">"), sc)
              + pad(str(g["deals"]), 7, ">") + pad(f"{g['isk']:,.0f}", 20, ">")
              + pad(vol_str, 18, ">"))
    print("(~ = total excludes item(s) whose volume couldn't be resolved.)")
def main():
    ap = argparse.ArgumentParser(description="Scan an EVE region for negative-spread flips.")
    ap.add_argument("--region", type=int, default=10000002,
                    help="region_id (default 10000002 = The Forge / Jita)")
    ap.add_argument("--sales-tax", type=float, default=0.075,
                    help="effective sales tax as a fraction (default 0.075; lower it as your "
                         "Accounting skill / standings improve)")
    ap.add_argument("--cross-station", action="store_true",
                    help="allow buy and sell at different stations in the region (requires hauling)")
    ap.add_argument("--min-isk", type=float, default=0.0,
                    help="hide opportunities below this total ISK (default 0)")
    ap.add_argument("--top", type=int, default=40, help="rows to print (default 40)")
    ap.add_argument("--from-jita", action="store_true",
                    help="only keep deals where one leg is a Jita station and the other leg "
                         "is within --max-jumps of Jita (combine with --cross-station to allow "
                         "the other leg to be a different station)")
    ap.add_argument("--max-jumps", type=int, default=5,
                    help="max jumps from Jita for the non-Jita leg, used with --from-jita "
                         "(default 5). One-way unless --round-trip is set.")
    ap.add_argument("--round-trip", action="store_true",
                    help="with --from-jita, treat --max-jumps as a round-trip cap "
                         "(one-way jump count is doubled before filtering/display) "
                         "to account for hauling there AND back")
    ap.add_argument("--route-flag", choices=["shortest", "secure", "insecure"],
                    default="shortest",
                    help="ESI routing preference for the haul leg (default shortest). "
                         "'secure' avoids low/null entirely; 'shortest' may cut through "
                         "low/null (the Risk column tells you when it does)")
    ap.add_argument("--avoid-lowsec", action="store_true",
                    help="drop any deal whose From, To, or route passes through "
                         "lowsec/nullsec -- highsec-only results")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI colour (also auto-disabled when stdout isn't a TTY)")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the cached order book and force a fresh pull from ESI "
                         "(normally unnecessary -- the cache auto-refreshes via ESI's own "
                         "Expires/ETag metadata)")
    ap.add_argument("--cache-dir", default=None,
                    help="local cache directory (default: ./.eve_scanner_cache next to this "
                         "script)")
    args = ap.parse_args()
    global USE_COLOR
    USE_COLOR = sys.stdout.isatty() and not args.no_color
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(__file__).resolve().parent / ".eve_scanner_cache"
    session = requests.Session()
    station_cache, volume_cache, system_cache, route_cache = load_lookup_cache(cache_dir)
    try:
        orders, snap_meta = get_orders(args.region, session, cache_dir, args.refresh)
        print(f"  {len(orders)} total orders.", file=sys.stderr)
        results = [r for r in find_spreads(orders, args.sales_tax, not args.cross_station)
                   if r["isk_opportunity"] >= args.min_isk]
        if args.from_jita:
            trip_desc = "round-trip" if args.round_trip else "one-way"
            print(f"Filtering to deals with a Jita leg, other leg <={args.max_jumps} "
                  f"jumps {trip_desc} (resolving stations/routes, cached where possible) ...",
                  file=sys.stderr)
            results = enrich_locations(results, args.round_trip, args.route_flag,
                                        session, station_cache, route_cache)
            results = filter_from_jita(results, args.max_jumps)
        if not results:
            print("No profitable negative spreads found after tax. Normal for a liquid hub.")
            return
        if args.avoid_lowsec:
            print("Filtering out deals that touch lowsec/nullsec (--avoid-lowsec) ...",
                  file=sys.stderr)
        # Resolve stations/routes/security lazily for just the rows we'll print.
        # (--from-jita already ran enrich_locations on the full result set.)
        shown = build_shown(results, args.top, args.from_jita, args.avoid_lowsec,
                            args.round_trip, args.route_flag, session,
                            station_cache, route_cache, system_cache)
        if not shown:
            print("No deals left to show (all filtered out).")
            return
        names = resolve_names({r["type_id"] for r in shown}, session)
        unique_types = {r["type_id"] for r in shown}
        print(f"Resolving cargo volumes for {len(unique_types)} item types "
              "(cached where possible) ...", file=sys.stderr)
        # One update per row (volume lookups are cached, so repeated type_ids
        # are cheap); size the bar to the rows we iterate, not unique types.
        vol_bar = ProgressBar(len(shown), prefix="Resolving volumes")
        for r in shown:
            vol_per_unit = resolve_volume(r["type_id"], volume_cache, session)
            r["total_volume"] = vol_per_unit * r["flippable_qty"] if vol_per_unit is not None else None
            vol_bar.update()
        vol_bar.done()
        # Build snapshot timestamp strings for the header.
        lm = snap_meta.get("last_modified")
        exp = snap_meta.get("expires")
        now_ts = time.time()
        snap_time_str = (datetime.fromtimestamp(
            _http_date_to_epoch(lm), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if lm and _http_date_to_epoch(lm) else "unknown")
        exp_str = _expiry_str(exp, now_ts) if exp else "expiry unknown"
        mode = "CROSS-STATION (hauling)" if args.cross_station else "SAME-STATION (instant flip)"
        print(f"\n{c(f'Region {args.region}', BOLD)} | {mode} | sales tax {args.sales_tax:.1%} "
              f"| route flag '{args.route_flag}'"
              f"\n{c('Snapshot:', DIM)} {snap_time_str}  |  {exp_str}"
              f"  |  {len(orders):,} orders scanned  |  {len(results):,} spreads found")
        jumps_header = "Jumps(RT)" if args.round_trip else "Jumps"
        header = (pad("Item", 40) + pad("Buy", 15, ">") + pad("Sell", 15, ">")
                  + pad("Net/u", 13, ">") + pad("Mgn%", 8, ">") + pad("Qty", 9, ">")
                  + pad("ISK opp", 17, ">") + pad("Vol m3", 14, ">") + "  "
                  + pad("From (buy here)", 32) + pad("Sec", 5, ">") + "  "
                  + pad("To (sell here)", 32) + pad("Sec", 5, ">")
                  + pad(jumps_header, 10, ">") + pad("Risk", 7, ">"))
        print(c(header, BOLD + CYAN))
        print(c("-" * len(header), DIM))
        total_volume, unknown_volume = 0.0, 0
        for r in shown:
            name = names.get(r["type_id"], str(r["type_id"]))
            if r["total_volume"] is not None:
                vol_str = f"{r['total_volume']:,.1f}"
                total_volume += r["total_volume"]
            else:
                vol_str = "?"
                unknown_volume += 1
            risk_sec = row_risk_sec(r)
            risk_band = sec_band(risk_sec)
            risk_color = sec_color(risk_sec)
            line = (
                pad(name, 40)
                + pad(f"{r['buy_price']:,.2f}", 15, ">")
                + pad(f"{r['sell_price']:,.2f}", 15, ">")
                + c(pad(f"{r['net_per_unit']:,.2f}", 13, ">"), GREEN)
                + c(pad(f"{r['margin_pct']:.1f}%", 8, ">"), GREEN)
                + pad(f"{r['flippable_qty']:,}", 9, ">")
                + c(pad(f"{r['isk_opportunity']:,.0f}", 17, ">"), GREEN)
                + pad(vol_str, 14, ">") + "  "
                + c(pad(r["sell_station_name"], 32), sec_color(r.get("from_sec")))
                + c(pad(sec_str(r.get("from_sec")), 5, ">"), sec_color(r.get("from_sec"))) + "  "
                + c(pad(r["buy_station_name"], 32), sec_color(r.get("to_sec")))
                + c(pad(sec_str(r.get("to_sec")), 5, ">"), sec_color(r.get("to_sec")))
                + c(pad(str(r["jumps_total"]), 10, ">"), risk_color)
                + c(pad(_RISK_LABEL[risk_band], 7, ">"), risk_color)
            )
            print(line)
        print(c("-" * len(header), DIM))
        note = f" ({unknown_volume} item(s) had unresolvable volume, excluded)" if unknown_volume else ""
        print(f"Total cargo volume for all {len(shown)} rows shown: {total_volume:,.1f} m3{note}")
        print("From = where you buy (sell order location); To = where you deliver and sell "
              "(buy order location).")
        print(f"Sec/Risk colour: {c('high >=0.5', GREEN)}  {c('low 0.1-0.4', YELLOW)}  "
              f"{c('null <=0.0', RED)}. Risk column = SAFE (all highsec) / LOWSEC / NULLSEC, "
              "based on the worst sec at either station OR anywhere on the haul route.")
        print_grouped_by_from(shown)
        if args.cross_station:
            print("\nNote: cross-station rows require moving goods between stations "
                  "(location IDs differ). Factor in haul time and gank risk -- "
                  "watch the Risk column for LOWSEC/NULLSEC exposure.")
    finally:
        save_lookup_cache(cache_dir, station_cache, volume_cache, system_cache, route_cache)
if __name__ == "__main__":
    main()