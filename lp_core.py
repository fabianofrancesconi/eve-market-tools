#!/usr/bin/env python3
"""
Shared data layer for the EVE LP-store tools (CLI `lp-scanner.py` and the web
UI `lp-web.py`). Everything here is pure data/logic -- no printing, no curses,
no HTML -- so both front-ends compute identical numbers.

Pipeline: corp name -> corp_id -> LP offers (ESI) -> Jita IV-4 prices
(Fuzzwork) -> profit/ISK-per-LP evaluation, plus on-demand per-offer detail
(the full shopping list of required items and the m3 it occupies).
"""
import json
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ESI = "https://esi.evetech.net"
FUZZWORK_AGG = "https://market.fuzzwork.co.uk/aggregates/"
JITA_STATION_ID = 60003760  # Jita IV - Moon 4 - Caldari Navy Assembly Plant
JITA_REGION_ID = 10000002   # The Forge (region that contains Jita)
TRADE_HUBS = {
    60003760: {"name": "Jita 4-4",     "region_id": 10000002},
    60008494: {"name": "Amarr 8-20",   "region_id": 10000043},
    60004588: {"name": "Rens 6-8",     "region_id": 10000030},
    60011866: {"name": "Dodixie 9-20", "region_id": 10000032},
    60005686: {"name": "Hek 8-12",     "region_id": 10000042},
}
COMPAT_DATE = "2025-08-26"
USER_AGENT = "lp-store-scanner/1.0 (fabiano.francesconi@gmail.com)"
HEADERS = {
    "X-Compatibility-Date": COMPAT_DATE,
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}
# How long to trust a cached LP-store offer list (offers are near-static; prices
# are always fetched fresh).
OFFERS_TTL_SECONDS = 24 * 3600
# Ask/bid spread (%) at/above which an item is treated as illiquid: the sell
# price isn't backed by real buyers, so profit projected off it is unreliable.
HIGH_SPREAD_PCT = 25.0
# Market-saturation tuning. Daily traded volume (region history) is the real
# absorption rate; standing buy orders are just a snapshot.
HISTORY_DAYS = 30          # how many recent days of history feed the median
HISTORY_TTL_SECONDS = 12 * 3600   # reuse the price-chart cache window


def default_cache_dir():
    return Path(__file__).resolve().parent / ".eve_scanner_cache"


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


# --- ESI / market access ---------------------------------------------------
class LPError(Exception):
    """User-facing error (bad corp name, no LP store, etc.)."""


def resolve_corp_id(name, session):
    """NPC corporation name -> (corp_id, canonical_name). Raises LPError if the
    name doesn't match a corporation."""
    r = session.post(f"{ESI}/universe/ids/", json=[name], headers=HEADERS, timeout=30)
    r.raise_for_status()
    corps = r.json().get("corporations") or []
    if not corps:
        raise LPError(f"No corporation matched '{name}'. Check the exact NPC corp name "
                      f"(e.g. 'Serpentis Inquest', 'State Protectorate').")
    exact = [x for x in corps if x["name"].lower() == name.lower()]
    chosen = exact[0] if exact else corps[0]
    return chosen["id"], chosen["name"]


