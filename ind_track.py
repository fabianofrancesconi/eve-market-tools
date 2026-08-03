"""Pure inventory + sales + listing reconciliation for tracked industry builds.

WHY THIS MODULE EXISTS (read this before touching anything below)
=================================================================
A tracked build is a *produced lot*: a number of units, a frozen per-unit craft
cost, and the time it was delivered. Three questions get asked of a product's
lots on every screen refresh:

  1. How much of it has *sold*, and for what profit?   (money)
  2. How much is *listed* on the market right now?      (inventory on sale)
  3. What *stage* is each lot at — built / listed / sold? (the card + the badge)

The bug this module was rewritten to kill (2026-07): those three answers used to
be computed by *separate* code with *separate* lot lists, so they drifted. The
market-order "🔗 LINKED" badge said one thing while the tracker card said
another, again and again, because "is it listed?" was answered twice — once by
the server's stage derivation and once by an independent client rule — over
subtly different lot sets.

THE ONE RULE
------------
:func:`reconcile` is now the **single source of truth**. Given a product's lots,
its wallet sell fills, and its current open-order volume, it returns — from ONE
ordered pass — every lot's sold / held / listed counts, its lifecycle stage, and
the single ``listed_anchor`` (the oldest still-held lot that carries the open
order). Both the tracker card's stage and the market order's LINKED badge read
these same numbers, so this is a *theorem*, not a hope:

    a product's order shows LINKED
      ⟺ reconcile.listed_anchor is not None
      ⟺ some delivered lot has stage == "listed"
      ⟺ reconcile.flow["listed"] > 0

There is no second way to decide "listed" anywhere in the app.

MODEL DETAILS
-------------
* Money is never tied to a market order. EVE items are fungible — ESI reports "a
  unit of type T sold at price P", never "this unit came from batch A" — so sold
  units are FIFO-allocated across a product's lots, oldest-produced first, and
  only onto lots already delivered when the sale happened (you can't sell a unit
  before you make it). Re-pricing an unsold order is invisible (profit is the
  real wallet fill, not the listing); a cancel fabricates no sale (no wallet
  transaction, nothing to accrue).
* Listing is laid against *held* (produced-but-unsold, non-abandoned) stock, also
  oldest-first — the same order sales fill — so one market order flags the oldest
  still-held batch, not every delivered batch of the product.
* Everything is recomputed from scratch each sweep from (a) the dedup'd wallet
  ledger and (b) the current lots + open-order volume. The only accumulated state
  is the ledger (dedup by ``transaction_id``); every per-lot figure is derived,
  so late ESI data self-heals on the next recompute.

Every function here is pure — plain dicts in, plain data out. ``lp-web`` owns all
fetching/persistence and feeds this module already-parsed lots / fills / volumes.

THE TWO-STREAM MODEL (read before debugging "sold looks wrong")
---------------------------------------------------------------
Sale tracking runs on TWO independent streams because no single ESI feed is both
timely AND authoritative for money:

  * UNITS (sold / held / stage) ← the ``observed_fills`` order-diff stream: units
    the app physically watched leave when an open order's ``volume_remain``
    dropped. Near-real-time (``/orders`` updates within a sweep), so the lifecycle
    is correct immediately.
  * MONEY (net / cost / profit) ← the ``fills`` wallet stream
    (``/wallet/transactions``): the true ISK, but ESI's transactions feed lags the
    real sale by up to hours (sometimes much longer than the ~1h it advertises).

Per lot, ``sold`` (units) is the UNION (max) of the two streams' FIFO allocations;
``sold_paid`` is the wallet-confirmed subset that ``net``/``cost``/``profit``
actually cover. So a build reads "sold" the instant its order empties, while its
profit trails until the wallet catches up — and because the streams are never
concatenated, nothing double-books. See :func:`reconcile`.

This split is the fix for the recurring bug where a build showed ``0 / N sold``
(or sat in BUILT with phantom "held" units) while its order had visibly sold out —
the wallet feed simply hadn't reported the sale yet. Reproduced on Standup Light
Missile (37848) twice: 2026-07-30 (``sold=0``) and 2026-07-31 (``sold=3206`` of a
4200 batch whose order had fully filled; the closing 994-unit sale was ~15h behind
in the wallet feed while ``/wallet`` balance and ``/orders`` both saw it instantly).

REMAINING SHARP EDGES (still true after the split — check before blaming the pass):

1. **Money still lags; ``sold_paid < sold`` is normal mid-settlement.** The UNIT
   view is right immediately, but realized profit / portfolio ISK only catch up
   when the wallet feed reports the fill. Archiving is gated on
   ``sold_paid == sold`` (see :func:`~lp-web.do_ind_builds_archive`) precisely so a
   build can't freeze its realized snapshot before the money lands — a frozen
   snapshot is never re-derived. If profit looks low but stage is "sold", the
   wallet is still behind; it converges. Still surface a failing feed:
   ``wallet_tx_health`` (a gold "sales sync failed" badge) flags a *broken* pull as
   opposed to a merely slow one.

2. **A wallet-only sale (instant-sell / dump) has no order-diff event.** Dumping to
   a buy order produces a wallet fill but no ``volume_remain`` drop, so the union
   counts it via the wallet side — but its UNITS then lag instead of leading. This
   is fine (an instant-sell is realized immediately in-game and the wallet feed is
   usually quick for it), just the opposite asymmetry from a listed sale.

3. **The delivery gate applies to BOTH streams.** A sale (observed or wallet) only
   lands on a lot with ``done_at <= fill.ts``. When a newer build is delivered
   while older stock of the same fungible item is still selling, pre-delivery sales
   fail the gate and pile into ``summary.unallocated``. The order-diff stream is
   attached to the CURRENT open order, so in practice its events post-date the live
   lot's delivery and attach cleanly — but a fill (either stream) that genuinely
   predates the only live lot is still stranded by design (older inventory belongs
   to now-archived builds whose sales are frozen).

4. **Archived/frozen builds are excluded from the FIFO, by design — double-edged.**
   Dropping dead builds (:func:`~lp-web._live_builds`) killed the old LINKED-vs-built
   drift, but a fungible sale that "should" have hit an archived batch instead flows
   to the oldest *live* lot (or to unallocated). Archiving freezes realized profit;
   later sales of the same type can't retro-adjust it.

5. **The observed ledger is append-only.** Like the wallet ledger, it only grows
   (deduped by event id). Rows are tiny; if it ever needs bounding, prune only fills
   already fully covered by the wallet stream — pruning a still-uncovered observed
   fill would revert a sold build to built.
"""


