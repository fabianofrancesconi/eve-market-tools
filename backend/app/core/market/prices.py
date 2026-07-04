"""Market price fetching — Fuzzwork aggregates and ESI-direct live orders."""
import time
from pathlib import Path

from ..shared.constants import (
    ESI, FUZZWORK_AGG, HEADERS, JITA_STATION_ID, JITA_REGION_ID, USER_AGENT,
)
from ..shared.cache import default_cache_dir, load_json, save_json
from ..esi.client import check_esi_rate_limit

PRICE_CACHE_TTL = 300  # 5 minutes — matches ESI's cache header for market orders


def fetch_prices(type_ids, session, station_id=JITA_STATION_ID):
    """Best sell (min) / best buy (max) and depth at the given station per type_id,
    via Fuzzwork's station aggregate (batched). 0/missing prices -> None."""
    out = {}
    ids = sorted(set(type_ids))
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        r = session.get(FUZZWORK_AGG,
                        params={"station": station_id, "types": ",".join(map(str, chunk))},
                        headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        for tid_str, d in r.json().items():
            sell, buy = d.get("sell", {}), d.get("buy", {})
            out[int(tid_str)] = {
                "sell_min": float(sell.get("min") or 0) or None,
                "buy_max": float(buy.get("max") or 0) or None,
                "sell_volume": float(sell.get("volume") or 0),
                "buy_volume": float(buy.get("volume") or 0),
            }
    return out


def _esi_orders_for_type(type_id, session, station_id, region_id):
    """Fetch all orders for one type in a region from ESI, filter to station."""
    orders, page = [], 1
    while page <= 10:
        r = session.get(f"{ESI}/markets/{region_id}/orders/",
                        params={"type_id": type_id, "order_type": "all", "page": page},
                        headers=HEADERS, timeout=30)
        check_esi_rate_limit(r)
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        orders.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
    return [o for o in orders if o.get("location_id") == station_id]


def _summarise_orders(orders):
    """From a list of ESI orders at one station, compute sell_min, buy_max,
    sell_volume, buy_volume."""
    sells = [o for o in orders if not o["is_buy_order"]]
    buys = [o for o in orders if o["is_buy_order"]]
    sell_min = min((o["price"] for o in sells), default=0) or None
    buy_max = max((o["price"] for o in buys), default=0) or None
    sell_volume = sum(o["volume_remain"] for o in sells)
    buy_volume = sum(o["volume_remain"] for o in buys)
    return {
        "sell_min": sell_min,
        "buy_max": buy_max,
        "sell_volume": float(sell_volume),
        "buy_volume": float(buy_volume),
    }


def fetch_prices_esi(type_ids, session, station_id=JITA_STATION_ID,
                     region_id=JITA_REGION_ID, cache_dir=None, refresh=False,
                     emit=None):
    """Like fetch_prices() but queries ESI directly for live orders per type,
    with a 5-minute disk cache. More accurate than Fuzzwork aggregates (no lag,
    real order depth). Pass refresh=True to bypass the cache."""
    if cache_dir is None:
        cache_dir = default_cache_dir()
    cache_path = Path(cache_dir) / f"esi_prices_{station_id}.json"
    now = time.time()
    cached = load_json(cache_path, {})
    cache_valid = (now - cached.get("_ts", 0) < PRICE_CACHE_TTL)

    ids = sorted(set(type_ids))
    out = {}
    if not refresh and cache_valid:
        missing = []
        for tid in ids:
            entry = cached.get(str(tid))
            if entry:
                out[tid] = entry
            else:
                missing.append(tid)
        if not missing:
            return out
    else:
        missing = ids

    total = len(missing)
    for idx, tid in enumerate(missing):
        try:
            orders = _esi_orders_for_type(tid, session, station_id, region_id)
            out[tid] = _summarise_orders(orders)
        except Exception:
            out[tid] = {"sell_min": None, "buy_max": None,
                        "sell_volume": 0.0, "buy_volume": 0.0}
        if emit and idx % 20 == 0:
            emit(idx, total)

    # Merge into existing cache (don't overwrite unrelated types)
    for tid, v in out.items():
        cached[str(tid)] = v
    cached["_ts"] = now
    save_json(cache_path, cached)
    return out
