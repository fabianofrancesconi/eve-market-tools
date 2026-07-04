"""Location, route, and security enrichment for arbitrage results."""
from ..shared.constants import ESI, HEADERS, JITA_SYSTEM_ID
from ..shared.cache import load_json, save_json

_RISK_LABEL = {"high": "SAFE", "low": "LOWSEC", "null": "NULLSEC", "unknown": "?"}


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


def filter_from_jita(results, max_jumps):
    """Keep only rows where one leg is in Jita and jumps_total <= max_jumps."""
    return [r for r in results
            if (r.get("sell_system_id") == JITA_SYSTEM_ID or
                r.get("buy_system_id") == JITA_SYSTEM_ID)
            and r.get("jumps_total", 0) <= max_jumps]


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


def build_shown(results, top, already_enriched, avoid_lowsec, round_trip, route_flag,
                session, station_cache, route_cache, system_cache):
    shown = []
    for r in results:
        if not already_enriched:
            if not enrich_locations([r], round_trip, route_flag,
                                    session, station_cache, route_cache):
                continue
        enrich_security([r], session, system_cache)
        band = sec_band(row_risk_sec(r))
        if avoid_lowsec and band != "high":
            continue
        r["risk"] = _RISK_LABEL.get(band, "?")
        shown.append(r)
        if len(shown) >= top:
            break
    return shown
