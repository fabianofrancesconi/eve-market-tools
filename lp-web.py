#!/usr/bin/env python3
"""
EVE Market Tools — unified web UI.

Three apps in one local server:
  • LP Store  — ranks LP-store offers by ISK/LP with drill-down shopping lists.
  • Arbitrage — scans a region for negative-spread (instant-flip) opportunities.
  • Industry  — ranks manufacturable items (T1 + T2 invention) by ISK/hour after
                material, job-install and blueprint cost, from a local SDE copy.

    pip install requests
    python lp-web.py            # opens http://localhost:8765
    python lp-web.py --port 9000 --no-browser
"""
__version__ = "1.20.1"

import argparse
import base64
import json
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="4" fill="#080d11"/>'
    b'<rect x="3" y="21" width="7" height="8" rx="1" fill="#4fc3f7"/>'
    b'<rect x="12.5" y="15" width="7" height="14" rx="1" fill="#4fc3f7"/>'
    b'<rect x="22" y="8" width="7" height="21" rx="1" fill="#c8a040"/>'
    b'<polyline points="6.5,19 16,13 25.5,6" stroke="#4caf76"'
    b' stroke-width="2.5" fill="none" stroke-linecap="round"'
    b' stroke-linejoin="round"/>'
    b'</svg>'
)
_FAVICON_B64 = base64.b64encode(_FAVICON_SVG).decode()

import requests

import arb_core
import ind_core
from lp_core import (
    ESI, HEADERS, HIGH_SPREAD_PCT, JITA_STATION_ID, LPError, build_detail, default_cache_dir,
    TRADE_HUBS, enrich_liquidity, evaluate, fetch_history_prices, fetch_history_volumes,
    fetch_orderbook_jita, fetch_prices, fetch_sell_order_stats, get_offers, load_json,
    resolve_corp_id, resolve_corp_name, resolve_names, resolve_volumes, save_json,
    suggested_list_price,
)

SESSION = requests.Session()
CACHE_DIR = default_cache_dir()
SETTINGS_PATH = CACHE_DIR / "lp_web_settings.json"
ARB_SETTINGS_PATH = CACHE_DIR / "arb_settings.json"
IND_SETTINGS_PATH = CACHE_DIR / "ind_settings.json"
REFRESHED_CORPS = set()

REGION_NAMES = {
    10000002: "The Forge (Jita)",
    10000043: "Domain (Amarr)",
    10000032: "Sinq Laison (Dodixie)",
    10000042: "Metropolis (Hek)",
    10000030: "Heimatar (Rens)",
}

# Arb lookup caches — loaded lazily from disk on first arb scan, updated in-memory.
_ARB_STATION_CACHE: dict = {}
_ARB_VOLUME_CACHE: dict = {}
_ARB_SYSTEM_CACHE: dict = {}
_ARB_ROUTE_CACHE: dict = {}
_ARB_CACHES_LOADED = False


def _ensure_arb_caches():
    global _ARB_STATION_CACHE, _ARB_VOLUME_CACHE, _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE, _ARB_CACHES_LOADED
    if not _ARB_CACHES_LOADED:
        _ARB_STATION_CACHE, _ARB_VOLUME_CACHE, _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE = \
            arb_core.load_lookup_cache(CACHE_DIR)
        _ARB_CACHES_LOADED = True


# ── LP scanner helpers ──────────────────────────────────────────────────────

def load_settings():
    return load_json(SETTINGS_PATH, {})


def save_settings(d):
    save_json(SETTINGS_PATH, d)


def load_arb_settings():
    return load_json(ARB_SETTINGS_PATH, {})


def save_arb_settings(d):
    save_json(ARB_SETTINGS_PATH, d)


def load_ind_settings():
    return load_json(IND_SETTINGS_PATH, {})


def save_ind_settings(d):
    save_json(IND_SETTINGS_PATH, d)


def _all_type_ids(offers):
    ids = set()
    for o in offers:
        ids.add(o["type_id"])
        for req in o.get("required_items", []):
            ids.add(req["type_id"])
    return ids


def do_scan(q):
    corp_arg = (q.get("corp", [""])[0] or "").strip()
    corp_id_arg = q.get("corp_id", [""])[0].strip()
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    max_spread = q.get("max_spread", [""])[0].strip()
    max_spread = float(max_spread) if max_spread else None
    min_profit = q.get("min_profit", [""])[0].strip()
    min_profit = float(min_profit) if min_profit else None

    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID

    s = load_settings()
    s.update({
        "corp": corp_arg,
        "lp": str(int(lp)),
        "max_spread": str(max_spread) if max_spread is not None else "",
        "tax": str(tax),
        "broker": str(broker),
        "station": str(station_id),
    })
    save_settings(s)

    if corp_id_arg:
        corp_id = int(corp_id_arg)
        corp_name = resolve_corp_name(corp_id, SESSION)
    elif corp_arg:
        corp_id, corp_name = resolve_corp_id(corp_arg, SESSION)
    else:
        raise LPError("Enter a corporation name (or id).")

    force = q.get("refresh", ["0"])[0] in ("1", "true", "on")
    fresh = force or corp_id not in REFRESHED_CORPS
    if fresh:
        reason = "forced by user" if force else "first scan this session"
        print(f"[LP] Refreshing offers for {corp_name} ({reason})", file=sys.stderr)
    offers = get_offers(corp_id, SESSION, CACHE_DIR, refresh=fresh)
    REFRESHED_CORPS.add(corp_id)
    offers_meta = load_json(CACHE_DIR / f"lpstore_{corp_id}.json", {})
    prices = fetch_prices(_all_type_ids(offers), SESSION, station_id=station_id)
    sellable, unsellable = evaluate(offers, prices, lp, tax, broker)
    if min_profit is not None:
        sellable = [r for r in sellable
                    if r["profit_best"] is not None and r["profit_best"] >= min_profit]
    if max_spread is not None:
        sellable = [r for r in sellable
                    if r["spread_pct"] is not None and r["spread_pct"] <= max_spread]

    names = resolve_names(_all_type_ids(offers), SESSION, CACHE_DIR)
    volumes = resolve_volumes({r["name_id"] for r in sellable}, SESSION, CACHE_DIR)
    rows = []
    for r in sellable:
        sp = r["spread_pct"]
        _vol = volumes.get(r["name_id"])
        rows.append({
            "offer_id": r["offer_id"],
            "name": names.get(r["name_id"], str(r["name_id"])),
            "qty": r["qty"],
            "lp_cost": r["lp_cost"],
            "cost_ea": r["isk_cost"] + r["req_cost"],
            "ask": r["ask"],
            "bid": r["bid"],
            "spread_pct": sp,
            "isk_per_lp_patient": r["isk_per_lp_patient"],
            "isk_per_lp_instant": r["isk_per_lp_instant"],
            "isk_per_lp_best": r["isk_per_lp_best"],
            "max_units": r["max_units"],
            "total_profit_patient": r["total_profit_patient"],
            "total_profit_instant": r["total_profit_instant"],
            "total_profit_best": r["total_profit_best"],
            "buy_volume": r["buy_volume"],
            "output_volume": None if _vol is None else _vol * r["qty"],
            "req_missing": r["req_missing"],
            "ak_cost": r["ak_cost"],
            "illiquid": sp is None or sp >= HIGH_SPREAD_PCT,
            # Market-saturation signals are filled in lazily by /api/liquidity
            # (one history call per type) so the initial scan stays fast.
            # liq_loaded flips true once the fill lands; tradeability is the
            # client-computed blend of daily_vol + days_to_clear.
            "type_id": r["name_id"],
            "sell_volume": r.get("sell_volume"),
            "daily_vol": None,
            "days_to_clear": None,
            "tradeability": None,
            # Suggested per-unit sell-order price; filled by /api/liquidity
            # alongside the saturation signals (needs market history).
            "list_price": None,
            # Age of the current cheapest sell order at the hub (seconds);
            # also filled by /api/liquidity (one live order-book call).
            "floor_age": None,
            "liq_loaded": False,
        })
    return {
        "corp_id": corp_id,
        "corp_name": corp_name,
        "lp": lp,
        "tax": tax,
        "broker": broker,
        "station_id": station_id,
        "station_name": TRADE_HUBS[station_id]["name"],
        "high_spread_pct": HIGH_SPREAD_PCT,
        "count": len(rows),
        "unsellable": len(unsellable),
        "rows": rows,
        "scanned_at": time.time(),
        "offers_fetched_at": offers_meta.get("fetched_at"),
    }


def do_liquidity(q):
    """Background fill for the market-saturation columns. Recomputes the same
    sellable rows as /api/scan (so capped figures use the identical LP budget /
    fees), fetches daily traded volume per reward type from region history, and
    returns {offer_id: {daily_vol, days_to_clear, capped_units, capped_profit}}.

    Split out from the scan because it costs one history call per type -- the
    front end fires it after the table is already on screen and patches rows in
    place as the answer arrives."""
    corp_id = int(q["corp_id"][0])
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]

    offers = get_offers(corp_id, SESSION, CACHE_DIR, refresh=False)
    prices = fetch_prices(_all_type_ids(offers), SESSION, station_id=station_id)
    sellable, _ = evaluate(offers, prices, lp, tax, broker)
    reward_ids = {r["name_id"] for r in sellable}
    daily_vols = fetch_history_volumes(reward_ids, region_id, SESSION, CACHE_DIR)
    # Fair-value anchor for the suggested list price -- reuses the same cached
    # history files the volume fetch just wrote, so no extra ESI round-trips.
    fair_prices = fetch_history_prices(reward_ids, region_id, SESSION, CACHE_DIR)
    liq = enrich_liquidity(sellable, daily_vols)
    # Freshness of the current cheapest sell order, deduped per reward type
    # (one live order-book call each -- order books aren't cacheable, so this
    # is the slow part of the fill).
    floor_age_by_type = {}
    for r in sellable:
        tid = r["name_id"]
        if tid not in floor_age_by_type:
            stats = fetch_sell_order_stats(tid, SESSION, station_id=station_id,
                                           region_id=region_id)
            floor_age_by_type[tid] = stats["age_seconds"] if stats else None
        liq[r["offer_id"]]["list_price"] = suggested_list_price(
            r.get("ask"), fair_prices.get(tid))
        liq[r["offer_id"]]["floor_age"] = floor_age_by_type[tid]
    return {"liquidity": liq}


def _resolve_corp_names(ids):
    """POST ids to /universe/names/ → list of corporation entries.

    ESI returns 404 for the *entire* batch if even one id is unresolvable
    (some ids from /npccorps/ are stale). Binary-split on failure so a single
    bad id only drops itself instead of poisoning the whole batch.
    """
    if not ids:
        return []
    nr = SESSION.post(f"{ESI}/universe/names/", json=ids, headers=HEADERS, timeout=30)
    if nr.status_code == 200:
        body = nr.json()
        if isinstance(body, list):
            return [{"id": e["id"], "name": e["name"]}
                    for e in body
                    if isinstance(e, dict) and e.get("category") == "corporation"]
        return []
    if len(ids) == 1:
        print(f"[corps] dropping unresolvable id {ids[0]} "
              f"({nr.status_code})", file=sys.stderr)
        return []
    mid = len(ids) // 2
    return _resolve_corp_names(ids[:mid]) + _resolve_corp_names(ids[mid:])


