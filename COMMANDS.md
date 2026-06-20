# eve-scanner quick commands

## LP-store web UI (`lp-web.py`)

Browser front-end for the LP scanner. Same numbers as the CLI (both import
`lp_core.py`), but clickable: type a corp + your LP, get the ranked table, then
**click any row** to see the full shopping list of items to buy, their Jita
prices, and the **m3 of cargo** each leg of the haul occupies. A redemptions box
in the detail panel scales the whole plan up/down live.

```
pip install requests            # only dependency
python lp-web.py                # serves http://localhost:8765 and opens a browser
python lp-web.py --port 9000 --no-browser
```

Controls at the top: corporation, LP budget, sell mode (patient/instant), max
spread %, sales tax, broker fee. Click column headers to sort. Illiquid rows
(spread >= 25%) are flagged `!` and their spread shows red. The detail panel
shows total profit, ISK/LP, the required-item shopping list, cargo volume to
haul in (required items) and out (the reward to Jita), and liquidity warnings.

---

## LP-store profit scanner (`lp-scanner.py`)

Separate tool: give it an NPC corporation name and how much LP you have, and it
ranks that corp's loyalty-store offers by **ISK per LP** for reselling at Jita
IV-4. It prices every reward item *and* every required input live (Fuzzwork Jita
aggregate), nets out sales tax / broker fee, and tells you how many units your LP
buys plus the total profit and ISK outlay.

```
# Best way to spend 500k Serpentis Inquest LP (patient: list Jita sell orders)
python lp-scanner.py "Serpentis Inquest" 500000

# Value sales by dumping into Jita BUY orders instead (instant, no broker fee,
# capped by buy depth)
python lp-scanner.py "Serpentis Inquest" 500000 --instant

# Skip name lookup with a known corporation_id; show more rows
python lp-scanner.py --corp-id 1000157 250000 --top 40

# Only show genuinely liquid items (drop anything with a >20% ask/bid spread).
# Strongly recommended -- raw ISK/LP is dominated by thin-market mirages.
python lp-scanner.py "State Protectorate" 500000 --max-spread 20

# Tune your real fees (better Accounting/Broker Relations skills + standings)
python lp-scanner.py "Federation Navy" 1000000 --sales-tax 0.034 --broker-fee 0.01
```

| Flag | Default | Meaning |
|---|---|---|
| `corp` (positional) | – | NPC corp name, quoted (e.g. `"Serpentis Inquest"`) |
| `lp` (positional) | – | Loyalty Points you have to spend |
| `--corp-id` | – | Use a corporation_id directly, skip name resolution |
| `--instant` | off | Value sales into Jita buy orders (tax only) vs. listing sell orders |
| `--sales-tax` | 0.045 | Sales tax fraction |
| `--broker-fee` | 0.015 | Broker fee fraction (sell-order mode only) |
| `--top` | 20 | Rows to print |
| `--min-profit` | – | Hide offers below this per-unit ISK profit |
| `--max-spread` | – | Hide offers whose Jita ask/bid spread exceeds this % (items with zero bids are always dropped) |
| `--refresh` | off | Re-fetch offers (otherwise cached 24h; prices are always live) |

**Always read the buy side.** The table shows `Ask` (lowest sell), `Bid`
(highest real buy order), `Spr%` (the spread) and `BuyVol` (standing buy
demand). A wide spread (flagged `!`, default ≥25%) means the `Ask` price the
profit is computed from is aspirational — nobody is actually bidding near it, so
the headline ISK/LP is fiction. Use `--max-spread 20` to strip those out and see
what you can really sell. Faction navy modules, navy ammo/drones, datacores and
cap boosters are the dependable tight-spread earners in faction-warfare stores.

---


Cache lives in `.eve_scanner_cache/` next to `eve-scanner.py`. Delete that folder any time to wipe everything.

## Caching (you mostly don't think about this)

The order-book cache is driven by ESI's own freshness metadata, not a timer:

- Inside ESI's `Expires` window (market orders refresh on a ~5 min cycle) the cache is served with **zero** network calls.
- Once expired, the scanner sends the stored `ETag` as a conditional request. ESI replies `304 Not Modified` (a few bytes) when the market hasn't moved, so the cache is reused and its expiry bumped; only a genuinely new snapshot triggers a full re-download.
- If ESI is unreachable, the last cached book is reused (stale) instead of failing.

So you can re-run as often as you like — iterating on `--max-jumps`, `--top`, `--min-isk`, etc. is effectively free. Use `--refresh` only if you want to force a full pull (rarely needed).

## Output

Every scan prints the detailed deal table, and — when the deals span more than one pickup location — a second **"By source station (From)"** view that aggregates cumulative ISK opportunity per source station (sorted high to low). Use it to spot the single station where stocking up captures the most profit. Cached runs also print when the data expires, e.g. `expires 13:25:00 UTC (~4 min)`.

## Basic scans

```
# Default: The Forge / Jita region, same-station instant flips, 7.5% tax
python eve-scanner.py

# Allow hauling between different stations in the region (real arbitrage)
python eve-scanner.py --cross-station

# Scan a different region (Domain / Amarr)
python eve-scanner.py --region 10000043

# Lower sales tax (better Accounting skill/standings), show more rows
python eve-scanner.py --sales-tax 0.034 --top 60

# Hide small opportunities (under 1M ISK total)
python eve-scanner.py --min-isk 1000000
```

## Jita + jump-range filtering

```
# Deals with a Jita leg, other leg within 5 jumps one-way (default), hauling allowed
python eve-scanner.py --cross-station --from-jita

# Same, but treat 5 jumps as a ROUND-TRIP cap (there and back)
python eve-scanner.py --cross-station --from-jita --round-trip

# Tighter: only 3 jumps one-way from Jita
python eve-scanner.py --cross-station --from-jita --max-jumps 3

# Wider hauling net: 10 jumps round-trip, worthwhile deals only
python eve-scanner.py --cross-station --from-jita --max-jumps 10 --round-trip --min-isk 5000000
```

## Useful recipes

```
# "What can I instant-flip in Jita right now?" -- biggest same-station mispricings
python eve-scanner.py --top 25

# Realistic hauler run: Jita-anchored, <=5 jumps round-trip, skip the noise,
# lower tax to reflect decent Accounting skills
python eve-scanner.py --cross-station --from-jita --round-trip \
    --sales-tax 0.045 --min-isk 2000000 --top 30

# Scan Amarr (Domain) the same way
python eve-scanner.py --region 10000043 --cross-station --from-jita --round-trip --min-isk 2000000

# Quick high-value-only sweep of the whole Forge region (no jump filter)
python eve-scanner.py --cross-station --min-isk 10000000 --top 50

# Force-refresh then iterate on range for free (cache revalidation is cheap anyway)
python eve-scanner.py --refresh --cross-station --from-jita
python eve-scanner.py --cross-station --from-jita --max-jumps 3
python eve-scanner.py --cross-station --from-jita --max-jumps 8 --round-trip
```

## Common region IDs

| Region | ID | Hub |
|---|---|---|
| The Forge | 10000002 | Jita |
| Domain | 10000043 | Amarr |
| Sinq Laison | 10000032 | Dodixie |
| Heimatar | 10000030 | Rens |
| Metropolis | 10000042 | Hek |

## All flags

| Flag | Default | Meaning |
|---|---|---|
| `--region` | 10000002 | Region ID (10000002 = The Forge/Jita) |
| `--sales-tax` | 0.075 | Sales tax as a fraction |
| `--cross-station` | off | Allow buy/sell at different stations |
| `--min-isk` | 0 | Hide opportunities below this total ISK |
| `--top` | 40 | Rows to print |
| `--from-jita` | off | Only keep deals with a Jita leg |
| `--max-jumps` | 5 | Max jumps from Jita for the other leg |
| `--round-trip` | off | Treat `--max-jumps` as round-trip (doubles one-way count) |
| `--refresh` | off | Force a full ESI pull (cache otherwise auto-refreshes via ESI metadata) |
| `--cache-dir` | `./.eve_scanner_cache` | Cache directory location |
