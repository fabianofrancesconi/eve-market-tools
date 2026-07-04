# Redesign → Master feature-parity tracker

The `redesign` branch is a v2.0.0 rewrite (FastAPI backend + React/Vite frontend)
of the original monolithic app (`lp-web.py` + `static/index.html` on `master`).
The rewrite ported the computation engines faithfully (all core tests pass) but
dropped or mis-wired a lot of the UI/behaviour layer. This file tracks the work
to reach full parity, **one tab at a time** (finish a tab before starting the next).

Legend: `[x]` done + verified · `[~]` partial · `[ ]` not started.

---

## LP Store  ✅ COMPLETE

### Correctness / dead controls (Category A)
- [x] **Tradeability weight** actually affects the ranking — recomputed client-side
  as a percentile blend of daily-vol vs days-to-clear (mirrors master
  `_computeTradeability`). Was: toggle updated persisted state but was never used.
  (`frontend/src/pages/LpPage.tsx` `withTradeability`)
- [x] **↻ Refresh** forces a fresh LP-offer pull — added `refresh` param to
  `GET /api/lp/scan` (→ `get_offers(..., refresh=True)`) and wired the button to
  send `&refresh=true`. Was: button re-ran the same cached query (24h stale).
- [x] **Refresh button enabled after a restore** (regression found in verification:
  it was gated on `scanTrigger>0`, now gated on `corp`).
- [x] **Sort persists** across reloads — `DataTable` gained `defaultSortKey/Dir` +
  `onSortChange`; LP wires them to `lpSortKey/lpSortDir`. Was: store fields dead.
- [x] **Unsellable offers rendered** — merged into the table dimmed/italic, exempt
  from the spread/illiquid filters, with a `· N unsellable` status count. Was:
  `unsellable` fetched but never shown.
- [x] **Last-scan restore on load** — `GET /api/lp/last-scan` hydrates the table
  instantly on mount (no ESI hit); auto-scan only if nothing to restore. Was:
  `save-scan` wrote to disk but `last-scan` was never called.
- [x] **`/api/lp/liquidity` resilience** — per-call try/except so a transient ESI
  error degrades to nulls instead of 500-ing the whole enrichment batch.
- [x] **Detail panel field mapping** (bug found during verification) — the panel
  was wired to fields `build_detail` never returns (`name`, `qty`, `sell_value_*`,
  `tax_*`, `broker_*`, `net_*`, `acquisition_cost`), so the header stuck on
  "Loading…" and the cost/profit breakdown was blank. Rewired to the real shape
  (`output.*`, `isk_fee`, `total_cost`, `revenue_*`); the per-mode tax/broker
  breakdown is reconstructed client-side from ask/bid + fee rates.
- [x] **DataTable setState-in-render fix** — `onColWidthsChange` was being called
  inside a `setWidths` updater (React "update parent while rendering child").

### Feature completeness (Category B)
- [x] Add **Buy Demand** + **Units** columns (hidden by default, like master).
- [x] **Per-character LP-budget lock + LP balance badge** — logged in, the budget
  is driven by the character's loyalty points for the viewed corp (matched by
  `corporation_id`) and locked with a 🔒 badge. (Logged-out path verified live;
  logged-in path verified by logic — sandbox has no EVE SSO.)
- [x] **Lot tracker** in the detail panel (per-required-item lots + running
  remainder + removable chips).
- [x] **Column resize + drag-reorder** with persistence — generic `DataTable`
  support (`table-layout: fixed`), LP persists `lpColOrder`/`lpColWidths`.

### Verified in browser
v2.0.0 badge; tradeability recomputes on toggle (13→14, 30→26, …); sort `Spread↓`
survives reload; 386 rows incl. 53 unsellable; reload restores from last-scan with
**no** scan call; Refresh fires `…&refresh=true`; detail panel title + profit
breakdown render real data; lot tracker "5/23 · 18 left"; column resize + reorder
apply and persist; no setState-in-render errors. `tsc` clean; 425 backend tests pass.

---

## Arbitrage  ✅ COMPLETE
- [x] **↻ Refresh button** — forces a fresh region order-book pull (wires the
  existing `refresh` param); **stale-book notice** shown when the cached snapshot
  has expired (result frame now carries `snapshot` freshness + totals).
- [x] Persist **Min ISK** — wired to `arbMinIsk` (was dead local state); default 0
  ("any", matching master) instead of the old 1,000,000.