# ── Wallet ledger maintenance ────────────────────────────────────────────────

def merge_sell_fills(ledger, transactions, parse_ts):
    """Fold new wallet *sell* transactions into a product-keyed ledger, deduping
    by ``transaction_id``.

    ``ledger`` is ``{str(product_type_id): [fill, ...]}`` where each fill is
    ``{transaction_id, ts, units, price}``. Product keys are strings so the
    ledger survives a JSON round-trip unchanged (JSON object keys are always
    strings) — callers must look up with ``str(pid)``. ``transactions`` is the
    raw ESI wallet-transactions list (each ``{transaction_id, date, type_id,
    quantity, unit_price, is_buy, ...}``); ``parse_ts`` turns an ESI date string
    into a unix timestamp (or None). Buy transactions and rows missing an id/qty
    are ignored. Returns ``(ledger, changed)`` with ``ledger`` mutated in place;
    ``changed`` is True iff at least one new fill was added.

    A transaction id is globally unique and immutable, so re-running every sweep
    never double-books and order re-pricing / cancellation is invisible to it by
    construction. This is the *entire* accumulated state of sale tracking.
    """
    seen = {str(f["transaction_id"])
            for fills in ledger.values() for f in fills
            if f.get("transaction_id") is not None}
    changed = False
    for t in transactions or []:
        if t.get("is_buy"):
            continue
        tid = t.get("transaction_id")
        if tid is None or str(tid) in seen:
            continue
        pid = t.get("type_id")
        qty = t.get("quantity") or 0
        if pid is None or qty <= 0:
            continue
        ts = parse_ts(t.get("date"))
        ledger.setdefault(str(pid), []).append({
            "transaction_id": tid,
            "ts": ts,
            "units": qty,
            "price": t.get("unit_price") or 0.0,
        })
        seen.add(str(tid))
        changed = True
    return ledger, changed


