"""Name and ID resolution via ESI."""
from pathlib import Path

from .cache import load_json, save_json
from .constants import ESI, HEADERS


def resolve_names(type_ids, session, cache_dir):
    """type_id -> name via /universe/names/ (<=1000/call), persistently cached."""
    path = Path(cache_dir) / "lp_names.json"
    cache = {int(k): v for k, v in load_json(path, {}).items()}
    missing = [t for t in type_ids if t and t > 0 and t not in cache]
    for i in range(0, len(missing), 1000):
        chunk = missing[i:i + 1000]
        try:
            r = session.post(f"{ESI}/universe/names/", json=chunk, headers=HEADERS, timeout=30)
            r.raise_for_status()
            for entry in r.json():
                cache[entry["id"]] = entry["name"]
        except Exception:
            pass
    if missing:
        save_json(path, {str(k): v for k, v in cache.items()})
    return cache


def resolve_volumes(type_ids, session, cache_dir):
    """type_id -> packaged m3, via /universe/types/{id}/. Persistently cached."""
    path = Path(cache_dir) / "lp_volumes.json"
    cache = {int(k): v for k, v in load_json(path, {}).items()}
    changed = False
    for t in type_ids:
        if t in cache:
            continue
        r = session.get(f"{ESI}/universe/types/{t}/", headers=HEADERS, timeout=30)
        cache[t] = (r.json().get("packaged_volume", r.json().get("volume"))
                    if r.status_code == 200 else None)
        changed = True
    if changed:
        save_json(path, {str(k): v for k, v in cache.items()})
    return cache


def resolve_station_region(station_id, session, cache_dir):
    """NPC station_id -> region_id, persistently cached.
    Player/Upwell structure ids (>= 1e9) return None without a network call."""
    if station_id is None or station_id >= 1_000_000_000:
        return None
    path = Path(cache_dir) / "station_regions.json"
    cache = {int(k): v for k, v in load_json(path, {}).items()}
    if station_id in cache:
        return cache[station_id]
    try:
        import requests
        r = session.get(f"{ESI}/universe/stations/{station_id}/", headers=HEADERS, timeout=15)
        r.raise_for_status()
        system_id = r.json()["system_id"]
        r2 = session.get(f"{ESI}/universe/systems/{system_id}/", headers=HEADERS, timeout=15)
        r2.raise_for_status()
        constellation_id = r2.json()["constellation_id"]
        r3 = session.get(f"{ESI}/universe/constellations/{constellation_id}/",
                         headers=HEADERS, timeout=15)
        r3.raise_for_status()
        region_id = r3.json()["region_id"]
    except (Exception, KeyError, ValueError):
        return None
    cache[station_id] = region_id
    save_json(path, {str(k): v for k, v in cache.items()})
    return region_id
