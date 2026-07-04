"""Live order-book queries — station orders, aggregated books, floor stats."""
import time
from datetime import datetime, timezone
from pathlib import Path

from ..shared.constants import ESI, HEADERS, JITA_STATION_ID, JITA_REGION_ID
from ..esi.client import check_esi_rate_limit


def _fetch_station_orders(type_id, side, session, station_id, region_id):
    """Raw live orders for one type at one station (paged), filtered to
    station_id and sorted in consumption order: sells cheapest-first, buys
    highest-first. Each order keeps its full ESI fields (price, volume_remain,
    issued, duration, ...). Not cached -- the book moves constantly."""
    orders, page = [], 1
    while page <= 5:  # a single type at one station is almost always 1 page
        r = session.get(f"{ESI}/markets/{region_id}/orders/",
                        params={"type_id": type_id, "order_type": side, "page": page},
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
    at_station = [o for o in orders if o.get("location_id") == station_id]
    at_station.sort(key=lambda o: o["price"], reverse=(side == "buy"))
    return at_station


def fetch_orderbook_jita(type_id, side, session,
                         station_id=JITA_STATION_ID, region_id=JITA_REGION_ID,
                         max_levels=200):
    """Live order book for one type at the given station, as aggregated price
    levels so a caller can walk it to get the true cost/revenue of a multi-unit
    fill (the cheapest seller rarely stocks everything you need).

    side: "sell" (asks, cheapest first) or "buy" (bids, highest first).
    Returns [[price, volume], ...] filtered to station_id, sorted in the order
    you'd consume it. Not cached (the book moves constantly)."""
    levels = {}
    for o in _fetch_station_orders(type_id, side, session, station_id, region_id):
        levels[o["price"]] = levels.get(o["price"], 0) + o["volume_remain"]
    book = [[p, v] for p, v in levels.items()]
    book.sort(key=lambda x: x[0], reverse=(side == "buy"))
    return book[:max_levels]


def _issued_age_seconds(issued, now):
    """ESI 'issued' ISO-8601 stamp (e.g. '2016-09-03T05:12:25Z') -> age in
    seconds relative to `now` (epoch). None if missing/unparseable."""
    if not issued:
        return None
    try:
        dt = datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return now - dt.timestamp()


def fetch_sell_order_stats(type_id, session, station_id=JITA_STATION_ID,
                           region_id=JITA_REGION_ID, now=None):
    """Freshness of the sell-side floor for `type_id` at `station_id`.

    Market orders carry an 'issued' timestamp (and a 'duration' in days), so we
    can tell how recently the current cheapest price was listed -- a floor
    placed hours ago in a thin market means the price is actively moving, which
    is hard to see from the aggregate min alone. Returns:

      best_price          lowest sell price at the station
      issued / age_seconds the OLDEST order sitting at best_price and how long
                           ago it was listed (how long this floor has stood, at
                           minimum -- later ties undercut to the same price)
      duration_days       that order's listed duration (90 for a max order)
      orders_at_best      how many distinct orders share the floor price
      sell_orders_total   total sell orders at the station (thin vs deep)

    None when nothing is listed for the type at the station."""
    orders = _fetch_station_orders(type_id, "sell", session, station_id, region_id)
    if not orders:
        return None
    now = time.time() if now is None else now
    best_price = orders[0]["price"]  # sorted cheapest-first
    at_floor = [o for o in orders if o["price"] == best_price]
    oldest = min(at_floor, key=lambda o: o.get("issued") or "")
    return {
        "best_price": best_price,
        "issued": oldest.get("issued"),
        "age_seconds": _issued_age_seconds(oldest.get("issued"), now),
        "duration_days": oldest.get("duration"),
        "orders_at_best": len(at_floor),
        "sell_orders_total": len(orders),
    }


def fetch_order_rank(type_id, side, my_order_id, session, station_id, region_id):
    """1-based queue position of `my_order_id` among open `side` orders for
    `type_id` at `station_id` -- price first, then issued time (EVE's real
    tiebreak for same-price orders: whoever listed first matches first).
    None if the order isn't in this book (filled, cancelled, or a structure
    we can't see into)."""
    orders = _fetch_station_orders(type_id, side, session, station_id, region_id)
    orders.sort(key=lambda o: o.get("issued") or "")              # stable tiebreak
    orders.sort(key=lambda o: o["price"], reverse=(side == "buy"))  # stable: keeps tiebreak
    for i, o in enumerate(orders):
        if o.get("order_id") == my_order_id:
            return {"rank": i + 1, "total": len(orders), "is_best": i == 0,
                    "best_price": orders[0]["price"]}
    return None