def merge_observed_fills(ledger, events):
    """Fold new order-diff *sale* events into a product-keyed observed-units
    ledger, deduping by the event's own ``id``.

    ``ledger`` is ``{str(type_id): [{event_id, ts, units}]}`` — the PHYSICAL
    record of units the app watched leave via an open order's ``volume_remain``
    dropping (or the order filling). It carries no price: money always comes from
    the wallet ledger (:func:`merge_sell_fills`); this one exists only so the unit
    lifecycle (sold / held / stage) tracks in real time instead of waiting on the
    laggy wallet-transactions feed. ``events`` is the order-event list from
    ``_compute_order_deltas`` (each ``{id, ts, type_id, sold, expired, ...}``).

    Only a **partial fill** (``e["partial"]`` — the order stayed open with less
    volume than before) is booked. Only a *buyer* can shrink an open order — you
    can't partially cancel, and a relist/contract moves the whole order — so a
    partial drop is a PROVABLE sale, safe to lead the wallet. A **full
    disappearance** (the order vanished entirely) is ambiguous: buyout, cancel,
    and contract-away are indistinguishable from ``/orders`` alone, so it is NOT
    booked here — the units stay held and only a wallet transaction can mark them
    sold (``sold`` means: another player has them and ISK came in). This is the
    fix for "cancel + relist showed as sold and settled forever". An **expired**
    order returned its units to the hangar (never sold); expired and buy-order
    events are skipped. Events missing an id / type_id / positive ``sold`` are
    ignored. The event id is unique per (order, sweep), so re-running never
    double-books. Returns ``(ledger, changed)``; ``ledger`` mutated in place.
    """
    seen = {str(f["event_id"])
            for fills in ledger.values() for f in fills
            if f.get("event_id") is not None}
    changed = False
    for e in events or []:
        if e.get("expired") or e.get("is_buy_order") or not e.get("partial"):
            continue
        eid = e.get("id")
        pid = e.get("type_id")
        units = e.get("sold") or 0
        if eid is None or pid is None or units <= 0 or str(eid) in seen:
            continue
        ledger.setdefault(str(pid), []).append({
            "event_id": eid,
            "ts": e.get("ts"),
            "units": units,
        })
        seen.add(str(eid))
        changed = True
    return ledger, changed


def prune_legacy_duplicates(ledger, window=3 * 86400):
    """Drop migration-synthesized ``legacy-*`` fills that a real wallet fill now
    duplicates — the double-booking that let FIFO over-allocate a lot to "sold".

    The one-time v1.150 migration seeded a synthetic ``legacy-<build>-<n>`` fill
    for every historical *order-diff* sale (inferred from an open order's
    ``volume_remain`` dropping, carrying no wallet ``transaction_id``). Any such
    sale still inside ESI's ~30-day wallet window later re-arrives as a *real*
    wallet transaction and is merged as its own fill — so the same units book
    twice, and FIFO fills the lot to capacity, reading it as fully sold.

    A legacy fill is a duplicate of a real (numeric-id) fill in the same product
    when they agree on ``units`` and their ``ts`` is within ``window`` seconds;
    among candidates the price-closest real is chosen (order-diff recorded the
    listed price, the wallet the actual fill, so they can drift by a few ISK).
    Matching is greedy and 1:1 — two legacy fills never collapse onto one real,
    and a legacy fill with no real twin (a sale older than the wallet window,
    whose synthetic fill is the *only* record of that profit) is kept untouched.
    Idempotent. ``ledger`` is mutated in place; returns ``(ledger, removed)``.
    """
    def _is_legacy(f):
        return str(f.get("transaction_id", "")).startswith("legacy-")

    removed = 0
    for pid, fills in ledger.items():
        reals = [f for f in fills if not _is_legacy(f)]
        claimed = [False] * len(reals)
        kept = []
        for f in fills:
            if not _is_legacy(f):
                kept.append(f)
                continue
            best = None
            best_dp = None
            for i, rf in enumerate(reals):
                if claimed[i] or f.get("units") != rf.get("units"):
                    continue
                if abs((f.get("ts") or 0) - (rf.get("ts") or 0)) > window:
                    continue
                dp = abs((f.get("price") or 0.0) - (rf.get("price") or 0.0))
                if best is None or dp < best_dp:
                    best, best_dp = i, dp
            if best is None:
                kept.append(f)
            else:
                claimed[best] = True
                removed += 1
        ledger[pid] = kept
    return ledger, removed


