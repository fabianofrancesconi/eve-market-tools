#!/usr/bin/env python3
"""
Arbitrage (negative-spread) scanner data layer for lp-web.py.
All ESI fetch + scan logic from eve-scanner.py, CLI/terminal code removed.
"""
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ESI = "https://esi.evetech.net"
FUZZWORK_AGG = "https://market.fuzzwork.co.uk/aggregates/"
JITA_STATION_ID = 60003760
JITA_SYSTEM_ID = 30000142
COMPAT_DATE = "2025-08-26"
USER_AGENT = "negative-spread-scanner/1.0 (fabiano.francesconi@gmail.com)"
HEADERS = {
    "X-Compatibility-Date": COMPAT_DATE,
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}
_FUZZWORK_HEADERS = {"User-Agent": USER_AGENT}
_FUZZWORK_BATCH = 200
_TYPES_CACHE_TTL = 600  # 10 min — type lists change slowly
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
    raw = load_json(cache_dir / "lookups.json", {})
    stations = {int(k): v for k, v in raw.get("stations", {}).items()}
    volumes = {int(k): v for k, v in raw.get("volumes", {}).items()}
    systems = {int(k): v for k, v in raw.get("systems", {}).items()}
    routes = {}
    for k, v in raw.get("routes", {}).items():
        parts = k.split(":")
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
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


def _meta_from_headers(headers):
    return {
        "etag": headers.get("ETag"),
        "expires": _http_date_to_epoch(headers.get("Expires")),
        "last_modified": headers.get("Last-Modified"),
    }


def _normalize_cache(cached, path):
    if cached is None:
        return None
    if isinstance(cached, list):
        return {"orders": cached, "etag": None, "expires": None,
                "last_modified": None, "fetched_at": path.stat().st_mtime}
    return cached


def _store_orders(path, orders, meta, now):
    save_json(path, {
        "orders": orders,
        "etag": meta.get("etag"),
        "expires": meta.get("expires"),
        "last_modified": meta.get("last_modified"),
        "fetched_at": now,
    })