- [x] Default **Max jumps** 6 (store default + backend default aligned to 6).
- [x] SSE **error frame** + frontend error state — the generator now wraps its body
  and emits `event: error`; `use-sse` distinguishes server errors from connection
  drops; the page renders the message instead of a silently-stuck progress bar.
- [x] Richer status line: deals · mode · spreads / orders · book age.

### Verified in browser / curl
Rich status "0 deals · Cross-station · 84 spreads / 421,608 orders · book 0h old";
Refresh sends `refresh=true`; Min ISK 5,000,000 persists; invalid region emits an
`event: error` ("Scan failed: 404…") instead of hanging; no console errors.
`tsc` clean; 425 backend tests pass. (Also removed the dead `scanner` import.)

## Industry  🚧 IN PROGRESS (biggest gap — largely anonymous-only vs master's character-aware tab)
- [x] **Detail panel field-mapping fix** (bug found during verification — worse than
  LP): the panel read `detail.materials` / `detail.product_name`, but `build_industry_detail`
  returns `required_items` / nested `product` and no `tech_level`/`missing_skills` —
  so expanding a row **crashed** on `undefined.map`. Rewired to the real shape; the
  detail endpoint now also computes `bpo_prices` (BP price/payback), `missing_skills`,
  and `tech_level`. Verified: "Occult XL" T2 expands with a full materials table.
- [ ] **Auth on the industry router** + feed real `skill_profile` and owned
  blueprints into scan/detail (`industry.py:129` `skill_profile = {}  # TODO`).
- [ ] **"My skills" toggle** (actual trained levels gate buildability + build time).
- [ ] **Owned blueprints** → real ME/TE, BPO/BPC status, max runs; ME/TE inputs in
  detail (currently hardcoded `me:0, te:0`).
- [ ] **⏱ Timer column** — live per-second countdown of running manufacturing jobs.
- [ ] **Hide-my-BPCs** filter + **cross-character ownership** annotations.
- [ ] **Build-location profiles + wizard** (system cost index / structure bonus /
  facility tax / SCC surcharge). Currently just a flat "Job Rate %".
- [ ] **Favorites/watchlist ★** + filter chips (Favorites / My BPs / Hidden / All).
- [ ] **⟳ Refresh SDE** button (endpoint `/api/industry/refresh-sde` exists, no UI).
- [ ] **BPO cross-region search** (needs new `/api/industry/bpo-search` endpoint).
- [ ] **"Pull live prices" (ESI)** in the detail row.
- [x] Pass `bpo_prices` to `/api/industry/detail` (was blank) — done with the detail fix above.
- [ ] Column reorder/resize/picker (Industry uses its own inline table).
- [ ] Wire the Industry **tradeability weight** toggle (dead, like LP was).

## Character / Overview
- [ ] **Resolve loyalty-point corp names** — backend `fetch_loyalty_points` returns
  raw `corporation_id`; the Overview tab shows "Corp #123" instead of the name
  (found while wiring the LP budget lock).
- [ ] **Sales-activity feed** (fill/partial-sale events + dismiss); needs backend
  order-event tracking.
- [ ] Market-order columns: **Jita sell price**, **queue rank**, **Expires**,
  last-sale tooltip.
- [ ] **"Next sync in M:SS" timer** + **live per-second job countdowns** (currently
  recomputed only every 5-min refetch).

## Global / cross-cutting
- [x] **Version** single source (`backend/app/__init__.py __version__`) — fixed the
  1.76.0 (main.py) vs 2.0.0 (pyproject) mismatch; badge now shows 2.0.0.
- [ ] **Skill-derived tax/broker auto-fill** (global cost bar: sales tax from
  Accounting, broker from Broker Relations).
- [ ] **Server-synced settings** across devices (`/api/settings*` exist, never called).
- [ ] **Column-layout persistence** (order/width/visibility).
- [ ] Decide on **per-user cache/scan isolation** (scan state is global-on-disk).
- [ ] Remove confirmed dead imports (`arbitrage.py:9`, `character.py:19`,
  `lp.py enrich_liquidity`, `lib/constants.ts`, `lib/utils.ts cn()`).

## Auth / SSO
- [ ] `AuthCallbackPage` error UI (surface `?auth_error=`; currently sticks on
  "Completing…").
- [ ] Decide/doc the **per-user Client-ID** story (master had a settings panel;
  v2 is server-configured multi-user).

## Docs
- [ ] Rewrite `README.md` + `COMMANDS.md` for the v2 architecture (they still
  describe the single-script / localStorage / file-token model).