def _load_npc_corps():
    path = CACHE_DIR / "npc_corps.json"
    cached = load_json(path, None)
    if cached:
        return cached
    print("[corps] fetching NPC corporation list from ESI…", file=sys.stderr)
    r = SESSION.get(f"{ESI}/corporations/npccorps/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    ids = r.json()
    corps = []
    for i in range(0, len(ids), 1000):
        corps.extend(_resolve_corp_names(ids[i:i + 1000]))
    corps.sort(key=lambda c: c["name"])
    print(f"[corps] resolved {len(corps)} of {len(ids)} NPC corporations",
          file=sys.stderr)
    save_json(path, corps)
    return corps


NPC_CORPS = []


def get_npc_corps():
    global NPC_CORPS
    if not NPC_CORPS:
        try:
            NPC_CORPS = _load_npc_corps()
        except Exception as e:  # noqa: BLE001
            print(f"[corps] failed to load NPC corporations: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return []
    return NPC_CORPS


def do_prefs(q):
    s = load_settings()
    for k in ("sort_key", "sort_dir", "col_widths", "col_order", "col_layout_v",
              "hide_illiquid", "hide_unaffordable", "active_tab", "trade_weight"):
        if k in q:
            s[k] = q[k][0]
    save_settings(s)
    return {"ok": True}


def do_detail(q):
    corp_id = int(q["corp_id"][0])
    offer_id = int(q["offer_id"][0])
    lp = float(q.get("lp", ["0"])[0] or 0)
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]

    offers = get_offers(corp_id, SESSION, CACHE_DIR)
    offer = next((o for o in offers if o.get("offer_id") == offer_id), None)
    if offer is None:
        raise LPError(f"Offer {offer_id} not found for corp {corp_id}.")

    tids = {offer["type_id"]} | {r["type_id"] for r in offer.get("required_items", [])}
    prices = fetch_prices(tids, SESSION, station_id=station_id)
    names = resolve_names(tids, SESSION, CACHE_DIR)
    volumes = resolve_volumes(tids, SESSION, CACHE_DIR)
    detail = build_detail(offer, prices, names, volumes, lp, tax, broker)
    detail["high_spread_pct"] = HIGH_SPREAD_PCT

    # Market saturation for the reward item (one cached history call).
    out_tid = offer["type_id"]
    daily_vol = fetch_history_volumes({out_tid}, region_id, SESSION, CACHE_DIR).get(out_tid)
    detail["daily_vol"] = daily_vol
    detail["days_to_clear"] = (
        detail["sell_volume"] / daily_vol if daily_vol and daily_vol > 0 else None)
    # Suggested per-unit sell-order price, anchored to the 30-day fair value
    # (shares the cached history just fetched above -- no extra ESI call).
    fair = fetch_history_prices({out_tid}, region_id, SESSION, CACHE_DIR).get(out_tid)
    detail["fair_price"] = fair
    detail["suggested_list"] = suggested_list_price(detail["ask"], fair)
    # Freshness of the current cheapest sell order (one live order-book call).
    detail["sell_order_stats"] = fetch_sell_order_stats(
        out_tid, SESSION, station_id=station_id, region_id=region_id)

    for it in detail["required_items"]:
        it["book"] = fetch_orderbook_jita(it["type_id"], "sell", SESSION,
                                          station_id=station_id, region_id=region_id)
    # Always fetch the output buy-order book so the instant-sell column can walk
    # it (the patient column values the reward at the lowest sell order / ask).
    detail["output"]["buy_book"] = fetch_orderbook_jita(
        detail["output"]["type_id"], "buy", SESSION,
        station_id=station_id, region_id=region_id)
    return detail


def do_history(q):
    type_id = int(q["type_id"][0])
    region_id = int(q.get("region_id", ["10000002"])[0])
    cache_path = CACHE_DIR / f"mhist_{region_id}_{type_id}.json"
    cached = load_json(cache_path, None)
    if cached and time.time() - cached.get("_ts", 0) < 43200:  # 12-hour cache
        return {"history": cached["data"]}
    r = SESSION.get(
        f"{ESI}/markets/{region_id}/history/",
        params={"type_id": type_id},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = sorted(r.json(), key=lambda x: x["date"])
    save_json(cache_path, {"_ts": time.time(), "data": data})
    return {"history": data}


# ── Arbitrage scanner ───────────────────────────────────────────────────────

def do_arb_prefs(q):
    s = load_arb_settings()
    for k in ("region", "sales_tax", "cross_station", "min_isk", "max_jumps",
              "avoid_lowsec", "route_flag"):
        if k in q:
            s[k] = q[k][0]
    save_arb_settings(s)
    return {"ok": True}


def do_arb_scan(q, emit=None):
    """Run the arb scan, optionally streaming SSE progress via emit(dict)."""
    def _emit(d):
        if emit:
            emit(d)

    region = int(q.get("region", ["10000002"])[0])
    sales_tax = float(q.get("sales_tax", ["0.075"])[0])
    cross_station = q.get("cross_station", ["1"])[0] in ("1", "true", "on")
    min_isk = float(q.get("min_isk", ["0"])[0] or 0)
    max_jumps = int(q.get("max_jumps", ["6"])[0])
    avoid_lowsec = q.get("avoid_lowsec", ["0"])[0] in ("1", "true", "on")
    route_flag = q.get("route_flag", ["shortest"])[0]

    s = load_arb_settings()
    s.update({
        "region": str(region),
        "sales_tax": str(sales_tax),
        "cross_station": "1" if cross_station else "0",
        "min_isk": str(min_isk) if min_isk else "",
        "max_jumps": str(max_jumps),
        "avoid_lowsec": "1" if avoid_lowsec else "0",
        "route_flag": route_flag,
    })
    save_arb_settings(s)

    _ensure_arb_caches()

    # Phase 1 — type list
    def types_progress(stage, **kw):
        if stage == "cache":
            _emit({"type": "progress", "pct": 8,
                   "msg": f"Type list cached ({kw['count']:,} types)", "sub": ""})
        elif stage == "page":
            pages = kw.get("pages", 1)
            pct = max(2, min(8, round(2 + kw["page"] / pages * 6)))
            _emit({"type": "progress", "pct": pct,
                   "msg": f"Fetching type list — page {kw['page']} of {pages}",
                   "sub": f"{kw['count']:,} types found"})

    all_types = arb_core.fetch_region_types(region, SESSION, CACHE_DIR,
                                            progress_cb=types_progress)

    # Phase 2 — Fuzzwork region aggregates → candidate types
    def fuzzwork_progress(stage, **kw):
        pct = 8 + round(kw["chunk"] / kw["total"] * 52)
        _emit({"type": "progress", "pct": pct,
               "msg": f"Price aggregates — batch {kw['chunk']} of {kw['total']}",
               "sub": f"{kw['types_done']:,} of {len(all_types):,} types priced"})

    _emit({"type": "progress", "pct": 8,
           "msg": f"Querying price aggregates for {len(all_types):,} types…", "sub": ""})
    prices = arb_core.fetch_fuzzwork_region(all_types, region, SESSION,
                                            progress_cb=fuzzwork_progress)
    candidates = arb_core.arb_candidates(prices, sales_tax)

    # Phase 3 — per-candidate orders from ESI
    _emit({"type": "progress", "pct": 60,
           "msg": f"Found {len(candidates)} candidate types — fetching orders…", "sub": ""})
    all_orders = []
    for i, type_id in enumerate(candidates):
        all_orders.extend(arb_core.fetch_type_orders(region, type_id, SESSION))
        if i % 10 == 0 or i == len(candidates) - 1:
            pct = 60 + round((i + 1) / max(len(candidates), 1) * 25)
            _emit({"type": "progress", "pct": pct,
                   "msg": f"Fetching orders — {i + 1} of {len(candidates)} types",
                   "sub": f"{len(all_orders):,} orders collected"})

    _emit({"type": "progress", "pct": 85,
           "msg": f"Analyzing {len(all_orders):,} orders…", "sub": "Finding profitable spreads"})

    results = [r for r in arb_core.find_spreads(all_orders, sales_tax, not cross_station)
               if r["isk_opportunity"] >= min_isk]

    if cross_station:
        # Enrich all results (capped) then filter to Jita-leg deals within max_jumps.
        # round_trip=True so jumps counts the haul both ways.
        _emit({"type": "progress", "pct": 87,
               "msg": f"Found {len(results):,} cross-station spreads — resolving stations…",
               "sub": f"Filtering to Jita legs ≤{max_jumps} jumps round-trip"})
        enriched = arb_core.enrich_locations(
            results[:500], round_trip=True, route_flag=route_flag,
            session=SESSION, station_cache=_ARB_STATION_CACHE, route_cache=_ARB_ROUTE_CACHE,
        )
        from_jita = arb_core.filter_from_jita(enriched, max_jumps)
        _emit({"type": "progress", "pct": 92,
               "msg": f"{len(from_jita)} deals within {max_jumps} jumps of Jita — checking security…",
               "sub": ""})
        shown = []
        for r in from_jita:
            arb_core.enrich_security([r], SESSION, _ARB_SYSTEM_CACHE)
            if avoid_lowsec and arb_core.sec_band(arb_core.row_risk_sec(r)) != "high":
                continue
            shown.append(r)
        shown.sort(key=lambda r: r["isk_opportunity"], reverse=True)
    else:
        # Same-station: just take the top 40 by ISK opportunity
        _emit({"type": "progress", "pct": 87,
               "msg": f"Found {len(results):,} same-station spreads — resolving stations…",
               "sub": "Looking up station names and security status"})
        shown = arb_core.build_shown(
            results, 40, False, avoid_lowsec, False, route_flag,
            SESSION, _ARB_STATION_CACHE, _ARB_ROUTE_CACHE, _ARB_SYSTEM_CACHE,
        )

    _emit({"type": "progress", "pct": 90,
           "msg": "Resolving item names & cargo volumes…", "sub": ""})

    if shown:
        names = arb_core.resolve_names({r["type_id"] for r in shown}, SESSION)
    else:
        names = {}

    for r in shown:
        vol = arb_core.resolve_volume(r["type_id"], _ARB_VOLUME_CACHE, SESSION)
        r["total_volume"] = vol * r["flippable_qty"] if vol is not None else None

    arb_core.save_lookup_cache(
        CACHE_DIR, _ARB_STATION_CACHE, _ARB_VOLUME_CACHE,
        _ARB_SYSTEM_CACHE, _ARB_ROUTE_CACHE,
    )

    _emit({"type": "progress", "pct": 97, "msg": "Formatting results…", "sub": ""})

    rows = []
    for r in shown:
        risk_sec = arb_core.row_risk_sec(r)
        risk_band = arb_core.sec_band(risk_sec)
        from_sec_raw = r.get("from_sec")
        to_sec_raw = r.get("to_sec")
        rows.append({
            "type_id": r["type_id"],
            "name": names.get(r["type_id"], str(r["type_id"])),
            "sell_price": r["sell_price"],
            "buy_price": r["buy_price"],
            "net_per_unit": r["net_per_unit"],
            "margin_pct": r["margin_pct"],
            "flippable_qty": r["flippable_qty"],
            "isk_opportunity": r["isk_opportunity"],
            "total_volume": r["total_volume"],
            "sell_station": r.get("sell_station_name", str(r["sell_location"])),
            "buy_station": r.get("buy_station_name", str(r["buy_location"])),
            "from_sec": arb_core.round_sec(from_sec_raw),
            "from_sec_band": arb_core.sec_band(from_sec_raw),
            "to_sec": arb_core.round_sec(to_sec_raw),
            "to_sec_band": arb_core.sec_band(to_sec_raw),
            "jumps": r.get("jumps_total", 0),
            "risk": arb_core._RISK_LABEL[risk_band],
            "risk_band": risk_band,
        })

    return {
        "region": region,
        "region_name": REGION_NAMES.get(region, f"Region {region}"),
        "cross_station": cross_station,
        "max_jumps": max_jumps,
        "sales_tax": sales_tax,
        "count": len(rows),
        "total_spreads": len(results),
        "total_orders": len(all_orders),
        "snap_expires": None,
        "snap_fetched_at": time.time(),
        "scanned_at": time.time(),
        "rows": rows,
    }


# ── Industry planner ────────────────────────────────────────────────────────

# After ranking by ISK/hour, only the top rows get a (cached, one-call-per-type)
# market-history lookup for the "days to sell" column — bounds the work on a
# broad scan while still covering everything worth looking at.
IND_HISTORY_TOP_N = 80

_IND_PREF_KEYS = ("profiles", "profile", "market_group", "me", "te", "job_rate",
                  "sales_tax", "broker", "runs", "bp_owned", "amortize_runs",
                  "station", "skills_level", "buildable_only")


def do_ind_prefs(q):
    s = load_ind_settings()
    for k in _IND_PREF_KEYS:
        if k in q:
            s[k] = q[k][0]
    save_ind_settings(s)
    return {"ok": True}


def do_ind_groups(q):
    """Top-level market groups for the category dropdown (builds the SDE first
    if needed)."""
    ind_core.load_sde_industry(CACHE_DIR, SESSION)
    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        return {"groups": ind_core.top_market_groups(conn)}
    finally:
        conn.close()


def _ind_params(q):
    """Parse the shared scan/detail knobs. Percentages (job rate, taxes) come
    from the UI as whole numbers and are converted to fractions here."""
    return {
        "me": int(float(q.get("me", ["0"])[0] or 0)),
        "te": int(float(q.get("te", ["0"])[0] or 0)),
        "job_rate": float(q.get("job_rate", ["6"])[0] or 0) / 100.0,
        "sales_tax": float(q.get("sales_tax", ["4.5"])[0] or 0) / 100.0,
        "broker_fee": float(q.get("broker", ["1.5"])[0] or 0) / 100.0,
        "runs": max(1, int(float(q.get("runs", ["1"])[0] or 1))),
        "bp_owned": q.get("bp_owned", ["0"])[0] in ("1", "true", "on"),
        "amortize_runs": max(1, int(float(q.get("amortize_runs", ["1"])[0] or 1))),
        "skills_level": int(float(q.get("skills_level", ["5"])[0] or 0)),
    }


def do_ind_scan(q, emit=None):
    """Rank manufacturable items by profitability. Streams SSE progress."""
    def _emit(d):
        if emit:
            emit(d)

    market_group = q.get("market_group", ["all"])[0]
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    region_id = TRADE_HUBS[station_id]["region_id"]
    refresh_sde = q.get("refresh_sde", ["0"])[0] in ("1", "true", "on")
    buildable_only = q.get("buildable_only", ["0"])[0] in ("1", "true", "on")
    params = _ind_params(q)

    s = load_ind_settings()
    for k in _IND_PREF_KEYS:
        if k in q:
            s[k] = q[k][0]
    save_ind_settings(s)

    _emit({"type": "progress", "pct": 4, "msg": "Loading blueprint database…", "sub": ""})
    ind_core.load_sde_industry(
        CACHE_DIR, SESSION, refresh=refresh_sde,
        emit=lambda m: _emit({"type": "progress", "pct": 6, "msg": m, "sub": ""}))
    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        if market_group and market_group != "all":
            group_ids = ind_core.expand_market_groups(conn, [int(market_group)])
            candidates = ind_core.manufacturing_candidates(conn, group_ids)
        else:
            candidates = ind_core.manufacturing_candidates(conn)
        _emit({"type": "progress", "pct": 18,
               "msg": f"{len(candidates):,} manufacturable items — loading recipes…", "sub": ""})
        bps = ind_core.assemble_blueprints(conn, candidates)
        ind_core.assemble_invention(conn, bps)

        type_ids = set()
        for bp in bps:
            type_ids.add(bp["product_id"])
            type_ids.add(bp["blueprint_id"])
            type_ids.update(mid for mid, _ in bp["materials"])
            if bp.get("invention"):
                type_ids.update(dc for dc, _ in bp["invention"]["datacores"])

        _emit({"type": "progress", "pct": 30,
               "msg": f"Pricing {len(type_ids):,} item types at "
                      f"{TRADE_HUBS[station_id]['name']}…", "sub": ""})
        prices = fetch_prices(type_ids, SESSION, station_id)
        adjusted = ind_core.fetch_adjusted_prices(SESSION, CACHE_DIR)
        volumes = ind_core.volumes_for(conn, type_ids)
        bpo_prices = {bp["blueprint_id"]: prices[bp["blueprint_id"]]["sell_min"]
                      for bp in bps
                      if (prices.get(bp["blueprint_id"]) or {}).get("sell_min")}

        _emit({"type": "progress", "pct": 78, "msg": "Computing profitability…", "sub": ""})
        params.update({"bpo_prices": bpo_prices, "volumes": volumes})
        rows = ind_core.evaluate_industry(bps, prices, adjusted, params)
        if buildable_only:
            rows = [r for r in rows if r["buildable"]]
    finally:
        conn.close()

    # Days-to-sell for the top rows only (one cached call per product type).
    top = rows[:IND_HISTORY_TOP_N]
    if top:
        _emit({"type": "progress", "pct": 88,
               "msg": f"Checking market depth for the top {len(top)} items…", "sub": ""})
        daily = fetch_history_volumes({r["product_id"] for r in top}, region_id,
                                      SESSION, CACHE_DIR)
        for r in top:
            dv = daily.get(r["product_id"])
            r["daily_vol"] = dv
            r["days_to_sell"] = ((r["out_qty"] * r["runs"]) / dv) if dv else None

    _emit({"type": "progress", "pct": 97, "msg": "Formatting results…", "sub": ""})
    return {
        "station_id": station_id,
        "station_name": TRADE_HUBS[station_id]["name"],
        "market_group": market_group,
        "runs": params["runs"],
        "count": len(rows),
        "scanned_at": time.time(),
        "rows": rows,
    }


def do_ind_detail(q):
    """Full breakdown for one blueprint, with accurate (ESI packaged) cargo
    volumes resolved lazily for just this item's inputs and output."""
    blueprint_id = int(q["blueprint_id"][0])
    station_id = int(q.get("station", [str(JITA_STATION_ID)])[0] or JITA_STATION_ID)
    if station_id not in TRADE_HUBS:
        station_id = JITA_STATION_ID
    params = _ind_params(q)

    conn = ind_core.connect_sde(CACHE_DIR)
    try:
        row = conn.execute(
            "SELECT p.blueprint_id, p.product_id, p.quantity AS out_qty, "
            "t.type_name, t.market_group_id, t.tech_level, t.volume AS out_volume "
            "FROM products p JOIN types t ON t.type_id = p.product_id "
            "WHERE p.blueprint_id = ? AND p.activity_id = ?",
            (blueprint_id, ind_core.ACT_MANUFACTURING)).fetchone()
        if not row:
            raise LPError(f"No manufacturing blueprint {blueprint_id}.")
        bp = ind_core.assemble_blueprints(conn, [dict(row)])[0]
        ind_core.assemble_invention(conn, [bp])
    finally:
        conn.close()

    type_ids = {bp["product_id"], bp["blueprint_id"]}
    type_ids.update(mid for mid, _ in bp["materials"])
    if bp.get("invention"):
        type_ids.update(dc for dc, _ in bp["invention"]["datacores"])
    prices = fetch_prices(type_ids, SESSION, station_id)
    params["adjusted"] = ind_core.fetch_adjusted_prices(SESSION, CACHE_DIR)
    params["bpo_prices"] = {
        bp["blueprint_id"]: (prices.get(bp["blueprint_id"]) or {}).get("sell_min")}
    volumes = resolve_volumes(type_ids, SESSION, CACHE_DIR)
    names = resolve_names(type_ids, SESSION, CACHE_DIR)
    detail = ind_core.build_industry_detail(bp, prices, names, volumes, params)
    detail["product"]["tech_level"] = bp.get("tech_level")
    detail["station_name"] = TRADE_HUBS[station_id]["name"]
    return detail


# ── HTTP handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_emit(self, data):
        try:
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_sse_scan(self, q, scan_fn, tag):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        emit = self._sse_emit
        try:
            result = scan_fn(q, emit=emit)
            emit({"type": "result", **result})
        except LPError as e:
            print(f"[{tag}] LPError: {e}", file=sys.stderr)
            emit({"type": "error", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            emit({"type": "error", "error": f"{type(e).__name__}: {e}"})

    def _handle_arb_scan(self, q):
        self._handle_sse_scan(q, do_arb_scan, "arb")

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
            elif parsed.path == "/favicon.ico":
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(_FAVICON_SVG)))
                self.end_headers()
                self.wfile.write(_FAVICON_SVG)
            elif parsed.path == "/api/corps":
                self._send_json(get_npc_corps())
            elif parsed.path == "/api/settings":
                merged = load_settings()
                merged["arb"] = load_arb_settings()
                merged["ind"] = load_ind_settings()
                self._send_json(merged)
            elif parsed.path == "/api/prefs":
                self._send_json(do_prefs(q))
            elif parsed.path == "/api/scan":
                self._send_json(do_scan(q))
            elif parsed.path == "/api/liquidity":
                self._send_json(do_liquidity(q))
            elif parsed.path == "/api/detail":
                self._send_json(do_detail(q))
            elif parsed.path == "/api/history":
                self._send_json(do_history(q))
            elif parsed.path == "/api/arb/prefs":
                self._send_json(do_arb_prefs(q))
            elif parsed.path == "/api/arb/scan":
                self._handle_arb_scan(q)
            elif parsed.path == "/api/ind/prefs":
                self._send_json(do_ind_prefs(q))
            elif parsed.path == "/api/ind/groups":
                self._send_json(do_ind_groups(q))
            elif parsed.path == "/api/ind/scan":
                self._handle_sse_scan(q, do_ind_scan, "ind")
            elif parsed.path == "/api/ind/detail":
                self._send_json(do_ind_detail(q))
            else:
                self._send_json({"error": "not found"}, 404)
        except LPError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)


# ── Front-end ───────────────────────────────────────────────────────────────

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EVE Market Tools</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,__FAVICON__">
<style>
  :root {
    --bg:#080d11; --panel:#0f1923; --panel2:#162130; --panel3:#1c2a3a;
    --line:#1f3044; --line2:#2a3f55;
    --fg:#c8d8e8; --dim:#5a7a95; --dim2:#3d5a70;
    --cyan:#4fc3f7; --cyan2:#29b6f6; --green:#4caf76; --green2:#66bb6a;
    --yellow:#f0c040; --red:#e05555; --accent:#1e5799;
    --accent2:#2471c8; --gold:#c8a040;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg);
    font:15px/1.5 "Segoe UI",system-ui,sans-serif; height:100vh; overflow:hidden; }
  a { color:var(--cyan); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .hidden { display:none !important; }
  /* Inline spinner shown in saturation cells while the background fetch runs. */
  .spin { display:inline-block; width:11px; height:11px; vertical-align:-1px;
    border:2px solid var(--dim2); border-top-color:var(--cyan);
    border-radius:50%; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* ── Custom tooltip (replaces native title=) ─────────────────────── */
  #tooltip {
    position:fixed; z-index:9999; max-width:280px;
    padding:8px 11px;
    background:linear-gradient(180deg, var(--panel3) 0%, var(--panel2) 100%);
    border:1px solid var(--line2); border-radius:7px;
    color:var(--fg); font-size:12.5px; line-height:1.45; letter-spacing:.1px;
    box-shadow:0 8px 26px rgba(0,0,0,.55);
    pointer-events:none; opacity:0; transform:translateY(3px);
    transition:opacity .11s ease, transform .11s ease;
  }
  #tooltip.show { opacity:1; transform:translateY(0); }
  #tooltip b, #tooltip .k { color:var(--cyan); font-weight:600; }
  [data-tip] { cursor:help; }
  th[data-tip], button[data-tip], label[data-tip] { cursor:pointer; }

  /* ── Top bar ─────────────────────────────────────────────────────── */
  header {
    padding:0 18px;
    height:46px;
    border-bottom:1px solid var(--line);
    display:flex; gap:0; align-items:center;
    background:linear-gradient(180deg, #0f1f30 0%, var(--panel) 100%);
    box-shadow:0 2px 12px rgba(0,0,0,.5);
    flex-shrink:0;
  }
  .logo {
    font-size:17px; font-weight:700; color:var(--cyan); letter-spacing:.5px;
    white-space:nowrap; text-shadow:0 0 18px rgba(79,195,247,.35);
    padding-right:16px; margin-right:8px;
    border-right:1px solid var(--line2);
  }
  .logo span { color:var(--gold); }
  .logo .ver { font-size:10px; font-weight:400; color:var(--dim2);
    letter-spacing:.5px; margin-left:6px; vertical-align:middle; }
  .tabs { display:flex; gap:0; }
  .tab {
    background:transparent; border:none; border-bottom:2px solid transparent;
    color:var(--dim); font:inherit; font-size:14px; font-weight:600;
    padding:0 18px; height:46px; cursor:pointer;
    transition:color .12s, border-color .12s;
  }
  .tab:hover { color:var(--fg); }
  .tab.active { color:var(--cyan); border-bottom-color:var(--cyan2); }

  /* ── Control bar ─────────────────────────────────────────────────── */
  .ctrlbar {
    padding:0 18px 7px; height:56px; flex-shrink:0;
    border-bottom:1px solid var(--line);
    background:var(--panel);
    display:flex; gap:10px; align-items:flex-end; flex-wrap:nowrap; overflow:hidden;
  }
  .field { display:flex; flex-direction:column; gap:1px; }
  .field label { font-size:10px; text-transform:uppercase; letter-spacing:.7px;
    color:var(--dim); font-weight:600; }
  input, select {
    background:var(--panel2); border:1px solid var(--line2); color:var(--fg);
    border-radius:4px; padding:4px 8px; font:inherit; font-size:14px;
    transition:border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus {
    outline:none; border-color:var(--cyan2);
    box-shadow:0 0 0 2px rgba(41,182,246,.15);
  }
  input[type=number] { width:90px; }
  input#corp { width:210px; }
  input#arb-minisk { width:110px; }
  .corp-wrap { position:relative; }
  .corp-wrap input { padding-left:28px; width:100%; }
  .corp-icon {
    position:absolute; left:8px; top:50%; transform:translateY(-50%);
    color:var(--dim); font-size:13px; pointer-events:none; user-select:none;
  }
  .corp-drop {
    position:fixed; z-index:200;
    background:var(--panel2); border:1px solid var(--cyan2);
    border-radius:4px;
    box-shadow:0 8px 28px rgba(0,0,0,.6);
    max-height:240px; overflow-y:auto;
  }
  .corp-drop-item {
    padding:7px 12px; cursor:pointer; font-size:14px; color:var(--fg);
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    transition:background .08s;
  }
  .corp-drop-item:hover, .corp-drop-item.hi {
    background:var(--accent); color:#fff;
  }
  .corp-drop-empty {
    padding:8px 12px; font-size:13px; color:var(--dim); font-style:italic;
  }
  .btn-group { display:flex; gap:6px; align-self:flex-end; align-items:center; }
  .check-field {
    display:inline-flex; align-items:center; gap:5px;
    font-size:13px; color:var(--dim); cursor:pointer; white-space:nowrap; user-select:none;
  }
  .check-field:hover { color:var(--fg); }
  .check-field input[type=checkbox] {
    accent-color:var(--cyan2); width:14px; height:14px; cursor:pointer; flex-shrink:0;
  }
  button {
    border:none; border-radius:4px; cursor:pointer; font:inherit; font-size:14px;
    font-weight:600; padding:5px 14px; transition:filter .12s, background .12s;
    white-space:nowrap;
  }
  button.primary {
    background:linear-gradient(180deg,#2080d0 0%,#1560a8 100%);
    color:#fff; box-shadow:0 1px 4px rgba(0,0,0,.4);
  }
  button.primary:hover { filter:brightness(1.15); }
  button.primary:disabled { filter:brightness(.6); cursor:default; }
  button.secondary {
    background:var(--panel2); border:1px solid var(--line2);
    color:var(--dim); font-weight:500;
  }
  button.secondary:hover { border-color:var(--cyan2); color:var(--fg); }
  /* Tradeability balance presets (segmented control). */
  .balance-group { display:inline-flex; align-items:center; gap:0; white-space:nowrap; }
  .balance-label { font-size:13px; color:var(--dim); margin-right:7px; }
  .balance-btn {
    background:var(--panel2); border:1px solid var(--line2); border-left-width:0;
    color:var(--dim); font-weight:500; font-size:13px; padding:5px 11px; border-radius:0;
  }
  .balance-btn:first-of-type { border-left-width:1px; border-radius:4px 0 0 4px; }
  .balance-btn:last-of-type { border-radius:0 4px 4px 0; }
  .balance-btn:hover { color:var(--fg); }
  .balance-btn.on { background:var(--accent); color:#fff; border-color:var(--accent2); }

  /* ── Status bar ──────────────────────────────────────────────────── */
  #statusbar {
    padding:4px 18px; font-size:13px; min-height:27px; flex-shrink:0;
    background:var(--panel); border-bottom:1px solid var(--line);
    display:flex; align-items:center; gap:8px; color:var(--fg);
  }
  #statusbar.err { color:var(--red); }
  #statusbar .ts { color:var(--dim); font-size:11px; margin-left:4px; }
  #statusbar .pill {
    display:inline-flex; align-items:center; gap:5px;
    background:var(--panel3); border:1px solid var(--line2);
    border-radius:20px; padding:1px 10px; font-size:12px; color:var(--dim);
  }
  #statusbar .pill b { color:var(--fg); font-weight:600; }

  /* ── Layout ──────────────────────────────────────────────────────── */
  main { display:flex; height:calc(100vh - 131px); overflow:hidden; }
  .tablewrap { flex:1; overflow:auto; min-width:0; }

  /* ── Tables ──────────────────────────────────────────────────────── */
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:14px; }
  th, td { padding:7px 12px; text-align:right; white-space:nowrap;
    border-bottom:1px solid var(--line); }
  th:first-child, td:first-child { text-align:left; padding-left:16px; }
  td:last-child, th:last-child { padding-right:16px; }
  th {
    position:sticky; top:0; z-index:2;
    background:linear-gradient(180deg,#132030 0%,#0f1923 100%);
    color:var(--dim); font-size:11px; text-transform:uppercase;
    letter-spacing:.6px; font-weight:700; cursor:pointer; user-select:none;
    border-bottom:2px solid var(--line2);
  }
  th:hover { color:var(--cyan); }
  th.sorted { color:var(--cyan2); }
  .resizer {
    position:absolute; top:0; right:0; width:12px; height:100%;
    cursor:col-resize; z-index:3;
  }
  .resizer::after {
    content:""; position:absolute;
    top:18%; right:3px; width:2px; height:64%;
    background:var(--line2); border-radius:1px; pointer-events:none;
    transition:background .12s, width .12s;
  }
  .resizer:hover::after, .resizer.active::after { background:var(--cyan); width:3px; }
  body.col-resizing { cursor:col-resize; user-select:none; }
  /* Column drag-to-reorder. box-shadow markers avoid any layout shift. */
  th[draggable=true] { cursor:grab; }
  th.col-dragging { opacity:.45; cursor:grabbing; }
  th.drop-before { box-shadow: inset 3px 0 0 var(--cyan2); }
  th.drop-after  { box-shadow: inset -3px 0 0 var(--cyan2); }
  body.col-dragging-active { cursor:grabbing; }

  /* LP table */
  #tbl th, #tbl td { overflow:hidden; text-overflow:ellipsis; }
  #tbl td:first-child, #tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  #tbl tbody tr { cursor:pointer; transition:background .08s; }
  #tbl tbody tr:hover { background:var(--panel2); }
  #tbl tbody tr.sel { background:rgba(32,113,196,.18); border-left:3px solid var(--cyan2); }
  #tbl tbody tr.sel td:first-child { padding-left:13px; }
  #tbl tbody tr.illiquid { opacity:.75; }
  #tbl tbody tr.illiquid td.spread { color:var(--red); }
  #tbl tbody tr.unaffordable td { color:var(--dim2); }

  /* ARB table */
  #arb-tbl th { position:sticky; }
  #arb-tbl th, #arb-tbl td { overflow:hidden; text-overflow:ellipsis; }
  #arb-tbl td:first-child, #arb-tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  #arb-tbl tbody tr { transition:background .08s; }
  #arb-tbl tbody tr:hover { background:var(--panel2); }
  td.sec-high  { color:var(--green2); font-weight:500; }
  td.sec-low   { color:var(--yellow); font-weight:500; }
  td.sec-null  { color:var(--red);    font-weight:500; }
  td.sec-unknown { color:var(--dim); }
  td.risk-high  { color:var(--green2); font-weight:600; }
  td.risk-low   { color:var(--yellow); font-weight:600; }
  td.risk-null  { color:var(--red);    font-weight:600; }
  td.risk-unknown { color:var(--dim); }

  td.pos { color:var(--green2); font-weight:500; }
  td.neg { color:var(--red); }
  /* The better of the two sell-mode columns (list vs instant sell). */
  td.win { background:rgba(79,195,247,.10); box-shadow:inset 2px 0 0 var(--cyan2); font-weight:700; }
  td.spread.tight { color:var(--green); }
  td.spread.mid { color:var(--yellow); }
  .flag { color:var(--red); font-weight:700; font-size:12px; margin-left:2px; }

  /* ── Dedicated Recipe List Section ───────────────────────────────── */
  .recipe-list {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:4px 14px; margin-bottom:14px;
  }
  .recipe-list-item {
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 0; border-bottom:1px solid var(--line); font-size:13px;
  }
  .recipe-list-item:last-child { border-bottom:none; }
  .recipe-list-item .name { color:var(--dim); }
  .recipe-list-item .val { color:var(--fg); font-weight:600; }
  .recipe-list-item .val.lp { color:#81d4fa; }
  .recipe-list-item .val.isk { color:#a5d6a7; }
  #detail {
    flex-shrink:0; width:0; overflow:hidden;
    transition:width .18s cubic-bezier(.4,0,.2,1);
    background:var(--panel);
  }
  #detail.open { width:580px; border-left:1px solid var(--line2);
    box-shadow:-16px 0 40px rgba(0,0,0,.6); }
  #detail .inner { width:580px; max-width:96vw; padding:20px 22px;
    overflow-y:auto; overflow-x:hidden; height:100%; }
  #detail .dheader { display:flex; align-items:flex-start; justify-content:space-between;
    margin-bottom:4px; }
  #detail h2 { font-size:20px; color:var(--cyan); font-weight:700; line-height:1.2;
    text-shadow:0 0 20px rgba(79,195,247,.2); }
  #detail .sub { color:var(--dim); font-size:12px; margin-bottom:14px; }
  .close { cursor:pointer; color:var(--dim); font-size:20px; line-height:1;
    padding:2px 4px; border-radius:3px; flex-shrink:0; }
  .close:hover { color:var(--fg); background:var(--panel3); }
  .redrow { display:flex; align-items:center; gap:10px; margin:14px 0 4px;
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:8px 12px; }
  .redrow label { color:var(--dim); font-size:13px; white-space:nowrap; }
  .redrow input { width:90px; font-size:15px; font-weight:600; }
  .redrow .maxlink { font-size:12px; color:var(--dim); }
  .kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin:12px 0; }
  .kpi {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:7px 10px; position:relative; overflow:hidden;
  }
  .kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:var(--line2); }
  .kpi.accent::before { background:linear-gradient(90deg,var(--cyan2),transparent); }
  .kpi .l { font-size:9px; text-transform:uppercase; letter-spacing:.5px;
    color:var(--dim); font-weight:700; }
  .kpi .v { font-size:16px; font-weight:700; margin-top:2px; }
  .v.pos { color:var(--green2); } .v.neg { color:var(--red); }
  h3 {
    font-size:11px; text-transform:uppercase; letter-spacing:.7px; font-weight:700;
    color:var(--dim); border-bottom:1px solid var(--line); padding-bottom:5px;
    margin:18px 0 8px;
  }
  table.mini { font-size:13px; width:100%; border-collapse:collapse; }
  table.mini th { position:static; background:none; color:var(--dim);
    font-size:10px; letter-spacing:.5px; border-bottom:1px solid var(--line); padding:4px 8px; }
  table.mini td { padding:6px 8px; border-bottom:1px solid var(--line);
    color:var(--fg); vertical-align:top; }
  table.mini th:first-child, table.mini td:first-child { text-align:left;
    white-space:normal; word-break:break-word; }
  table.mini tr:last-child td { border-bottom:none; }
  table.mini tr:hover td { background:var(--panel2); }
  table.mini .total td { font-weight:700; border-top:1px solid var(--line2);
    background:var(--panel3); }
  table.mini .subtotal td { font-weight:600; border-top:1px solid var(--line);
    color:var(--fg); }
  .note {
    display:flex; align-items:flex-start; gap:7px;
    background:rgba(240,192,64,.07); border:1px solid rgba(240,192,64,.25);
    border-radius:5px; padding:8px 10px; color:var(--yellow); font-size:13px;
    margin:6px 0;
  }
  .note::before { content:"⚠"; flex-shrink:0; }
  .note.bad { background:rgba(224,85,85,.08); border-color:rgba(224,85,85,.3);
    color:var(--red); }
  .note.bad::before { content:"✕"; }
  .muted { color:var(--dim); font-size:12px; line-height:1.5; margin-top:10px; }

  /* ── Arb progress overlay ────────────────────────────────────────── */
  #arb-progress {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    height:100%; gap:10px; padding:24px;
  }
  .prog-label { font-size:15px; font-weight:600; color:var(--fg); text-align:center; }
  .prog-track {
    width:340px; max-width:90vw; height:6px;
    background:var(--line2); border-radius:3px; overflow:hidden;
  }
  .prog-fill {
    height:100%; width:0%;
    background:linear-gradient(90deg, var(--accent2), var(--cyan2));
    border-radius:3px; transition:width .35s ease;
  }
  .prog-sub { font-size:12px; color:var(--dim); text-align:center; min-height:16px; }

  /* ── Lot tracker ─────────────────────────────────────────────────── */
  .lot-tracker { display:flex; flex-direction:column; gap:5px; }
  .lot-row {
    background:var(--panel2); border:1px solid var(--line2); border-radius:5px;
    padding:7px 10px;
  }
  .lot-label { font-size:12px; color:var(--dim); margin-bottom:5px; }
  .lot-label .lot-need { color:var(--fg); font-weight:600; }
  .lot-controls { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .lot-tags { display:flex; flex-wrap:wrap; gap:3px; }
  .lot-tag {
    background:var(--panel3); border:1px solid var(--line2); border-radius:3px;
    padding:1px 7px; font-size:12px; display:inline-flex; align-items:center; gap:5px;
  }
  .lot-tag .rm { cursor:pointer; color:var(--dim); font-size:10px; line-height:1; }
  .lot-tag .rm:hover { color:var(--red); }
  .lot-num { width:70px; font-size:13px; padding:3px 6px; }
  .lot-sum { font-size:13px; font-weight:600; }

  /* ── Column picker ───────────────────────────────────────────────── */
  .col-picker {
    position:fixed; z-index:300;
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:6px 0; box-shadow:0 6px 24px rgba(0,0,0,.55); min-width:170px;
  }
  .col-picker.hidden { display:none; }
  .col-picker label {
    display:flex; align-items:center; gap:8px;
    padding:5px 14px; cursor:pointer; font-size:13px; user-select:none;
  }
  .col-picker label:hover { background:var(--panel3); }
  .col-picker input[type=checkbox] { margin:0; accent-color:var(--cyan2); }

  /* ── Price history chart ─────────────────────────────────────────── */
  .chart-wrap { position:relative; width:100%; height:160px; margin:8px 0 4px; }
  .chart-canvas { width:100%; height:100%; display:block; border-radius:4px;
    cursor:crosshair; background:var(--panel2); }
  .chart-tip {
    position:absolute; pointer-events:none; display:none;
    background:rgba(8,13,17,.96); border:1px solid var(--line2);
    border-radius:4px; padding:5px 9px; font-size:11px; white-space:nowrap;
    z-index:10; color:var(--fg);
  }
  .chart-stats {
    font-size:11px; color:var(--dim); margin-bottom:6px;
    display:flex; flex-wrap:wrap; gap:5px;
  }
  .chart-stats span { background:var(--panel3); border:1px solid var(--line2);
    border-radius:4px; padding:2px 8px; display:inline-flex; align-items:baseline;
    gap:5px; cursor:help; }
  .chart-stats .k { text-transform:uppercase; font-size:9px; letter-spacing:.4px;
    color:var(--dim); }
  .chart-stats .v { color:var(--fg); font-weight:600; }
  .chart-stats .d { font-weight:600; }
  .chart-cross {
    position:absolute; top:0; bottom:20px; width:1px;
    background:rgba(200,216,232,.3); pointer-events:none; display:none;
  }
  .chart-expand-btn {
    position:absolute; top:4px; right:4px; z-index:5;
    background:rgba(8,13,17,.78); border:1px solid var(--line2);
    color:var(--dim); font-size:13px; padding:1px 6px; line-height:1.5;
    border-radius:3px; cursor:pointer;
  }
  .chart-expand-btn:hover { color:var(--fg); border-color:var(--cyan2); }
  /* Expand chart modal */
  #chartExpandModal {
    position:fixed; inset:0; z-index:600; background:rgba(0,0,0,.78);
    display:flex; align-items:center; justify-content:center;
  }
  #chartExpandModal.hidden { display:none; }
  .chart-expand-box {
    background:var(--panel2); border:1px solid var(--line2); border-radius:8px;
    padding:20px 22px; width:880px; max-width:97vw;
    box-shadow:0 20px 60px rgba(0,0,0,.7);
  }
  .chart-expand-head {
    display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
  }
  .chart-expand-head h3 { font-size:16px; font-weight:700; color:var(--cyan); margin:0; }
  /* ARB chart modal */
  #arbChartModal {
    position:fixed; inset:0; z-index:500; background:rgba(0,0,0,.72);
    display:flex; align-items:center; justify-content:center;
  }
  #arbChartModal.hidden { display:none; }
  .arb-chart-box {
    background:var(--panel2); border:1px solid var(--line2); border-radius:8px;
    padding:20px 22px; width:620px; max-width:95vw;
    box-shadow:0 20px 60px rgba(0,0,0,.7);
  }
  .arb-chart-head {
    display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
  }
  .arb-chart-head h3 { font-size:16px; font-weight:700; color:var(--cyan); margin:0; }

  /* ── Industry ────────────────────────────────────────────────────── */
  .ind-presets { display:inline-flex; gap:3px; margin-left:4px; }
  .ind-preset { padding:4px 7px; font-size:11px; }
  #ind-detail {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:12px 14px; margin-bottom:12px;
  }
  .ind-d-head { font-size:14px; color:var(--fg); margin-bottom:10px; position:relative; }
  .ind-d-close { position:absolute; right:0; top:0; cursor:pointer; color:var(--dim); padding:0 4px; }
  .ind-d-close:hover { color:var(--fg); }
  .ind-d-grid {
    display:grid; grid-template-columns:auto auto; gap:3px 18px;
    font-size:12px; margin-bottom:12px; max-width:560px;
  }
  .ind-d-grid span { color:var(--dim); }
  .ind-d-grid b { text-align:right; color:var(--fg); }
  .ind-d-mats { width:100%; border-collapse:collapse; font-size:12px; }
  .ind-d-mats th, .ind-d-mats td { padding:3px 8px; border-bottom:1px solid var(--line2); }
  .ind-d-mats th { color:var(--dim); text-align:left; font-weight:600; }
  .ind-d-mats td.num, .ind-d-mats th.num { text-align:right; }
  #ind-tbl th { cursor:pointer; user-select:none; }
