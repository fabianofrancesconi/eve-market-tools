#!/usr/bin/env python3
"""
EVE Online LP-store profit scanner (CLI).

Given an NPC corporation name and an amount of Loyalty Points, works out which
item in that corp's LP store gives the most profit when bought with LP (+ ISK +
any required items) and resold at Jita IV-4. Data logic lives in lp_core.py
(shared with the web UI lp-web.py); this file is just the command line + table.

How profit is computed, per redemption:
    revenue = quantity * Jita price of the item you receive
    net rev = revenue * (1 - sales_tax [- broker_fee if you list a sell order])
    cost    = offer's ISK fee + Jita cost of every required input item
    profit  = net_rev - cost
    ISK/LP  = profit / offer's LP cost   (the headline efficiency metric)

Usage:
    pip install requests
    python lp-scanner.py "Serpentis Inquest" 500000
    python lp-scanner.py "State Protectorate" 500000 --max-spread 20
    python lp-scanner.py --corp-id 1000180 250000 --instant
"""
import argparse
import sys
from pathlib import Path

import requests

from lp_core import (
    HIGH_SPREAD_PCT, LPError, default_cache_dir, evaluate, fetch_prices,
    get_offers, resolve_corp_id, resolve_corp_name, resolve_names,
)

# --- tiny terminal helpers (no external deps) ------------------------------
RESET, BOLD, DIM, GREEN, YELLOW, RED, CYAN = (
    "\033[0m", "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[36m")
USE_COLOR = True


def c(text, code):
    if not USE_COLOR or not code:
        return text
    return f"{code}{text}{RESET}"


def pad(text, width, align="<"):
    text = str(text)
    if len(text) > width:
        text = text[:width]
    return f"{text:{align}{width}}"


