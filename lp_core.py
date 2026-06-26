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
# Fraction of one day's traded volume you can realistically offload before your
# own selling starts to move the price. The "capped" profit only counts the
# redemptions whose output fits inside this slice -- profit you can likely keep
# even if everyone else is dumping the same LP offer.
ABSORB_FRACTION = 0.10


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


def fetch_orderbook_jita(type_id, side, session,
                         station_id=JITA_STATION_ID, region_id=JITA_REGION_ID,
                         max_levels=200):
    """Live order book for one type at the given station, as aggregated price
    levels so a caller can walk it to get the true cost/revenue of a multi-unit
    fill (the cheapest seller rarely stocks everything you need).

    side: "sell" (asks, cheapest first) or "buy" (bids, highest first).
    Returns [[price, volume], ...] filtered to station_id, sorted in the order
    you'd consume it. Not cached (the book moves constantly)."""
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
    levels = {}
    for o in orders:
        if o.get("location_id") != station_id:
            continue
        levels[o["price"]] = levels.get(o["price"], 0) + o["volume_remain"]
    book = [[p, v] for p, v in levels.items()]
    book.sort(key=lambda x: x[0], reverse=(side == "buy"))
    return book[:max_levels]


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
    """Median of the last `days` daily traded volumes from an ESI history list.
    Median (not mean) so a single whale day doesn't inflate the rate. None when
    there's no usable history."""
    vols = [d.get("volume") for d in history[-days:]]
    vols = [v for v in vols if v is not None]
    if not vols:
        return None
    return statistics.median(vols)


def fetch_history_volumes(type_ids, region_id, session, cache_dir, refresh=False):
    """type_id -> median daily traded volume (last HISTORY_DAYS) in `region_id`,
    via ESI market history. One HTTP round-trip per uncached type -- this is the
    expensive call, so resolve it off the main scan path / in the background.

    Shares the `mhist_{region}_{type}.json` cache files the price-chart endpoint
    uses (same format, HISTORY_TTL_SECONDS window). None for a type with no
    recorded history (the market never traded it) or on fetch failure."""
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
        out[tid] = _median_daily_volume(data)
    return out


def enrich_liquidity(sellable, daily_vols, absorb_fraction=ABSORB_FRACTION):
    """Annotate evaluate()'s sellable rows with market-saturation figures, keyed
    by offer_id so a front-end can patch rows in place after a background fetch.

      daily_vol      median units traded per day in the hub's region (or None).
      days_to_clear  units currently listed on sell orders / daily_vol -- how long
                     the competing supply ALREADY on the market takes to absorb.
                     None when there's no history; None when daily_vol is 0 (the
                     market never trades it, so it effectively never clears -- the
                     caller distinguishes the two via daily_vol).
      capped_units   redemptions whose output fits inside `absorb_fraction` of one
                     day's volume -- what you can offload before moving the price.
      capped_profit  profit_per * capped_units -- the crowding-robust profit. The
                     gap to total_profit is how much the tide can wash away.
    """
    out = {}
    for r in sellable:
        tid = r["name_id"]
        dv = daily_vols.get(tid)
        sell_vol = r.get("sell_volume") or 0
        qty = r.get("qty", 1) or 1
        if dv and dv > 0:
            days = sell_vol / dv
            capped_units = min(r.get("max_units", 0),
                               math.floor(absorb_fraction * dv / qty))
        else:
            days = None
            capped_units = 0
        out[r["offer_id"]] = {
            "daily_vol": dv,
            "days_to_clear": days,
            "capped_units": capped_units,
            "capped_profit": r.get("profit_per", 0) * capped_units,
        }
    return out


# --- evaluation ------------------------------------------------------------
def _spread_pct(sell_min, buy_max):
    if sell_min and buy_max:
        return (sell_min - buy_max) / sell_min * 100.0
    if sell_min and not buy_max:
        return 100.0  # asks exist, zero bids -> nobody is buying
    return None