def fetch_region_types(region_id, session, cache_dir, refresh=False, progress_cb=None):
    """All type_ids with active orders in the region from ESI.
    Cached to disk for _TYPES_CACHE_TTL seconds; refresh=True bypasses."""
    path = cache_dir / f"types_region_{region_id}.json"
    if not refresh:
        cached = load_json(path, None)
        if cached and time.time() - cached.get("fetched_at", 0) < _TYPES_CACHE_TTL:
            if progress_cb:
                progress_cb("cache", count=len(cached["types"]))
            return cached["types"]

    types, page = [], 1
    while True:
        r = session.get(f"{ESI}/markets/{region_id}/types/",
                        params={"page": page}, headers=HEADERS, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        types.extend(batch)
        total_pages = int(r.headers.get("X-Pages", 1))
        if progress_cb:
            progress_cb("page", page=page, pages=total_pages, count=len(types))
        if page >= total_pages:
            break
        page += 1

    save_json(path, {"fetched_at": time.time(), "types": types})
    return types


def fetch_fuzzwork_region(type_ids, region_id, session, progress_cb=None):
    """Min sell / max buy per type_id for the region via Fuzzwork aggregates."""
    out = {}
    ids = sorted(set(type_ids))
    total = (len(ids) + _FUZZWORK_BATCH - 1) // _FUZZWORK_BATCH
    for i, start in enumerate(range(0, len(ids), _FUZZWORK_BATCH)):
        chunk = ids[start:start + _FUZZWORK_BATCH]
        r = session.get(FUZZWORK_AGG,
                        params={"region": region_id,
                                "types": ",".join(map(str, chunk))},
                        headers=_FUZZWORK_HEADERS, timeout=30)
        r.raise_for_status()
        for tid_str, d in r.json().items():
            sell = d.get("sell", {})
            buy = d.get("buy", {})
            out[int(tid_str)] = {
                "sell_min": float(sell.get("min") or 0) or None,
                "buy_max": float(buy.get("max") or 0) or None,
            }
        if progress_cb:
            progress_cb("chunk", chunk=i + 1, total=total,
                        types_done=start + len(chunk))
    return out


def arb_candidates(prices, sales_tax):
    """Type IDs where best-buy (after tax) beats best-sell — guaranteed no false negatives."""
    return [
        tid for tid, p in prices.items()
        if p.get("sell_min") and p.get("buy_max")
        and p["buy_max"] * (1.0 - sales_tax) > p["sell_min"]
    ]


def fetch_type_orders(region_id, type_id, session):
    """All current orders for one type in the region from ESI (not cached)."""
    orders, page = [], 1
    while page <= 5:
        r = session.get(f"{ESI}/markets/{region_id}/orders/",
                        params={"type_id": type_id, "order_type": "all", "page": page},
                        headers=HEADERS, timeout=30)
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        orders.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
    return orders


def get_orders(region_id, session, cache_dir, refresh, progress_cb=None):
    """Fetch or revalidate the region order book.

    progress_cb(stage, **kw) is called at key moments:
      stage="cache"  → kw: orders (int)
      stage="revalidate" → (no extra kw)
      stage="page"   → kw: page, pages, orders
      stage="stale"  → kw: orders (reusing on ESI error)
    """
    def _cb(stage, **kw):
        if progress_cb:
            progress_cb(stage, **kw)

    path = cache_dir / f"orders_region_{region_id}.json"
    cached = _normalize_cache(load_json(path, None), path)
    now = time.time()
    if not refresh and cached and cached.get("orders"):
        # Always serve cache on a regular scan — only hit ESI when the user clicks Refresh.
        snap_meta = {k: cached.get(k) for k in ("etag", "expires", "last_modified", "fetched_at")}
        _cb("cache", orders=len(cached["orders"]))
        return cached["orders"], snap_meta
    _cb("page", page=0, pages=None, orders=0)
    orders, meta = _fetch_region_orders(region_id, session, progress_cb=progress_cb)
    _store_orders(path, orders, meta, now)
    return orders, {**meta, "fetched_at": now}


def _fetch_region_orders(region_id, session, etag=None, progress_cb=None):
    orders, page = [], 1
    meta = {}
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
            meta = _meta_from_headers(r.headers)
        batch = r.json()
        if not batch:
            break
        orders.extend(batch)
        total_pages = int(r.headers.get("X-Pages", 1))
        if progress_cb:
            progress_cb("page", page=page, pages=total_pages, orders=len(orders))
        if page >= total_pages:
            break
        page += 1
    return orders, meta


def find_spreads(orders, sales_tax, same_station_only):
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
        net_proceeds = buy["price"] * (1.0 - sales_tax)
        if net_proceeds > sell["price"]:
            qty = min(sell["volume_remain"], buy["volume_remain"])
            min_vol = max(sell.get("min_volume", 1), buy.get("min_volume", 1))
            if qty < min_vol:
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
    if system_id in cache:
        return cache[system_id]
    r = session.get(f"{ESI}/universe/systems/{system_id}/", headers=HEADERS, timeout=30)
    info = None
    if r.status_code == 200:
        data = r.json()
        info = {"name": data.get("name", str(system_id)), "sec": data.get("security_status")}
    cache[system_id] = info
    return info


def route_info(origin_system, dest_system, flag, cache, session):
    if origin_system == dest_system:
        return 0, [origin_system]
    key = (origin_system, dest_system, flag)
    if key in cache:
        v = cache[key]
        if isinstance(v, list):
            return len(v) - 1, v
        return v, None
    r = session.get(f"{ESI}/route/{origin_system}/{dest_system}/",
                    params={"flag": flag}, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        cache[key] = None
        return None, None
    route = r.json()
    cache[key] = route
    return len(route) - 1, route


def enrich_locations(results, round_trip, route_flag, session, station_cache, route_cache):
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
            ends = [s for s in (r["from_sec"], r["to_sec"]) if s is not None]
            r["route_min_sec"] = min(ends) if ends else None
    return rows


def round_sec(sec):
    if sec is None:
        return None
    if 0.0 < sec < 0.05:
        return 0.1
    return round(sec, 1)


def filter_from_jita(results, max_jumps):
    """Keep only already-enriched rows where one leg is in the Jita system
    and jumps_total is within max_jumps."""
    return [r for r in results
            if (r.get("sell_system_id") == JITA_SYSTEM_ID or
                r.get("buy_system_id") == JITA_SYSTEM_ID)
            and r.get("jumps_total", 0) <= max_jumps]


def sec_band(sec):
    if sec is None:
        return "unknown"
    r = round_sec(sec)
    if r >= 0.5:
        return "high"
    if r >= 0.1:
        return "low"
    return "null"


def row_risk_sec(r):
    secs = [r.get("from_sec"), r.get("to_sec"), r.get("route_min_sec")]
    secs = [s for s in secs if s is not None]
    return min(secs) if secs else None


def build_shown(results, top, already_enriched, avoid_lowsec, round_trip, route_flag,
                session, station_cache, route_cache, system_cache):
    shown = []
    for r in results:
        if not already_enriched:
            if not enrich_locations([r], round_trip, route_flag,
                                    session, station_cache, route_cache):
                continue
        enrich_security([r], session, system_cache)
        if avoid_lowsec and sec_band(row_risk_sec(r)) != "high":
            continue
        shown.append(r)
        if len(shown) >= top:
            break
    return shown


def resolve_names(type_ids, session):
    names, ids = {}, list(type_ids)
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        r = session.post(f"{ESI}/universe/names/", json=chunk, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for entry in r.json():
            names[entry["id"]] = entry["name"]
    return names


def resolve_volume(type_id, cache, session):
    if type_id in cache:
        return cache[type_id]
    r = session.get(f"{ESI}/universe/types/{type_id}/", headers=HEADERS, timeout=30)
    vol = None
    if r.status_code == 200:
        data = r.json()
        vol = data.get("packaged_volume", data.get("volume"))
    cache[type_id] = vol
    return vol
