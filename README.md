# EVE Market Tools

> **Try it now** — no install required: **[eve-market-tools-production.up.railway.app](https://eve-market-tools-production.up.railway.app/)**

A web app bundling a suite of market, industry and PvE-guide utilities for EVE
Online, served from a single Python script with no framework dependencies beyond
`requests` (plus `psycopg` when run as a hosted multi-user service). Log in with
your EVE account and the tools fill in with your own wallet, skills, loyalty
points, blueprints and running jobs.

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
[![Docker](https://img.shields.io/badge/ghcr.io-eve--market--tools-blue?logo=docker)](https://ghcr.io/fabianofrancesconi/eve-market-tools)

---

## Tools

The app is a single-page shell with tabs across the top: **LP Store**,
**Arbitrage**, **Industry**, **Exploration**, **Abyss**, **Overview** (shown once
you log in) and **Notes**.

### LP Store Scanner
Ranks LP store offers for any NPC corporation by ISK/LP efficiency. Enter a
corporation and your LP budget to see a sorted table of offers with profit per
redemption, total profit, buy demand and a **Tradeability** score (weighting you
can tilt toward liquidity or quiet markets). Sell into any of the five major hubs
(Jita, Amarr, Rens, Dodixie, Hek). Filter by name, hide illiquid or unaffordable
rows, and pick which columns show. Click any row for a drill-down panel with the
full shopping list, live hub order-book costs, cargo volumes and a redemption
calculator. When logged in, your live LP balance for the corp is shown inline.

### Arbitrage Scanner
Downloads the full public order book for a region and finds cross-station
negative-spread opportunities — items you can buy in one station and resell into a
buy order in another at a profit after sales tax. Or run it in same-station mode
for instant flips. Filters to deals with a Jita leg within a configurable
round-trip jump range, with a routing preference (shortest / secure / insecure)
and a highsec-only toggle. A progress bar streams the download live; the region
book is cached and reused across scans. Click a row for a price-history chart.

### Industry Planner
Ranks manufacturable items by how worthwhile they are to build, after material
cost (bought at a selectable hub), job install cost and blueprint cost. Covers T1
manufacturing and T2 invention (datacores, success probability, runs-per-BPC).
Sorts by ISK/hour using real build times, layers in required skills, a
market-history tradeability / "days to sell" score, and input/output cargo m³ for
a chosen batch size.

Build-location profiles capture each station or structure's system cost index,
structure bonus, facility tax and SCC surcharge via a wizard, so the job cost
reflects where you actually build. Star items to pin them to the top; toggles let
you show only buildable items, hide T2, hide blueprints you only own as copies, or
include blueprints you don't own. When logged in, the planner uses your character's
*actual* trained skills, knows which blueprints you own (BPO vs BPC), and shows
your running manufacturing jobs as a live countdown in the Timer column (matched by
blueprint). Columns are drag-to-reorder. Click any row for a full per-item
breakdown. Blueprint data comes from a local SQLite copy of the Fuzzwork SDE dump,
rebuilt on demand.

### Exploration guide
A hands-on walkthrough for hacking exploration sites — data/relic sites, ghost
sites, sleeper caches, wormhole sites and gas clouds. Browse every site type
grouped in the sidebar or search by name, then read a single flowing guide per
site: what it is, the key facts, and a step-by-step "how to hack it" walkthrough
including the escalation special event. A pinned "How hacking works" rulebook
overlay explains the minigame from scratch. Mechanics and numbers are sourced from
the EVE University wiki.

### Abyss guide
A combat companion for Abyssal Deadspace. Pick your **Tier** and **Weather** to see
the run's conditions — difficulty, the bonus/penalty and the damage type to exploit
— then pick the **faction** of the room you actually landed in to get per-enemy
cards: what each enemy deals, what it's weak to, kill-priority tags (logistics and
tackle called out), spawn likelihood, and best-missile recommendations. Sourced
from the EVE University wiki plus community telemetry and fits.

### Overview (character)
Available once you log in. A dashboard of your capsuleer: wallet balance with a
zoomable/pannable wallet-history chart, total SP, loyalty points per corporation,
your skill queue with finish times, your currently running industry jobs with live
countdowns, and your active market orders — with expired-but-unsold orders called
out distinctly from completed sales, and a relative expiry countdown. Link
**multiple characters** (alts) to one login and switch the active one from the
header dropdown.

### Notes
A built-in notepad with foldered, drag-to-reorder notes and adjustable font size —
handy for shopping lists, route plans or anything you want to keep alongside the
tools. Notes are saved per account.

---

## Log in with EVE

Log in with your EVE Online account to pull live character data into the tools.
Click **Log in with EVE** in the top-right corner. You can link **multiple
characters** (alts) to one login and switch the active one from the header
dropdown; click the **✕** on a character to log it out.

What logging in adds:
- The **Overview** tab (see above) — wallet, wallet-history chart, SP, loyalty
  points, skill queue, running industry jobs and active market orders.
- **LP Store** shows your live LP balance for the corporation you're viewing.
- **Industry** uses your character's *actual* trained skill levels for build times
  and the Build? gate, knows which blueprints you own (BPO vs BPC), and drives the
  Timer column from your real running jobs (re-pulled every 5 minutes, EVE's cache
  cadence for industry jobs). The Industry planner **requires** login.
- Sales tax and broker fee are derived from your Accounting / Broker Relations
  skills automatically.

Login is **optional when running locally** (the market tools work without it, minus
the character-specific features and the Industry planner) but **required on a
shared/hosted deployment** — see *Deployment modes* below.

### One-time setup

EVE's SSO requires registering an application (free, takes a minute). The Client ID
and callback are **configured via environment variables on the server**, not
entered in the UI:

1. Go to **[developers.eveonline.com/applications](https://developers.eveonline.com/applications)** and create a new application.
2. Set **Connection Type** to *Authentication Only*.
3. Set the **Callback URL** to exactly match `EVE_CALLBACK_URL` (see below). For local use that's `http://localhost:8765/callback`; for a hosted deploy it's `https://your-host/callback`.
4. Copy the application's **Client ID**.
5. Start the server with the two environment variables set:

   ```bash
   EVE_CLIENT_ID=<your-client-id> \
   EVE_CALLBACK_URL=http://localhost:8765/callback \
   python lp-web.py
   ```

   `EVE_CALLBACK_URL` is optional locally (defaults to `http://localhost:<port>/callback`); set it explicitly for any non-localhost host.
6. Click **Log in with EVE**, approve on EVE's site, and you'll be redirected back logged in.

This uses the OAuth2 **PKCE** native-app flow, so there is **no client secret**. The
scopes requested are read-only: skills, skill queue, wallet, loyalty points, market
orders, blueprints and industry jobs.

## Deployment modes

The same `lp-web.py` runs in one of two modes, chosen automatically by whether a `DATABASE_URL` is set:

- **Local / single-user (no `DATABASE_URL`)** — all state (tokens, settings, caches) lives in the cache directory (`.eve_scanner_cache/`). Login is optional; there's one implicit user. This is the default `python lp-web.py` behaviour and what the test suite exercises.
- **Hosted / multi-user (`DATABASE_URL` set, e.g. Postgres)** — the app becomes a real multi-user service. Login with EVE is **mandatory**: a browser session cookie is issued on login and every API endpoint requires it, so unauthenticated visitors can't see or touch anyone's data. Each login is an **account** (a set of linked characters); all durable state — SSO tokens, per-account settings (searches, filters, columns — server-authoritative, so the same account sees an identical view across browsers/devices), notes, delivered-run and order-sale counters — is stored in Postgres (`mono_*` tables). Rebuildable caches (the SDE, ESI/market JSON) stay on disk. Run it as a **single replica**: sessions/accounts are cached in-process and token refresh is serialised per-process.

---

## Docker

Pre-built images are published to the GitHub Container Registry on every release.

### Quick start

```bash
docker run -p 8765:8765 \
  -v eve-scanner-cache:/app/.eve_scanner_cache \
  ghcr.io/fabianofrancesconi/eve-market-tools:latest
```

Then open `http://localhost:8765`.

The named volume `eve-scanner-cache` keeps the order book and LP store cache across container restarts so you don't re-download everything each run.

### Docker Compose

```yaml
services:
  eve-market-tools:
    image: ghcr.io/fabianofrancesconi/eve-market-tools:latest
    ports:
      - "8765:8765"
    volumes:
      - eve-scanner-cache:/app/.eve_scanner_cache
    restart: unless-stopped

volumes:
  eve-scanner-cache:
```

Save as `docker-compose.yml` and run:

```bash
docker compose up -d
```

### Ansible

```yaml
- name: Deploy EVE Market Tools
  hosts: your_host
  tasks:
    - name: Pull latest image
      community.docker.docker_image:
        name: ghcr.io/fabianofrancesconi/eve-market-tools
        tag: latest
        source: pull
        force_source: true
      register: image_pull

    - name: Run container
      community.docker.docker_container:
        name: eve-market-tools
        image: ghcr.io/fabianofrancesconi/eve-market-tools:latest
        ports:
          - "8765:8765"
        volumes:
          - eve-scanner-cache:/app/.eve_scanner_cache
        restart_policy: unless-stopped
        recreate: "{{ image_pull.changed }}"
        state: started
```

`recreate: "{{ image_pull.changed }}"` ensures the container is only restarted when a newer image was actually downloaded — repeated runs with no new release are a no-op.

Requires the `community.docker` collection (`ansible-galaxy collection install community.docker`).

### Releases

A new GitHub Release and versioned image tag are created whenever a `v*` tag is pushed:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This publishes:
- `ghcr.io/fabianofrancesconi/eve-market-tools:1.0.0`
- `ghcr.io/fabianofrancesconi/eve-market-tools:latest`
- `ghcr.io/fabianofrancesconi/eve-market-tools:sha-<commit>`

The Docker image is only built on `v*` tag pushes.

---

## Running from source

### Requirements

```
pip install requests
```

Python 3.8 or later. `requests` is the only runtime dependency for local use; a
hosted multi-user deploy additionally needs `psycopg` (see `requirements.txt`).

### Usage

```bash
python lp-web.py
```

Opens `http://localhost:8765` in your default browser automatically.

```bash
python lp-web.py --port 9000   # custom port
python lp-web.py --no-browser  # don't auto-open browser
```

---

## Global cost settings

Sales tax and broker fee are shared by the LP Store and Industry tools. When you're
logged in they're computed from your **Accounting** and **Broker Relations** skills
automatically (`7.5% × (1 − 0.11 × level)` and `3% − 0.3% × level`), so the profit
numbers match your character.

## LP Store tab

| Field | Default | Notes |
|-------|---------|-------|
| Corporation | — | Any NPC corp name; autocomplete from the full ESI list |
| LP budget | 500,000 | Used to calculate max redemptions and total profit |
| Max spread % | 20 | Hide offers where ask/bid spread exceeds this threshold |
| Market | Jita 4-4 | Hub to price and sell into (Jita / Amarr / Rens / Dodixie / Hek) |
| Search | — | Filter the table by item name |
| Tradeability | Balanced | Tilt the liquidity-vs-quiet-market weighting of the score |

Toggles hide illiquid or unaffordable rows; **Columns ▾** picks visible columns. LP
store offer lists are cached for 24 hours; hub prices are always fetched live. Use
**⟳ Refresh** to force a fresh pull from ESI.

---

## Arbitrage tab

| Field | Default | Notes |
|-------|---------|-------|
| Region | The Forge (Jita) | Region to scan |
| Mode | Cross-station (haul) | Cross-station finds haul opportunities; Same-station finds instant flips |
| Min ISK opp | — | Filter out low-value opportunities |
| Max jumps (RT) | 6 | Round-trip jump cap; only deals with a Jita leg within this range are shown |
| Route | Shortest | ESI routing preference (shortest / secure / insecure) for jump-count calculation |
| Highsec only | off | Hide any deal whose route touches lowsec or nullsec |

The full region order book is cached locally and reused across scans — only **⟳
Refresh** hits ESI again. When the cached snapshot has expired (ESI's ~5 min market
cycle), a notice appears in the status bar prompting you to refresh. A scan progress
bar streams live updates as the order book downloads (first run per region can take
~30 seconds; subsequent scans are instant from cache). Click a row for a price
history chart.

---

## Industry tab

*Requires login.*

| Field | Default | Notes |
|-------|---------|-------|
| Category | All | Market group to scan (or everything) |
| Source hub | Jita | Trade hub where materials are priced and the product is sold |
| Build location | — | Saved station/structure profile (cost index, bonuses, taxes); ＋ adds one via a wizard, ✎ edits |
| Min trade | 0 | Hide products whose market absorbs too little volume |
| Balance | Balanced | Tilt the tradeability score toward liquidity or quiet markets |
| Search | — | Filter the table by item name |

Toggles: **Buildable only** (skills you have), **Include unobtainable** (blueprints
you don't own), **Hide T2**, **Hide my BPCs**. Blueprint data is built once from the
Fuzzwork SDE dump into a local SQLite database. Use **⟳ Refresh SDE** to rebuild it
after a game patch. Favourites, the build-location profiles, and the column order
persist across visits.

---

## Data sources

- **ESI** (`https://esi.evetech.net`) — LP store offers, order books, universe names, routes, market history, and (when logged in) your wallet, skills, loyalty points, orders and jobs
- **Fuzzwork aggregates** (`https://market.fuzzwork.co.uk/aggregates/`) — best bid/ask prices per hub for the LP and industry scanners
- **Fuzzwork SDE dump** (`https://www.fuzzwork.co.uk/dump/latest/csv/`) — static blueprint, material and type data for the Industry planner
- **EVE University wiki** and community sources — mechanics, numbers and tactics behind the Exploration and Abyss guides (credited inline in each guide)

ESI excludes most player-built Upwell structure markets, so prices can differ slightly from the in-game view. Combat/exploration figures shift between patches — treat them as approximate.

---

## Cache

All cached data lives in `.eve_scanner_cache/` next to the script (or in the Docker volume):

| File | Contents |
|------|----------|
| `orders_region_*.json` | Full order book per region (with ESI ETag for conditional revalidation) |
| `lpstore_*.json` | LP store offers per corporation (24 h TTL) |
| `lookups.json` | Station names, system security, routes (static universe data, kept indefinitely) |
| `lp_names.json` | Item name lookups |
| `lp_volumes.json` | Packaged cargo volumes |
| `npc_corps.json` | NPC corporation list |
| `sde_industry.sqlite` | Blueprint/material/type data built from the Fuzzwork SDE dump (rebuilt on demand) |
| `lp_web_settings.json` | Last-used LP scanner form values |
| `arb_settings.json` | Last-used Arbitrage form values |
| `ind_settings.json` | Industry planner form values, build-location profiles, favourites and column order |

In local single-user mode these hold the settings; on a hosted multi-user deploy the
durable state lives in Postgres instead, and only the rebuildable caches stay on
disk. In the browser, form preferences (corporation, budgets, filters, column widths
and order, sort order) are also mirrored to `localStorage` and restored on the next
visit.

---

## License

MIT