def evaluate(offers, prices, lp_budget, sales_tax, broker_fee, instant):
    """Annotate offers with profit / ISK-per-LP and budget projections.
    Returns (sellable_sorted_by_isk_per_lp, unsellable). Each sellable row also
    carries offer_id + required_items so a detail view can be built later."""
    rows = []
    for o in offers:
        lp_cost = o.get("lp_cost") or 0
        if lp_cost <= 0:
            continue
        qty = o.get("quantity", 1)
        out_tid = o["type_id"]
        p = prices.get(out_tid, {})
        unit_price = p.get("buy_max") if instant else p.get("sell_min")
        if not unit_price:
            rows.append({"name_id": out_tid, "lp_cost": lp_cost, "qty": qty,
                         "offer_id": o.get("offer_id"), "unsellable": True})
            continue

        fee_factor = (1 - sales_tax) if instant else (1 - sales_tax - broker_fee)
        revenue = qty * unit_price * fee_factor

        req_cost, req_missing = 0.0, False
        for req in o.get("required_items", []):
            rp = prices.get(req["type_id"], {}).get("sell_min")
            if not rp:
                req_missing = True
                continue
            req_cost += req["quantity"] * rp
        isk_cost = o.get("isk_cost") or 0

        profit = revenue - isk_cost - req_cost
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
            "unit_price": unit_price,
            "ask": p.get("sell_min"),
            "bid": p.get("buy_max"),
            "spread_pct": _spread_pct(p.get("sell_min"), p.get("buy_max")),
            "buy_volume": p["buy_volume"],
            "sell_volume": p["sell_volume"],
            "profit_per": profit,
            "isk_per_lp": profit / lp_cost,
            "max_units": max_units,
            "total_profit": profit * max_units,
            "total_isk_in": max_units * (isk_cost + req_cost),
            "lp_used": max_units * lp_cost,
            "unsellable": False,
        })
    sellable = [r for r in rows if not r["unsellable"]]
    sellable.sort(key=lambda r: r["isk_per_lp"], reverse=True)
    return sellable, [r for r in rows if r["unsellable"]]


def build_detail(offer, prices, names, volumes, lp_budget, sales_tax, broker_fee, instant):
    """Full per-offer breakdown for the detail view: the shopping list of
    required inputs (qty, Jita unit price, line cost, m3) and the output, all
    per single redemption, plus the max redemptions the LP budget allows and
    the m3 each leg of the haul occupies.

    All money/volume figures are PER REDEMPTION so a front-end can multiply by a
    user-chosen number of redemptions without another round-trip."""
    out_tid = offer["type_id"]
    qty = offer.get("quantity", 1)
    lp_cost = offer.get("lp_cost") or 0
    isk_fee = offer.get("isk_cost") or 0
    p = prices.get(out_tid, {})
    unit_price = p.get("buy_max") if instant else p.get("sell_min")
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

    fee_factor = (1 - sales_tax) if instant else (1 - sales_tax - broker_fee)
    revenue_net = (qty * unit_price * fee_factor) if unit_price else None
    total_cost = isk_fee + req_cost
    profit = (revenue_net - total_cost) if revenue_net is not None else None
    isk_per_lp = (profit / lp_cost) if (profit is not None and lp_cost) else None
    max_units = math.floor(lp_budget / lp_cost) if (lp_budget and lp_cost) else 0
    out_vol_per_redemption = (out_vol_each * qty) if out_vol_each is not None else None

    return {
        "offer_id": offer.get("offer_id"),
        "output": {
            "type_id": out_tid,
            "name": names.get(out_tid, str(out_tid)),
            "quantity": qty,
            "unit_price": unit_price,
            "volume_each": out_vol_each,
            "volume_per_redemption": out_vol_per_redemption,
        },
        "required_items": required,
        "ask": p.get("sell_min"),
        "bid": p.get("buy_max"),
        "spread_pct": _spread_pct(p.get("sell_min"), p.get("buy_max")),
        "buy_volume": p.get("buy_volume", 0),
        "sell_volume": p.get("sell_volume", 0),
        "instant": instant,
        # per single redemption:
        "lp_cost": lp_cost,
        "isk_fee": isk_fee,
        "req_cost": req_cost,
        "req_missing_price": req_missing_price,
        "total_cost": total_cost,
        "revenue_net": revenue_net,
        "profit": profit,
        "isk_per_lp": isk_per_lp,
        "input_volume_per_redemption": req_vol_each_total,   # m3 you haul TO the LP corp
        "output_volume_per_redemption": out_vol_per_redemption,  # m3 you haul to Jita to sell
        # budget projection:
        "max_units": max_units,
    }