def confirm_filled_events(events, ledger, window=6 * 3600):
    """Reconcile ambiguous full-disappearance order events against the wallet
    sell ledger, tagging each one the wallet has since backed as confirmed sold.

    A ``filled`` (full-disappearance) order event is *ambiguous* on its own —
    buyout, cancel, and contract-away are indistinguishable from ``/orders`` — so
    the app never books it as sold from the order diff alone. But a genuine buyout
    leaves a **wallet transaction** for the same ``type_id`` at (about) the same
    time, which lands in the per-product sell ledger keyed by that transaction_id.
    This matches the two up so a confirmed sale stops nagging as "confirm in
    wallet": in the notification panel it becomes a plain ✓ sold, and in the
    Industry summary it leaves the "pending sale?" limbo and reads as sold.

    ``events`` is the order-event list from :func:`_compute_order_deltas` /
    ``_get_order_events`` (each ``{id, ts, type_id, sold, filled, partial,
    expired, is_buy_order, ...}``). ``ledger`` is the wallet sell ledger
    ``{str(type_id): [{transaction_id, ts, units, price}, ...]}``. A ``filled``
    event confirms iff the ledger holds one or more fills of the same product
    whose ``ts`` is within ``window`` seconds of the event (either side — a
    transaction can post slightly before *or* after the order diff notices the
    order gone) and whose units sum to at least the event's ``sold``.

    Matching is greedy and 1:1 on fills: each wallet fill can back at most one
    event, so two orders of the same item that vanished together don't both claim
    the same transaction (only one is confirmed until a second fill appears).
    Fills are consumed oldest-first; among events, oldest-first, so the earliest
    vanish binds to the earliest matching fill. Pure and idempotent: returns a NEW
    list of events (the inputs are not mutated), each a shallow copy carrying two
    added keys — ``wallet_confirmed`` (bool) and, when confirmed,
    ``confirmed_price`` (the units-weighted mean fill price, since the order's
    listed price can differ from the actual fill price). Non-``filled`` events
    (partials, expiries, buy orders) pass through untouched with
    ``wallet_confirmed=False``.
    """
    # Fills per product, oldest-first, with a per-fill "already claimed" flag so
    # one transaction never confirms two separate vanished orders.
    avail = {}
    for pid, fills in (ledger or {}).items():
        avail[str(pid)] = sorted(
            ({"ts": f.get("ts") or 0, "units": f.get("units") or 0,
              "price": f.get("price") or 0.0, "used": False}
             for f in fills if (f.get("units") or 0) > 0),
            key=lambda f: f["ts"])

    out = []
    # Oldest event first so the earliest vanish claims the earliest fill.
    for e in sorted(events or [], key=lambda x: x.get("ts") or 0):
        ev = dict(e)
        ev["wallet_confirmed"] = False
        need = ev.get("sold") or 0
        ambiguous = (ev.get("filled") and not ev.get("partial")
                     and not ev.get("expired") and not ev.get("is_buy_order"))
        if ambiguous and need > 0:
            ets = ev.get("ts") or 0
            cand = [f for f in avail.get(str(ev.get("type_id")), [])
                    if not f["used"] and abs(f["ts"] - ets) <= window]
            if sum(f["units"] for f in cand) >= need:
                got = 0
                cost = 0.0
                for f in cand:
                    if got >= need:
                        break
                    f["used"] = True
                    got += f["units"]
                    cost += f["units"] * f["price"]
                ev["wallet_confirmed"] = True
                ev["confirmed_price"] = (cost / got) if got else (ev.get("price") or 0.0)
        out.append(ev)
    return out


# ── Primitive allocators (building blocks of reconcile; individually tested) ──

def _open(lot):
    """Is this an *open, on-the-market-able* delivered lot? A lot is open iff it
    is delivered (``done_at`` set) and neither ``abandoned`` (unsold remainder
    written off) nor ``archived`` (a closed position the user filed away — the
    tracker board never shows it in a lane). Only open lots can carry a live sell
    order / the 🔗 badge; closed lots keep their already-realized sales but hold
    nothing the app treats as on the market, so an order never lands on them."""
    return (lot.get("done_at") is not None
            and not lot.get("abandoned") and not lot.get("archived"))


