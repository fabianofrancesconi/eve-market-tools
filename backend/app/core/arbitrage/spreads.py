"""Spread detection, order fetching, and flip calculation."""
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from ..shared.constants import ESI, HEADERS
from ..shared.cache import load_json, save_json
from ..esi.client import check_esi_rate_limit


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


def fetch_type_orders(region_id, type_id, session):
    """All current orders for one type in the region from ESI (not cached)."""
    orders, page = [], 1
    while page <= 5:
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
    return orders


def get_orders(region_id, session, cache_dir, refresh, progress_cb=None):
    """Fetch or revalidate the region order book."""
    def _cb(stage, **kw):
        if progress_cb:
            progress_cb(stage, **kw)

    path = Path(cache_dir) / f"orders_region_{region_id}.json"
    cached = _normalize_cache(load_json(path, None), path)
    now = time.time()
    if not refresh and cached and cached.get("orders"):
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