</style>
</head>
<body>

<header>
  <div class="logo">EVE <span>MARKET TOOLS</span><span class="ver">v__VERSION__</span></div>
  <nav class="tabs">
    <button class="tab active" data-tab="lp">LP Store</button>
    <button class="tab" data-tab="arb">Arbitrage</button>
    <button class="tab" data-tab="ind">Industry</button>
  </nav>
</header>

<!-- LP controls -->
<div id="lp-controls" class="ctrlbar">
  <div class="field"><label>Corporation</label>
    <div class="corp-wrap">
      <span class="corp-icon">⌕</span>
      <input id="corp" placeholder="Search corporation…" autocomplete="off" spellcheck="false">
    </div>
  </div>
  <div class="field"><label>LP budget</label><input id="lp" type="number" value="500000"></div>
  <div class="field"><label>Max spread %</label><input id="maxspread" type="number" placeholder="off" value="20"></div>
  <div class="field"><label>Sales tax %</label><input id="tax" type="number" step="0.1" value="4.5"></div>
  <div class="field"><label>Broker fee %</label><input id="broker" type="number" step="0.1" value="1.5"></div>
  <div class="field"><label>Market</label>
    <select id="market">
      <option value="60003760">Jita 4-4</option>
      <option value="60008494">Amarr 8-20</option>
      <option value="60004588">Rens 6-8</option>
      <option value="60011866">Dodixie 9-20</option>
      <option value="60005686">Hek 8-12</option>
    </select>
  </div>
  <div class="btn-group">
    <button id="go" class="primary">Scan</button>
    <button id="refresh" class="secondary" data-tip="Re-fetch offers and prices from ESI">⟳ Refresh</button>
    <label class="check-field" data-tip="Show or hide illiquid rows"><input type="checkbox" id="toggleIlliquid"> Hide illiquid !</label>
    <label class="check-field" data-tip="Hide offers you can't afford"><input type="checkbox" id="toggleAffordable"> Hide unaffordable</label>
    <span class="balance-group" data-tip="How the Tradeability score weights liquidity vs competition">
      <span class="balance-label">Tradeability:</span>
      <button class="balance-btn" data-w="0.5">Balanced</button>
      <button class="balance-btn" data-w="0.75">Favor liquidity</button>
      <button class="balance-btn" data-w="0.25">Favor quiet markets</button>
    </span>
    <button id="colPickerBtn" class="secondary" data-tip="Choose visible columns">Columns ▾</button>
  </div>