def _ordered_delivered(lots):
    """A product's delivered lots, oldest-produced first. The ONE ordering used
    everywhere sold/listed FIFO is laid down — by ``done_at`` (the production
    time), then ``id`` as a stable tiebreak. A lot is *delivered* iff it has a
    non-None ``done_at``; a lot with ``units`` <= 0 still sorts here but simply
    contributes no capacity. Sharing this key is what makes the sold-oldest-first
    and listed-oldest-first passes land on the *same* lots."""
    return sorted(
        (l for l in lots if l.get("done_at") is not None),
        key=lambda l: (l.get("done_at"), str(l.get("id"))))


def allocate_fifo(lots, fills):
    """FIFO-allocate a product's sold units across its produced lots.

    ``lots`` — delivered lots for one product, each
    ``{id, units, cost_per_unit, sales_tax, done_at}`` where ``units`` is the
    *allocatable capacity* (produced output, minus any abandoned write-off).
    Allocated oldest-produced first (see :func:`_ordered_delivered`). Archived
    lots are NOT passed here — reconcile drops them from every pass (their profit
    is frozen at archive time), so only live and abandoned lots compete for fills.
    ``fills`` — sale fills for the same product, each
    ``{units, price, ts, transaction_id}``. Consumed oldest-sold first (by
    ``ts``); a fill spills across as many lots as needed.

    A fill only ever lands on a lot **already delivered when the sale happened**
    (``lot.done_at <= fill.ts``) — you can't sell a unit before you produce it.
    Without this gate the cumulative ledger's surplus fills (flipped stock,
    deleted/untracked builds, pre-tracking sales) would spill into a
    freshly-delivered lot and flip it straight to sold while the goods sit in the
    hangar. Such pre-delivery sales stay ``unallocated``. A fill with no ``ts``
    (legacy/migration) is treated as newest, so it can still land anywhere.

    Returns ``(per_lot, summary)``:
      * ``per_lot`` — ``{lot_id: {sold, net, cost, profit}}`` for every lot
        (zero-filled). ``net`` uses that lot's frozen ``sales_tax`` (revenue
        after tax); ``cost`` its frozen ``cost_per_unit``; ``profit = net-cost``.
      * ``summary`` — ``{sold, net, cost, profit, unallocated}`` across the
        product. ``unallocated`` is units sold beyond total production (flipped
        stock) — excluded from every lot's profit; it isn't this batch's output.

    A lot missing cost/tax (old snapshot) still counts its ``sold`` units but
    contributes None-safe zeros to net/cost so it never poisons totals — its own
    ``profit`` is reported as None.
    """
    ordered = [l for l in _ordered_delivered(lots) if (l.get("units") or 0) > 0]
    per_lot = {l["id"]: {"sold": 0, "net": 0.0, "cost": 0.0, "profit": 0.0,
                         "_costable": True}
               for l in lots}
    caps = [[l, l.get("units") or 0] for l in ordered]   # remaining capacity, in order
    fills_sorted = sorted(fills or [],
                          key=lambda f: (f.get("ts") if f.get("ts") is not None
                                         else float("inf")))
    total_sold = 0
    unallocated = 0
    for f in fills_sorted:
        remaining = f.get("units") or 0
        price = f.get("price") or 0.0
        fts = f.get("ts")
        total_sold += remaining
        # Scan every lot in FIFO order each fill: the oldest lot with free
        # capacity may be one this fill must skip (not yet delivered when it
        # sold), so a single monotonic pointer would wrongly stall.
        for cap_entry in caps:
            if remaining <= 0:
                break
            lot, cap = cap_entry
            if cap <= 0:
                continue
            done_at = lot.get("done_at")
            if fts is not None and done_at is not None and done_at > fts:
                continue    # lot not yet produced when this sale happened
            take = min(remaining, cap)
            cap_entry[1] -= take
            remaining -= take
            rec = per_lot[lot["id"]]
            rec["sold"] += take
            cpu = lot.get("cost_per_unit")
            tax = lot.get("sales_tax") or 0.0
            if cpu is None:
                rec["_costable"] = False
            else:
                rec["net"] += take * price * (1 - tax)
                rec["cost"] += take * cpu
        if remaining > 0:
            unallocated += remaining
    net = cost = profit = 0.0
    sold = 0
    for rec in per_lot.values():
        sold += rec["sold"]
        if rec.pop("_costable") and rec["sold"] > 0:
            rec["profit"] = rec["net"] - rec["cost"]
            net += rec["net"]
            cost += rec["cost"]
            profit += rec["profit"]
        else:
            rec["profit"] = None if rec["sold"] > 0 else 0.0
    summary = {"sold": sold, "net": net, "cost": cost, "profit": profit,
               "unallocated": unallocated}
    return per_lot, summary