def isk(n):
    """Compact ISK: 1.23B / 45.6M / 789.0K / 12."""
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{n / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{n / 1e3:,.1f}K"
    return f"{n:,.0f}"


def main():
    ap = argparse.ArgumentParser(
        description="Find the most profitable LP-store item to buy and resell at Jita IV-4.")
    ap.add_argument("corp", nargs="?", help="NPC corporation name (quote it, e.g. \"Serpentis Inquest\")")
    ap.add_argument("lp", type=float, help="amount of Loyalty Points you have to spend")
    ap.add_argument("--corp-id", type=int, default=None,
                    help="use a corporation_id directly instead of resolving a name")
    ap.add_argument("--instant", action="store_true",
                    help="value sales by dumping into Jita BUY orders (sales tax only, no wait, "
                         "capped by buy depth) instead of listing a sell order")
    ap.add_argument("--sales-tax", type=float, default=0.045,
                    help="sales tax fraction (default 0.045; lower with Accounting skill)")
    ap.add_argument("--broker-fee", type=float, default=0.015,
                    help="broker fee fraction for listing a sell order (default 0.015; "
                         "ignored with --instant)")
    ap.add_argument("--top", type=int, default=20, help="rows to print (default 20)")
    ap.add_argument("--min-profit", type=float, default=None,
                    help="hide offers whose per-unit profit is below this ISK")
    ap.add_argument("--max-spread", type=float, default=None,
                    help="hide offers whose Jita ask/bid spread exceeds this %% "
                         "(e.g. 25). High spread = aspirational sell price, few/no real "
                         "buyers. Items with zero bids are always dropped.")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    ap.add_argument("--refresh", action="store_true", help="ignore cached LP offers, re-fetch")
    ap.add_argument("--cache-dir", default=None,
                    help="cache directory (default ./.eve_scanner_cache next to this script)")
    args = ap.parse_args()

    if args.corp_id is None and not args.corp:
        ap.error("give a corporation name (or --corp-id)")

    global USE_COLOR
    USE_COLOR = sys.stdout.isatty() and not args.no_color
    cache_dir = Path(args.cache_dir) if args.cache_dir else default_cache_dir()
    session = requests.Session()

    try:
        # 1) corp -> id (or id -> name when --corp-id was given)
        if args.corp_id is not None:
            corp_id = args.corp_id
            corp_name = resolve_corp_name(corp_id, session)
        else:
            corp_id, corp_name = resolve_corp_id(args.corp, session)

        # 2) offers
        offers = get_offers(corp_id, session, cache_dir, args.refresh)
        if not offers:
            sys.exit("No offers returned for that corporation.")

        # 3) price every output + required input item at Jita
        type_ids = set()
        for o in offers:
            type_ids.add(o["type_id"])
            for req in o.get("required_items", []):
                type_ids.add(req["type_id"])
        print(f"Pricing {len(type_ids)} item types at Jita IV-4 ...", file=sys.stderr)
        prices = fetch_prices(type_ids, session)
    except LPError as e:
        sys.exit(str(e))

    # 4) evaluate + filter
    sellable, unsellable = evaluate(offers, prices, args.lp,
                                    args.sales_tax, args.broker_fee, args.instant)
    if args.min_profit is not None:
        sellable = [r for r in sellable if r["profit_per"] >= args.min_profit]
    if args.max_spread is not None:
        sellable = [r for r in sellable
                    if r["spread_pct"] is not None and r["spread_pct"] <= args.max_spread]
    if not sellable:
        sys.exit("No offers left after filtering (try a higher --max-spread / lower --min-profit).")

    names = resolve_names(type_ids, session, cache_dir)

    # 5) report
    mode = "instant (dump to Jita BUY orders)" if args.instant else "patient (list a Jita SELL order)"
    fee_txt = (f"{args.sales_tax:.1%} tax" if args.instant
               else f"{args.sales_tax:.1%} tax + {args.broker_fee:.1%} broker")
    print(f"\n{c(corp_name, BOLD)}  |  budget {c(f'{args.lp:,.0f} LP', BOLD)}  |  "
          f"sell mode: {mode}  |  fees: {fee_txt}")
    print(c(f"Ranked by ISK per LP. {len(sellable)} sellable offers "
            f"({len(unsellable)} have no Jita market).", DIM))

    # Size the Item column to the longest name shown so nothing gets truncated.
    def row_flags(r):
        f = ""
        if r["req_missing"]:
            f += "*"          # a required input had no Jita price (cost understated)
        if r["ak_cost"]:
            f += "^"          # offer also costs Analysis Kredits (not counted)
        if r["spread_pct"] is None or r["spread_pct"] >= HIGH_SPREAD_PCT:
            f += "!"          # wide/absent bid -> the ask price is not real demand
        return f

    shown_rows = [(r, names.get(r["name_id"], str(r["name_id"])) + row_flags(r))
                  for r in sellable[:args.top]]
    name_w = max([len("Item")] + [len(cell) for _, cell in shown_rows])

    header = (pad("Item", name_w) + pad("Qty", 6, ">") + pad("LP cost", 10, ">")
              + pad("Cost/ea", 10, ">") + pad("Ask", 11, ">") + pad("Bid", 11, ">")
              + pad("Spr%", 7, ">") + pad("Profit/ea", 11, ">") + pad("ISK/LP", 9, ">")
              + pad("Max", 7, ">") + pad("TotProfit", 11, ">") + pad("BuyVol", 9, ">"))
    print(c(header, BOLD + CYAN))
    print(c("-" * len(header), DIM))

    best = sellable[0]
    for r, name_cell in shown_rows:
        spread = r["spread_pct"]
        ipl = r["isk_per_lp"]
        ipl_col = GREEN if ipl > 0 else RED
        spread_str = "no bid" if spread is None else f"{spread:.0f}%"
        spread_col = RED if (spread is None or spread >= HIGH_SPREAD_PCT) else (
            YELLOW if spread >= 10 else GREEN)
        line = (pad(name_cell, name_w)
                + pad(f"{r['qty']:,}", 6, ">")
                + pad(f"{r['lp_cost']:,}", 10, ">")
                + pad(isk(r["isk_cost"] + r["req_cost"]), 10, ">")
                + pad(isk(r["ask"]) if r["ask"] else "-", 11, ">")
                + pad(isk(r["bid"]) if r["bid"] else "-", 11, ">")
                + c(pad(spread_str, 7, ">"), spread_col)
                + c(pad(isk(r["profit_per"]), 11, ">"), ipl_col)
                + c(pad(f"{ipl:,.1f}", 9, ">"), ipl_col)
                + pad(f"{r['max_units']:,}", 7, ">")
                + c(pad(isk(r["total_profit"]), 11, ">"), ipl_col)
                + pad(f"{r['buy_volume']:,.0f}", 9, ">"))
        print(line)
    print(c("-" * len(header), DIM))

    # Headline recommendation for the budget.
    if best["max_units"] >= 1:
        leftover = args.lp - best["lp_used"]
        best_name = names.get(best["name_id"], str(best["name_id"]))
        ipl_txt = c(f"{best['isk_per_lp']:,.1f} ISK/LP", BOLD)
        isk_in_txt = c(isk(best["total_isk_in"]) + " ISK", BOLD)
        profit_txt = c(isk(best["total_profit"]) + " ISK profit", GREEN + BOLD)
        print(f"\n{c('Best pick:', BOLD)} {c(best_name, GREEN)} at {ipl_txt}.")
        print(f"  Spend {isk(best['lp_used'])} LP -> redeem {best['max_units']:,}x "
              f"(needs {isk_in_txt} on top of LP) -> ~{profit_txt}.")
        if best["spread_pct"] is None:
            print(c("  WARNING: there are NO buy orders for this item -- the profit assumes "
                    "you eventually sell at the ask, which may never fill. Treat as illiquid.", RED))
        elif best["spread_pct"] >= HIGH_SPREAD_PCT:
            print(c(f"  WARNING: {best['spread_pct']:.0f}% ask/bid spread -- the ask is "
                    f"aspirational. Highest real bid is only {isk(best['bid'])} "
                    f"(vs {isk(best['ask'])} ask). You'd likely have to undercut hard.", YELLOW))
        if best["buy_volume"] < best["max_units"]:
            print(c(f"  Note: standing buy demand is only ~{best['buy_volume']:,.0f} units; "
                    f"selling all {best['max_units']:,} will take time / repeated relisting.", YELLOW))
        if leftover > 0:
            print(c(f"  {leftover:,.0f} LP left over (not enough for another unit of the best offer).", DIM))
    else:
        print(c(f"\nYour {args.lp:,.0f} LP isn't enough for even one unit of the best offer "
                f"({best['lp_cost']:,} LP each).", YELLOW))

    print(c("\nLegend: ISK/LP = profit per loyalty point (higher is better). "
            "Profit is net of fees and any required-item costs.", DIM))
    print(c("  Cost/ea = full ISK to acquire one (store ISK fee + Jita cost of required items), "
            "on top of the LP.", DIM))
    print(c("  Ask = lowest Jita sell price (what you'd list at).  Bid = highest Jita BUY "
            "order (what someone will actually pay right now).", DIM))
    print(c(f"  Spr% = ask/bid spread; >= {HIGH_SPREAD_PCT:.0f}% (flagged !) means thin/illiquid -- "
            "the ask isn't backed by real demand.  BuyVol = units of standing buy demand.", DIM))
    print(c("  * required input had no Jita price (true cost higher).   "
            "^ offer also costs Analysis Kredits (not counted).", DIM))
    print(c("  Prices are a live Jita IV-4 snapshot and can be manipulated -- verify big buys.", DIM))


if __name__ == "__main__":
    main()