</div>
<div id="colPicker" class="col-picker hidden"></div>

<!-- ARB controls -->
<div id="arb-controls" class="ctrlbar hidden">
  <div class="field"><label>Region</label>
    <select id="arb-region">
      <option value="10000002">The Forge (Jita)</option>
      <option value="10000043">Domain (Amarr)</option>
      <option value="10000032">Sinq Laison (Dodixie)</option>
      <option value="10000042">Metropolis (Hek)</option>
      <option value="10000030">Heimatar (Rens)</option>
    </select>
  </div>
  <div class="field"><label>Mode</label>
    <select id="arb-cross">
      <option value="1" selected>Cross-station (haul)</option>
      <option value="0">Same-station (instant flip)</option>
    </select>
  </div>
  <div class="field"><label>Sales tax %</label>
    <input id="arb-tax" type="number" step="0.1" value="7.5" style="width:80px">
  </div>
  <div class="field"><label>Min ISK opp</label>
    <input id="arb-minisk" type="number" placeholder="0">
  </div>
  <div class="field" id="arb-maxjumps-field"><label>Max jumps (RT)</label>
    <input id="arb-maxjumps" type="number" value="6" min="1" max="50" style="width:70px">
  </div>
  <div class="field"><label>Route</label>
    <select id="arb-route">
      <option value="shortest">Shortest</option>
      <option value="secure">Secure (highsec only)</option>
      <option value="insecure">Insecure</option>
    </select>
  </div>
  <div class="btn-group">
    <button id="arb-go" class="primary">Scan</button>
    <button id="arb-toggleLowsec" class="secondary toggle" data-tip="Hide deals touching lowsec/nullsec">Highsec only</button>
  </div>
</div>

<!-- Industry controls -->
<div id="ind-controls" class="ctrlbar hidden">
  <div class="field"><label>Category</label>
    <select id="ind-group"><option value="all">All (slow)</option></select>
  </div>
  <div class="field"><label>Tax profile</label>
    <select id="ind-profile" data-tip="Saved job-cost-rate presets for a build location"></select>
  </div>
  <div class="field"><label>Job cost %</label>
    <input id="ind-jobrate" type="number" step="0.1" value="6" style="width:75px"
           data-tip="Installation cost as a % of EIV — read it off the in-game Industry window (it already bundles system index + facility tax + SCC).">
    <button id="ind-saveprofile" class="secondary" style="padding:4px 8px" data-tip="Save the current job cost % as a named profile">＋</button>
  </div>
  <div class="field"><label>Source hub</label>
    <select id="ind-station">
      <option value="60003760">Jita 4-4</option>
      <option value="60008494">Amarr 8-20</option>
      <option value="60004588">Rens 6-8</option>
      <option value="60011866">Dodixie 9-20</option>
      <option value="60005686">Hek 8-12</option>
    </select>
  </div>
  <div class="field"><label>ME</label><input id="ind-me" type="number" min="0" max="10" value="10" style="width:55px"></div>
  <div class="field"><label>TE</label><input id="ind-te" type="number" min="0" max="20" value="20" style="width:55px"></div>
  <div class="field"><label>Skills @</label>
    <input id="ind-skills" type="number" min="0" max="5" value="5" style="width:55px"
           data-tip="Assume every required skill is at this level (gates buildable + reduces build time)">
  </div>
  <div class="field"><label>Sales tax %</label><input id="ind-tax" type="number" step="0.1" value="4.5" style="width:70px"></div>
  <div class="field"><label>Broker %</label><input id="ind-broker" type="number" step="0.1" value="1.5" style="width:70px"></div>
  <div class="field"><label>Batch (runs)</label>
    <input id="ind-runs" type="number" min="1" value="1" style="width:80px">
    <span class="ind-presets">
      <button class="ind-preset secondary" data-n="1">1</button>
      <button class="ind-preset secondary" data-n="100">100</button>
      <button class="ind-preset secondary" data-n="10000">10k</button>
    </span>
  </div>
  <div class="field"><label>Amortize/runs</label>
    <input id="ind-amort" type="number" min="1" value="1" style="width:70px"
           data-tip="Spread a bought BPO's price over this many runs (ignored if you own it)">
  </div>
  <div class="btn-group">
    <button id="ind-go" class="primary">Scan</button>
    <label class="check-field" data-tip="Mark the blueprint as already owned — drops its cost from the math"><input type="checkbox" id="ind-owned"> I own the BP</label>
    <label class="check-field" data-tip="Only show items every required skill (at the level above) can build"><input type="checkbox" id="ind-buildable"> Buildable only</label>
    <button id="ind-refresh" class="secondary" data-tip="Re-download the blueprint database from Fuzzwork">⟳ Refresh SDE</button>
  </div>
</div>

<div id="statusbar"></div>

<main>
  <!-- LP tab -->
  <div id="lp-tablewrap" class="tablewrap">
    <table id="tbl"><colgroup id="cg"></colgroup><thead></thead><tbody></tbody></table>
  </div>
  <!-- ARB tab -->
  <div id="arb-tablewrap" class="tablewrap hidden">
    <div id="arb-progress" class="hidden">
      <div class="prog-label" id="arb-prog-label">Initializing…</div>
      <div class="prog-track"><div class="prog-fill" id="arb-prog-fill"></div></div>
      <div class="prog-sub" id="arb-prog-sub"></div>
    </div>
    <table id="arb-tbl"><colgroup id="arb-cg"></colgroup><thead></thead><tbody></tbody></table>
  </div>
  <!-- Industry tab -->
  <div id="ind-tablewrap" class="tablewrap hidden">
    <div id="ind-progress" class="hidden">
      <div class="prog-label" id="ind-prog-label">Initializing…</div>
      <div class="prog-track"><div class="prog-fill" id="ind-prog-fill"></div></div>
      <div class="prog-sub" id="ind-prog-sub"></div>
    </div>
    <div id="ind-detail" class="hidden"></div>
    <table id="ind-tbl"><thead></thead><tbody></tbody></table>
  </div>
  <!-- LP detail panel -->
  <div id="detail"><div class="inner"></div></div>
  <!-- Price history modal (ARB rows) -->
  <div id="arbChartModal" class="hidden">
    <div class="arb-chart-box">
      <div class="arb-chart-head">
        <h3 id="arbChartTitle"></h3>
        <span class="close" id="arbChartClose">✕</span>
      </div>
      <div class="chart-wrap" style="height:200px">
        <canvas class="chart-canvas" id="arbChartCanvas"></canvas>
        <div class="chart-tip" id="arbChartTip"></div>
        <div class="chart-cross"></div>
        <button class="chart-expand-btn" data-tip="Expand chart">⤢</button>
      </div>
      <div class="chart-stats" id="arbChartStats" style="margin-top:6px"></div>
    </div>
  </div>
  <!-- Expanded chart modal (LP + ARB) -->
  <div id="chartExpandModal" class="hidden">
    <div class="chart-expand-box">
      <div class="chart-expand-head">
        <h3 id="chartExpandTitle"></h3>
        <span class="close" id="chartExpandClose">✕</span>
      </div>
      <div class="chart-wrap" style="height:320px">
        <canvas class="chart-canvas" id="chartExpandCanvas"></canvas>
        <div class="chart-tip" id="chartExpandTip"></div>
        <div class="chart-cross"></div>
      </div>
      <div class="chart-stats" id="chartExpandStats" style="margin-top:6px"></div>
    </div>
  </div>
</main>

<script>
const $ = s => document.querySelector(s);
const COL_LAYOUT_VERSION = 6;

// Tax / broker are shown to the user as percent (4.5) but stored & sent to the
// backend as fractions (0.045). Convert at the input boundary only.
function pctToFrac(v){ const n=parseFloat(v); return isNaN(n)?"":String(n/100); }
function fracToPct(v){ const n=parseFloat(v); return isNaN(n)?"":String(+(n*100).toFixed(4)); }

// ── Shared utils ─────────────────────────────────────────────────────────
function fmtISK(n){
  if(n===null||n===undefined) return "-";
  const a=Math.abs(n);
  if(a>=1e9) return (n/1e9).toFixed(2)+"B";
  if(a>=1e6) return (n/1e6).toFixed(2)+"M";
  if(a>=1e3) return (n/1e3).toFixed(1)+"K";
  return Math.round(n).toLocaleString();
}
function fmtNum(n){ return (n===null||n===undefined)? "-" : Math.round(n).toLocaleString(); }
function fmtVol(n){ return (n===null||n===undefined)? "?" : n.toLocaleString(undefined,{maximumFractionDigits:1})+" m³"; }
function fmtSpread(s){ return s===null? "no bid" : Math.round(s)+"%"; }
// Days-to-clear. capped_profit===null is the "not fetched yet" sentinel (the
// background /api/liquidity call hasn't landed); daily_vol distinguishes "never
// traded" (null) from "history exists but no recent volume" (0).
const _SPIN = "<span class='spin'></span>";
function fmtDays(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(r.daily_vol===null) return "no data";
  if(r.daily_vol===0) return "∞";
  return v<1 ? "<1 d" : Math.round(v)+" d";
}
function fmtVolPerDay(v,r){
  if(!r.liq_loaded) return _SPIN;
  return v===null ? "no data" : fmtNum(v)+"/d";
}
// Suggested per-unit list price — needs market history, so it rides the same
// background /api/liquidity fill (spinner until it lands).
function fmtListPrice(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no data" : fmtISK(v);
}
// Age of the current cheapest sell order ("8h ago"). Also from the background
// fill (one live order-book call per type), so spinner until it lands.
function fmtFloorAge(v,r){
  if(!r.liq_loaded) return _SPIN;
  return (v===null||v===undefined) ? "no orders" : fmtAgo(v);
}
// Tradeability: 0–100 blend of liquidity + low-competition, color-graded red→green.
function fmtTrade(v,r){
  if(!r.liq_loaded) return _SPIN;
  if(v===null||v===undefined) return "—";
  return `<span style="color:hsl(${Math.round(v*1.2)},70%,58%);font-weight:600">${Math.round(v)}</span>`;
}
function fmtTs(epoch){
  if(!epoch) return "unknown";
  return fmtAgo(Math.round((Date.now()/1000)-epoch));
}
// A raw age in seconds → "8h ago" / "3d ago".
function fmtAgo(sec){
  if(sec===null||sec===undefined) return "unknown";
  sec=Math.round(sec);
  if(sec<5) return "just now";
  if(sec<60) return `${sec}s ago`;
  if(sec<3600) return `${Math.floor(sec/60)}m ago`;
  if(sec<86400) return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}
function setStatus(html,err){
  const s=$("#statusbar"); s.innerHTML=html; s.className=err?"err":"";
}

// ── localStorage persistence ──────────────────────────────────────────────
const LS_KEY='eve-scanner';
function saveLS(){
  try{
    localStorage.setItem(LS_KEY,JSON.stringify({
      corp:$("#corp").value,lp:$("#lp").value,
      maxspread:$("#maxspread").value,tax:pctToFrac($("#tax").value),broker:pctToFrac($("#broker").value),
      market:$("#market").value,
      sort_key:STATE.sort.key,sort_dir:STATE.sort.dir,
      col_widths:STATE.colw,col_order:STATE.colOrder,col_layout_v:COL_LAYOUT_VERSION,col_vis:STATE.colVis,
      hide_illiquid:STATE.hideIlliquid?'1':'0',
      hide_unaffordable:STATE.hideUnaffordable?'1':'0',
      trade_weight:STATE.tradeWeight,
      active_tab:ACTIVE_TAB,
      arb:{region:$("#arb-region").value,cross_station:$("#arb-cross").value,
        sales_tax:pctToFrac($("#arb-tax").value),min_isk:$("#arb-minisk").value,
        max_jumps:$("#arb-maxjumps").value,route_flag:$("#arb-route").value,
        avoid_lowsec:ARB.avoidLowsec?'1':'0'}
    }));
  }catch(e){}
}

// ── Tab switching ─────────────────────────────────────────────────────────
let ACTIVE_TAB = "lp";
function switchTab(tab){
  ACTIVE_TAB = tab;
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active", t.dataset.tab===tab));
  $("#lp-controls").classList.toggle("hidden", tab!=="lp");
  $("#arb-controls").classList.toggle("hidden", tab!=="arb");
  $("#ind-controls").classList.toggle("hidden", tab!=="ind");
  $("#lp-tablewrap").classList.toggle("hidden", tab!=="lp");
  $("#arb-tablewrap").classList.toggle("hidden", tab!=="arb");
  $("#ind-tablewrap").classList.toggle("hidden", tab!=="ind");
  if(tab!=="lp") closeDetail();
  setStatus("");
  document.title = tab==="lp" ? "EVE LP Store Scanner"
                : tab==="arb" ? "EVE Arbitrage Scanner" : "EVE Industry Planner";
  fetch(`/api/prefs?active_tab=${tab}`).catch(()=>{}); saveLS();
  if(tab==="ind" && !IND.groupsLoaded) loadIndGroups();
}
document.querySelectorAll(".tab").forEach(t=>{
  t.onclick = ()=>switchTab(t.dataset.tab);
});

// ══════════════════════════════════════════════════════════════════════════
// LP TAB
// ══════════════════════════════════════════════════════════════════════════
let STATE = {rows:[], sort:{key:"isk_per_lp_best", dir:-1}, ctx:{}, selOffer:null,
             colw:{}, colVis:{}, hideIlliquid:false, hideUnaffordable:false, lastScanData:null,
             tradeWeight:0.5,  // liquidity↔competition blend: 0=all competition, 1=all liquidity
             lotTrackerOpen:false, recipeOpen:false,
             shoppingOpen:true, costOpen:false, cargoOpen:false, saleOpen:false};

// Tradeability = a 0–100 blend of two raw signals, each scored by its rank
// against the other offers in this store (so there's no invented "good volume"
// constant): liquidity (higher daily_vol = better) and low competition (lower
// days_to_clear = better). STATE.tradeWeight sets the proportion. Recomputed
// here on every render and whenever the user changes the balance preset.
function computeTradeability(){
  const loaded=STATE.rows.filter(r=>r.liq_loaded && r.daily_vol!==null);
  if(!loaded.length){ STATE.rows.forEach(r=>r.tradeability=null); return; }
  const vols=loaded.map(r=>r.daily_vol);
  const days=loaded.map(r=> r.days_to_clear===null ? Infinity : r.days_to_clear);
  const w=STATE.tradeWeight;
  const pctRank=(arr,v,higherBetter)=>{
    const n=arr.length; if(n<=1) return 100;
    let beats=0;
    for(const x of arr){ if(x===v) continue; if(higherBetter? v>x : v<x) beats++; }
    return beats/(n-1)*100;
  };
  for(const r of STATE.rows){
    if(!r.liq_loaded || r.daily_vol===null){ r.tradeability=null; continue; }
    const liq=pctRank(vols, r.daily_vol, true);
    const comp=pctRank(days, r.days_to_clear===null?Infinity:r.days_to_clear, false);
    r.tradeability=w*liq + (1-w)*comp;
  }
}
let LP_RESIZING = false;