def allocate_listed(lots, per_lot, listed_units):
    """Distribute a product's live open-order volume across its lots' *held*
    (unsold) stock, oldest-produced first — so a single market order doesn't flag
    every delivered build of that product as "listed".

    ``lots`` — the product's delivered, non-abandoned lots, each
    ``{id, units, done_at}`` (``units`` = produced output).
    ``per_lot`` — the map from :func:`allocate_fifo` (each lot's ``sold`` count,
    so we list only its *unsold* remainder).
    ``listed_units`` — total ``volume_remain`` on the product's current open sell
    orders. Never tied to a specific order; laid against held stock oldest-first
    (the same order sales fill), so the oldest still-held batch shows on market.

    Returns ``{lot_id: listed_units}`` for every input lot (0 when nothing of it
    is on the market). Total listed is capped at units actually held — you can't
    have more of a tracked build on the market than you still hold.
    """
    out = {l["id"]: 0 for l in lots}
    remaining = max(0, listed_units or 0)
    for l in _ordered_delivered(lots):
        held = max(0, (l.get("units") or 0) - per_lot.get(l["id"], {}).get("sold", 0))
        take = min(remaining, held)
        out[l["id"]] = take
        remaining -= take
        if remaining <= 0:
            break
    return out


def product_pipeline(lots, per_lot, listed_units):
    """Aggregate one product's unit flow for the pipeline board.

    ``lots`` — every *non-abandoned* lot for the product (delivered or not), each
    carrying ``units`` and a ``done_at`` (None until delivered).
    ``per_lot`` — the map from :func:`allocate_fifo`.
    ``listed_units`` — units of this product on current open sell orders.

    Returns unit counts:
      * ``in_production`` — units of lots not yet delivered (planned/building).
      * ``produced``      — units of delivered lots.
      * ``sold``          — FIFO-allocated sold units (capped at ``produced``).
      * ``in_stock``      — ``produced - sold`` (held, whether listed or not).
      * ``listed``        — ``min(listed_units, in_stock)``.
      * ``unlisted``      — ``in_stock - listed``.
    """
    in_production = sum((l.get("units") or 0) for l in lots
                        if l.get("done_at") is None)
    produced = sum((l.get("units") or 0) for l in lots
                   if l.get("done_at") is not None)
    sold = sum(per_lot.get(l["id"], {}).get("sold", 0) for l in lots)
    sold = min(sold, produced)
    in_stock = max(0, produced - sold)
    listed = max(0, min(listed_units or 0, in_stock))
    return {
        "in_production": in_production,
        "produced": produced,
        "sold": sold,
        "in_stock": in_stock,
        "listed": listed,
        "unlisted": max(0, in_stock - listed),
    }


# ── The single authority ─────────────────────────────────────────────────────

