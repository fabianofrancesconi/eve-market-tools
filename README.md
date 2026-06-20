# EVE Market Tools

A local web app with two market utilities for EVE Online, served from a single Python script with no framework dependencies beyond `requests`.

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
[![Docker](https://img.shields.io/badge/ghcr.io-eve--market--tools-blue?logo=docker)](https://ghcr.io/fabianofrancesconi/eve-market-tools)

---

## Tools

### LP Store Scanner
Ranks LP store offers for any NPC corporation by ISK/LP efficiency. Enter your corporation and LP budget to see a sorted table of offers with profit per redemption, total profit, buy demand, and spread quality. Click any row to open a drill-down panel with the full shopping list, live Jita order-book costs, cargo volumes, and a redemption calculator.

### Arbitrage Scanner
Downloads the full public order book for a region and finds cross-station negative-spread opportunities — items where you can buy a sell order in one station and resell into a buy order in another at a profit after sales tax. Filters to deals with a Jita leg within a configurable round-trip jump range.

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

    - name: Run container
      community.docker.docker_container:
        name: eve-market-tools
        image: ghcr.io/fabianofrancesconi/eve-market-tools:latest
        ports:
          - "8765:8765"
        volumes:
          - eve-scanner-cache:/app/.eve_scanner_cache
        restart_policy: unless-stopped
        state: started
```

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

## Data sources

- **ESI** (`https://esi.evetech.net`) — LP store offers, order books, universe names, routes
- **Fuzzwork aggregates** (`https://market.fuzzwork.co.uk/aggregates/`) — Jita IV-4 best bid/ask prices for LP scanner

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
| `lp_web_settings.json` | Last-used LP scanner form values |
| `arb_settings.json` | Last-used Arbitrage form values |

Form preferences (corporation, LP budget, arb settings, column widths, sort order) are also saved in the browser's `localStorage` and restored automatically on the next visit.

---

## License

MIT
