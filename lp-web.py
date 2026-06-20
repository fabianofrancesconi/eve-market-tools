#!/usr/bin/env python3
"""
Web UI for the EVE LP-store profit scanner.

Launches a small local web server (stdlib only -- no Flask) that serves a
single-page app: type a corp name + your LP, get the ranked offer table, click
any row to see the full shopping list of items to buy (with Jita prices) and the
m3 of cargo each leg of the haul occupies.

    pip install requests        # (only dependency, shared with the CLI)
    python lp-web.py            # then open http://localhost:8765
    python lp-web.py --port 9000 --no-browser

All the numbers come from lp_core.py, the same module the CLI uses.
"""
import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import requests

from lp_core import (
    ESI, HEADERS, HIGH_SPREAD_PCT, LPError, build_detail, default_cache_dir,
    evaluate, fetch_orderbook_jita, fetch_prices, get_offers, load_json,
    resolve_corp_id, resolve_corp_name, resolve_names, resolve_volumes, save_json,
)

SESSION = requests.Session()
CACHE_DIR = default_cache_dir()
SETTINGS_PATH = CACHE_DIR / "lp_web_settings.json"
# Corps whose offers we've already re-fetched this server session. Each launch
# starts empty, so the first scan of a corp after startup pulls fresh from ESI;
# later scans of the same corp reuse it. (Prices + order books are always live.)
REFRESHED_CORPS = set()


def load_settings():
    """Last-used form settings, or {} if none saved yet."""
    return load_json(SETTINGS_PATH, {})


def save_settings(d):
    save_json(SETTINGS_PATH, d)


def _all_type_ids(offers):
    ids = set()
    for o in offers:
        ids.add(o["type_id"])
        for req in o.get("required_items", []):
            ids.add(req["type_id"])
    return ids


def do_scan(q):
    """Run a full scan and return the ranked table as JSON-able data."""
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

    # Remember what the user typed so the form repopulates next launch. Save the
    # already-parsed+defaulted values (not raw query strings) so we never write
    # empty strings that would be falsy on load. Merge so we don't clobber other
    # prefs (e.g. the chosen table sort).
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

    # Force a fresh ESI pull on the first scan of a corp after startup, or
    # whenever the user explicitly asks (Refresh button -> refresh=1).
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
        "scanned_at": time.time(),                       # when prices/books were pulled
        "offers_fetched_at": offers_meta.get("fetched_at"),  # when offers came from ESI
    }