const fmtIpl = v => (v===null||v===undefined) ? "—" : v.toLocaleString(undefined,{maximumFractionDigits:1});
const COLS = [
  {k:"name",               t:"Reward Item",     w:220, defvis:true,  tip:"The item this LP offer gives you.  * = a required input has no Jita price  ·  ^ = costs Analysis Kredits  ·  ! = illiquid (spread ≥25%)"},
  {k:"isk_per_lp_patient", t:"List ISK/LP",        w:100, defvis:true,  tip:"Profit per Loyalty Point if you LIST a sell order at the ask and wait (pay sales tax + broker fee).", f:fmtIpl, pn:true},
  {k:"isk_per_lp_instant", t:"Instant-sell ISK/LP",w:120, defvis:true,  tip:"Profit per Loyalty Point if you INSTANT-SELL into a buy order at the bid (pay sales tax only).", f:fmtIpl, pn:true},
  {k:"total_profit_patient",t:"List profit",       w:105, defvis:true,  tip:"Total profit across your whole LP budget, listing sell orders at the ask.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"total_profit_instant",t:"Instant-sell profit",w:120, defvis:true,  tip:"Total profit across your whole LP budget, instant-selling into buy orders.", f:(v,r)=>r.max_units===0?"—":(v===null?"—":fmtISK(v)), pn:true, rowCtx:true},
  {k:"tradeability", t:"Tradeability",  w: 95, defvis:true,  tip:"0–100: how realistically you can sell at your price. Blends liquidity (Daily Vol) and low competition (Days to Clear), weighted by the Balance buttons. Higher is better; ranked within this store.", f:fmtTrade, rowCtx:true, cls:"spread"},
  {k:"daily_vol",    t:"Daily Vol",     w: 90, defvis:true,  tip:"Units traded per day at the hub (30-day median). High = deep market you can sell into; low = thin and hard to offload.", f:fmtVolPerDay, rowCtx:true},
  {k:"days_to_clear",t:"Days to Clear", w: 95, defvis:true,  tip:"Sell-side backlog: units listed ÷ units sold per day. “5 d” = 5 days of stock ahead of you. <1 d sells fast; ∞ = barely trades.", f:fmtDays, rowCtx:true, cls:"spread"},
  {k:"spread_pct",   t:"Spread",        w: 70, defvis:true,  tip:"Ask vs bid gap. ≥25% (!) means the ask isn't backed by real buyers — the patient (sell) figure is unreliable, prefer the buy column.", f:fmtSpread, cls:"spread"},
  {k:"max_units",    t:"Max Runs",      w: 80, defvis:true,  tip:"Redemptions your LP budget affords (budget ÷ LP per run). Affordability only — it doesn't check whether the market can absorb them.", f:v=>v===0?"—":fmtNum(v)},
  {k:"lp_cost",      t:"LP / Run",      w: 80, defvis:true,  tip:"Loyalty Points per redemption.", f:fmtNum},
  {k:"cost_ea",      t:"ISK / Run",     w: 95, defvis:true,  tip:"ISK + required input costs per redemption.", f:fmtISK},
  {k:"list_price",   t:"List @",        w:100, defvis:true,  tip:"Suggested per-unit price to put on your sell order: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value. Per unit of the reward item.", f:fmtListPrice, rowCtx:true},
  {k:"floor_age",    t:"Floor age",     w: 95, defvis:true,  tip:"How long ago the current cheapest sell order at the hub was posted (from its issued timestamp). A fresh floor in a thin market means the price is actively moving. “no orders” = nothing listed.", f:fmtFloorAge, rowCtx:true, cls:"spread"},
  {k:"ask",          t:"Ask (sell)",    w: 95, defvis:false, tip:"Lowest sell order price at the hub — what the patient column lists at.", f:fmtISK},
  {k:"bid",          t:"Bid (buy)",     w: 95, defvis:false, tip:"Highest buy order price at the hub — what the instant column dumps into.", f:fmtISK},
  {k:"buy_volume",   t:"Buy Demand",    w: 95, defvis:false, tip:"Units on hub buy orders — how many you could sell instantly.", f:fmtNum},
  {k:"qty",          t:"Units",         w: 55, defvis:false, tip:"Units per redemption.", f:fmtNum},
  {k:"output_volume",t:"Vol m³",        w:140, defvis:false, tip:"Packaged m³ per redemption, and total for all runs in parentheses.", f:(v,r)=>{ if(v===null) return "?"; const per=fmtVol(v); return r.max_units>0?`${per} (${fmtVol(v*r.max_units)})`:per; }, rowCtx:true},
];
COLS.forEach(c=>{ STATE.colVis[c.k]=c.defvis; STATE.colw[c.k]=c.w; });
const COL_BY_KEY=Object.fromEntries(COLS.map(c=>[c.k,c]));
STATE.colOrder=COLS.map(c=>c.k);  // user-reorderable; persisted with col widths
// Resolve STATE.colOrder to column objects, dropping unknown keys and appending
// any columns that aren't listed yet (so a saved order survives COLS additions).
function orderedCols(){
  const seen=new Set(), out=[];
  for(const k of STATE.colOrder){ const c=COL_BY_KEY[k]; if(c&&!seen.has(k)){ out.push(c); seen.add(k); } }
  for(const c of COLS) if(!seen.has(c.k)){ out.push(c); seen.add(c.k); }
  return out;
}
function visCols(){ return orderedCols().filter(c=>STATE.colVis[c.k]!==false); }

function lpSetColgroup(){
  $("#cg").innerHTML=visCols().map(c=>`<col style="width:${STATE.colw[c.k]||c.w}px">`).join("");
}

function startLPResize(e, key){
  e.preventDefault(); e.stopPropagation();
  LP_RESIZING=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#tbl").style.tableLayout="fixed";
  const startX=e.clientX, startW=STATE.colw[key]||80;
  function mm(ev){ STATE.colw[key]=Math.max(40,startW+(ev.clientX-startX)); lpSetColgroup(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    saveLPColWidths();
    setTimeout(()=>{ LP_RESIZING=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

// ── Column drag-to-reorder ────────────────────────────────────────────────
// HTML5 drag-and-drop on the <th>s. The resizer's mousedown preventDefault()
// suppresses a drag starting from the resize grip, and a sort-click never fires
// after a real drag, so the three header interactions stay independent.
let LP_DRAG_KEY=null;
function clearLPDropMarks(){
  document.querySelectorAll("#tbl thead th").forEach(th=>th.classList.remove("drop-before","drop-after"));
}
function lpDropAfter(th,clientX){
  const r=th.getBoundingClientRect();
  return clientX > r.left + r.width/2;
}
function reorderLPCols(srcKey,dstKey,after){
  if(!srcKey||srcKey===dstKey) return;
  const order=orderedCols().map(c=>c.k);   // full order, hidden cols included
  order.splice(order.indexOf(srcKey),1);
  let to=order.indexOf(dstKey);
  if(after) to+=1;
  order.splice(to,0,srcKey);
  STATE.colOrder=order;
  saveLPColWidths();   // col_order rides along with widths under the same version
  renderTable();
}
function wireLPColDrag(th){
  th.addEventListener("dragstart",e=>{
    LP_DRAG_KEY=th.dataset.k;
    e.dataTransfer.effectAllowed="move";
    try{ e.dataTransfer.setData("text/plain",LP_DRAG_KEY); }catch(_){}
    th.classList.add("col-dragging");
    document.body.classList.add("col-dragging-active");
  });
  th.addEventListener("dragend",()=>{
    th.classList.remove("col-dragging");
    document.body.classList.remove("col-dragging-active");
    clearLPDropMarks();
    setTimeout(()=>{ LP_DRAG_KEY=null; },0);
  });
  th.addEventListener("dragover",e=>{
    if(!LP_DRAG_KEY) return;
    e.preventDefault();
    e.dataTransfer.dropEffect="move";
    clearLPDropMarks();
    if(th.dataset.k!==LP_DRAG_KEY)
      th.classList.add(lpDropAfter(th,e.clientX)?"drop-after":"drop-before");
  });
  th.addEventListener("dragleave",()=>th.classList.remove("drop-before","drop-after"));
  th.addEventListener("drop",e=>{
    e.preventDefault();
    const after=lpDropAfter(th,e.clientX);
    clearLPDropMarks();
    reorderLPCols(LP_DRAG_KEY, th.dataset.k, after);
  });
}

function renderTable(){
  computeTradeability();
  const thead=$("#tbl thead"), tbody=$("#tbl tbody");
  const vc=visCols();
  $("#tbl").style.tableLayout="fixed";
  lpSetColgroup();
  thead.innerHTML="<tr>"+vc.map(c=>{
    const active=STATE.sort.key===c.k;
    const arrow=active?(STATE.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th draggable="true" data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(LP_RESIZING){ LP_RESIZING=false; return; }
      if(LP_DRAG_KEY){ return; }  // tail end of a reorder, not a sort click
      const k=th.dataset.k;
      if(STATE.sort.key===k) STATE.sort.dir*=-1;
      else STATE.sort={key:k, dir:k==="name"?1:-1};
      saveLPSort(); renderTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startLPResize(e,vc[i].k));
    wireLPColDrag(th);
  });
  const rows=[...STATE.rows]
    .filter(r=>!STATE.hideIlliquid||!r.illiquid)
    .filter(r=>!STATE.hideUnaffordable||r.max_units>0)
    .sort((a,b)=>{
      const k=STATE.sort.key, d=STATE.sort.dir;
      let x=a[k], y=b[k];
      if(typeof x==="string") return x.localeCompare(y)*d;
      if(x===null) x=-Infinity; if(y===null) y=-Infinity;
      return (x-y)*d;
    });
  tbody.innerHTML=rows.map(r=>{
    const tds=vc.map(c=>{
      let v=r[c.k], txt=c.f?(c.rowCtx?c.f(v,r):c.f(v)):v;
      let cls=c.cls||"";
      if(c.k==="spread_pct"&&v!==null) cls+=v<10?" tight":v<25?" mid":"";
      if(c.k==="name"){
        let flag=""; if(r.req_missing) flag+="*"; if(r.ak_cost) flag+="^"; if(r.illiquid) flag+="!";
        txt=txt+(flag?` <span class="flag">${flag}</span>`:"");
      }
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      // Mark the better of the two sell-mode cells so the comparison reads at a glance.
      if((c.k==="isk_per_lp_patient"||c.k==="isk_per_lp_instant")
         && r.isk_per_lp_best!==null && v!==null && v===r.isk_per_lp_best) cls+=" win";
      if((c.k==="total_profit_patient"||c.k==="total_profit_instant")
         && r.total_profit_best!==null && v!==null && v===r.total_profit_best && r.max_units>0) cls+=" win";
      return `<td class="${cls}">${txt}</td>`;
    }).join("");
    return `<tr class="${r.illiquid?'illiquid':''} ${r.offer_id===STATE.selOffer?'sel':''}" data-id="${r.offer_id}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr=>tr.onclick=()=>openDetail(+tr.dataset.id));
}

async function scan(forceRefresh=false){
  const corp=$("#corp").value.trim();
  if(!corp){ setStatus("Enter a corporation name.",true); return; }
  const btn=$("#refresh");
  if(forceRefresh){ btn.disabled=true; btn.textContent="⟳ Fetching…"; }
  setStatus("Scanning "+corp+(forceRefresh?" (refreshing from ESI)":"")+" …");
  STATE.ctx={lp:$("#lp").value, tax:pctToFrac($("#tax").value), broker:pctToFrac($("#broker").value), station:$("#market").value};
  const p=new URLSearchParams({corp, ...STATE.ctx});
  const ms=$("#maxspread").value.trim(); if(ms) p.set("max_spread",ms);
  if(forceRefresh) p.set("refresh","1");
  try{
    const res=await fetch("/api/scan?"+p);
    const data=await res.json();
    if(data.error){ setStatus(data.error,true); return; }
    STATE.rows=data.rows; STATE.ctx.corp_id=data.corp_id; STATE.selOffer=null;
    STATE.lastScanData=data; closeDetail(); renderLPStatus(); renderTable();
    fillLiquidity();
  }catch(e){ setStatus("Request failed: "+e,true); }
  finally{ btn.disabled=false; btn.textContent="⟳ Refresh"; }
}

// Background-fill the market-saturation columns (Days to Clear / Capped Profit)
// after the table is already on screen. One history call per type server-side,
// so this can take a few seconds on a fresh corp; rows show "…" until it lands.
async function fillLiquidity(){
  const corpId=STATE.ctx.corp_id; if(!corpId) return;
  const p=new URLSearchParams({corp_id:corpId, lp:STATE.ctx.lp,
    tax:STATE.ctx.tax, broker:STATE.ctx.broker, station:STATE.ctx.station});
  try{
    const d=await (await fetch("/api/liquidity?"+p)).json();
    if(d.error||!d.liquidity) return;
    if(STATE.ctx.corp_id!==corpId) return;  // user re-scanned; drop stale fill
    const liq=d.liquidity;
    for(const r of STATE.rows){
      const e=liq[r.offer_id];
      if(e){ r.daily_vol=e.daily_vol; r.days_to_clear=e.days_to_clear; r.list_price=e.list_price; r.floor_age=e.floor_age; r.liq_loaded=true; }
    }
    renderTable();
    if(STATE.detail&&STATE.selOffer) renderDetail();
  }catch(e){ /* leave the "…" placeholders; non-fatal */ }
}

function renderLPStatus(){
  const d=STATE.lastScanData; if(!d||ACTIVE_TAB!=="lp") return;
  setStatus(
    `<span class="pill"><b>${d.corp_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> offers</span>`
    +`<span class="pill"><b>${Number(d.lp).toLocaleString()}</b> LP · list vs instant sell</span>`
    +`<span class="ts">offers ${fmtTs(d.offers_fetched_at)} · prices ${fmtTs(d.scanned_at)}</span>`);
}

function saveLPSort(){
  const s=STATE.sort;
  fetch(`/api/prefs?sort_key=${encodeURIComponent(s.key)}&sort_dir=${s.dir}`).catch(()=>{}); saveLS();
}
function saveLPColWidths(){
  fetch(`/api/prefs?col_widths=${encodeURIComponent(JSON.stringify(STATE.colw))}`
    +`&col_order=${encodeURIComponent(JSON.stringify(STATE.colOrder))}`
    +`&col_layout_v=${COL_LAYOUT_VERSION}`).catch(()=>{}); saveLS();
}

// ── Column picker ─────────────────────────────────────────────────────────
(function(){
  const btn=document.getElementById("colPickerBtn");
  const picker=document.getElementById("colPicker");
  function renderPicker(){
    picker.innerHTML=COLS.map(c=>`<label><input type="checkbox" data-k="${c.k}"${STATE.colVis[c.k]!==false?' checked':''}> ${c.t}</label>`).join("");
    picker.querySelectorAll("input").forEach(cb=>{
      cb.onchange=()=>{ STATE.colVis[cb.dataset.k]=cb.checked; renderTable(); saveLS(); };
    });
  }
  btn.onclick=e=>{
    e.stopPropagation();
    if(!picker.classList.contains("hidden")){ picker.classList.add("hidden"); return; }
    renderPicker();
    const r=btn.getBoundingClientRect();
    picker.style.top=(r.bottom+4)+"px";
    picker.style.left=r.left+"px";
    picker.classList.remove("hidden");
  };
  document.addEventListener("click",()=>picker.classList.add("hidden"));
  picker.addEventListener("click",e=>e.stopPropagation());
})();

// ── LP detail panel ───────────────────────────────────────────────────────
async function openDetail(offerId){
  STATE.selOffer=offerId; STATE.recipeOpen=false; renderTable();
  const p=new URLSearchParams({corp_id:STATE.ctx.corp_id, offer_id:offerId,
    lp:STATE.ctx.lp, tax:STATE.ctx.tax, broker:STATE.ctx.broker,
    station:STATE.ctx.station});
  const inner=$("#detail .inner");
  inner.innerHTML="<div class='muted'>Loading volumes…</div>";
  $("#detail").classList.add("open");
  try{
    const d=await (await fetch("/api/detail?"+p)).json();
    if(d.error){ inner.innerHTML=`<span style='color:var(--red)'>${d.error}</span>`; return; }
    STATE.detail=d; renderDetail();
  }catch(e){ inner.innerHTML=`<span style='color:var(--red)'>${e}</span>`; }
}
function closeDetail(){ $("#detail").classList.remove("open"); STATE.selOffer=null; }

function renderDetail(){
  const d=STATE.detail;
  const def=Math.max(d.max_units||0,1);
  const inner=$("#detail .inner");
  inner.innerHTML=`
    <div class="dheader">
      <div><h2>${d.output.name}</h2>
        <div class="sub">${d.output.quantity}× per redemption · offer #${d.offer_id} ·
          list vs instant sell</div>
      </div>
      <span class="close" id="closeBtn">✕</span>
    </div>
    <div class="chart-wrap"><canvas class="chart-canvas" id="detailChart"></canvas><div class="chart-tip" id="detailChartTip"></div><div class="chart-cross"></div><button class="chart-expand-btn" data-tip="Expand chart">⤢</button></div>
    <div class="chart-stats" id="detailChartStats"></div>
    <div class="redrow">
      <label>Redemptions</label>
      <input id="reds" type="number" min="1" value="${def}">
      <span class="maxlink">max LP affords: <a href="#" id="maxLink">${fmtNum(d.max_units)}</a></span>
    </div>
    <div id="dbody"></div>`;
  $("#closeBtn").onclick=closeDetail;
  $("#reds").oninput=renderBody;
  const ml=$("#maxLink");
  if(ml) ml.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(d.max_units,1); renderBody(); };
  renderBody();
  const regionId=_STATION_TO_REGION[parseInt(STATE.ctx.station)]||10000002;
  requestAnimationFrame(()=>{
    const c=document.getElementById('detailChart');
    if(c) _attachChart(c,document.getElementById('detailChartTip'),document.getElementById('detailChartStats'),d.output.type_id,regionId,d.ask||d.bid||null,d.output.name);
  });
}

function walkBook(book, qty){
  let need=qty, cost=0, filled=0, last=null;
  for(const lvl of (book||[])){
    if(need<=0) break;
    const take=Math.min(need,lvl[1]);
    cost+=take*lvl[0]; filled+=take; need-=take; last=lvl[0];
  }
  return {cost, filled, avg:filled>0?cost/filled:null, shortBy:Math.max(0,qty-filled), lastPrice:last};
}

function bindLotCalcs(savedLots){
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{
    const tid=row.dataset.tid;
    const need=parseInt(row.dataset.need)||0;
    const tagsEl=row.querySelector(".lot-tags");
    const numEl=row.querySelector(".lot-num");
    const sumEl=row.querySelector(".lot-sum");
    row._lotNums=(savedLots&&savedLots[tid])?[...savedLots[tid]]:[];

    function renderChips(){
      tagsEl.innerHTML=row._lotNums.map((v,i)=>
        `<span class="lot-tag">${fmtNum(v)}<span class="rm" data-i="${i}">×</span></span>`
      ).join("");
      tagsEl.querySelectorAll(".rm").forEach(rm=>{
        rm.onclick=()=>{ row._lotNums.splice(+rm.dataset.i,1); renderChips(); };
      });
      const tot=row._lotNums.reduce((a,b)=>a+b,0);
      if(!row._lotNums.length){ sumEl.textContent=""; return; }
      const rem=need-tot;
      if(rem<=0){ sumEl.textContent=`${fmtNum(tot)} ✓`; sumEl.style.color="var(--green2)"; }
      else { sumEl.textContent=`${fmtNum(tot)} · ${fmtNum(rem)} more`; sumEl.style.color="var(--yellow)"; }
    }

    numEl.addEventListener("keydown",e=>{
      if(e.key==="Enter"||e.key===" "){
        e.preventDefault();
        const v=parseInt(numEl.value);
        if(v>0){ row._lotNums.push(v); numEl.value=""; renderChips(); }
      }
    });
    renderChips();
  });
  const toggle=document.getElementById("lotTrackerToggle");
  if(toggle) toggle.onclick=()=>{
    STATE.lotTrackerOpen=!STATE.lotTrackerOpen;
    toggle.textContent=(STATE.lotTrackerOpen?"▼":"▶")+" Lot tracker";
    document.querySelector(".lot-tracker").style.display=STATE.lotTrackerOpen?"":"none";
  };
  const recipeToggle=document.getElementById("recipeToggle");
  if(recipeToggle) recipeToggle.onclick=()=>{
    STATE.recipeOpen=!STATE.recipeOpen;
    recipeToggle.textContent=(STATE.recipeOpen?"▼":"▶")+" Base Recipe (1× redemption)";
    document.querySelector(".recipe-list").style.display=STATE.recipeOpen?"":"none";
  };
  ["shoppingToggle","costToggle","cargoToggle","saleToggle"].forEach((id,i)=>{
    const keys=["shoppingOpen","costOpen","cargoOpen","saleOpen"];
    const el=document.getElementById(id);
    if(!el) return;
    const labelText=el.textContent.replace(/^[▼▶] /,"");
    el.onclick=()=>{
      const key=keys[i];
      STATE[key]=!STATE[key];
      el.textContent=(STATE[key]?"▼":"▶")+" "+labelText;
      document.querySelector(`[data-sec="${id}"]`).style.display=STATE[key]?"":"none";
    };
  });
}

function renderBody(){
  const d=STATE.detail;
  const n=Math.max(1,parseInt($("#reds").value||"1"));
  const tax=parseFloat(STATE.ctx.tax)||0.045, broker=parseFloat(STATE.ctx.broker)||0.015;
  const hub=(STATE.lastScanData&&STATE.lastScanData.station_name)||"the selected hub";
  const pn=v=>v>0?"pos":(v<0?"neg":"");
  const savedLots={};
  document.querySelectorAll(".lot-row[data-tid]").forEach(row=>{ if(row._lotNums&&row._lotNums.length) savedLots[row.dataset.tid]=[...row._lotNums]; });
  let reqCost=0, anyShort=false, reqVol=0, reqVolMissing=false;
  const reqRows=d.required_items.map(it=>{
    const need=it.quantity*n;
    const w=walkBook(it.book,need);
    const remPrice=w.lastPrice||it.unit_price||0;
    const line=w.cost+w.shortBy*remPrice;
    const noPrice=(it.unit_price===null&&w.filled===0);
    if(!noPrice) reqCost+=line;
    const short=w.shortBy>0; if(short) anyShort=true;
    if(it.line_volume===null) reqVolMissing=true; else reqVol+=it.line_volume*n;
    const vol=it.line_volume===null?'?':fmtVol(it.line_volume*n);
    return `<tr><td>${it.name}${short?' <span class="flag" data-tip="Not enough on market">!</span>':''}</td>
      <td>${fmtNum(need)}</td>
      <td>${w.avg===null?(it.unit_price===null?'<span class="flag">*</span>':fmtISK(it.unit_price)):fmtISK(w.avg)}</td>
      <td>${noPrice?'<span class="flag">?</span>':fmtISK(line)}</td>
      <td>${vol}</td></tr>`;
  }).join("");
  // Patient: list the whole reward quantity at the ask, pay sales tax + broker fee.
  const soldQtyP=d.output.quantity*n;
  const grossP=d.ask?soldQtyP*d.ask:null;
  const taxP=grossP===null?0:grossP*tax, brokerP=grossP===null?0:grossP*broker;
  const revenueP=grossP===null?null:grossP-taxP-brokerP;
  // Instant: walk down the live buy orders, pay sales tax only.
  const wI=walkBook(d.output.buy_book,d.output.quantity*n);
  const soldQtyI=wI.filled, sellShort=wI.shortBy>0;
  const grossI=(d.bid!==null&&soldQtyI>0)?wI.cost:null;
  const taxI=grossI===null?0:grossI*tax;
  const revenueI=grossI===null?null:grossI-taxI;

  const lpTot=d.lp_cost*n, isk_fee=d.isk_fee*n, cost=isk_fee+reqCost;
  const profitP=revenueP===null?null:revenueP-cost;
  const profitI=revenueI===null?null:revenueI-cost;
  const inVol=d.input_volume_per_redemption*n, outVol=(d.output_volume_per_redemption||0)*n;
  const pcls=v=>v===null?'':v>=0?'pos':'neg';
  let warn="";
  if(anyShort) warn+=`<div class="note">! Not enough sell orders at ${hub} for some required items.</div>`;
  if(sellShort) warn+=`<div class="note">Instant sell: only ${fmtNum(soldQtyI)} of ${fmtNum(d.output.quantity*n)} fit the current ${hub} buy orders.</div>`;
  if(d.spread_pct===null) warn+=`<div class="note bad">No buy orders exist — instant-sell can't fill and a listed sell order may never clear.</div>`;
  else if(d.spread_pct>=d.high_spread_pct) warn+=`<div class="note">${Math.round(d.spread_pct)}% spread — the ask isn't backed by real demand; the list figure is optimistic.</div>`;
  if(d.req_missing_price) warn+=`<div class="note">* A required item has no ${hub} price — true cost is higher.</div>`;

  const recipeItems=[];
  recipeItems.push(`
    <div class="recipe-list-item">
      <span class="name">Loyalty Points (LP)</span>
      <span class="val lp">${fmtNum(d.lp_cost)} LP</span>
    </div>`);
  if(d.isk_fee>0) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">Redemption ISK</span>
        <span class="val isk">${fmtISK(d.isk_fee)} ISK</span>
      </div>`);
  }
  for(const it of d.required_items) {
    recipeItems.push(`
      <div class="recipe-list-item">
        <span class="name">${it.name}</span>
        <span class="val">× ${fmtNum(it.quantity)}</span>
      </div>`);
  }
  const recipeHTML = `
    <h3 id="recipeToggle" style="cursor:pointer;user-select:none">${STATE.recipeOpen?'▼':'▶'} Base Recipe (1× redemption)</h3>
    <div class="recipe-list" style="${STATE.recipeOpen?'':'display:none'}">
      ${recipeItems.join("")}
    </div>`;

  const sec=(id, stateKey, label, content)=>`
    <h3 id="${id}" style="cursor:pointer;user-select:none">${STATE[stateKey]?'▼':'▶'} ${label}</h3>
    <div class="detail-section" data-sec="${id}" style="${STATE[stateKey]?'':'display:none'}">${content}</div>`;

  // Freshness of the current cheapest sell order — how recently the floor was
  // set and how thin the sell side is (fresh floor + few sellers = price moving).
  let freshHTML="";
  const sos=d.sell_order_stats;
  if(sos){
    const sellers=sos.sell_orders_total;
    const tie=sos.orders_at_best>1?` · ${sos.orders_at_best} orders tied at the floor`:"";
    freshHTML=`<p class="muted" style="margin:-4px 0 12px" data-tip="From each order's issued timestamp. The cheapest price has held for at least this long; later sellers undercut to match it.">Cheapest sell listed <b style="color:var(--fg)">${fmtAgo(sos.age_seconds)}</b>${tie} · ${fmtNum(sellers)} sell order${sellers===1?'':'s'} at ${hub}.</p>`;
  }

  $("#dbody").innerHTML=`
    <div class="kpis">
      <div class="kpi accent"><div class="l">List profit</div><div class="v ${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</div></div>
      <div class="kpi accent"><div class="l">Instant-sell profit</div><div class="v ${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</div></div>
      <div class="kpi" data-tip="Item cost + redemption ISK per ${n}× run${n>1?'s':''} (the LP cost is shown separately).">
        <div class="l">Item + ISK cost</div><div class="v">${fmtISK(cost)}</div></div>
      <div class="kpi"><div class="l">LP cost</div><div class="v">${fmtNum(lpTot)} LP</div></div>
      <div class="kpi" data-tip="Suggested per-unit sell-order price: the lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">
        <div class="l">Suggested list / unit</div><div class="v">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</div></div>
      <div class="kpi"><div class="l">Volume</div><div class="v">${fmtVol(Math.max(inVol||0,outVol||0))}</div></div>
    </div>
    ${warn}
    ${sec("shoppingToggle","shoppingOpen",`Shopping list — ${n}× redemption${n>1?'s':''}`,
      d.required_items.length?`<table class="mini"><thead><tr>
          <th style="text-align:left">Required item</th><th>Total qty</th><th>Avg unit</th><th>Line cost</th><th>Volume</th></tr></thead>
          <tbody>${reqRows}
          <tr class="total"><td>Total</td><td></td><td></td><td>${fmtISK(reqCost)}</td><td>${reqVolMissing?'?':fmtVol(reqVol)}</td></tr></tbody></table>
      <h3 id="lotTrackerToggle" style="cursor:pointer;user-select:none">${STATE.lotTrackerOpen?'▼':'▶'} Lot tracker</h3>
      <div class="lot-tracker" style="${STATE.lotTrackerOpen?'':'display:none'}">${d.required_items.map(it=>`
        <div class="lot-row" data-tid="${it.type_id}" data-need="${it.quantity*n}">
          <div class="lot-label">${it.name} <span class="lot-need">× ${fmtNum(it.quantity*n)} needed</span></div>
          <div class="lot-controls">
            <input type="number" class="lot-num" min="1" placeholder="qty" data-tip="Type a quantity, then press Enter or Space to add">
            <div class="lot-tags"></div>
            <span class="lot-sum"></span>
          </div>
        </div>`).join("")}
      </div>`
        :`<div class="muted">No required items — just LP + ISK.</div>`)}
    ${recipeHTML}
    ${sec("costToggle","costOpen","Cost breakdown",`
      <table class="mini"><tbody>
        <tr><td>Required items total</td><td>${fmtISK(reqCost)}</td></tr>
        <tr><td>Redemption ISK</td><td>${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td>Total acquisition cost</td><td>${fmtISK(cost)}</td></tr>
      </tbody></table>`)}
    ${sec("cargoToggle","cargoOpen","Cargo volume",`
      <table class="mini"><tbody>
        <tr><td style="text-align:left">Required items → LP corp station</td><td>${fmtVol(inVol)}</td></tr>
        <tr><td style="text-align:left">Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → ${hub}</td><td>${fmtVol(outVol)}</td></tr>
        <tr class="total"><td style="text-align:left">Ship cargo needed (larger leg)</td><td>${fmtVol(Math.max(inVol||0,outVol||0))}</td></tr>
      </tbody></table>`)}
    ${sec("saleToggle","saleOpen","Profit breakdown",`
      <div class="recipe-list-item" style="border:1px solid var(--line2);border-radius:6px;padding:9px 12px;margin-bottom:12px">
        <span class="name" data-tip="Per-unit price to put on your sell order. The lowest current sell, unless that's below the 30-day fair value (someone's dumping) — then it holds at fair value.">Suggested list price <span style="color:var(--dim2)">/ unit</span></span>
        <span class="val isk">${d.suggested_list===null?'—':fmtISK(d.suggested_list)}</span>
      </div>
      ${d.suggested_list===null?'':`<p class="muted" style="margin:-4px 0 6px">Lowest sell ${d.ask===null?'—':fmtISK(d.ask)} · 30-day fair value ${d.fair_price===null?'—':fmtISK(d.fair_price)}.</p>`}
      ${freshHTML}
      <table class="mini"><thead><tr>
        <th style="text-align:left"></th>
        <th data-tip="Sell value (listed at ask) — list the reward at the lowest sell order and pay sales tax + broker fee.">List<br><span style="color:var(--dim);font-weight:400">sell order</span></th>
        <th data-tip="Sell value (walking buy orders) — instant-sell the reward into the highest buy orders and pay sales tax only.">Instant sell<br><span style="color:var(--dim);font-weight:400">buy order</span></th>
      </tr></thead><tbody>
        <tr><td style="text-align:left">Sell value</td>
          <td>${grossP===null?'—':fmtISK(grossP)}</td>
          <td>${grossI===null?'—':fmtISK(grossI)}</td></tr>
        <tr><td style="text-align:left">− Sales tax (${(tax*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(taxP)}</td>
          <td class="neg">${grossI===null?'—':'−'+fmtISK(taxI)}</td></tr>
        <tr><td style="text-align:left">− Broker fee (${(broker*100).toFixed(1)}%)</td>
          <td class="neg">${grossP===null?'—':'−'+fmtISK(brokerP)}</td>
          <td style="color:var(--dim)">n/a</td></tr>
        <tr class="subtotal"><td style="text-align:left">Net revenue</td>
          <td>${revenueP===null?'—':fmtISK(revenueP)}</td>
          <td>${revenueI===null?'—':fmtISK(revenueI)}</td></tr>
        <tr><td style="text-align:left">− Items cost</td>
          <td class="neg">−${fmtISK(reqCost)}</td><td class="neg">−${fmtISK(reqCost)}</td></tr>
        <tr><td style="text-align:left">− Redemption ISK</td>
          <td class="neg">−${fmtISK(isk_fee)}</td><td class="neg">−${fmtISK(isk_fee)}</td></tr>
        <tr class="total"><td style="text-align:left">Profit</td>
          <td class="${pcls(profitP)}">${profitP===null?'—':fmtISK(profitP)}</td>
          <td class="${pcls(profitI)}">${profitI===null?'—':fmtISK(profitI)}</td></tr>
      </tbody></table>
      <p class="muted" style="margin-top:14px">Costs use the live ${hub} order book.
        List values the reward at the lowest sell order (sales tax + broker fee);
        instant-sell walks down the buy orders (sales tax only).</p>`)}`;
  bindLotCalcs(savedLots);
}

// LP control wiring
$("#go").onclick = ()=>scan(false);
$("#refresh").onclick = ()=>scan(true);
let ALL_CORPS=[], _corpsLoading=false, _corpsRetry=0;
async function _fetchCorps(){
  if(_corpsLoading||_corpsRetry>8) return;
  _corpsLoading=true;
  try{
    const r=await (await fetch("/api/corps")).json();
    if(Array.isArray(r)&&r.length){
      ALL_CORPS=r; _corpsRetry=0;
      if(document.activeElement===_corpInput&&_corpInput.value.length>=2)
        _corpOpen(_corpInput.value);
    } else {
      _corpsRetry++;
      setTimeout(_fetchCorps, 3000);
    }
  }catch(e){ _corpsRetry++; setTimeout(_fetchCorps,3000); }
  _corpsLoading=false;
}
_fetchCorps();

// ── Corp search dropdown ──────────────────────────────────────────────────
// Appended to <body> so no parent CSS interferes.
const _corpInput=$("#corp");
let _corpHi=-1;
const _corpDrop=document.createElement("div");
_corpDrop.className="corp-drop";
_corpDrop.style.display="none";
document.body.appendChild(_corpDrop);

function _corpClose(){ _corpDrop.style.display="none"; _corpHi=-1; }
function _corpItems(){ return _corpDrop.querySelectorAll(".corp-drop-item"); }

function _corpSelect(name){
  _corpInput.value=name; _corpClose();
  saveLS(); clearTimeout(lpScanTimer); scan(false);
}

function _corpOpen(q){
  if(!q||q.length<2){ _corpClose(); return; }
  if(!ALL_CORPS.length){ _fetchCorps(); }
  const lower=q.toLowerCase();
  const hits=ALL_CORPS.filter(c=>c.name.toLowerCase().includes(lower)).slice(0,20);
  _corpDrop.innerHTML = hits.length
    ? hits.map(c=>`<div class="corp-drop-item">${c.name.replace(/</g,"&lt;")}</div>`).join("")
    : `<div class="corp-drop-empty">${ALL_CORPS.length?'No match':'Loading corp list — retrying…'}</div>`;
  _corpDrop.querySelectorAll(".corp-drop-item").forEach(el=>{
    el.addEventListener("mousedown",e=>{ e.preventDefault(); _corpSelect(el.textContent); });
  });
  _corpHi=-1;
  const r=_corpInput.getBoundingClientRect();
  Object.assign(_corpDrop.style,{
    top:(r.bottom+3)+"px",
    left:r.left+"px",
    width:Math.max(240,r.width)+"px",
    display:"block"
  });
}

function _corpHighlight(idx){
  const items=_corpItems();
  items.forEach(el=>el.classList.remove("hi"));
  _corpHi=Math.max(-1,Math.min(idx,items.length-1));
  if(_corpHi>=0){ items[_corpHi].classList.add("hi"); items[_corpHi].scrollIntoView({block:"nearest"}); }
}

_corpInput.addEventListener("input",e=>_corpOpen(e.target.value));
_corpInput.addEventListener("blur",()=>setTimeout(_corpClose,150));
_corpInput.addEventListener("keydown",e=>{
  const items=_corpItems();
  if(e.key==="ArrowDown"){ e.preventDefault(); _corpHighlight(_corpHi+1); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); _corpHighlight(_corpHi-1); }
  else if(e.key==="Enter"){
    if(_corpHi>=0&&items[_corpHi]){ _corpSelect(items[_corpHi].textContent); }
    else{ clearTimeout(lpScanTimer); scan(false); }
  }
  else if(e.key==="Escape"){ _corpClose(); }
});
document.addEventListener("click",e=>{ if(!_corpInput.contains(e.target)&&!_corpDrop.contains(e.target)) _corpClose(); });
let lpScanTimer;
function scheduleScan(delay=800){ clearTimeout(lpScanTimer); lpScanTimer=setTimeout(()=>scan(false),delay); }
["#lp","#maxspread","#tax","#broker","#market"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change",()=>{ saveLS(); scheduleScan(800); });
  if(sel!=="#market") el.addEventListener("input",()=>{ saveLS(); scheduleScan(800); });
});
$("#toggleIlliquid").onchange=()=>{
  STATE.hideIlliquid=$("#toggleIlliquid").checked;
  fetch(`/api/prefs?hide_illiquid=${STATE.hideIlliquid?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
$("#toggleAffordable").onchange=()=>{
  STATE.hideUnaffordable=$("#toggleAffordable").checked;
  fetch(`/api/prefs?hide_unaffordable=${STATE.hideUnaffordable?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
// Tradeability balance presets — set the liquidity↔competition weight, re-rank.
function syncBalanceButtons(){
  document.querySelectorAll(".balance-btn").forEach(b=>
    b.classList.toggle("on", parseFloat(b.dataset.w)===STATE.tradeWeight));
}
document.querySelectorAll(".balance-btn").forEach(b=>{
  b.onclick=()=>{
    STATE.tradeWeight=parseFloat(b.dataset.w);
    syncBalanceButtons();
    fetch(`/api/prefs?trade_weight=${STATE.tradeWeight}`).catch(()=>{}); saveLS();
    renderTable();
  };
});
syncBalanceButtons();
setInterval(renderLPStatus, 30000);

// ══════════════════════════════════════════════════════════════════════════
// PRICE HISTORY CHART
// ══════════════════════════════════════════════════════════════════════════
const _STATION_TO_REGION = {
  60003760:10000002, 60008494:10000043,
  60004588:10000030, 60011866:10000032, 60005686:10000042,
};
const _histCache = {};
const _CHART_PAD = {t:18,r:76,b:20,l:6};

function _sma(vals, n){
  return vals.map((_,i)=>i<n-1?null:vals.slice(i-n+1,i+1).reduce((s,v)=>s+v,0)/n);
}

function _drawChart(canvas, hist, currentPrice){
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||560, H=canvas.offsetHeight||160;
  canvas.width=W*dpr; canvas.height=H*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,W,H);

  if(!hist.length){
    ctx.fillStyle='#5a7a95'; ctx.font='12px system-ui'; ctx.textAlign='center';
    ctx.fillText('No market history for this region',W/2,H/2); return;
  }

  const PAD=_CHART_PAD;
  const volH=Math.floor(H*.22);
  const priceH=H-PAD.t-PAD.b-volH-2;
  const cW=W-PAD.l-PAD.r;
  const n=hist.length;

  const avgs=hist.map(d=>d.average);
  const vols=hist.map(d=>d.volume);
  const maArr=_sma(avgs,30);
  const ath=Math.max(...avgs);
  const allP=[...avgs,...hist.map(d=>d.highest),...hist.map(d=>d.lowest)].filter(Boolean);
  if(currentPrice) allP.push(currentPrice);
  const pMin=Math.min(...allP)*.99, pMax=Math.max(...allP)*1.01;
  const vMax=Math.max(...vols)||1;

  const px=i=>PAD.l+(i/Math.max(n-1,1))*cW;
  const py=v=>PAD.t+priceH*(1-(v-pMin)/(pMax-pMin));
  const vy=v=>H-PAD.b-(v/vMax)*volH;

  // Grid
  ctx.strokeStyle='rgba(31,48,68,.9)'; ctx.lineWidth=.5;
  for(let i=0;i<=3;i++){
    const y=PAD.t+(priceH/3)*i;
    ctx.beginPath(); ctx.moveTo(PAD.l,y); ctx.lineTo(W-PAD.r,y); ctx.stroke();
  }

  // Reference lines (ATH and current price)
  ctx.save(); ctx.lineWidth=1;
  ctx.setLineDash([3,3]);
  ctx.strokeStyle='rgba(224,85,85,.55)';
  ctx.beginPath(); ctx.moveTo(PAD.l,py(ath)); ctx.lineTo(W-PAD.r,py(ath)); ctx.stroke();
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.strokeStyle='rgba(76,175,118,.55)';
    ctx.beginPath(); ctx.moveTo(PAD.l,py(currentPrice)); ctx.lineTo(W-PAD.r,py(currentPrice)); ctx.stroke();
  }
  ctx.restore();

  // Volume bars (green above MA, red below)
  const bw=Math.max(1,cW/n*.7);
  hist.forEach((d,i)=>{
    const above=maArr[i]===null||d.average>=maArr[i];
    ctx.fillStyle=above?'rgba(76,175,118,.28)':'rgba(224,85,85,.18)';
    const yTop=vy(d.volume);
    ctx.fillRect(px(i)-bw/2,yTop,bw,H-PAD.b-yTop);
  });

  // 30-day MA line
  ctx.save(); ctx.strokeStyle='#f0c040'; ctx.lineWidth=1.2;
  ctx.beginPath(); let maFirst=true;
  maArr.forEach((v,i)=>{
    if(v===null) return;
    if(maFirst){ctx.moveTo(px(i),py(v));maFirst=false;}
    else ctx.lineTo(px(i),py(v));
  });
  ctx.stroke(); ctx.restore();

  // Price area gradient fill
  const grad=ctx.createLinearGradient(0,PAD.t,0,PAD.t+priceH);
  grad.addColorStop(0,'rgba(79,195,247,.18)');
  grad.addColorStop(1,'rgba(79,195,247,.01)');
  ctx.beginPath();
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.lineTo(px(n-1),PAD.t+priceH); ctx.lineTo(px(0),PAD.t+priceH);
  ctx.closePath(); ctx.fillStyle=grad; ctx.fill();

  // Price line
  ctx.beginPath(); ctx.strokeStyle='#4fc3f7'; ctx.lineWidth=1.5;
  avgs.forEach((v,i)=>i===0?ctx.moveTo(px(i),py(v)):ctx.lineTo(px(i),py(v)));
  ctx.stroke();

  // Right-side labels
  ctx.font='9px system-ui'; ctx.textAlign='left';
  ctx.fillStyle='#e05555';
  ctx.fillText('ATH '+fmtISK(ath),W-PAD.r+3,py(ath)+3);
  if(currentPrice&&currentPrice>=pMin&&currentPrice<=pMax){
    ctx.fillStyle='#4caf76';
    ctx.fillText(fmtISK(currentPrice),W-PAD.r+3,py(currentPrice)+3);
  }
  const lastMA=maArr[n-1];
  if(lastMA){ ctx.fillStyle='#f0c040'; ctx.fillText('MA '+fmtISK(lastMA),W-PAD.r+3,py(lastMA)+3); }

  // X-axis date labels
  ctx.fillStyle='#3d5a70'; ctx.font='8px system-ui'; ctx.textAlign='center';
  const step=Math.ceil(n/5);
  for(let i=0;i<n;i+=step) ctx.fillText(hist[i].date.slice(5),px(i),H-PAD.b+10);
  if((n-1)%step!==0) ctx.fillText(hist[n-1].date.slice(5),px(n-1),H-PAD.b+10);
}

function _chartStats(hist, currentPrice){
  if(!hist.length) return '';
  const avgs=hist.map(d=>d.average);
  const ath=Math.max(...avgs);
  const lastMA=_sma(avgs,30).at(-1);
  const price=currentPrice||avgs.at(-1);
  const pctAth=ath>0?((price-ath)/ath*100):null;
  const pctMA=lastMA?((price-lastMA)/lastMA*100):null;
  let s=`<span data-tip="Latest sell price — the figure used for profit calculations.">`
    +`<span class="k">Current</span><span class="v" style="color:var(--cyan)">${fmtISK(price)}</span></span>`;
  if(pctAth!==null){
    const col=pctAth>=-3?'var(--red)':pctAth>=-15?'var(--yellow)':'var(--dim)';
    s+=`<span data-tip="All-time high daily average over the chart window, and how far current price sits below it.">`
      +`<span class="k">ATH</span><span class="v">${fmtISK(ath)}</span>`
      +`<span class="d" style="color:${col}">${pctAth.toFixed(1)}%</span></span>`;
  }
  if(pctMA!==null){
    const col=pctMA>=0?'var(--green2)':'var(--red)';
    s+=`<span data-tip="Current price vs the 30-day moving average. Positive means trading above trend.">`
      +`<span class="k">vs 30d MA</span><span class="v">${fmtISK(lastMA)}</span>`
      +`<span class="d" style="color:${col}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}% ${pctMA>=0?'▲':'▼'}</span></span>`;
  }
  return s;
}

async function _loadHistory(typeId, regionId){
  const k=`${typeId}_${regionId}`;
  if(!_histCache[k]){
    try{
      const d=await (await fetch(`/api/history?type_id=${typeId}&region_id=${regionId}`)).json();
      _histCache[k]=(d.history||[]).slice(-90);
    }catch{ _histCache[k]=[]; }
  }
  return _histCache[k];
}

async function _attachChart(canvas, tipEl, statsEl, typeId, regionId, currentPrice, title=''){
  canvas.style.opacity='.4';
  const hist=await _loadHistory(typeId, regionId);
  canvas.style.opacity='1';
  _drawChart(canvas, hist, currentPrice);
  if(statsEl) statsEl.innerHTML=_chartStats(hist, currentPrice);
  // Wire expand button if the parent wrap has one
  const expandBtn=canvas.parentElement&&canvas.parentElement.querySelector('.chart-expand-btn');
  if(expandBtn) expandBtn.onclick=()=>openExpandChart(typeId,regionId,currentPrice,title);
  if(!tipEl) return;
  const crossEl=canvas.parentElement&&canvas.parentElement.querySelector('.chart-cross');
  canvas.onmousemove=e=>{
    if(!hist.length) return;
    const r=canvas.getBoundingClientRect();
    const W=canvas.offsetWidth||r.width;
    // Map mouse X into the data drawing area (accounts for left/right padding)
    const drawW=W-_CHART_PAD.l-_CHART_PAD.r;
    const xInDraw=Math.max(0,Math.min(drawW,(e.clientX-r.left)-_CHART_PAD.l));
    const idx=Math.round(xInDraw/Math.max(drawW,1)*(hist.length-1));
    // Snap crosshair to the exact data-point x
    const crossX=_CHART_PAD.l+idx/Math.max(hist.length-1,1)*drawW;
    if(crossEl){crossEl.style.left=crossX+'px';crossEl.style.display='block';}
    const d=hist[idx];
    const ma=_sma(hist.map(h=>h.average),30)[idx];
    const pctMA=ma?((d.average-ma)/ma*100):null;
    const tx=Math.min(crossX+12,W-158);
    const ty=Math.max(2,e.clientY-r.top-75);
    tipEl.style.cssText=`display:block;left:${tx}px;top:${ty}px`;
    tipEl.innerHTML=`<div style="color:var(--dim);margin-bottom:2px">${d.date}</div>`
      +`<div>Avg <b style="color:var(--cyan)">${fmtISK(d.average)}</b></div>`
      +`<div>H/L ${fmtISK(d.highest)} / ${fmtISK(d.lowest)}</div>`
      +(ma?`<div>MA30 ${fmtISK(ma)} <span style="color:${pctMA>=0?'var(--green2)':'var(--red)'}">${pctMA>=0?'+':''}${pctMA.toFixed(1)}%</span></div>`:'')
      +`<div style="color:var(--dim)">Vol ${fmtNum(d.volume)}</div>`;
  };
  canvas.onmouseleave=()=>{
    tipEl.style.display='none';
    if(crossEl) crossEl.style.display='none';
  };
}

// ══════════════════════════════════════════════════════════════════════════
// ARB TAB
// ══════════════════════════════════════════════════════════════════════════
let ARB = {rows:[], sort:{key:"isk_opportunity", dir:-1}, colw:{}, lastData:null, avoidLowsec:false, es:null};
let ARB_RESIZING = false;

const ARB_COLS = [
  {k:"name",           t:"Item",        w:240, tip:"Item to flip."},
  {k:"sell_price",     t:"Ask",         w:120, tip:"Lowest sell order — what you pay to buy the item.", f:fmtISK},
  {k:"buy_price",      t:"Bid",         w:120, tip:"Highest buy order — what you receive when you sell instantly.", f:fmtISK},
  {k:"net_per_unit",   t:"Net/u",       w:105, tip:"Profit per unit after sales tax.", f:fmtISK, pn:true},
  {k:"margin_pct",     t:"Margin %",    w: 80, tip:"Net profit as % of ask price.", f:v=>v.toFixed(1)+"%", pn:true},
  {k:"flippable_qty",  t:"Qty",         w: 75, tip:"Units available (min of sell vol and buy vol).", f:fmtNum},
  {k:"isk_opportunity",t:"ISK Opp",     w:115, tip:"Total ISK profit if you flip all available units.", f:fmtISK, pn:true},
  {k:"total_volume",   t:"Vol m³",      w: 90, tip:"Total cargo volume for the flippable quantity.", f:v=>v===null?"?":fmtVol(v)},
  {k:"sell_station",   t:"From",        w:220, tip:"Station where you buy (sell order location)."},
  {k:"from_sec",       t:"Sec",         w: 52, tip:"Security status of From station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"from_sec_band"},
  {k:"buy_station",    t:"To",          w:220, tip:"Station where you deliver and sell.", cls:""},
  {k:"to_sec",         t:"Sec",         w: 52, tip:"Security status of To station's system.", f:v=>v===null?"?":v.toFixed(1), secBand:"to_sec_band"},
  {k:"jumps",          t:"Jumps",       w: 65, tip:"Jump count From→To (0 = same station).", f:fmtNum},
  {k:"risk",           t:"Risk",        w: 80, tip:"SAFE = all highsec. LOWSEC/NULLSEC = route touches lower security.", riskBand:"risk_band"},
];

function arbSetColgroup(){
  $("#arb-cg").innerHTML=ARB_COLS.map(c=>{
    const w=ARB.colw[c.k]; return `<col${w?` style="width:${w}px"`:""}>`;
  }).join("");
}

function startArbResize(e, key){
  e.preventDefault(); e.stopPropagation();
  ARB_RESIZING=true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#arb-tbl").style.tableLayout="fixed";
  const startX=e.clientX, startW=ARB.colw[key]||80;
  function mm(ev){ ARB.colw[key]=Math.max(40,startW+(ev.clientX-startX)); arbSetColgroup(); }
  function mu(){
    document.removeEventListener("mousemove",mm);
    document.removeEventListener("mouseup",mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    setTimeout(()=>{ ARB_RESIZING=false; },0);
  }
  document.addEventListener("mousemove",mm);
  document.addEventListener("mouseup",mu);
}

function renderArbTable(){
  const thead=$("#arb-tbl thead"), tbody=$("#arb-tbl tbody");
  const haveW=ARB_COLS.every(c=>ARB.colw[c.k]);
  $("#arb-tbl").style.tableLayout=haveW?"fixed":"auto";
  arbSetColgroup();
  thead.innerHTML="<tr>"+ARB_COLS.map(c=>{
    const active=ARB.sort.key===c.k;
    const arrow=active?(ARB.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(ARB_RESIZING){ ARB_RESIZING=false; return; }
      const k=th.dataset.k;
      if(ARB.sort.key===k) ARB.sort.dir*=-1;
      else ARB.sort={key:k, dir:k==="name"||k==="sell_station"||k==="buy_station"?1:-1};
      renderArbTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startArbResize(e,ARB_COLS[i].k));
  });
  if(!haveW){
    requestAnimationFrame(()=>{
      thead.querySelectorAll("th").forEach((th,i)=>{
        const c=ARB_COLS[i];
        ARB.colw[c.k]=ARB.colw[c.k]||c.w||Math.ceil(th.getBoundingClientRect().width);
      });
      $("#arb-tbl").style.tableLayout="fixed"; arbSetColgroup();
    });
  }
  const rows=[...ARB.rows].sort((a,b)=>{
    const k=ARB.sort.key, d=ARB.sort.dir;
    let x=a[k], y=b[k];
    if(typeof x==="string") return x.localeCompare(y)*d;
    if(x===null) x=-Infinity; if(y===null) y=-Infinity;
    return (x-y)*d;
  });
  tbody.innerHTML=rows.map((r,i)=>{
    const tds=ARB_COLS.map(c=>{
      let v=r[c.k], txt=c.f?c.f(v):(v===null||v===undefined?"-":v);
      let cls=c.cls||"";
      if(c.secBand) cls+=" sec-"+r[c.secBand];
      if(c.riskBand) cls+=" risk-"+r[c.riskBand];
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      const titleAttr=(c.k==="sell_station"||c.k==="buy_station")&&v?` data-tip="${String(v).replace(/"/g,'&quot;')}"` :"";
      return `<td class="${cls.trim()}"${titleAttr}>${txt}</td>`;
    }).join("");
    return `<tr style="cursor:pointer" data-ridx="${i}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach((tr,i)=>{
    tr.onclick=()=>{
      if(ARB_RESIZING){ARB_RESIZING=false;return;}
      openArbChart(rows[i]);
    };
  });
}

function renderArbStatus(){
  const d=ARB.lastData; if(!d||ACTIVE_TAB!=="arb") return;
  const mode=d.cross_station?`Cross-station ≤${d.max_jumps}J RT`:"Same-station";
  const stale = d.snap_expires && (Date.now()/1000) > d.snap_expires;
  const staleNote = stale
    ? ` <span style="color:var(--yellow);font-size:12px">· order book expired — click ⟳ Refresh for latest prices</span>`
    : "";
  setStatus(
    `<span class="pill"><b>${d.region_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> deals · <b>${d.total_spreads}</b> spreads · ${mode}</span>`
    +`<span class="ts">book ${fmtTs(d.snap_fetched_at)} · scan ${fmtTs(d.scanned_at)}</span>`
    +staleNote);
}

function showArbProgress(msg, sub, pct){
  $("#arb-tbl").classList.add("hidden");
  $("#arb-progress").classList.remove("hidden");
  $("#arb-prog-label").textContent = msg;
  $("#arb-prog-sub").textContent = sub || "";
  $("#arb-prog-fill").style.width = (pct || 0) + "%";
}
function hideArbProgress(){
  $("#arb-progress").classList.add("hidden");
  $("#arb-tbl").classList.remove("hidden");
}

function scanArb(){
  // Close any in-flight scan.
  if(ARB.es){ ARB.es.close(); ARB.es=null; }

  const btn=$("#arb-go");
  btn.disabled=true; btn.textContent="Scanning…";

  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    pctToFrac($("#arb-tax").value),
    min_isk:      $("#arb-minisk").value||"0",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });

  showArbProgress("Connecting to ESI…", "", 1);
  setStatus("Scanning…");

  const es = new EventSource("/api/arb/scan?"+p);
  ARB.es = es;

  es.onmessage = e => {
    let data;
    try{ data=JSON.parse(e.data); }catch(err){ return; }

    if(data.type==="progress"){
      showArbProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg + (data.sub ? " — "+data.sub : ""));

    } else if(data.type==="result"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      ARB.rows=data.rows; ARB.lastData=data;
      hideArbProgress();
      renderArbStatus(); renderArbTable();

    } else if(data.type==="error"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      hideArbProgress();
      setStatus(data.error, true);
    }
  };

  es.onerror = () => {
    es.close(); ARB.es=null;
    btn.disabled=false; btn.textContent="Scan";
    hideArbProgress();
    setStatus("Connection error — server may have stopped.", true);
  };
}

function saveArbPrefs(){
  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    pctToFrac($("#arb-tax").value),
    min_isk:      $("#arb-minisk").value||"",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });
  fetch("/api/arb/prefs?"+p).catch(()=>{}); saveLS();
}
function updateArbJumpsVisibility(){
  const cross=$("#arb-cross").value==="1";
  $("#arb-maxjumps-field").style.display=cross?"":"none";
}
$("#arb-cross").addEventListener("change",()=>{ updateArbJumpsVisibility(); saveArbPrefs(); });
["#arb-region","#arb-tax","#arb-minisk","#arb-maxjumps","#arb-route"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveArbPrefs);
  el.addEventListener("input", saveArbPrefs);
});
$("#arb-go").onclick=()=>scanArb();
$("#arb-toggleLowsec").onclick=()=>{
  ARB.avoidLowsec=!ARB.avoidLowsec;
  $("#arb-toggleLowsec").classList.toggle("active",ARB.avoidLowsec);
  saveArbPrefs();
  if(ARB.rows.length) scanArb(false);
};
setInterval(renderArbStatus, 30000);

function openExpandChart(typeId, regionId, currentPrice, title){
  document.getElementById('arbChartModal').classList.add('hidden');
  document.getElementById('chartExpandTitle').textContent=title||'';
  document.getElementById('chartExpandStats').textContent='';
  document.getElementById('chartExpandModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('chartExpandCanvas');
    if(c) _attachChart(c,document.getElementById('chartExpandTip'),document.getElementById('chartExpandStats'),typeId,regionId,currentPrice,title);
  });
}

function openArbChart(row){
  const regionId=parseInt($("#arb-region").value)||10000002;
  document.getElementById('arbChartTitle').textContent=row.name;
  document.getElementById('arbChartStats').textContent='';
  document.getElementById('arbChartModal').classList.remove('hidden');
  requestAnimationFrame(()=>{
    const c=document.getElementById('arbChartCanvas');
    if(c) _attachChart(c,document.getElementById('arbChartTip'),document.getElementById('arbChartStats'),row.type_id,regionId,row.sell_price||null,row.name);
  });
}
(()=>{
  const arbModal=document.getElementById('arbChartModal');
  const expModal=document.getElementById('chartExpandModal');
  document.getElementById('arbChartClose').onclick=()=>arbModal.classList.add('hidden');
  document.getElementById('chartExpandClose').onclick=()=>expModal.classList.add('hidden');
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){arbModal.classList.add('hidden');expModal.classList.add('hidden');}
  });
  arbModal.onclick=e=>{if(e.target===arbModal) arbModal.classList.add('hidden');};
  expModal.onclick=e=>{if(e.target===expModal) expModal.classList.add('hidden');};
})();

// ══════════════════════════════════════════════════════════════════════════
// INDUSTRY TAB
// ══════════════════════════════════════════════════════════════════════════
let IND = {rows:[], sort:{key:"isk_per_hour_best", dir:-1}, lastData:null, es:null,
           groupsLoaded:false, profiles:[]};

const fmtDur = s => {
  if(s===null||s===undefined) return "—";
  const h=Math.floor(s/3600), m=Math.round((s%3600)/60);
  return h>0 ? `${h}h ${m}m` : `${m}m`;
};
const fmtPct1 = v => (v===null||v===undefined) ? "—" : (v*100).toFixed(1)+"%";
const fmtDaysSell = v => (v===null||v===undefined) ? "—" : v.toFixed(1);

const IND_COLS = [
  {k:"product_name",       t:"Item",        w:230, tip:"The manufactured item. * = an input has no sell price at the source hub."},
  {k:"tech_level",         t:"T",           w: 40, tip:"Tech level.", f:v=>v?("T"+v):"—"},
  {k:"isk_per_hour_best",  t:"ISK/hr",      w:115, tip:"Profit per hour of manufacturing time — the headline 'worth it' number.", f:fmtISK, pn:true},
  {k:"profit_best",        t:"Profit/run",  w:110, tip:"Best-of patient/instant profit for one run.", f:fmtISK, pn:true},
  {k:"total_profit_best",  t:"Profit×N",    w:115, tip:"Profit across the whole batch (Runs).", f:fmtISK, pn:true},
  {k:"margin_best",        t:"Margin",      w: 75, tip:"Profit as a % of total cost.", f:fmtPct1, pn:true},
  {k:"build_time",         t:"Build",       w: 80, tip:"Time for one run after TE + skills.", f:fmtDur},
  {k:"total_cost",         t:"Cost/run",    w:105, tip:"Materials + job install + blueprint, per run.", f:fmtISK},
  {k:"bp_cost",            t:"BP cost",     w: 95, tip:"Blueprint cost per run (0 if you own it).", f:fmtISK},
  {k:"ask",                t:"Sell",        w:105, tip:"Item's lowest sell order at the source hub.", f:v=>v===null?"—":fmtISK(v)},
  {k:"input_volume",       t:"Cargo in",    w: 95, tip:"m³ of materials to haul in for the batch.", f:v=>v?fmtVol(v):"—"},
  {k:"output_volume",      t:"Cargo out",   w: 95, tip:"m³ of finished items to haul out for the batch.", f:v=>v?fmtVol(v):"—"},
  {k:"days_to_sell",       t:"Days to sell",w: 95, tip:"Batch size ÷ daily traded volume (top items only).", f:fmtDaysSell},
  {k:"buildable",          t:"Build?",      w: 65, tip:"Can every required skill (at the Skills level) make it?", f:v=>v?"✓":"✗"},
];

function renderIndTable(){
  const thead=$("#ind-tbl thead"), tbody=$("#ind-tbl tbody");
  thead.innerHTML="<tr>"+IND_COLS.map(c=>{
    const active=IND.sort.key===c.k;
    const arrow=active?(IND.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` data-tip="${c.tip.replace(/"/g,'&quot;')}"`:"";
    const w=c.w?` style="width:${c.w}px"`:"";
    return `<th data-k="${c.k}"${tip}${w}${active?' class="sorted"':''}>${c.t}${arrow}</th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach(th=>{
    th.onclick=()=>{
      const k=th.dataset.k;
      if(IND.sort.key===k) IND.sort.dir*=-1;
      else IND.sort={key:k, dir:k==="product_name"?1:-1};
      renderIndTable();
    };
  });
  const rows=[...IND.rows].sort((a,b)=>{
    const k=IND.sort.key, d=IND.sort.dir;
    let x=a[k], y=b[k];
    if(typeof x==="string") return String(x).localeCompare(String(y))*d;
    if(x===null||x===undefined) x=-Infinity;
    if(y===null||y===undefined) y=-Infinity;
    return (x-y)*d;
  });
  tbody.innerHTML=rows.map((r,i)=>{
    const tds=IND_COLS.map(c=>{
      let v=r[c.k], txt=c.f?c.f(v,r):(v===null||v===undefined?"—":v);
      if(c.k==="product_name" && r.missing_price) txt+=" *";
      let cls=c.cls||"";
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      if(c.k==="buildable") cls+=v?" pos":" neg";
      return `<td class="${cls.trim()}">${txt}</td>`;
    }).join("");
    return `<tr style="cursor:pointer" data-ridx="${i}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach((tr,i)=>{
    tr.onclick=()=>openIndDetail(rows[i]);
  });
}

function renderIndStatus(){
  const d=IND.lastData; if(!d||ACTIVE_TAB!=="ind") return;
  setStatus(
    `<span class="pill"><b>${d.count.toLocaleString()}</b> items · source <b>${d.station_name}</b></span>`
    +`<span class="pill">batch <b>${d.runs.toLocaleString()}</b> runs</span>`
    +`<span class="ts">scan ${fmtTs(d.scanned_at)}</span>`);
}

function showIndProgress(msg, sub, pct){
  $("#ind-tbl").classList.add("hidden");
  $("#ind-detail").classList.add("hidden");
  $("#ind-progress").classList.remove("hidden");
  $("#ind-prog-label").textContent=msg;
  $("#ind-prog-sub").textContent=sub||"";
  $("#ind-prog-fill").style.width=(pct||0)+"%";
}
function hideIndProgress(){
  $("#ind-progress").classList.add("hidden");
  $("#ind-tbl").classList.remove("hidden");
}

function indParams(extra){
  const p={
    market_group: $("#ind-group").value,
    station:      $("#ind-station").value,
    me:           $("#ind-me").value||"0",
    te:           $("#ind-te").value||"0",
    job_rate:     $("#ind-jobrate").value||"0",
    sales_tax:    $("#ind-tax").value||"0",
    broker:       $("#ind-broker").value||"0",
    runs:         $("#ind-runs").value||"1",
    amortize_runs:$("#ind-amort").value||"1",
    skills_level: $("#ind-skills").value||"0",
    bp_owned:     $("#ind-owned").checked?"1":"0",
    buildable_only:$("#ind-buildable").checked?"1":"0",
  };
  return new URLSearchParams(Object.assign(p, extra||{}));
}

function scanInd(refreshSde){
  if(IND.es){ IND.es.close(); IND.es=null; }
  const btn=$("#ind-go"); btn.disabled=true; btn.textContent="Scanning…";
  const p=indParams(refreshSde?{refresh_sde:"1"}:null);
  showIndProgress("Loading blueprint database…","",1);
  setStatus("Scanning…");
  const es=new EventSource("/api/ind/scan?"+p); IND.es=es;
  es.onmessage=e=>{
    let data; try{ data=JSON.parse(e.data); }catch(err){ return; }
    if(data.type==="progress"){
      showIndProgress(data.msg, data.sub||"", data.pct||0);
      setStatus(data.msg+(data.sub?" — "+data.sub:""));
    } else if(data.type==="result"){
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      IND.rows=data.rows; IND.lastData=data;
      hideIndProgress(); renderIndStatus(); renderIndTable();
    } else if(data.type==="error"){
      es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
      hideIndProgress(); setStatus(data.error, true);
    }
  };
  es.onerror=()=>{
    es.close(); IND.es=null; btn.disabled=false; btn.textContent="Scan";
    hideIndProgress(); setStatus("Connection error — server may have stopped.", true);
  };
}

function openIndDetail(row){
  const box=$("#ind-detail");
  box.classList.remove("hidden");
  box.innerHTML=`<div class="ind-d-head">Loading ${row.product_name}…</div>`;
  box.scrollIntoView({block:"nearest"});
  const p=indParams({blueprint_id:row.blueprint_id});
  fetch("/api/ind/detail?"+p).then(r=>r.json()).then(d=>{
    if(d.error){ box.innerHTML=`<div class="ind-d-head">${d.error}</div>`; return; }
    renderIndDetail(d);
  }).catch(()=>{ box.innerHTML=`<div class="ind-d-head">Failed to load detail.</div>`; });
}

function renderIndDetail(d){
  const isk=v=>v===null||v===undefined?"—":fmtISK(v);
  const mats=d.required_items.map(m=>
    `<tr><td>${m.name}</td><td class="num">${fmtNum(m.eff_qty)}</td>`
    +`<td class="num">${isk(m.unit_price)}</td><td class="num">${isk(m.line_cost)}</td>`
    +`<td class="num">${isk(m.line_cost_batch)}</td>`
    +`<td class="num">${m.line_volume_batch?fmtVol(m.line_volume_batch):"—"}</td></tr>`).join("");
  const tier=d.product.tech_level?("T"+d.product.tech_level):"";
  let invHtml="";
  if(d.invention){
    const iv=d.invention;
    const dcs=iv.datacores.map(c=>
      `<tr><td>${c.name}</td><td class="num">${fmtNum(c.quantity)}</td>`
      +`<td class="num">${isk(c.unit_price)}</td><td class="num">${isk(c.line_cost)}</td></tr>`).join("");
    invHtml=`
      <div class="ind-d-head" style="margin-top:10px">Invention (T2)</div>
      <div class="ind-d-grid">
        <span>Success probability</span><b>${(iv.probability*100).toFixed(1)}% (base ${(iv.base_probability*100).toFixed(1)}%)</b>
        <span>Runs per invented BPC</span><b>${fmtNum(iv.runs_per_bpc)}</b>
        <span>Invention cost / T2 run</span><b>${isk(iv.cost_per_run)}</b>
      </div>
      <table class="ind-d-mats"><thead><tr><th>Datacore</th><th class="num">Qty</th>
        <th class="num">Unit</th><th class="num">Line</th></tr></thead><tbody>${dcs}</tbody></table>`;
  }
  $("#ind-detail").innerHTML=`
    <div class="ind-d-head">
      <b>${d.product.name}</b> ${tier} · ${d.runs.toLocaleString()} run(s) · source ${d.station_name}
      <span class="ind-d-close" onclick="$('#ind-detail').classList.add('hidden')">✕</span>
    </div>
    <div class="ind-d-grid">
      <span>Material cost/run</span><b>${isk(d.material_cost)}</b>
      <span>Job install/run (EIV ${isk(d.eiv)} × ${(d.job_rate*100).toFixed(1)}%)</span><b>${isk(d.job_cost)}</b>
      <span>Blueprint cost/run</span><b>${isk(d.bp_cost)}</b>
      <span>Total cost/run</span><b>${isk(d.total_cost)}</b>
      <span>Sell @ ask (patient)</span><b>${isk(d.revenue_patient)}</b>
      <span>Profit/run — list</span><b class="${d.profit_patient>0?'pos':'neg'}">${isk(d.profit_patient)}</b>
      <span>Profit/run — instant</span><b class="${d.profit_instant>0?'pos':'neg'}">${isk(d.profit_instant)}</b>
      <span>Build time/run</span><b>${fmtDur(d.build_time)}</b>
      <span>Cargo in / out (batch)</span><b>${d.input_volume_batch?fmtVol(d.input_volume_batch):"—"} / ${d.output_volume_batch?fmtVol(d.output_volume_batch):"—"}</b>
    </div>
    <table class="ind-d-mats"><thead><tr><th>Material</th><th class="num">Qty/run</th>
      <th class="num">Unit</th><th class="num">Line/run</th><th class="num">Line×N</th>
      <th class="num">m³×N</th></tr></thead><tbody>${mats}</tbody></table>
    ${invHtml}`;
}

function loadIndGroups(){
  fetch("/api/ind/groups").then(r=>r.json()).then(d=>{
    if(!d.groups) return;
    const sel=$("#ind-group"), cur=sel.value;
    sel.innerHTML='<option value="all">All (slow)</option>'
      +d.groups.map(g=>`<option value="${g.id}">${g.name}</option>`).join("");
    sel.value=cur; IND.groupsLoaded=true;
  }).catch(()=>{});
}

// ── Tax profiles (named job-cost-rate presets) ──────────────────────
function renderIndProfiles(){
  const sel=$("#ind-profile");
  sel.innerHTML='<option value="">— custom —</option>'
    +IND.profiles.map((p,i)=>`<option value="${i}">${p.name}</option>`).join("");
}
function applyIndProfile(){
  const i=$("#ind-profile").value;
  if(i!==""&&IND.profiles[i]){ $("#ind-jobrate").value=IND.profiles[i].job_rate; saveIndPrefs(); }
}
function addIndProfile(){
  const name=prompt("Name this tax profile (e.g. Ikuchi, NPC station):");
  if(!name) return;
  IND.profiles.push({name, job_rate:$("#ind-jobrate").value||"6"});
  renderIndProfiles();
  $("#ind-profile").value=String(IND.profiles.length-1);
  saveIndPrefs();
}

function saveIndPrefs(){
  const p=indParams({
    profiles: JSON.stringify(IND.profiles),
    profile:  $("#ind-profile").value,
  });
  fetch("/api/ind/prefs?"+p).catch(()=>{}); saveLS();
}

// wiring
$("#ind-go").onclick=()=>scanInd(false);
$("#ind-refresh").onclick=()=>scanInd(true);
$("#ind-saveprofile").onclick=addIndProfile;
$("#ind-profile").addEventListener("change", applyIndProfile);
document.querySelectorAll(".ind-preset").forEach(b=>{
  b.onclick=()=>{ $("#ind-runs").value=b.dataset.n; saveIndPrefs(); };
});
["#ind-group","#ind-station","#ind-me","#ind-te","#ind-jobrate","#ind-tax",
 "#ind-broker","#ind-runs","#ind-amort","#ind-skills"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change", saveIndPrefs);
});
["#ind-owned","#ind-buildable"].forEach(sel=>$(sel).addEventListener("change", saveIndPrefs));

// ══════════════════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════════════════
updateArbJumpsVisibility();  // reflect default cross-station selection before settings load
async function loadSettings(){
  let s=null;
  try{ s=JSON.parse(localStorage.getItem(LS_KEY)); }catch(e){}
  if(!s){ try{ s=await (await fetch("/api/settings")).json(); }catch(e){} }
  if(s && Object.keys(s).length){
      if(s.corp) $("#corp").value=s.corp;
      if(s.lp)   $("#lp").value=s.lp;
      if(s.market) $("#market").value=s.market;
      const _ms=s.maxspread??s.max_spread; if(_ms!=null) $("#maxspread").value=_ms;
      if(s.tax)   $("#tax").value=fracToPct(s.tax);
      if(s.broker) $("#broker").value=fracToPct(s.broker);
      if(s.sort_key && COLS.some(c=>c.k===s.sort_key))
        STATE.sort={key:s.sort_key, dir:Number(s.sort_dir)===1?1:-1};
      if(s.col_widths && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          STATE.colw=(typeof s.col_widths==="string"?JSON.parse(s.col_widths):s.col_widths)||{};
        }catch(e){}
      }
      if(s.col_order && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          const ord=typeof s.col_order==="string"?JSON.parse(s.col_order):s.col_order;
          if(Array.isArray(ord)){
            const known=ord.filter(k=>COL_BY_KEY[k]);
            if(known.length) STATE.colOrder=known;  // orderedCols() appends any missing
          }
        }catch(e){}
      }
      if(s.hide_illiquid==="1"){ STATE.hideIlliquid=true; $("#toggleIlliquid").checked=true; }
      if(s.hide_unaffordable==="1"){ STATE.hideUnaffordable=true; $("#toggleAffordable").checked=true; }
      if(s.trade_weight!==undefined && s.trade_weight!==""){
        const tw=parseFloat(s.trade_weight);
        if([0.25,0.5,0.75].includes(tw)){ STATE.tradeWeight=tw; syncBalanceButtons(); }
      }
      if(s.col_vis && typeof s.col_vis==="object")
        COLS.forEach(c=>{ if(c.k in s.col_vis) STATE.colVis[c.k]=!!s.col_vis[c.k]; });
      // Arb settings
      const a=s.arb||{};
      if(a.region) $("#arb-region").value=a.region;
      if(a.cross_station==="0"||a.cross_station==="1") $("#arb-cross").value=a.cross_station;
      if(a.sales_tax) $("#arb-tax").value=fracToPct(a.sales_tax);
      if(a.min_isk)   $("#arb-minisk").value=a.min_isk;
      if(a.max_jumps) $("#arb-maxjumps").value=a.max_jumps;
      if(a.route_flag) $("#arb-route").value=a.route_flag;
      if(a.avoid_lowsec==="1"){
        ARB.avoidLowsec=true;
        $("#arb-toggleLowsec").classList.add("active");
      }
      updateArbJumpsVisibility();
      // Industry settings
      const ind=s.ind||{};
      if(ind.market_group) $("#ind-group").value=ind.market_group;
      if(ind.station) $("#ind-station").value=ind.station;
      if(ind.me!==undefined&&ind.me!=="") $("#ind-me").value=ind.me;
      if(ind.te!==undefined&&ind.te!=="") $("#ind-te").value=ind.te;
      if(ind.job_rate) $("#ind-jobrate").value=ind.job_rate;
      if(ind.sales_tax) $("#ind-tax").value=ind.sales_tax;
      if(ind.broker) $("#ind-broker").value=ind.broker;
      if(ind.runs) $("#ind-runs").value=ind.runs;
      if(ind.amortize_runs) $("#ind-amort").value=ind.amortize_runs;
      if(ind.skills_level!==undefined&&ind.skills_level!=="") $("#ind-skills").value=ind.skills_level;
      if(ind.bp_owned==="1") $("#ind-owned").checked=true;
      if(ind.buildable_only==="1") $("#ind-buildable").checked=true;
      if(ind.profiles){ try{ IND.profiles=JSON.parse(ind.profiles)||[]; }catch(e){} }
      renderIndProfiles();
      if(ind.profile) $("#ind-profile").value=ind.profile;
      // Restore last active tab
      if(s.active_tab==="arb") switchTab("arb");
      else if(s.active_tab==="ind") switchTab("ind");
  }
  // Auto-run LP scanner if corp is set
  if(ACTIVE_TAB==="lp" && $("#corp").value.trim()) scan(false);
}
// ── Custom tooltip engine ──────────────────────────────────────────
// Reads data-tip on any element and shows a themed, cursor-following
// tooltip instead of the browser's default title= popup.
(function(){
  const tip=document.createElement("div");
  tip.id="tooltip"; document.body.appendChild(tip);
  let cur=null;
  document.addEventListener("mousemove",e=>{
    const el=e.target.closest?e.target.closest("[data-tip]"):null;
    if(el){
      if(el!==cur){ cur=el; tip.textContent=el.getAttribute("data-tip"); tip.classList.add("show"); }
      const pad=14, w=tip.offsetWidth, h=tip.offsetHeight;
      let x=e.clientX+pad, y=e.clientY+pad;
      if(x+w>innerWidth-8)  x=Math.max(8, e.clientX-w-pad);
      if(y+h>innerHeight-8) y=Math.max(8, e.clientY-h-pad);
      tip.style.left=x+"px"; tip.style.top=y+"px";
    } else if(cur){ cur=null; tip.classList.remove("show"); }
  },{passive:true});
  document.addEventListener("mouseleave",()=>{ cur=null; tip.classList.remove("show"); });
  // Hide while scrolling/clicking so it never lingers in a stale spot.
  document.addEventListener("scroll",()=>{ if(cur){ cur=null; tip.classList.remove("show"); } }, true);
})();

loadSettings();
</script>
</body>
</html>""".replace("__VERSION__", __version__).replace("__FAVICON__", _FAVICON_B64)


def main():
    ap = argparse.ArgumentParser(description="EVE Market Tools web UI.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=get_npc_corps, daemon=True).start()
    print(f"EVE Market Tools running at {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
