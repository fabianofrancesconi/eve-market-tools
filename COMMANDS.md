# EVE Market Tools — quick reference

Everything lives in one web app (`lp-web.py`). There are no standalone CLI
scripts anymore — the old `lp-scanner.py` / `eve-scanner.py` tools were folded
into the web UI. See `README.md` for the full write-up; this is the cheat sheet.

## Running

```bash
pip install requests            # only runtime dependency for local use
python lp-web.py                # serves http://localhost:8765 and opens a browser
python lp-web.py --port 9000    # custom port
python lp-web.py --no-browser   # don't auto-open the browser
```

Log in with your EVE account (top-right) to load your wallet, skills, loyalty
points, blueprints and running jobs. Login is optional locally (except the
Industry planner) and mandatory on a hosted `DATABASE_URL` deploy.

## Tabs

| Tab | What it does |
|-----|--------------|
| **LP Store** | Rank an NPC corp's loyalty-store offers by ISK/LP. Enter corp + LP budget, pick a hub to sell into, sort/filter, click a row for the shopping list, live costs, cargo m³ and a redemption calculator. Shows your live LP balance when logged in. |
| **Arbitrage** | Download a region's order book and find cross-station (haul) or same-station (flip) negative-spread deals, filtered by Jita jump range, route type and highsec-only. Click a row for a price-history chart. |
| **Industry** | Rank manufacturable items by ISK/hour after materials, job cost and BPC cost (T1 build + T2 invention). Build-location profiles, tradeability/days-to-sell, cargo m³, favourites, drag-reorder columns. Uses your real skills, owned blueprints and running jobs. *Requires login.* |
| **Exploration** | Per-site-type hacking walkthroughs (data/relic, ghost, sleeper cache, wormhole, gas) plus a "How hacking works" rulebook. |
| **Abyss** | Abyssal Deadspace companion: pick Tier + Weather for the run's conditions, pick the room's faction for per-enemy kill-priority, weakness and best-missile cards. |
| **Overview** | Your capsuleer dashboard: wallet + history chart, SP, LP per corp, skill queue, running jobs, and active market orders (expired-unsold vs sold called out). Multiple characters, switchable from the header. *Appears once logged in.* |
| **Notes** | Foldered, drag-to-reorder notepad saved per account. |

## Global cost settings

Sales tax and broker fee (shared by LP Store + Industry) are computed from your
Accounting / Broker Relations skills when logged in, so profit figures match your
character.

## Caching (you mostly don't think about this)

The order-book cache is driven by ESI's own freshness metadata, not a timer:

- Inside ESI's `Expires` window (market orders refresh on a ~5 min cycle) the cache is served with **zero** network calls.
- Once expired, the app sends the stored `ETag` as a conditional request. ESI replies `304 Not Modified` (a few bytes) when the market hasn't moved, so the cache is reused and its expiry bumped; only a genuinely new snapshot triggers a full re-download.
- If ESI is unreachable, the last cached book is reused (stale) instead of failing.

LP store offers are cached 24 h; hub prices are always live. Use **⟳ Refresh** on a
tab to force a fresh pull, and **⟳ Refresh SDE** on Industry to rebuild the
blueprint database after a game patch.

Cache lives in `.eve_scanner_cache/` next to `lp-web.py` (or the Docker volume).
Delete that folder any time to wipe the on-disk caches.

## Common region IDs (Arbitrage)

| Region | ID | Hub |
|---|---|---|
| The Forge | 10000002 | Jita |
| Domain | 10000043 | Amarr |
| Sinq Laison | 10000032 | Dodixie |
| Metropolis | 10000042 | Hek |
| Heimatar | 10000030 | Rens |
