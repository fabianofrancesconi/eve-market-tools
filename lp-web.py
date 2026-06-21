#!/usr/bin/env python3
"""
EVE Market Tools — unified web UI.

Two apps in one local server:
  • LP Store  — ranks LP-store offers by ISK/LP with drill-down shopping lists.
  • Arbitrage — scans a region for negative-spread (instant-flip) opportunities.

    pip install requests
    python lp-web.py            # opens http://localhost:8765
    python lp-web.py --port 9000 --no-browser
"""
__version__ = "1.0.8"

import argparse
import base64
import json
import sys
import threading
import time
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
from lp_core import (
    ESI, HEADERS, HIGH_SPREAD_PCT, LPError, build_detail, default_cache_dir,
    evaluate, fetch_orderbook_jita, fetch_prices, get_offers, load_json,
    resolve_corp_id, resolve_corp_name, resolve_names, resolve_volumes, save_json,
)

SESSION = requests.Session()
CACHE_DIR = default_cache_dir()
SETTINGS_PATH = CACHE_DIR / "lp_web_settings.json"
ARB_SETTINGS_PATH = CACHE_DIR / "arb_settings.json"
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
    instant = q.get("instant", ["0"])[0] in ("1", "true", "on")
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)
    max_spread = q.get("max_spread", [""])[0].strip()
    max_spread = float(max_spread) if max_spread else None
    min_profit = q.get("min_profit", [""])[0].strip()
    min_profit = float(min_profit) if min_profit else None

    s = load_settings()
    s.update({
        "corp": corp_arg,
        "lp": str(int(lp)),
        "instant": "1" if instant else "0",
        "max_spread": str(max_spread) if max_spread is not None else "",
        "tax": str(tax),
        "broker": str(broker),
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
    prices = fetch_prices(_all_type_ids(offers), SESSION)
    sellable, unsellable = evaluate(offers, prices, lp, tax, broker, instant)
    if min_profit is not None:
        sellable = [r for r in sellable if r["profit_per"] >= min_profit]
    if max_spread is not None:
        sellable = [r for r in sellable
                    if r["spread_pct"] is not None and r["spread_pct"] <= max_spread]

    names = resolve_names(_all_type_ids(offers), SESSION, CACHE_DIR)
    rows = []
    for r in sellable:
        sp = r["spread_pct"]
        rows.append({
            "offer_id": r["offer_id"],
            "name": names.get(r["name_id"], str(r["name_id"])),
            "qty": r["qty"],
            "lp_cost": r["lp_cost"],
            "cost_ea": r["isk_cost"] + r["req_cost"],
            "ask": r["ask"],
            "bid": r["bid"],
            "spread_pct": sp,
            "profit_per": r["profit_per"],
            "isk_per_lp": r["isk_per_lp"],
            "max_units": r["max_units"],
            "total_profit": r["total_profit"],
            "buy_volume": r["buy_volume"],
            "req_missing": r["req_missing"],
            "ak_cost": r["ak_cost"],
            "illiquid": sp is None or sp >= HIGH_SPREAD_PCT,
        })
    return {
        "corp_id": corp_id,
        "corp_name": corp_name,
        "lp": lp,
        "instant": instant,
        "tax": tax,
        "broker": broker,
        "high_spread_pct": HIGH_SPREAD_PCT,
        "count": len(rows),
        "unsellable": len(unsellable),
        "rows": rows,
        "scanned_at": time.time(),
        "offers_fetched_at": offers_meta.get("fetched_at"),
    }


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
    for k in ("sort_key", "sort_dir", "col_widths", "col_layout_v", "hide_illiquid",
              "hide_unaffordable", "active_tab"):
        if k in q:
            s[k] = q[k][0]
    save_settings(s)
    return {"ok": True}


def do_detail(q):
    corp_id = int(q["corp_id"][0])
    offer_id = int(q["offer_id"][0])
    lp = float(q.get("lp", ["0"])[0] or 0)
    instant = q.get("instant", ["0"])[0] in ("1", "true", "on")
    tax = float(q.get("tax", ["0.045"])[0] or 0.045)
    broker = float(q.get("broker", ["0.015"])[0] or 0.015)

    offers = get_offers(corp_id, SESSION, CACHE_DIR)
    offer = next((o for o in offers if o.get("offer_id") == offer_id), None)
    if offer is None:
        raise LPError(f"Offer {offer_id} not found for corp {corp_id}.")

    tids = {offer["type_id"]} | {r["type_id"] for r in offer.get("required_items", [])}
    prices = fetch_prices(tids, SESSION)
    names = resolve_names(tids, SESSION, CACHE_DIR)
    volumes = resolve_volumes(tids, SESSION, CACHE_DIR)
    detail = build_detail(offer, prices, names, volumes, lp, tax, broker, instant)
    detail["high_spread_pct"] = HIGH_SPREAD_PCT

    for it in detail["required_items"]:
        it["book"] = fetch_orderbook_jita(it["type_id"], "sell", SESSION)
    if instant:
        detail["output"]["buy_book"] = fetch_orderbook_jita(
            detail["output"]["type_id"], "buy", SESSION)
    return detail


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
    refresh = q.get("refresh", ["0"])[0] in ("1", "true", "on")

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

    def book_progress(stage, **kw):
        if stage == "cache":
            _emit({"type": "progress", "pct": 65,
                   "msg": f"Using cached order book ({kw['orders']:,} orders)",
                   "sub": "Analyzing spreads…"})
        elif stage == "revalidate":
            _emit({"type": "progress", "pct": 2,
                   "msg": "Checking ESI for a newer snapshot…", "sub": ""})
        elif stage == "stale":
            _emit({"type": "progress", "pct": 65,
                   "msg": f"ESI unreachable — reusing {kw['orders']:,} cached orders",
                   "sub": ""})
        elif stage == "page":
            pages = kw.get("pages") or 1
            page = kw.get("page", 0)
            orders = kw.get("orders", 0)
            if page == 0:
                _emit({"type": "progress", "pct": 2,
                       "msg": "Downloading order book from ESI…",
                       "sub": "First run for this region can take ~30 s"})
            else:
                pct = max(5, min(62, round(5 + (page / pages) * 57)))
                _emit({"type": "progress", "pct": pct,
                       "msg": f"Downloading order book — page {page} of {pages}",
                       "sub": f"{orders:,} orders received so far"})

    orders, snap_meta = arb_core.get_orders(region, SESSION, CACHE_DIR, refresh,
                                            progress_cb=book_progress)

    _emit({"type": "progress", "pct": 68,
           "msg": f"Analyzing {len(orders):,} orders…", "sub": "Finding profitable spreads"})

    results = [r for r in arb_core.find_spreads(orders, sales_tax, not cross_station)
               if r["isk_opportunity"] >= min_isk]

    if cross_station:
        # Enrich all results (capped) then filter to Jita-leg deals within max_jumps.
        # round_trip=True so jumps counts the haul both ways.
        _emit({"type": "progress", "pct": 72,
               "msg": f"Found {len(results):,} cross-station spreads — resolving stations…",
               "sub": f"Filtering to Jita legs ≤{max_jumps} jumps round-trip"})
        enriched = arb_core.enrich_locations(
            results[:500], round_trip=True, route_flag=route_flag,
            session=SESSION, station_cache=_ARB_STATION_CACHE, route_cache=_ARB_ROUTE_CACHE,
        )
        from_jita = arb_core.filter_from_jita(enriched, max_jumps)
        _emit({"type": "progress", "pct": 82,
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
        _emit({"type": "progress", "pct": 72,
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
        "total_orders": len(orders),
        "snap_last_modified": snap_meta.get("last_modified"),
        "snap_expires": snap_meta.get("expires"),
        "snap_fetched_at": snap_meta.get("fetched_at"),
        "scanned_at": time.time(),
        "rows": rows,
    }


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

    def _handle_arb_scan(self, q):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        emit = self._sse_emit
        try:
            result = do_arb_scan(q, emit=emit)
            emit({"type": "result", **result})
        except LPError as e:
            emit({"type": "error", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "error": f"{type(e).__name__}: {e}"})

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
                self._send_json(merged)
            elif parsed.path == "/api/prefs":
                self._send_json(do_prefs(q))
            elif parsed.path == "/api/scan":
                self._send_json(do_scan(q))
            elif parsed.path == "/api/detail":
                self._send_json(do_detail(q))
            elif parsed.path == "/api/arb/prefs":
                self._send_json(do_arb_prefs(q))
            elif parsed.path == "/api/arb/scan":
                self._handle_arb_scan(q)
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
  .btn-group { display:flex; gap:6px; align-self:flex-end; margin-left:6px; }
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
  button.toggle.active {
    background:rgba(32,128,208,.18); border-color:var(--cyan2);
    color:var(--cyan); font-weight:600;
  }

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
  main { display:flex; height:calc(100vh - 131px); position:relative; overflow:hidden; }
  .tablewrap { flex:1; overflow:auto; }

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
  .resizer { position:absolute; top:0; right:0; width:6px; height:100%; cursor:col-resize; }
  .resizer:hover, .resizer.active { background:var(--accent2); }
  body.col-resizing { cursor:col-resize; user-select:none; }

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
  td.spread.tight { color:var(--green); }
  td.spread.mid { color:var(--yellow); }
  .flag { color:var(--red); font-weight:700; font-size:12px; margin-left:2px; }

  /* ── Detail panel (LP) ───────────────────────────────────────────── */
  #detail {
    position:absolute; top:0; right:0; height:100%; width:0; overflow:hidden;
    transition:width .18s cubic-bezier(.4,0,.2,1);
    border-left:1px solid var(--line2); z-index:5;
    background:var(--panel);
    box-shadow:-16px 0 40px rgba(0,0,0,.6);
  }
  #detail.open { width:580px; max-width:96vw; }
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
  .kpis { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:12px 0; }
  .kpi {
    background:var(--panel2); border:1px solid var(--line2); border-radius:6px;
    padding:10px 14px; position:relative; overflow:hidden;
  }
  .kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
    background:var(--line2); }
  .kpi.accent::before { background:linear-gradient(90deg,var(--cyan2),transparent); }
  .kpi .l { font-size:10px; text-transform:uppercase; letter-spacing:.6px;
    color:var(--dim); font-weight:700; }
  .kpi .v { font-size:20px; font-weight:700; margin-top:3px; }
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
</style>
</head>
<body>

<header>
  <div class="logo">EVE <span>MARKET TOOLS</span><span class="ver">v__VERSION__</span></div>
  <nav class="tabs">
    <button class="tab active" data-tab="lp">LP Store</button>
    <button class="tab" data-tab="arb">Arbitrage</button>
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
  <div class="field"><label>Sell mode</label>
    <select id="instant">
      <option value="0">Patient (sell order)</option>
      <option value="1">Instant (buy order)</option>
    </select>
  </div>
  <div class="field"><label>Max spread %</label><input id="maxspread" type="number" placeholder="off" value="20"></div>
  <div class="field"><label>Sales tax</label><input id="tax" type="number" step="0.001" value="0.045"></div>
  <div class="field"><label>Broker fee</label><input id="broker" type="number" step="0.001" value="0.015"></div>
  <div class="btn-group">
    <button id="go" class="primary">Scan</button>
    <button id="refresh" class="secondary" title="Re-fetch offers + prices from ESI">⟳ Refresh</button>
    <button id="toggleIlliquid" class="secondary toggle" title="Show/hide illiquid rows">Hide illiquid !</button>
    <button id="toggleAffordable" class="secondary toggle" title="Hide offers you can't afford">Hide unaffordable</button>
  </div>
</div>

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
  <div class="field"><label>Sales tax</label>
    <input id="arb-tax" type="number" step="0.001" value="0.075" style="width:80px">
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
    <button id="arb-refresh" class="secondary" title="Force fresh order book from ESI">⟳ Refresh</button>
    <button id="arb-toggleLowsec" class="secondary toggle" title="Hide deals touching lowsec/nullsec">Highsec only</button>
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
  <!-- LP detail panel -->
  <div id="detail"><div class="inner"></div></div>
</main>

<script>
const $ = s => document.querySelector(s);
const COL_LAYOUT_VERSION = 2;

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
function fmtTs(epoch){
  if(!epoch) return "unknown";
  const sec=Math.round((Date.now()/1000)-epoch);
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
      corp:$("#corp").value,lp:$("#lp").value,instant:$("#instant").value,
      maxspread:$("#maxspread").value,tax:$("#tax").value,broker:$("#broker").value,
      sort_key:STATE.sort.key,sort_dir:STATE.sort.dir,
      col_widths:STATE.colw,col_layout_v:COL_LAYOUT_VERSION,
      hide_illiquid:STATE.hideIlliquid?'1':'0',
      hide_unaffordable:STATE.hideUnaffordable?'1':'0',
      active_tab:ACTIVE_TAB,
      arb:{region:$("#arb-region").value,cross_station:$("#arb-cross").value,
        sales_tax:$("#arb-tax").value,min_isk:$("#arb-minisk").value,
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
  $("#lp-tablewrap").classList.toggle("hidden", tab!=="lp");
  $("#arb-tablewrap").classList.toggle("hidden", tab!=="arb");
  if(tab!=="lp") closeDetail();
  setStatus("");
  document.title = tab==="lp" ? "EVE LP Store Scanner" : "EVE Arbitrage Scanner";
  fetch(`/api/prefs?active_tab=${tab}`).catch(()=>{}); saveLS();
}
document.querySelectorAll(".tab").forEach(t=>{
  t.onclick = ()=>switchTab(t.dataset.tab);
});

// ══════════════════════════════════════════════════════════════════════════
// LP TAB
// ══════════════════════════════════════════════════════════════════════════
let STATE = {rows:[], sort:{key:"isk_per_lp", dir:-1}, ctx:{}, selOffer:null,
             colw:{}, hideIlliquid:false, hideUnaffordable:false, lastScanData:null};
let LP_RESIZING = false;

const COLS = [
  {k:"name",         t:"Reward Item",   w:280, tip:"Name of the item the LP offer rewards you with. * = a required input has no Jita price. ^ = offer costs Analysis Kredits. ! = illiquid (spread ≥25%)."},
  {k:"qty",          t:"Units",         w: 60, tip:"Units per redemption.", f:fmtNum},
  {k:"lp_cost",      t:"LP / Run",      w: 95, tip:"Loyalty Points per redemption.", f:fmtNum},
  {k:"cost_ea",      t:"ISK / Run",     w:105, tip:"ISK + required input costs per redemption.", f:fmtISK},
  {k:"ask",          t:"Jita Ask",      w:105, tip:"Lowest Jita IV-4 sell order price.", f:fmtISK},
  {k:"bid",          t:"Jita Bid",      w:105, tip:"Highest Jita IV-4 buy order price — what someone will pay right now.", f:fmtISK},
  {k:"spread_pct",   t:"Spread",        w: 75, tip:"Ask/bid spread. ≥25% (!) means the ask isn't backed by real buyers.", f:fmtSpread, cls:"spread"},
  {k:"isk_per_lp",   t:"ISK / LP",      w: 90, tip:"Profit per Loyalty Point — the headline efficiency metric.", f:v=>v.toLocaleString(undefined,{maximumFractionDigits:1}), pn:true},
  {k:"max_units",    t:"Redemptions",   w:105, tip:"How many times you can redeem with your LP budget.", f:v=>v===0?"—":fmtNum(v)},
  {k:"total_profit", t:"Total Profit",  w:115, tip:"Total profit if you spend your entire LP budget on this offer.", f:(v,r)=>r.max_units===0?"—":fmtISK(v), pn:true, rowCtx:true},
  {k:"buy_volume",   t:"Buy Demand",    w:105, tip:"Units on Jita buy orders — how many you could sell instantly.", f:fmtNum},
];

function lpSetColgroup(){
  $("#cg").innerHTML=COLS.map(c=>{
    const w=STATE.colw[c.k]; return `<col${w?` style="width:${w}px"`:""}>`;
  }).join("");
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

function renderTable(){
  const thead=$("#tbl thead"), tbody=$("#tbl tbody");
  const haveW=COLS.every(c=>STATE.colw[c.k]);
  $("#tbl").style.tableLayout=haveW?"fixed":"auto";
  lpSetColgroup();
  thead.innerHTML="<tr>"+COLS.map(c=>{
    const active=STATE.sort.key===c.k;
    const arrow=active?(STATE.sort.dir<0?" ▼":" ▲"):"";
    const tip=c.tip?` title="${c.tip.replace(/"/g,'&quot;')}"`: "";
    return `<th data-k="${c.k}"${tip}${active?' class="sorted"':''}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("")+"</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick=()=>{
      if(LP_RESIZING){ LP_RESIZING=false; return; }
      const k=th.dataset.k;
      if(STATE.sort.key===k) STATE.sort.dir*=-1;
      else STATE.sort={key:k, dir:k==="name"?1:-1};
      saveLPSort(); renderTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown",e=>startLPResize(e,COLS[i].k));
  });
  if(!haveW){
    requestAnimationFrame(()=>{
      thead.querySelectorAll("th").forEach((th,i)=>{
        const c=COLS[i];
        STATE.colw[c.k]=STATE.colw[c.k]||c.w||Math.ceil(th.getBoundingClientRect().width);
      });
      $("#tbl").style.tableLayout="fixed"; lpSetColgroup();
    });
  }
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
    const tds=COLS.map(c=>{
      let v=r[c.k], txt=c.f?(c.rowCtx?c.f(v,r):c.f(v)):v;
      let cls=c.cls||"";
      if(c.k==="spread_pct"&&v!==null) cls+=v<10?" tight":v<25?" mid":"";
      if(c.k==="name"){
        let flag=""; if(r.req_missing) flag+="*"; if(r.ak_cost) flag+="^"; if(r.illiquid) flag+="!";
        txt=txt+(flag?` <span class="flag">${flag}</span>`:"");
      }
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
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
  STATE.ctx={lp:$("#lp").value, instant:$("#instant").value, tax:$("#tax").value, broker:$("#broker").value};
  const p=new URLSearchParams({corp, ...STATE.ctx});
  const ms=$("#maxspread").value.trim(); if(ms) p.set("max_spread",ms);
  if(forceRefresh) p.set("refresh","1");
  try{
    const res=await fetch("/api/scan?"+p);
    const data=await res.json();
    if(data.error){ setStatus(data.error,true); return; }
    STATE.rows=data.rows; STATE.ctx.corp_id=data.corp_id; STATE.selOffer=null;
    STATE.lastScanData=data; closeDetail(); renderLPStatus(); renderTable();
  }catch(e){ setStatus("Request failed: "+e,true); }
  finally{ btn.disabled=false; btn.textContent="⟳ Refresh"; }
}

function renderLPStatus(){
  const d=STATE.lastScanData; if(!d||ACTIVE_TAB!=="lp") return;
  const mode=d.instant?"Instant":"Patient";
  setStatus(
    `<span class="pill"><b>${d.corp_name}</b></span>`
    +`<span class="pill"><b>${d.count}</b> offers</span>`
    +`<span class="pill"><b>${Number(d.lp).toLocaleString()}</b> LP · ${mode}</span>`
    +`<span class="ts">offers ${fmtTs(d.offers_fetched_at)} · prices ${fmtTs(d.scanned_at)}</span>`);
}

function saveLPSort(){
  const s=STATE.sort;
  fetch(`/api/prefs?sort_key=${encodeURIComponent(s.key)}&sort_dir=${s.dir}`).catch(()=>{}); saveLS();
}
function saveLPColWidths(){
  fetch(`/api/prefs?col_widths=${encodeURIComponent(JSON.stringify(STATE.colw))}&col_layout_v=${COL_LAYOUT_VERSION}`).catch(()=>{}); saveLS();
}

// ── LP detail panel ───────────────────────────────────────────────────────
async function openDetail(offerId){
  STATE.selOffer=offerId; renderTable();
  const p=new URLSearchParams({corp_id:STATE.ctx.corp_id, offer_id:offerId,
    lp:STATE.ctx.lp, instant:STATE.ctx.instant, tax:STATE.ctx.tax, broker:STATE.ctx.broker});
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
          ${d.instant?"instant (buy orders)":"patient (sell orders)"}</div></div>
      <span class="close" id="closeBtn">✕</span>
    </div>
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

function renderBody(){
  const d=STATE.detail;
  const n=Math.max(1,parseInt($("#reds").value||"1"));
  const tax=parseFloat(STATE.ctx.tax)||0.045, broker=parseFloat(STATE.ctx.broker)||0.015;
  const pn=v=>v>0?"pos":(v<0?"neg":"");
  let reqCost=0, anyShort=false;
  const reqRows=d.required_items.map(it=>{
    const need=it.quantity*n;
    const w=walkBook(it.book,need);
    const remPrice=w.lastPrice||it.unit_price||0;
    const line=w.cost+w.shortBy*remPrice;
    const noPrice=(it.unit_price===null&&w.filled===0);
    if(!noPrice) reqCost+=line;
    const short=w.shortBy>0; if(short) anyShort=true;
    const vol=it.line_volume===null?'?':fmtVol(it.line_volume*n);
    return `<tr><td>${it.name}${short?' <span class="flag" title="not enough on market">!</span>':''}</td>
      <td>${fmtNum(need)}</td>
      <td>${w.avg===null?(it.unit_price===null?'<span class="flag">*</span>':fmtISK(it.unit_price)):fmtISK(w.avg)}</td>
      <td>${noPrice?'<span class="flag">?</span>':fmtISK(line)}</td>
      <td>${vol}</td></tr>`;
  }).join("");
  let revenue, soldQty, sellShort=false;
  if(d.instant){
    const need=d.output.quantity*n;
    const w=walkBook(d.output.buy_book,need);
    revenue=w.cost*(1-tax); soldQty=w.filled; sellShort=w.shortBy>0;
  } else {
    soldQty=d.output.quantity*n;
    revenue=(d.ask?soldQty*d.ask*(1-tax-broker):null);
  }
  const lpTot=d.lp_cost*n, isk_fee=d.isk_fee*n, cost=isk_fee+reqCost;
  const profit=revenue===null?null:revenue-cost;
  const ipl=(profit===null||lpTot<=0)?null:profit/lpTot;
  const inVol=d.input_volume_per_redemption*n, outVol=(d.output_volume_per_redemption||0)*n;
  let warn="";
  if(anyShort) warn+=`<div class="note">! Not enough sell orders at Jita 4-4 for some required items.</div>`;
  if(d.instant&&sellShort) warn+=`<div class="note bad">Only ${fmtNum(soldQty)} of ${fmtNum(d.output.quantity*n)} can be sold into current Jita buy orders.</div>`;
  if(!d.instant){
    if(d.spread_pct===null) warn+=`<div class="note bad">No buy orders exist — listing at ask may never fill.</div>`;
    else if(d.spread_pct>=d.high_spread_pct) warn+=`<div class="note">${Math.round(d.spread_pct)}% spread — ask isn't backed by real demand.</div>`;
  }
  if(d.req_missing_price) warn+=`<div class="note">* A required item has no Jita price — true cost is higher.</div>`;
  $("#dbody").innerHTML=`
    <div class="kpis">
      <div class="kpi accent"><div class="l">Total profit</div><div class="v ${pn(profit)}">${fmtISK(profit)}</div></div>
      <div class="kpi accent"><div class="l">ISK / LP</div><div class="v ${pn(ipl)}">${ipl===null?'-':ipl.toLocaleString(undefined,{maximumFractionDigits:1})}</div></div>
      <div class="kpi"><div class="l">LP spent</div><div class="v">${fmtNum(lpTot)}</div></div>
      <div class="kpi"><div class="l">ISK cost</div><div class="v">${fmtISK(cost)}</div></div>
    </div>
    ${warn}
    <h3>Shopping list — ${n}× redemption${n>1?'s':''}</h3>
    ${d.required_items.length?`<table class="mini"><thead><tr>
        <th style="text-align:left">Required item</th><th>Total qty</th><th>Avg unit</th><th>Line cost</th><th>Volume</th></tr></thead>
        <tbody>${reqRows}</tbody></table>`
      :`<div class="muted">No required items — just LP + ISK.</div>`}
    <table class="mini" style="margin-top:8px"><tbody>
      <tr><td>Store ISK fee</td><td>${fmtISK(isk_fee)}</td></tr>
      <tr><td>Required items total</td><td>${fmtISK(reqCost)}</td></tr>
      <tr class="total"><td>Total acquisition cost</td><td>${fmtISK(cost)}</td></tr>
    </tbody></table>
    <h3>Cargo volume</h3>
    <table class="mini"><tbody>
      <tr><td style="text-align:left">Required items → LP corp station</td><td>${fmtVol(inVol)}</td></tr>
      <tr><td style="text-align:left">Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → Jita</td><td>${fmtVol(outVol)}</td></tr>
      <tr class="total"><td style="text-align:left">Ship cargo needed (larger leg)</td><td>${fmtVol(Math.max(inVol||0,outVol||0))}</td></tr>
    </tbody></table>
    <h3>Sale</h3>
    <table class="mini"><tbody>
      <tr><td style="text-align:left">Jita ask / bid</td><td>${fmtISK(d.ask)} / ${fmtISK(d.bid)}</td></tr>
      <tr><td style="text-align:left">${d.instant?'Revenue (walking buy orders, after tax)':'Net revenue (listed at ask, after fees)'}</td><td>${fmtISK(revenue)}</td></tr>
    </tbody></table>
    <p class="muted" style="margin-top:14px">Costs use the live Jita 4-4 order book.
      ${d.instant?'Revenue walks down buy orders.':'Reward valued at the lowest sell order.'}</p>`;
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
["#lp","#instant","#maxspread","#tax","#broker"].forEach(sel=>{
  const el=$(sel); if(!el) return;
  el.addEventListener("change",()=>{ saveLS(); scheduleScan(sel==="#instant"?0:800); });
  if(sel!=="#instant") el.addEventListener("input",()=>{ saveLS(); scheduleScan(800); });
});
$("#toggleIlliquid").onclick=()=>{
  STATE.hideIlliquid=!STATE.hideIlliquid;
  $("#toggleIlliquid").classList.toggle("active",STATE.hideIlliquid);
  $("#toggleIlliquid").textContent=STATE.hideIlliquid?"Show illiquid !":"Hide illiquid !";
  fetch(`/api/prefs?hide_illiquid=${STATE.hideIlliquid?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
$("#toggleAffordable").onclick=()=>{
  STATE.hideUnaffordable=!STATE.hideUnaffordable;
  $("#toggleAffordable").classList.toggle("active",STATE.hideUnaffordable);
  $("#toggleAffordable").textContent=STATE.hideUnaffordable?"Show unaffordable":"Hide unaffordable";
  fetch(`/api/prefs?hide_unaffordable=${STATE.hideUnaffordable?1:0}`).catch(()=>{}); saveLS();
  renderTable();
};
setInterval(renderLPStatus, 30000);

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
    const tip=c.tip?` title="${c.tip.replace(/"/g,'&quot;')}"`: "";
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
  tbody.innerHTML=rows.map(r=>{
    const tds=ARB_COLS.map(c=>{
      let v=r[c.k], txt=c.f?c.f(v):(v===null||v===undefined?"-":v);
      let cls=c.cls||"";
      if(c.secBand) cls+=" sec-"+r[c.secBand];
      if(c.riskBand) cls+=" risk-"+r[c.riskBand];
      if(c.pn) cls+=(v>0?" pos":(v<0?" neg":""));
      const titleAttr=(c.k==="sell_station"||c.k==="buy_station")&&v?` title="${String(v).replace(/"/g,'&quot;')}"` :"";
      return `<td class="${cls.trim()}"${titleAttr}>${txt}</td>`;
    }).join("");
    return `<tr>${tds}</tr>`;
  }).join("");
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

function scanArb(forceRefresh=false){
  // Close any in-flight scan.
  if(ARB.es){ ARB.es.close(); ARB.es=null; }

  const btn=$("#arb-go"), rbtn=$("#arb-refresh");
  btn.disabled=true; btn.textContent="Scanning…";
  if(forceRefresh){ rbtn.disabled=true; rbtn.textContent="⟳ Fetching…"; }

  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    $("#arb-tax").value,
    min_isk:      $("#arb-minisk").value||"0",
    max_jumps:    $("#arb-maxjumps").value||"6",
    route_flag:   $("#arb-route").value,
    avoid_lowsec: ARB.avoidLowsec?"1":"0",
  });
  if(forceRefresh) p.set("refresh","1");

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
      rbtn.disabled=false; rbtn.textContent="⟳ Refresh";
      ARB.rows=data.rows; ARB.lastData=data;
      hideArbProgress();
      renderArbStatus(); renderArbTable();

    } else if(data.type==="error"){
      es.close(); ARB.es=null;
      btn.disabled=false; btn.textContent="Scan";
      rbtn.disabled=false; rbtn.textContent="⟳ Refresh";
      hideArbProgress();
      setStatus(data.error, true);
    }
  };

  es.onerror = () => {
    es.close(); ARB.es=null;
    btn.disabled=false; btn.textContent="Scan";
    rbtn.disabled=false; rbtn.textContent="⟳ Refresh";
    hideArbProgress();
    setStatus("Connection error — server may have stopped.", true);
  };
}

function saveArbPrefs(){
  const p=new URLSearchParams({
    region:       $("#arb-region").value,
    cross_station: $("#arb-cross").value,
    sales_tax:    $("#arb-tax").value,
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
$("#arb-go").onclick=()=>scanArb(false);
$("#arb-refresh").onclick=()=>scanArb(true);
$("#arb-toggleLowsec").onclick=()=>{
  ARB.avoidLowsec=!ARB.avoidLowsec;
  $("#arb-toggleLowsec").classList.toggle("active",ARB.avoidLowsec);
  saveArbPrefs();
  if(ARB.rows.length) scanArb(false);
};
setInterval(renderArbStatus, 30000);

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
      if(s.instant==="0"||s.instant==="1") $("#instant").value=s.instant;
      const _ms=s.maxspread??s.max_spread; if(_ms!=null) $("#maxspread").value=_ms;
      if(s.tax)   $("#tax").value=s.tax;
      if(s.broker) $("#broker").value=s.broker;
      if(s.sort_key && COLS.some(c=>c.k===s.sort_key))
        STATE.sort={key:s.sort_key, dir:Number(s.sort_dir)===1?1:-1};
      if(s.col_widths && s.col_layout_v==COL_LAYOUT_VERSION){
        try{
          STATE.colw=(typeof s.col_widths==="string"?JSON.parse(s.col_widths):s.col_widths)||{};
        }catch(e){}
      }
      if(s.hide_illiquid==="1"){
        STATE.hideIlliquid=true;
        $("#toggleIlliquid").classList.add("active");
        $("#toggleIlliquid").textContent="Show illiquid !";
      }
      if(s.hide_unaffordable==="1"){
        STATE.hideUnaffordable=true;
        $("#toggleAffordable").classList.add("active");
        $("#toggleAffordable").textContent="Show unaffordable";
      }
      // Arb settings
      const a=s.arb||{};
      if(a.region) $("#arb-region").value=a.region;
      if(a.cross_station==="0"||a.cross_station==="1") $("#arb-cross").value=a.cross_station;
      if(a.sales_tax) $("#arb-tax").value=a.sales_tax;
      if(a.min_isk)   $("#arb-minisk").value=a.min_isk;
      if(a.max_jumps) $("#arb-maxjumps").value=a.max_jumps;
      if(a.route_flag) $("#arb-route").value=a.route_flag;
      if(a.avoid_lowsec==="1"){
        ARB.avoidLowsec=true;
        $("#arb-toggleLowsec").classList.add("active");
      }
      updateArbJumpsVisibility();
      // Restore last active tab
      if(s.active_tab==="arb") switchTab("arb");
  }
  // Auto-run LP scanner if corp is set
  if(ACTIVE_TAB==="lp" && $("#corp").value.trim()) scan(false);
}
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