def reconcile(lots, fills, listed_units, observed_fills=None):
    """Reconcile ONE product's lots against its wallet fills and open-order
    volume, in a single ordered pass — the sole place "sold", "listed" and the
    lifecycle stage are decided, so they can never disagree.

    TWO independent sale streams, on purpose (see KNOWN FAILURE MODES flaw 1):
      * ``fills`` — WALLET sell transactions. The source of truth for *money*
        (net / cost / profit), but ESI's ``/wallet/transactions`` feed lags the
        real sale by up to hours, so it must NOT gate the unit lifecycle.
      * ``observed_fills`` — units the app *physically watched leave* via the
        real-time order-diff (an open order's ``volume_remain`` dropping / the
        order filling). Timestamped ``{ts, units}`` (no price — money never comes
        from here). This is what drives ``sold`` / ``held`` / the stage, so a
        build reads "sold" the instant its order empties even while the wallet
        feed is still catching up. Money for those units lands later, from the
        wallet, and the two reconcile because each unit is capacity-capped and
        the streams are never merged (so nothing double-books).
    Per lot, ``sold`` (units) = ``max(wallet-allocated, observed-allocated)`` —
    the union, so a pure instant-sell the order-diff never saw (wallet-only) and
    a fresh listed sale the wallet hasn't reported yet (observed-only) both count.
    When ``observed_fills`` is None/empty the unit stream collapses to the wallet
    stream and behaviour is exactly as before (money and units share one FIFO).

    ``lots`` — every tracked build for the product, delivered or not, each a rich
    lot dict (see :func:`~lp-web._build_lot`):
      * ``id``
      * ``done_at``       — production timestamp (FIFO key + delivery gate), or
                            ``None`` while the lot is still in production.
      * ``planned_units`` — the batch's full output (used for in-production flow).
      * ``produced``      — produced units once delivered (0 while in production).
      * ``cap``           — allocatable capacity = produced − abandoned write-off.
      * ``cost_per_unit`` / ``sales_tax`` — frozen cost basis / tax for profit.
      * ``abandoned``     — the unsold remainder was written off; the lot is
                            closed, holds nothing, and can never be listed.

    Archived builds are *dead* and are never passed to reconcile — the caller
    (:func:`~lp-web._reconcile_products`) drops them first and reads their frozen
    realized snapshot instead, so their old lots can't absorb a fresh fill or
    win the listing anchor from a live build (the old LINKED-vs-Built bug).
    ``fills`` — the product's wallet sell fills (``{units, price, ts,
    transaction_id}``).
    ``listed_units`` — total ``volume_remain`` on the product's open sell orders.

    Returns ``{lots, summary, flow, listed_anchor}``:
      * ``lots`` — ``{lot_id: {sold, held, listed, net, cost, profit, stage,
        is_listed_anchor}}``. ``stage`` is ``built`` / ``listed`` / ``sold`` for
        delivered lots, ``None`` for in-production lots (the caller resolves
        planned vs building from the live job). ``is_listed_anchor`` is True on
        exactly one lot — the oldest still-held lot carrying the open order —
        and only when the product actually has listed stock.
      * ``summary`` — product money roll-up from :func:`allocate_fifo`.
      * ``flow`` — unit-flow pipeline from :func:`product_pipeline`.
      * ``listed_anchor`` — the ``lot_id`` a market order's 🔗 badge links to, or
        ``None`` when nothing of the product is on the market. The badge shows
        **iff** this is not None — the same condition that makes some lot's stage
        ``listed`` — so the order badge and the tracker card always agree.

    INVARIANTS (checked by the chaos tests, relied on by the UI):
      * ``sum(lot.sold) == summary.sold ≤ total produced`` (overflow → unalloc).
      * ``sum(lot.listed) == flow.listed == min(listed_units, in_stock)``.
      * ``lot.stage == "listed"  ⟺  lot.listed > 0``.
      * ``lot.stage == "sold"    ⟺  abandoned, or produced>0 and sold≥produced``.
      * ``listed_anchor is not None  ⟺  any(lot.stage == "listed")  ⟺
        flow.listed > 0``  — the LINKED-badge theorem.
      * ``lot.listed ≤ lot.held``; a lot is only listed from held stock.
      * Idempotent: reconciling the same inputs again yields the same result.
    """
    # 1. Money: FIFO sold across delivered lots (capacity = cap). WALLET stream —
    #    the only source of net/cost/profit, but it lags the real sale.
    alloc_lots = [{"id": l["id"], "units": l.get("cap") or 0,
                   "cost_per_unit": l.get("cost_per_unit"),
                   "sales_tax": l.get("sales_tax") or 0.0,
                   "done_at": l.get("done_at")}
                  for l in lots if l.get("done_at") is not None]
    per_lot, summary = allocate_fifo(alloc_lots, fills)

    # 1b. Units: the same FIFO over the PHYSICALLY-OBSERVED order-diff stream
    #     (price-free — money never comes from here). Real-time, so it's what the
    #     unit lifecycle (sold / held / stage) reads; the wallet only catches the
    #     money up later. Per lot, the unit "sold" is the UNION (max) of the two
    #     streams: a wallet-only instant-sell and an observed-only fresh listing
    #     sale both count, and because the streams are never concatenated the same
    #     unit can't book twice. No observed data → collapses to the wallet count.
    obs_per_lot, _obs_summary = allocate_fifo(alloc_lots, observed_fills or [])
    sold_units = {l["id"]: max(per_lot.get(l["id"], {}).get("sold", 0),
                               obs_per_lot.get(l["id"], {}).get("sold", 0))
                  for l in lots}

    # 2. Listing: live order volume over held stock of *open* delivered lots,
    #    oldest-first (units = produced; held = produced − sold). Held uses the
    #    UNION sold (physical), so a build whose order emptied stops being listed
    #    the instant the units leave, not when the wallet feed catches up.
    #    Abandoned lots are closed positions holding nothing, so the order (and
    #    the 🔗 badge it drives) must NOT land on them or the badge would point at
    #    a written-off lot while the visible one reads Built. (Archived lots never
    #    reach here.)
    sold_view = {lid: {"sold": n} for lid, n in sold_units.items()}
    listable = [{"id": l["id"], "units": l.get("produced") or 0,
                 "done_at": l.get("done_at")}
                for l in lots if _open(l)]
    listed_map = allocate_listed(listable, sold_view, listed_units)

    # 3. Pipeline flow over *open* lots (in-production + delivered, not
    #    abandoned/archived) — the same visible set the listing pass draws on, so
    #    ``flow.listed`` equals the per-lot listed sum and the theorem holds.
    flow_lots = [{"id": l["id"],
                  "units": ((l.get("planned_units") or 0) if l.get("done_at") is None
                            else (l.get("produced") or 0)),
                  "done_at": l.get("done_at")}
                 for l in lots if not l.get("abandoned") and not l.get("archived")]
    flow = product_pipeline(flow_lots, sold_view, listed_units)

    # 4. Per-lot record: UNITS (sold/held/stage) from the physical union;
    #    MONEY (net/cost/profit) verbatim from the wallet stream. ``sold`` is the
    #    unit count the UI shows and the stage keys off; net/cost/profit trail it
    #    until the wallet feed reports the same units (then they converge).
    out_lots = {}
    for l in lots:
        lid = l["id"]
        money = per_lot.get(lid, {"sold": 0, "net": 0.0, "cost": 0.0,
                                  "profit": 0.0})
        sold_ct = sold_units.get(lid, 0)
        rec = {"sold": sold_ct,
               "net": money.get("net", 0.0), "cost": money.get("cost", 0.0),
               "profit": money.get("profit", 0.0),
               "sold_paid": money.get("sold", 0)}   # units the wallet has confirmed
        listed_ct = listed_map.get(lid, 0)
        rec["listed"] = listed_ct
        rec["is_listed_anchor"] = False
        if l.get("done_at") is None:
            rec["held"] = 0
            rec["stage"] = None                     # caller: planned vs building
        elif l.get("abandoned"):
            rec["held"] = 0
            rec["stage"] = "sold"                    # remainder written off
        elif (l.get("produced") or 0) > 0 and sold_ct >= (l.get("produced") or 0):
            rec["held"] = 0
            rec["stage"] = "sold"
        else:
            rec["held"] = max(0, (l.get("cap") or 0) - sold_ct)
            rec["stage"] = "listed" if listed_ct > 0 else "built"
        out_lots[lid] = rec

    # 5. The single anchor: oldest delivered lot carrying listed stock. Its
    #    existence is exactly the LINKED-badge condition — the ONE derivation
    #    both the market-order badge and the tracker card read.
    listed_anchor = None
    for l in _ordered_delivered(lots):
        if out_lots[l["id"]]["listed"] > 0:
            listed_anchor = l["id"]
            out_lots[l["id"]]["is_listed_anchor"] = True
            break

    # summary.sold is the UNION unit total (so sum(lot.sold) == summary.sold),
    # while net/cost/profit stay the wallet money — units lead, money trails.
    summary = dict(summary)
    summary["sold_paid"] = summary.get("sold", 0)     # wallet-confirmed units
    summary["sold"] = sum(sold_units.values())

    return {"lots": out_lots, "summary": summary, "flow": flow,
            "listed_anchor": listed_anchor}
