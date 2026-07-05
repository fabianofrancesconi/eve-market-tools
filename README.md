# EVE Market Tools

> **Try it now** — no install required: **[eve-market-tools-production.up.railway.app](https://eve-market-tools-production.up.railway.app/)**

A web app with three market utilities for EVE Online, served from a single Python script with no framework dependencies beyond `requests`.

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
[![Docker](https://img.shields.io/badge/ghcr.io-eve--market--tools-blue?logo=docker)](https://ghcr.io/fabianofrancesconi/eve-market-tools)

---

## Tools

### LP Store Scanner
Ranks LP store offers for any NPC corporation by ISK/LP efficiency. Enter your corporation and LP budget to see a sorted table of offers with profit per redemption, total profit, buy demand, and spread quality. Click any row to open a drill-down panel with the full shopping list, live Jita order-book costs, cargo volumes, and a redemption calculator.

### Arbitrage Scanner
Downloads the full public order book for a region and finds cross-station negative-spread opportunities — items where you can buy a sell order in one station and resell into a buy order in another at a profit after sales tax. Filters to deals with a Jita leg within a configurable round-trip jump range.

### Industry Planner
Ranks manufacturable items by how worthwhile they are to build, after material cost (bought at a selectable hub), job install cost and blueprint cost. Covers T1 manufacturing and T2 invention (datacores, success probability, runs-per-BPC). Sorts by ISK/hour using real build times, layers in required skills, market-history "days to sell" / tradeability score, and input/output cargo m³ for a chosen batch size.

Build-location profiles capture each station or structure's ME/TE, system cost index, structure bonus, facility tax and SCC surcharge via a wizard, so the job cost reflects where you actually build. Star items to pin them to the top regardless of filters. When you're logged in with EVE, your actually-running manufacturing jobs show a live countdown in the Timer column (matched by blueprint). Columns are drag-to-reorder. Click any row for a full per-item breakdown. Blueprint data comes from a local SQLite copy of the Fuzzwork SDE dump, rebuilt on demand.

---

## Log in with EVE

Log in with your EVE Online account to pull live character data into the tools. Click **Log in with EVE** in the top-right corner. You can link **multiple characters** (alts) to one login and switch the active one from the header dropdown.

What it adds:
- A **Character** tab with your wallet balance, total SP, **loyalty points** per corporation, your **skill queue** with finish times, and your **currently running industry jobs** with live countdown timers.
- The **LP Store** tab shows your LP balance for the corporation you're viewing.
- The **Industry** planner gains a **My skills** toggle that swaps the uniform "Skills @" assumption for your character's *actual* trained skill levels, so build times and the Build? gate match your character.
- Your real running manufacturing jobs drive the Industry table's timer column (matched by blueprint), with a per-second live countdown. The job list is re-pulled from EVE every 5 minutes (EVE's cache cadence for industry jobs).

Login is **optional when running locally** (the tools work without it, minus the character-specific features) but **required on a shared/hosted deployment** — see *Deployment modes* below.

### One-time setup

EVE's SSO requires registering an application (free, takes a minute). Unlike older versions, the Client ID and callback are **configured via environment variables on the server**, not entered in the UI:

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

This uses the OAuth2 **PKCE** native-app flow, so there is **no client secret**. The scopes requested are read-only: skills, skill queue, wallet, loyalty points, market orders, blueprints and industry jobs. Click the **✕** on a character chip to log out that character.

## Deployment modes

The same `lp-web.py` runs in one of two modes, chosen automatically by whether a `DATABASE_URL` is set:

- **Local / single-user (no `DATABASE_URL`)** — all state (tokens, settings, caches) lives in the cache directory (`.eve_scanner_cache/`). Login is optional; there's one implicit user. This is the default `python lp-web.py` behaviour and what the test suite exercises.
- **Hosted / multi-user (`DATABASE_URL` set, e.g. Postgres)** — the app becomes a real multi-user service. Login with EVE is **mandatory**: a browser session cookie is issued on login and every API endpoint requires it, so unauthenticated visitors can't see or touch anyone's data. Each login is an **account** (a set of linked characters); all durable state — SSO tokens, per-account settings (searches, filters, columns — server-authoritative, so the same account sees an identical view across browsers/devices), delivered-run and order-sale counters — is stored in Postgres (`mono_*` tables). Rebuildable caches (the SDE, ESI/market JSON) stay on disk. Run it as a **single replica**: sessions/accounts are cached in-process and token refresh is serialised per-process.

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
- `ghcr.io/fabianofrancesconi/eve-market-tools:1.0`
- `ghcr.io/fabianofrancesconi/eve-market-tools:latest`

Pushes to `master` without a tag update `:latest` only.

---

## Running from source

### Requirements

```
pip install requests
```

Python 3.8 or later. No other dependencies.

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

## LP Store tab

| Field | Default | Notes |
|-------|---------|-------|
| Corporation | — | Any NPC corp name; autocomplete from the full ESI list |
| LP budget | 500,000 | Used to calculate max redemptions and total profit |
| Sell mode | Patient | Patient = list a sell order; Instant = sell into buy orders |
| Max spread % | 20 | Hide offers where ask/bid spread exceeds this threshold |
| Sales tax | 4.5% | Adjust to your Accounting skill level |
| Broker fee | 1.5% | Applies in Patient mode only |

LP store offer lists are cached for 24 hours. Jita prices are always fetched live. Use **⟳ Refresh** to force a fresh pull of a corp's offers from ESI.

---

## Arbitrage tab

| Field | Default | Notes |
|-------|---------|-------|
| Region | The Forge (Jita) | Region to scan |
| Mode | Cross-station (haul) | Cross-station finds haul opportunities; Same-station finds instant flips |
| Sales tax | 7.5% | Adjust to your Accounting skill level |
| Min ISK opp | — | Filter out low-value opportunities |
| Max jumps (RT) | 6 | Round-trip jump cap; only deals with a Jita leg within this range are shown |
| Route | Shortest | ESI routing preference for jump-count calculation |
| Highsec only | off | Hide any deal whose route touches lowsec or nullsec |

The full region order book is cached locally and reused across scans — only **⟳ Refresh** hits ESI again. When the cached snapshot has expired (ESI's ~5 min market cycle), a notice appears in the status bar prompting you to refresh.

A scan progress bar streams live updates as the order book downloads (first run per region can take ~30 seconds; subsequent scans are instant from cache).

---

## Industry tab

| Field | Default | Notes |
|-------|---------|-------|
| Category | All | Market group to scan (or everything) |
| Source hub | Jita | Trade hub where materials are priced and the product is sold |
| Build location | — | Saved station/structure profile (ME/TE, cost index, bonuses, taxes); ＋ adds one via a wizard, ✎ edits |
| Batch (runs) | 1 | Runs per job; scales profit×N, cargo and days-to-sell live without a rescan |
| Skills level | 5 | Assumed industry-skill level for build-time and buildability |
| Sales tax / broker | 4.5% / 1.5% | Applied to the product's sale |
| Min tradeability | 0 | Hide products whose market absorbs too little volume |

Blueprint data is built once from the Fuzzwork SDE dump into a local SQLite database. Use **⟳ Refresh SDE** to rebuild it after a game patch. Favourites, the build-location profiles, and the column order persist across visits.

---

## Data sources

- **ESI** (`https://esi.evetech.net`) — LP store offers, order books, universe names, routes, market history
- **Fuzzwork aggregates** (`https://market.fuzzwork.co.uk/aggregates/`) — best bid/ask prices per hub for the LP and industry scanners
- **Fuzzwork SDE dump** (`https://www.fuzzwork.co.uk/dump/latest/csv/`) — static blueprint, material and type data for the Industry planner

ESI excludes most player-built Upwell structure markets, so prices can differ slightly from the in-game view.

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

Form preferences (corporation, LP budget, arb settings, industry settings, column widths and order, sort order) are also saved in the browser's `localStorage` and restored automatically on the next visit.

---

## License

MIT