def resolve_corp_name(corp_id, session):
    """corporation_id -> name via /corporations/{id}/; falls back to 'corp <id>'."""
    try:
        r = session.get(f"{ESI}/corporations/{corp_id}/", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json().get("name") or f"corp {corp_id}"
    except requests.RequestException:
        pass
    return f"corp {corp_id}"


def get_offers(corp_id, session, cache_dir, refresh=False):
    """LP-store offers for a corporation, cached for OFFERS_TTL_SECONDS. Raises
    LPError if the corp has no LP store."""
    path = Path(cache_dir) / f"lpstore_{corp_id}.json"
    now = time.time()
    if not refresh:
        cached = load_json(path, None)
        if cached and now - cached.get("fetched_at", 0) < OFFERS_TTL_SECONDS:
            return cached["offers"]
    r = session.get(f"{ESI}/loyalty/stores/{corp_id}/offers/", headers=HEADERS, timeout=30)
    if r.status_code == 404:
        raise LPError(f"Corp {corp_id} has no LP store (ESI 404).")
    r.raise_for_status()
    offers = r.json()
    save_json(path, {"fetched_at": now, "offers": offers})
    return offers


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


# --- ESI-direct pricing (live orders, cached) --------------------------------
PRICE_CACHE_TTL = 300  # 5 minutes — matches ESI's cache header for market orders

def _esi_orders_for_type(type_id, session, station_id, region_id):
    """Fetch all orders for one type in a region from ESI, filter to station."""
    orders, page = [], 1
    while True:
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
    cached = {}
    if not refresh:
        cached = load_json(cache_path, {})
        ts = cached.get("_ts", 0)
        if now - ts < PRICE_CACHE_TTL:
            ids = sorted(set(type_ids))
            out = {}
            for tid in ids:
                entry = cached.get(str(tid))
                if entry:
                    out[tid] = entry
            missing = [tid for tid in ids if tid not in out]
            if not missing:
                return out
        else:
            cached = {}

    ids = sorted(set(type_ids))
    out = {}
    total = len(ids)
    for idx, tid in enumerate(ids):
        entry = cached.get(str(tid))
        if entry and not refresh:
            out[tid] = entry
            continue
        try:
            orders = _esi_orders_for_type(tid, session, station_id, region_id)
            out[tid] = _summarise_orders(orders)
        except Exception:
            out[tid] = {"sell_min": None, "buy_max": None,
                        "sell_volume": 0.0, "buy_volume": 0.0}
        if emit and idx % 20 == 0:
            emit(idx, total)

    to_save = {"_ts": now}
    for tid, v in out.items():
        to_save[str(tid)] = v
    save_json(cache_path, to_save)
    return out


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


def resolve_station_region(station_id, session, cache_dir):
    """NPC station_id -> region_id, persistently cached (it never changes).
    Player/Upwell structure ids (>= 1e9) need docking access we don't have, so
    those return None without a network call."""
    if station_id is None or station_id >= 1_000_000_000:
        return None
    path = Path(cache_dir) / "station_regions.json"
    cache = {int(k): v for k, v in load_json(path, {}).items()}
    if station_id in cache:
        return cache[station_id]
    try:
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
    except (requests.RequestException, KeyError, ValueError):
        return None
    cache[station_id] = region_id
    save_json(path, {str(k): v for k, v in cache.items()})
    return region_id


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


def resolve_names(type_ids, session, cache_dir):
    """type_id -> name via /universe/names/ (<=1000/call), persistently cached."""
    path = Path(cache_dir) / "lp_names.json"
    cache = {int(k): v for k, v in load_json(path, {}).items()}
    missing = [t for t in type_ids if t not in cache]
    for i in range(0, len(missing), 1000):
        chunk = missing[i:i + 1000]
        r = session.post(f"{ESI}/universe/names/", json=chunk, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for entry in r.json():
            cache[entry["id"]] = entry["name"]
    if missing:
        save_json(path, {str(k): v for k, v in cache.items()})
    return cache


def resolve_volumes(type_ids, session, cache_dir):
    """type_id -> packaged m3 (falls back to unpackaged volume), via
    /universe/types/{id}/ (no bulk endpoint). Persistently cached. None on
    failure. Resolve lazily (e.g. only the selected offer's items) -- one call
    per new type."""
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


def _median_daily_volume(history, days=HISTORY_DAYS):
    """Average daily volume over the last `days` CALENDAR days. ESI history
    omits days with zero trades, so we fill gaps with 0 to get the true daily
    rate.  Mean (not median) because median-with-zeros collapses to 0 for
    items that trade sporadically.  None when there's no usable history."""
    if not history:
        return None
    last_date = datetime.strptime(history[-1]["date"], "%Y-%m-%d")
    start_date = last_date - timedelta(days=days - 1)
    vol_by_date = {}
    for entry in history:
        d = datetime.strptime(entry["date"], "%Y-%m-%d")
        if d >= start_date:
            vol_by_date[d] = entry.get("volume") or 0
    total = sum(vol_by_date.values())
    if total == 0:
        return 0
    return total / days


def _median_daily_avg_price(history, days=HISTORY_DAYS):
    """Median of the last `days` daily *average* traded prices from an ESI
    history list. Median (not mean) so one fire-sale or gouge day doesn't skew
    the fair value. None when there's no usable history."""
    prices = [d.get("average") for d in history[-days:]]
    prices = [p for p in prices if p is not None]
    if not prices:
        return None
    return statistics.median(prices)


def _fetch_history_summary(type_ids, region_id, session, cache_dir, summarize,
                           refresh=False):
    """Shared per-type ESI market-history fetch + cache, reduced to one number
    per type by `summarize(history_list)`. One HTTP round-trip per uncached type
    -- the expensive call, so resolve it off the main scan path / in the
    background.

    Shares the `mhist_{region}_{type}.json` cache files the price-chart endpoint
    uses (same format, HISTORY_TTL_SECONDS window). Maps a type to None when it
    has no recorded history (the market never traded it) or on fetch failure."""
    out = {}
    now = time.time()
    for tid in sorted(set(type_ids)):
        path = Path(cache_dir) / f"mhist_{region_id}_{tid}.json"
        data = None
        if not refresh:
            cached = load_json(path, None)
            if cached and now - cached.get("_ts", 0) < HISTORY_TTL_SECONDS:
                data = cached["data"]
        if data is None:
            try:
                r = session.get(f"{ESI}/markets/{region_id}/history/",
                                params={"type_id": tid}, headers=HEADERS, timeout=20)
                if r.status_code != 200:
                    out[tid] = None
                    continue
                data = sorted(r.json(), key=lambda x: x["date"])
                save_json(path, {"_ts": now, "data": data})
            except requests.RequestException:
                out[tid] = None
                continue
        out[tid] = summarize(data)
    return out


def fetch_history_volumes(type_ids, region_id, session, cache_dir, refresh=False):
    """type_id -> median daily traded volume (last HISTORY_DAYS) in `region_id`,
    via ESI market history. None for a type with no recorded history or on
    fetch failure. See _fetch_history_summary for caching."""
    return _fetch_history_summary(type_ids, region_id, session, cache_dir,
                                  _median_daily_volume, refresh)


def fetch_history_prices(type_ids, region_id, session, cache_dir, refresh=False):
    """type_id -> median daily average traded price (last HISTORY_DAYS) in
    `region_id`, the "fair value" anchor for a suggested list price. None for a
    type with no recorded history or on fetch failure. Reuses the same cache
    files as fetch_history_volumes, so calling both costs no extra ESI calls."""
    return _fetch_history_summary(type_ids, region_id, session, cache_dir,
                                  _median_daily_avg_price, refresh)


def suggested_list_price(ask, fair):
    """Per-unit price to put on a sell order, anchored to history so a single
    lowball sell order doesn't drag the suggestion below fair value.

      ask  -- current lowest sell order at the hub (None if nothing is listed)
      fair -- 30-day median of the daily average traded price (None if no
              history); the fair-value anchor from fetch_history_prices.

    Returns the lowest current sell, UNLESS that sits below fair value (someone
    is dumping) -- then it holds at fair value rather than joining the race to
    the bottom. With only one signal available it returns that one; None when
    neither is."""
    if ask is None:
        return fair
    if fair is None:
        return ask
    return max(ask, fair)


def enrich_liquidity(sellable, daily_vols):
    """Annotate evaluate()'s sellable rows with the two raw market signals the
    Tradeability score blends, keyed by offer_id so a front-end can patch rows in
    place after the background history fetch.

      daily_vol      median units traded per day in the hub's region (or None) --
                     the LIQUIDITY signal: high = you can sell at your price.
      days_to_clear  units currently listed on sell orders / daily_vol -- the
                     COMPETITION signal: how long the supply ALREADY on the market
                     takes to absorb. None when there's no history; None when
                     daily_vol is 0 (the market never trades it, so it effectively
                     never clears -- the caller distinguishes the two via daily_vol).

    Both are raw counts -- no invented constant. The score that blends them (in a
    user-chosen proportion) is computed client-side."""
    out = {}
    for r in sellable:
        dv = daily_vols.get(r["name_id"])
        sell_vol = r.get("sell_volume") or 0
        days = (sell_vol / dv) if (dv and dv > 0) else None
        out[r["offer_id"]] = {"daily_vol": dv, "days_to_clear": days}
    return out


# --- evaluation ------------------------------------------------------------
def _spread_pct(sell_min, buy_max):
    if sell_min and buy_max:
        return (sell_min - buy_max) / sell_min * 100.0
    if sell_min and not buy_max:
        return 100.0  # asks exist, zero bids -> nobody is buying
    return None


def _best(*vals):
    """Highest of the supplied values, ignoring None. None if all are None."""
    present = [v for v in vals if v is not None]
    return max(present) if present else None


def evaluate(offers, prices, lp_budget, sales_tax, broker_fee):
    """Annotate offers with profit / ISK-per-LP and budget projections for BOTH
    sell modes at once:

      patient  — list a sell order at the ask, pay sales tax + broker fee.
      instant  — dump into a buy order at the bid, pay sales tax only.

    Every sellable row carries the *_patient / *_instant pair plus a *_best
    convenience field (the better of the two, ignoring an unpriced mode) so a
    front-end can compare them side by side. An offer is unsellable only when
    BOTH the ask and the bid are missing. Returns
    (sellable_sorted_by_isk_per_lp_best, unsellable); each sellable row also
    carries offer_id + required_items so a detail view can be built later."""
    rows = []
    patient_factor = 1 - sales_tax - broker_fee
    instant_factor = 1 - sales_tax
    for o in offers:
        lp_cost = o.get("lp_cost") or 0
        if lp_cost <= 0:
            continue
        qty = o.get("quantity", 1)
        out_tid = o["type_id"]
        p = prices.get(out_tid, {})
        ask = p.get("sell_min")
        bid = p.get("buy_max")
        if not ask and not bid:
            rows.append({"name_id": out_tid, "lp_cost": lp_cost, "qty": qty,
                         "offer_id": o.get("offer_id"), "unsellable": True})
            continue

        req_cost, req_missing = 0.0, False
        for req in o.get("required_items", []):
            rp = prices.get(req["type_id"], {}).get("sell_min")
            if not rp:
                req_missing = True
                continue
            req_cost += req["quantity"] * rp
        isk_cost = o.get("isk_cost") or 0
        base_cost = isk_cost + req_cost

        rev_patient = (qty * ask * patient_factor) if ask else None
        rev_instant = (qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - base_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - base_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
        ipl_patient = (profit_patient / lp_cost) if profit_patient is not None else None
        ipl_instant = (profit_instant / lp_cost) if profit_instant is not None else None
        ipl_best = _best(ipl_patient, ipl_instant)
        max_units = math.floor(lp_budget / lp_cost) if lp_budget else 0
        rows.append({
            "offer_id": o.get("offer_id"),
            "name_id": out_tid,
            "qty": qty,
            "lp_cost": lp_cost,
            "isk_cost": isk_cost,
            "req_cost": req_cost,
            "req_missing": req_missing,
            "ak_cost": o.get("ak_cost") or 0,
            "required_items": o.get("required_items", []),
            "ask": ask,
            "bid": bid,
            "spread_pct": _spread_pct(ask, bid),
            "buy_volume": p.get("buy_volume", 0),
            "sell_volume": p.get("sell_volume", 0),
            "profit_patient": profit_patient,
            "profit_instant": profit_instant,
            "profit_best": profit_best,
            "isk_per_lp_patient": ipl_patient,
            "isk_per_lp_instant": ipl_instant,
            "isk_per_lp_best": ipl_best,
            "max_units": max_units,
            "total_profit_patient": None if profit_patient is None else profit_patient * max_units,
            "total_profit_instant": None if profit_instant is None else profit_instant * max_units,
            "total_profit_best": None if profit_best is None else profit_best * max_units,
            "unsellable": False,
        })
    sellable = [r for r in rows if not r["unsellable"]]
    sellable.sort(key=lambda r: (r["isk_per_lp_best"] if r["isk_per_lp_best"] is not None
                                 else float("-inf")), reverse=True)
    return sellable, [r for r in rows if r["unsellable"]]


def build_detail(offer, prices, names, volumes, lp_budget, sales_tax, broker_fee):
    """Full per-offer breakdown for the detail view: the shopping list of
    required inputs (qty, Jita unit price, line cost, m3) and the output, all
    per single redemption, plus the max redemptions the LP budget allows and
    the m3 each leg of the haul occupies.

    Money figures are reported for BOTH sell modes (patient = list at ask, pay
    sales tax + broker fee; instant = dump into a buy order, pay sales tax only)
    so the front-end can show them side by side. All money/volume figures are
    PER REDEMPTION so a front-end can multiply by a user-chosen number of
    redemptions without another round-trip."""
    out_tid = offer["type_id"]
    qty = offer.get("quantity", 1)
    lp_cost = offer.get("lp_cost") or 0
    isk_fee = offer.get("isk_cost") or 0
    p = prices.get(out_tid, {})
    ask = p.get("sell_min")
    bid = p.get("buy_max")
    out_vol_each = volumes.get(out_tid)

    required = []
    req_cost = 0.0
    req_vol_each_total = 0.0
    req_missing_price = False
    for req in offer.get("required_items", []):
        tid, q = req["type_id"], req["quantity"]
        price = prices.get(tid, {}).get("sell_min")
        vol_each = volumes.get(tid)
        line_cost = (q * price) if price else None
        if line_cost is None:
            req_missing_price = True
        else:
            req_cost += line_cost
        line_vol = (q * vol_each) if vol_each is not None else None
        if line_vol is not None:
            req_vol_each_total += line_vol
        required.append({
            "type_id": tid,
            "name": names.get(tid, str(tid)),
            "quantity": q,
            "unit_price": price,
            "line_cost": line_cost,
            "volume_each": vol_each,
            "line_volume": line_vol,
        })

    total_cost = isk_fee + req_cost
    rev_patient = (qty * ask * (1 - sales_tax - broker_fee)) if ask else None
    rev_instant = (qty * bid * (1 - sales_tax)) if bid else None
    profit_patient = (rev_patient - total_cost) if rev_patient is not None else None
    profit_instant = (rev_instant - total_cost) if rev_instant is not None else None
    ipl_patient = (profit_patient / lp_cost) if (profit_patient is not None and lp_cost) else None
    ipl_instant = (profit_instant / lp_cost) if (profit_instant is not None and lp_cost) else None
    max_units = math.floor(lp_budget / lp_cost) if (lp_budget and lp_cost) else 0
    out_vol_per_redemption = (out_vol_each * qty) if out_vol_each is not None else None

    return {
        "offer_id": offer.get("offer_id"),
        "output": {
            "type_id": out_tid,
            "name": names.get(out_tid, str(out_tid)),
            "quantity": qty,
            "volume_each": out_vol_each,
            "volume_per_redemption": out_vol_per_redemption,
        },
        "required_items": required,
        "ask": ask,
        "bid": bid,
        "spread_pct": _spread_pct(ask, bid),
        "buy_volume": p.get("buy_volume", 0),
        "sell_volume": p.get("sell_volume", 0),
        # per single redemption — both sell modes:
        "lp_cost": lp_cost,
        "isk_fee": isk_fee,
        "req_cost": req_cost,
        "req_missing_price": req_missing_price,
        "total_cost": total_cost,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": profit_patient,
        "profit_instant": profit_instant,
        "profit_best": _best(profit_patient, profit_instant),
        "isk_per_lp_patient": ipl_patient,
        "isk_per_lp_instant": ipl_instant,
        "isk_per_lp_best": _best(ipl_patient, ipl_instant),
        "input_volume_per_redemption": req_vol_each_total,   # m3 you haul TO the LP corp
        "output_volume_per_redemption": out_vol_per_redemption,  # m3 you haul to Jita to sell
        # budget projection:
        "max_units": max_units,
    }