def _load_npc_corps():
    """Load all NPC corporation names, cached in lp_names.json (shared with
    resolve_names). Fetched once per install; 283 corps, small payload."""
    path = CACHE_DIR / "npc_corps.json"
    cached = load_json(path, None)
    if cached:
        return cached
    r = SESSION.get(f"{ESI}/corporations/npccorps/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    ids = r.json()
    # Resolve in chunks of 1000 via /universe/names/
    corps = []
    for i in range(0, len(ids), 1000):
        nr = SESSION.post(f"{ESI}/universe/names/", json=ids[i:i+1000],
                         headers=HEADERS, timeout=30)
        corps.extend({"id": e["id"], "name": e["name"]}
                     for e in nr.json() if e.get("category") == "corporation")
    corps.sort(key=lambda c: c["name"])
    save_json(path, corps)
    return corps

# Resolved once at startup (or from cache).
NPC_CORPS = []


def get_npc_corps():
    global NPC_CORPS
    if not NPC_CORPS:
        NPC_CORPS = _load_npc_corps()
    return NPC_CORPS


def do_prefs(q):
    """Merge lightweight UI prefs (e.g. table sort) into the settings file."""
    s = load_settings()
    for k in ("sort_key", "sort_dir", "col_widths", "col_layout_v", "hide_illiquid", "hide_unaffordable"):
        if k in q:
            s[k] = q[k][0]
    save_settings(s)
    return {"ok": True}


def do_detail(q):
    """Full breakdown (shopping list + volumes) for one offer."""
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
    volumes = resolve_volumes(tids, SESSION, CACHE_DIR)  # lazy: only this offer's items
    detail = build_detail(offer, prices, names, volumes, lp, tax, broker, instant)
    detail["high_spread_pct"] = HIGH_SPREAD_PCT

    # Attach live Jita 4-4 order books so the client can compute the TRUE cost of
    # a multi-unit fill (walking past the cheapest seller's limited stock).
    for it in detail["required_items"]:
        it["book"] = fetch_orderbook_jita(it["type_id"], "sell", SESSION)  # you BUY these
    if instant:
        detail["output"]["buy_book"] = fetch_orderbook_jita(  # you SELL into these
            detail["output"]["type_id"], "buy", SESSION)
    return detail


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

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

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
            elif parsed.path == "/api/corps":
                self._send_json(get_npc_corps())
            elif parsed.path == "/api/settings":
                self._send_json(load_settings())
            elif parsed.path == "/api/prefs":
                self._send_json(do_prefs(q))
            elif parsed.path == "/api/scan":
                self._send_json(do_scan(q))
            elif parsed.path == "/api/detail":
                self._send_json(do_detail(q))
            else:
                self._send_json({"error": "not found"}, 404)
        except LPError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 -- surface anything else to the UI
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EVE LP Store Profit Scanner</title>
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

  /* ── Header ─────────────────────────────────────────────── */
  header {
    padding:0 18px;
    height:58px;
    border-bottom:1px solid var(--line);
    display:flex; gap:12px; align-items:center; flex-wrap:nowrap;
    background:linear-gradient(180deg, #0f1f30 0%, var(--panel) 100%);
    box-shadow:0 2px 12px rgba(0,0,0,.5);
  }
  .logo {
    font-size:17px; font-weight:700; color:var(--cyan);
    letter-spacing:.5px; white-space:nowrap; margin-right:4px;
    text-shadow:0 0 18px rgba(79,195,247,.35);
    border-right:1px solid var(--line2); padding-right:16px;
  }
  .logo span { color:var(--gold); }
  .field { display:flex; flex-direction:column; gap:1px; }
  .field label { font-size:10px; text-transform:uppercase; letter-spacing:.7px;
    color:var(--dim); font-weight:600; }
  input, select {
    background:var(--panel2); border:1px solid var(--line2); color:var(--fg);
    border-radius:4px; padding:5px 9px; font:inherit; font-size:14px;
    transition:border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus {
    outline:none; border-color:var(--cyan2);
    box-shadow:0 0 0 2px rgba(41,182,246,.15);
  }
  input[type=number] { width:100px; } input#corp { width:190px; }
  .btn-group { display:flex; gap:6px; align-self:flex-end; padding-bottom:1px; }
  button {
    border:none; border-radius:4px; cursor:pointer; font:inherit; font-size:14px;
    font-weight:600; padding:6px 16px; transition:filter .12s, background .12s;
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

  /* ── Status bar ──────────────────────────────────────────── */
  #statusbar {
    padding:5px 18px; font-size:13px; min-height:28px;
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

  /* ── Layout ──────────────────────────────────────────────── */
  main { display:flex; height:calc(100vh - 87px); position:relative; overflow:hidden; }
  #tablewrap { flex:1; overflow:auto; }

  /* ── Main table ──────────────────────────────────────────── */
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
  #tbl th { position:sticky; }
  #tbl th, #tbl td { overflow:hidden; text-overflow:ellipsis; }
  /* Let the reward item name wrap so it's always fully readable */
  #tbl td:first-child, #tbl th:first-child { white-space:normal; word-break:break-word;
    overflow:visible; text-overflow:clip; line-height:1.3; }
  body.col-resizing { cursor:col-resize; user-select:none; }

  tbody tr { cursor:pointer; transition:background .08s; }
  tbody tr:hover { background:var(--panel2); }
  tbody tr.sel { background:rgba(32,113,196,.18); border-left:3px solid var(--cyan2); }
  tbody tr.sel td:first-child { padding-left:13px; }
  tbody tr.illiquid { opacity:.75; }
  tbody tr.illiquid td.spread { color:var(--red); }
  tbody tr.unaffordable td { color:var(--dim2); }

  td.pos { color:var(--green2); font-weight:500; }
  td.neg { color:var(--red); }
  td.spread.tight { color:var(--green); }
  td.spread.mid { color:var(--yellow); }
  .flag { color:var(--red); font-weight:700; font-size:12px; margin-left:2px; }

  /* ── Detail panel ────────────────────────────────────────── */
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
</style>
</head>
<body>
<header>
  <div class="logo">EVE <span>LP</span></div>
  <div class="field"><label>Corporation</label><input id="corp" list="corp-list" placeholder="Search corporation…" value="State Protectorate" autocomplete="off"><datalist id="corp-list"></datalist></div>
  <div class="field"><label>LP budget</label><input id="lp" type="number" value="500000"></div>
  <div class="field"><label>Sell mode</label>
    <select id="instant"><option value="0">Patient (sell order)</option>
      <option value="1">Instant (buy order)</option></select></div>
  <div class="field"><label>Max spread %</label><input id="maxspread" type="number" placeholder="off" value="20"></div>
  <div class="field"><label>Sales tax</label><input id="tax" type="number" step="0.001" value="0.045"></div>
  <div class="field"><label>Broker fee</label><input id="broker" type="number" step="0.001" value="0.015"></div>
  <div class="btn-group">
    <button id="go" class="primary">Scan</button>
    <button id="refresh" class="secondary" title="Re-fetch the latest offers + prices from ESI">⟳ Refresh</button>
    <button id="toggleIlliquid" class="secondary toggle" title="Show/hide illiquid rows (! flag)">Hide illiquid !</button>
    <button id="toggleAffordable" class="secondary toggle" title="Hide offers you can't afford with your LP budget">Hide unaffordable</button>
  </div>
</header>
<div id="statusbar"></div>
<main>
  <div id="tablewrap"><table id="tbl"><colgroup id="cg"></colgroup><thead></thead><tbody></tbody></table></div>
  <div id="detail"><div class="inner"></div></div>
</main>
<script>
const $ = s => document.querySelector(s);
const COL_LAYOUT_VERSION = 2;  // bump when default widths change to reset saved widths
let STATE = {rows:[], sort:{key:"isk_per_lp", dir:-1}, ctx:{}, selOffer:null, colw:{}, hideIlliquid:false, hideUnaffordable:false, lastScanData:null};
let RESIZING = false;

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

const COLS = [
  {k:"name",         t:"Reward Item",   w:280, tip:"Name of the item the LP offer rewards you with. * = a required input has no Jita price (cost understated). ^ = offer also costs Analysis Kredits. ! = illiquid (ask/bid spread ≥25% — the listed price isn't backed by real buyers)."},
  {k:"qty",          t:"Units",         w: 60, tip:"Units you receive per single redemption of this offer.", f:fmtNum},
  {k:"lp_cost",      t:"LP / Run",      w: 95, tip:"Loyalty Points required per redemption.", f:fmtNum},
  {k:"cost_ea",      t:"ISK / Run",     w:105, tip:"Total ISK you must spend per redemption: store ISK fee + Jita cost of all required input items. This is on top of the LP.", f:fmtISK},
  {k:"ask",          t:"Jita Ask",      w:105, tip:"Lowest Jita IV-4 sell order price — what you would list your reward at.", f:fmtISK},
  {k:"bid",          t:"Jita Bid",      w:105, tip:"Highest Jita IV-4 buy order price — what someone will pay right now without waiting.", f:fmtISK},
  {k:"spread_pct",   t:"Spread",        w: 75, tip:"Ask/bid spread: (Ask − Bid) / Ask. Small = liquid market with real buyers. ≥25% (red, ! flag) means the Ask is aspirational — nobody is bidding near it.", f:fmtSpread, cls:"spread"},
  {k:"isk_per_lp",   t:"ISK / LP",      w: 90, tip:"Profit per Loyalty Point spent (Profit per run ÷ LP per run). The core efficiency metric — higher is better. Use this to compare offers regardless of LP cost.", f:v=>v.toLocaleString(undefined,{maximumFractionDigits:1}), pn:true},
  {k:"max_units",    t:"Redemptions",   w:105, tip:"How many times you can redeem this offer with your current LP budget. — means your budget doesn't cover even one redemption.", f:v=>v===0?"—":fmtNum(v)},
  {k:"total_profit", t:"Total Profit",  w:115, tip:"Total profit if you spend your entire LP budget on this offer (Profit per redemption × Redemptions). — means you can't afford even one. Check Buy Demand — if Redemptions > Buy Demand you can't sell them all quickly.", f:(v,r)=>r.max_units===0?"—":fmtISK(v), pn:true, rowCtx:true},
  {k:"buy_volume",   t:"Buy Demand",    w:105, tip:"Units currently on Jita buy orders — how many you could sell instantly right now. Compare against Redemptions × Units to gauge how fast you can offload.", f:fmtNum},
];

function setColgroup(){
  $("#cg").innerHTML = COLS.map(c=>{
    const w=STATE.colw[c.k];
    return `<col${w?` style="width:${w}px"`:""}>`;
  }).join("");
}

function startResize(e, key){
  e.preventDefault(); e.stopPropagation();
  RESIZING = true;
  e.target.classList.add("active");
  document.body.classList.add("col-resizing");
  $("#tbl").style.tableLayout = "fixed";
  const startX = e.clientX, startW = STATE.colw[key] || 80;
  function mm(ev){ STATE.colw[key] = Math.max(40, startW + (ev.clientX - startX)); setColgroup(); }
  function mu(){
    document.removeEventListener("mousemove", mm);
    document.removeEventListener("mouseup", mu);
    e.target.classList.remove("active");
    document.body.classList.remove("col-resizing");
    saveColWidths();
    setTimeout(()=>{ RESIZING = false; }, 0);  // let the th click fire first, then clear
  }
  document.addEventListener("mousemove", mm);
  document.addEventListener("mouseup", mu);
}

function renderTable(){
  const thead = $("#tbl thead"), tbody = $("#tbl tbody");
  const haveW = COLS.every(c=>STATE.colw[c.k]);
  $("#tbl").style.tableLayout = haveW ? "fixed" : "auto";
  setColgroup();
  thead.innerHTML = "<tr>" + COLS.map(c=>{
    const active = STATE.sort.key===c.k;
    const arrow = active ? (STATE.sort.dir<0?" ▼":" ▲") : "";
    const tip = c.tip ? ` title="${c.tip.replace(/"/g,'&quot;')}"` : "";
    const cls = active ? ' class="sorted"' : "";
    return `<th data-k="${c.k}"${tip}${cls}>${c.t}${arrow}<span class="resizer"></span></th>`;
  }).join("") + "</tr>";
  thead.querySelectorAll("th").forEach((th,i)=>{
    th.onclick = ()=>{
      if(RESIZING){ RESIZING=false; return; }   // ignore the click that ends a drag
      const k=th.dataset.k;
      if(STATE.sort.key===k) STATE.sort.dir*=-1;
      else STATE.sort={key:k, dir: k==="name"?1:-1};
      saveSort();
      renderTable();
    };
    th.querySelector(".resizer").addEventListener("mousedown", e=>startResize(e, COLS[i].k));
  });
  if(!haveW){
    // First render: let the browser auto-size, then lock those widths so columns
    // become independently resizable (auto layout reflows neighbours otherwise).
    // Use the column's defined default width; fall back to measured if absent.
    requestAnimationFrame(()=>{
      thead.querySelectorAll("th").forEach((th,i)=>{
        const c = COLS[i];
        STATE.colw[c.k] = STATE.colw[c.k] || c.w || Math.ceil(th.getBoundingClientRect().width);
      });
      $("#tbl").style.tableLayout = "fixed";
      setColgroup();
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
  tbody.innerHTML = rows.map(r=>{
    const tds = COLS.map(c=>{
      let v=r[c.k], txt = c.f? (c.rowCtx? c.f(v,r) : c.f(v)) : v;
      let cls = c.cls||"";
      if(c.k==="spread_pct" && v!==null){
        cls += v<10?" tight":v<25?" mid":"";
      }
      if(c.k==="name"){
        let flag=""; if(r.req_missing) flag+="*"; if(r.ak_cost) flag+="^"; if(r.illiquid) flag+="!";
        txt = txt + (flag?` <span class="flag">${flag}</span>`:"");
      }
      if(c.pn) cls += (v>0?" pos":(v<0?" neg":""));
      return `<td class="${cls}">${txt}</td>`;
    }).join("");
    return `<tr class="${r.illiquid?'illiquid':''} ${r.offer_id===STATE.selOffer?'sel':''}" data-id="${r.offer_id}">${tds}</tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr=>tr.onclick=()=>openDetail(+tr.dataset.id));
}

function fmtTs(epoch){
  if(!epoch) return "unknown";
  const sec = Math.round((Date.now()/1000) - epoch);
  if(sec < 5)  return "just now";
  if(sec < 60) return `${sec}s ago`;
  if(sec < 3600) return `${Math.floor(sec/60)}m ago`;
  if(sec < 86400) return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}

async function scan(forceRefresh=false){
  const corp=$("#corp").value.trim();
  if(!corp){ setStatus("Enter a corporation name.", true); return; }
  const btn=$("#refresh");
  if(forceRefresh){ btn.disabled=true; btn.textContent="⟳ Fetching…"; }
  setStatus("Scanning "+corp+(forceRefresh?" (refreshing from ESI)":"")+" …");
  STATE.ctx = {
    lp:$("#lp").value, instant:$("#instant").value, tax:$("#tax").value, broker:$("#broker").value
  };
  const p = new URLSearchParams({corp, ...STATE.ctx});
  const ms=$("#maxspread").value.trim(); if(ms) p.set("max_spread", ms);
  if(forceRefresh) p.set("refresh","1");
  try{
    const res = await fetch("/api/scan?"+p);
    const data = await res.json();
    if(data.error){ setStatus(data.error, true); return; }
    STATE.rows = data.rows; STATE.ctx.corp_id = data.corp_id; STATE.selOffer=null;
    STATE.lastScanData = data;
    closeDetail();
    renderStatus();
    renderTable();
  }catch(e){ setStatus("Request failed: "+e, true); }
  finally{ btn.disabled=false; btn.textContent="⟳ Refresh"; }
}

async function openDetail(offerId){
  STATE.selOffer = offerId; renderTable();
  const p = new URLSearchParams({corp_id:STATE.ctx.corp_id, offer_id:offerId,
    lp:STATE.ctx.lp, instant:STATE.ctx.instant, tax:STATE.ctx.tax, broker:STATE.ctx.broker});
  const inner = $("#detail .inner");
  inner.innerHTML = "<div class='muted'>Loading volumes…</div>";
  $("#detail").classList.add("open");
  try{
    const d = await (await fetch("/api/detail?"+p)).json();
    if(d.error){ inner.innerHTML="<span style='color:var(--red)'>"+d.error+"</span>"; return; }
    STATE.detail = d; renderDetail();
  }catch(e){ inner.innerHTML="<span style='color:var(--red)'>"+e+"</span>"; }
}
function closeDetail(){ $("#detail").classList.remove("open"); STATE.selOffer=null; }

function renderDetail(){
  const d = STATE.detail;
  const def = Math.max(d.max_units||0, 1);
  const inner = $("#detail .inner");
  inner.innerHTML = `
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
  $("#reds").oninput = renderBody;
  const ml=$("#maxLink");
  if(ml) ml.onclick=e=>{ e.preventDefault(); $("#reds").value=Math.max(d.max_units,1); renderBody(); };
  renderBody();
}

// Walk an aggregated order book [[price,vol],...] to fill `qty`, summing the
// real cost/revenue across price levels (the cheapest level rarely has it all).
function walkBook(book, qty){
  let need=qty, cost=0, filled=0, last=null;
  for(const lvl of (book||[])){
    if(need<=0) break;
    const take=Math.min(need, lvl[1]);
    cost += take*lvl[0]; filled += take; need -= take; last=lvl[0];
  }
  return {cost, filled, avg: filled>0? cost/filled : null,
          shortBy: Math.max(0, qty-filled), lastPrice:last};
}

function renderBody(){
  const d = STATE.detail;
  const n = Math.max(1, parseInt($("#reds").value||"1"));
  const tax = parseFloat(STATE.ctx.tax)||0.045, broker = parseFloat(STATE.ctx.broker)||0.015;
  const pn = v => v>0?"pos":(v<0?"neg":"");

  // ---- required items: walk the live sell book for the full quantity ----
  let reqCost=0, anyShort=false;
  const reqRows = d.required_items.map(it=>{
    const need = it.quantity*n;
    const w = walkBook(it.book, need);
    // value any shortfall at the worst listed price (or best-price fallback)
    const remPrice = w.lastPrice || it.unit_price || 0;
    const line = w.cost + w.shortBy*remPrice;
    const noPrice = (it.unit_price===null && w.filled===0);
    if(!noPrice) reqCost += line;
    const short = w.shortBy>0;
    if(short) anyShort=true;
    const vol = it.line_volume===null? '?' : fmtVol(it.line_volume*n);
    return `<tr><td>${it.name}${short?' <span class="flag" title="not enough on market">!</span>':''}</td>
      <td>${fmtNum(need)}</td>
      <td>${w.avg===null? (it.unit_price===null?'<span class="flag">*</span>':fmtISK(it.unit_price)) : fmtISK(w.avg)}</td>
      <td>${noPrice?'<span class="flag">?</span>':fmtISK(line)}</td>
      <td>${vol}</td></tr>`;
  }).join("");

  // ---- sale: instant walks the buy book; patient lists at the ask ----
  let revenue, soldQty, sellShort=false;
  if(d.instant){
    const need=d.output.quantity*n;
    const w=walkBook(d.output.buy_book, need);
    revenue = w.cost*(1-tax);            // only what buy orders can actually absorb
    soldQty = w.filled; sellShort = w.shortBy>0;
  } else {
    soldQty = d.output.quantity*n;
    revenue = (d.ask? soldQty*d.ask*(1-tax-broker) : null);
  }

  const lpTot = d.lp_cost*n;
  const isk_fee = d.isk_fee*n;
  const cost = isk_fee + reqCost;
  const profit = revenue===null? null : revenue - cost;
  const ipl = (profit===null||lpTot<=0)? null : profit/lpTot;
  const inVol = d.input_volume_per_redemption*n;
  const outVol = (d.output_volume_per_redemption||0)*n;

  let warn="";
  if(anyShort) warn+=`<div class="note">! Not enough sell orders at Jita 4-4 for some required items — the shortfall is valued at the highest listed price, so real cost may be higher (or buy elsewhere).</div>`;
  if(d.instant && sellShort) warn+=`<div class="note bad">Only ${fmtNum(soldQty)} of ${fmtNum(d.output.quantity*n)} can be sold into current Jita buy orders — revenue counts only the fillable part.</div>`;
  if(!d.instant){
    if(d.spread_pct===null) warn+=`<div class="note bad">No buy orders exist — listing at the ask may never fill. Treat as illiquid.</div>`;
    else if(d.spread_pct>=d.high_spread_pct) warn+=`<div class="note">${Math.round(d.spread_pct)}% ask/bid spread — the ${fmtISK(d.ask)} ask isn't backed by real demand (top bid ${fmtISK(d.bid)}); you'd likely undercut.</div>`;
  }
  if(d.req_missing_price) warn+=`<div class="note">* a required item has no Jita price — true cost is higher than shown.</div>`;

  $("#dbody").innerHTML = `
    <div class="kpis">
      <div class="kpi accent"><div class="l">Total profit</div><div class="v ${pn(profit)}">${fmtISK(profit)}</div></div>
      <div class="kpi accent"><div class="l">ISK / LP</div><div class="v ${pn(ipl)}">${ipl===null?'-':ipl.toLocaleString(undefined,{maximumFractionDigits:1})}</div></div>
      <div class="kpi"><div class="l">LP spent</div><div class="v">${fmtNum(lpTot)}</div></div>
      <div class="kpi"><div class="l">ISK needed (cost)</div><div class="v">${fmtISK(cost)}</div></div>
    </div>
    ${warn}
    <h3>Shopping list — total to buy for ${n}× redemption${n>1?'s':''}</h3>
    ${d.required_items.length? `<table class="mini"><thead><tr>
        <th style="text-align:left">Required item</th><th>Total qty</th><th>Avg unit</th><th>Line cost</th><th>Volume</th></tr></thead>
        <tbody>${reqRows}</tbody></table>`
      : `<div class="muted">No required items — just LP + ISK.</div>`}
    <table class="mini" style="margin-top:8px"><tbody>
      <tr><td>Store ISK fee</td><td>${fmtISK(isk_fee)}</td></tr>
      <tr><td>Required items total (order-book)</td><td>${fmtISK(reqCost)}</td></tr>
      <tr class="total"><td>Total acquisition cost</td><td>${fmtISK(cost)}</td></tr>
    </tbody></table>
    <h3>Cargo volume</h3>
    <table class="mini"><tbody>
      <tr><td style="text-align:left">Required items → haul to LP corp station</td><td>${fmtVol(inVol)}</td></tr>
      <tr><td style="text-align:left">Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → haul to Jita</td><td>${fmtVol(outVol)}</td></tr>
      <tr class="total"><td style="text-align:left">Ship cargo hold needed (larger trip)</td><td>${fmtVol(Math.max(inVol||0, outVol||0))}</td></tr>
    </tbody></table>
    <h3>Sale</h3>
    <table class="mini"><tbody>
      <tr><td style="text-align:left">Jita ask / bid</td><td>${fmtISK(d.ask)} / ${fmtISK(d.bid)}</td></tr>
      <tr><td style="text-align:left">${d.instant?'Revenue (walking buy orders, after tax)':'Net revenue (listed at ask, after fees)'}</td><td>${fmtISK(revenue)}</td></tr>
    </tbody></table>
    <p class="muted" style="margin-top:14px">Costs use the live Jita 4-4 order book — buying ${n>1?'large quantities':'these'}
      walks past the cheapest seller's stock into pricier orders, so the average unit price rises with quantity.
      ${d.instant?'Revenue likewise walks down the buy orders.':'Reward valued at the lowest sell order (what you list against).'}
      If you farm the required tags yourself, your real cost is lower.</p>`;
}

function setStatus(html, err){ const s=$("#statusbar"); s.innerHTML=html; s.className=err?"err":""; }

function renderStatus(){
  const d=STATE.lastScanData; if(!d) return;
  const mode = d.instant? "Instant" : "Patient";
  setStatus(
    `<span class="pill"><b>${d.corp_name}</b></span>`
    + `<span class="pill"><b>${d.count}</b> offers</span>`
    + `<span class="pill"><b>${Number(d.lp).toLocaleString()}</b> LP · ${mode}</span>`
    + `<span class="ts">offers ${fmtTs(d.offers_fetched_at)} · prices ${fmtTs(d.scanned_at)}</span>`);
}
setInterval(renderStatus, 30000);  // keep relative timestamps ticking
$("#go").onclick = ()=>scan(false);
$("#refresh").onclick = ()=>scan(true);

// ── Corp field: load NPC corp list once, filter client-side (instant) ─────
let ALL_CORPS = [];
(async ()=>{
  try{
    ALL_CORPS = await (await fetch("/api/corps")).json();
    const dl = $("#corp-list");
    dl.innerHTML = ALL_CORPS.map(c=>`<option value="${c.name.replace(/"/g,'&quot;')}"></option>`).join("");
  }catch(e){}
})();
// Scan when the user picks a suggestion or leaves the field with a new value.
$("#corp").addEventListener("change", ()=>{ clearTimeout(scanTimer); scan(false); });
$("#corp").addEventListener("keydown", e=>{ if(e.key==="Enter"){ clearTimeout(scanTimer); scan(false); } });

// ── LP budget: Enter key or blur after change scans ────────────────────────
$("#lp").addEventListener("keydown", e=>{ if(e.key==="Enter"){ clearTimeout(scanTimer); scan(false); } });

// ── All other controls: debounced auto-scan on any change ─────────────────
let scanTimer;
function scheduleScan(delay=800){
  clearTimeout(scanTimer);
  scanTimer = setTimeout(()=>scan(false), delay);
}
["#lp","#instant","#maxspread","#tax","#broker"].forEach(sel=>{
  const el=$(sel);
  if(!el) return;
  // "change" for select/blur-committed inputs; "input" for live number typing
  el.addEventListener("change", ()=>scheduleScan(sel==="#instant"?0:800));
  if(sel!=="#instant") el.addEventListener("input", ()=>scheduleScan(800));
});
$("#toggleIlliquid").onclick = ()=>{
  STATE.hideIlliquid = !STATE.hideIlliquid;
  $("#toggleIlliquid").classList.toggle("active", STATE.hideIlliquid);
  $("#toggleIlliquid").textContent = STATE.hideIlliquid ? "Show illiquid !" : "Hide illiquid !";
  fetch(`/api/prefs?hide_illiquid=${STATE.hideIlliquid?1:0}`).catch(()=>{});
  renderTable();
};
$("#toggleAffordable").onclick = ()=>{
  STATE.hideUnaffordable = !STATE.hideUnaffordable;
  $("#toggleAffordable").classList.toggle("active", STATE.hideUnaffordable);
  $("#toggleAffordable").textContent = STATE.hideUnaffordable ? "Show unaffordable" : "Hide unaffordable";
  fetch(`/api/prefs?hide_unaffordable=${STATE.hideUnaffordable?1:0}`).catch(()=>{});
  renderTable();
};

function saveSort(){
  const s=STATE.sort;
  fetch(`/api/prefs?sort_key=${encodeURIComponent(s.key)}&sort_dir=${s.dir}`).catch(()=>{});
}
function saveColWidths(){
  fetch(`/api/prefs?col_widths=${encodeURIComponent(JSON.stringify(STATE.colw))}&col_layout_v=${COL_LAYOUT_VERSION}`).catch(()=>{});
}

async function loadSettings(){
  try{
    const s = await (await fetch("/api/settings")).json();
    if(s && Object.keys(s).length){
      if(s.corp != null && s.corp !== "") $("#corp").value = s.corp;
      if(s.lp != null && s.lp !== "") $("#lp").value = s.lp;
      if(s.instant === "0" || s.instant === "1") $("#instant").value = s.instant;
      if(s.max_spread != null) $("#maxspread").value = s.max_spread;
      if(s.tax != null && s.tax !== "") $("#tax").value = s.tax;
      if(s.broker != null && s.broker !== "") $("#broker").value = s.broker;
      if(s.sort_key && COLS.some(c=>c.k===s.sort_key))
        STATE.sort = {key:s.sort_key, dir: Number(s.sort_dir)===1?1:-1};
      if(s.col_widths && s.col_layout_v == COL_LAYOUT_VERSION){
        try{ STATE.colw = JSON.parse(s.col_widths) || {}; }catch(e){}
      }
      if(s.hide_illiquid==="1"){
        STATE.hideIlliquid = true;
        $("#toggleIlliquid").classList.add("active");
        $("#toggleIlliquid").textContent = "Show illiquid !";
      }
      if(s.hide_unaffordable==="1"){
        STATE.hideUnaffordable = true;
        $("#toggleAffordable").classList.add("active");
        $("#toggleAffordable").textContent = "Show unaffordable";
      }
    }
  }catch(e){}
  if($("#corp").value.trim()) scan(false);   // auto-run last scan on launch (startup already refreshed)
}
loadSettings();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Web UI for the EVE LP-store profit scanner.")
    ap.add_argument("--port", type=int, default=8765, help="port to serve on (default 8765)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")
    args = ap.parse_args()

    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"EVE LP Store Scanner web UI running at {url}", file=sys.stderr)
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
