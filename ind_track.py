"""Pure inventory + sales accounting for tracked industry builds.

The model in one breath: a build is a *produced lot* (a number of units plus a
frozen per-unit cost, dated when its manufacturing job delivered); every sale is
a *wallet transaction* (a stable ``transaction_id`` and the real fill price);
sold units are FIFO-allocated across a product's lots, oldest-produced first —
and only onto lots that were already delivered when the sale happened (you can't
sell a unit before you produce it).

No sale is ever tied to a specific market order. EVE treats two identical items
as fungible — ESI can tell you "a unit of type T sold at price P", never "this
unit came from batch A". So we don't pretend otherwise: money comes from the
wallet (keyed by ``transaction_id``, the one stable id a sale carries) and is
laid against the produced lots by production order. Two consequences fall out
for free:

  * Re-pricing an unsold order changes nothing here — profit is read from the
    wallet at the real fill price, not from the listing.
  * A cancelled order can't fabricate a phantom sale — a cancel produces no
    wallet transaction, so there is nothing to accrue.

Allocation is recomputed from scratch every sweep from (a) the dedup'd wallet
ledger and (b) the current build lots. The only accumulated state is the ledger
itself (dedup by ``transaction_id``); everything else is derived, so late ESI
data (a job that only now shows as delivered, a transaction that only now
appears) self-heals on the next recompute instead of needing incremental
patch-up.

Every function here is pure: it takes plain dicts and returns plain data.
``lp-web`` owns all fetching and persistence and feeds this module already-parsed
lots / fills / order volumes.
"""


def merge_sell_fills(ledger, transactions, parse_ts):
    """Fold new wallet *sell* transactions into a product-keyed ledger, deduping
    by ``transaction_id``.

    ``ledger`` is ``{str(product_type_id): [fill, ...]}`` where each fill is
    ``{transaction_id, ts, units, price}``. Product keys are strings so the
    ledger survives a JSON round-trip unchanged (JSON object keys are always
    strings) — callers must look up with ``str(pid)``. ``transactions`` is the raw ESI
    wallet-transactions list (each ``{transaction_id, date, type_id, quantity,
    unit_price, is_buy, ...}``); ``parse_ts`` turns an ESI date string into a
    unix timestamp (or None). Buy transactions and rows missing an id/qty are
    ignored. Returns ``(ledger, changed)`` with ``ledger`` mutated in place;
    ``changed`` is True iff at least one new fill was added.

    This is the *entire* accumulated state of sale tracking. A transaction id is
    globally unique and immutable, so re-running every sweep never double-books
    and order re-pricing / cancellation is invisible to it by construction.
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


def prune_legacy_duplicates(ledger, window=3 * 86400):
    """Drop migration-synthesized ``legacy-*`` fills that a real wallet fill now
    duplicates — the double-booking that let FIFO over-allocate every lot to
    "sold".

    The one-time v1.150 migration (:func:`lp-web._migrate_sell_state`) seeded a
    synthetic ``legacy-<build>-<n>`` fill for every historical *order-diff* sale
    (a sale inferred from an open order's ``volume_remain`` dropping, which
    carries no wallet ``transaction_id``). Any such sale still inside ESI's
    ~30-day wallet window later re-arrives as a *real* wallet transaction and is
    merged as its own fill — so the same units are booked twice. With a product's
    ledger reporting more units sold than were ever produced, FIFO fills every
    delivered lot to capacity and each reads as fully sold.

    A legacy fill is a duplicate of a real (numeric-id) fill in the same product
    when they agree on ``units`` and their ``ts`` is within ``window`` seconds;
    among the candidates the price-closest real is chosen (order-diff recorded the
    listed price, the wallet the actual fill, so they can drift by a few ISK).
    Matching is greedy and 1:1 — two legacy fills never collapse onto one real
    fill, and a legacy fill with no real twin (a sale older than the wallet
    window, whose synthetic fill is the *only* record of that historical profit)
    is kept untouched. Idempotent: once a legacy fill is removed there is nothing
    left to match on a later sweep. ``ledger`` is mutated in place; returns
    ``(ledger, removed)`` where ``removed`` is the count dropped.
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


def allocate_fifo(lots, fills):
    """FIFO-allocate a product's sold units across its produced lots.

    ``lots`` — the built output for one product, each
    ``{id, units, cost_per_unit, sales_tax, done_at}``. Allocated oldest-produced
    first (by ``done_at``, then ``id`` as a stable tiebreak). A lot with
    ``units`` <= 0 contributes nothing.
    ``fills`` — sale fills for the same product, each
    ``{units, price, ts, transaction_id}``. Consumed oldest-sold first (by
    ``ts``); a fill's units spill across as many lots as needed.

    A fill is only ever allocated to a lot that was **already delivered when the
    sale happened** (``lot.done_at <= fill.ts``) — you can't sell a unit before
    you produce it. Without this gate the cumulative wallet ledger's surplus
    fills (flipped stock, deleted/untracked builds, sales from before tracking)
    would spill into a freshly-delivered lot and flip it straight to sold/listed
    while the goods are still in your hangar. Such pre-delivery sales stay
    ``unallocated`` instead. A fill with no ``ts`` (legacy/migration) is treated
    as newest, so it can still land on any lot.

    Returns ``(per_lot, summary)``:
      * ``per_lot`` — ``{lot_id: {sold, net, cost, profit}}`` for every lot
        (zero-filled when nothing sold). ``net`` uses that lot's own frozen
        ``sales_tax`` (revenue after tax); ``cost`` uses its frozen
        ``cost_per_unit``; ``profit = net - cost``.
      * ``summary`` — ``{sold, net, cost, profit, unallocated}`` across the
        product. ``unallocated`` is units sold beyond total production (flipped
        stock from an untracked source) — deliberately excluded from every lot's
        profit, since it isn't this batch's output.

    Cost/tax may be missing on a lot (an old snapshot); such a lot still counts
    its ``sold`` units but contributes None-safe zeros to net/cost so it never
    poisons the totals — its ``profit`` is reported as None.
    """
    ordered = sorted(
        (l for l in lots if (l.get("units") or 0) > 0),
        key=lambda l: (l.get("done_at") if l.get("done_at") is not None else float("inf"),
                       str(l.get("id"))))
    per_lot = {l["id"]: {"sold": 0, "net": 0.0, "cost": 0.0, "profit": 0.0,
                         "_costable": True}
               for l in lots}
    # Remaining capacity per lot, in allocation order.
    caps = [[l, l.get("units") or 0] for l in ordered]
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
        # A sale can only draw from lots already delivered at sale time. We can't
        # use a single monotonic pointer: the oldest lot with free capacity may be
        # one this fill must skip (not yet delivered when it sold), so scan every
        # lot in FIFO order each time and take from the first eligible one.
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
            # Sold before any eligible lot was produced (flipped stock, untracked
            # or deleted builds, pre-tracking sales) — not this batch's output.
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


def product_pipeline(lots, per_lot, listed_units):
    """Aggregate one product's unit flow for the pipeline board.

    ``lots`` — every build for the product (built or not), each carrying at
    least ``units`` and a ``done_at`` (None until delivered).
    ``per_lot`` — the allocation map from :func:`allocate_fifo`.
    ``listed_units`` — units of this product on the character's *current* open
    sell orders (summed ``volume_remain`` right now); live, never attributed to
    a lot.

    Returns unit counts describing the flow:
      * ``in_production`` — units of lots not yet delivered (planned/building).
      * ``produced``      — units of delivered lots (excludes abandoned lots,
                            which the caller drops before calling).
      * ``sold``          — FIFO-allocated sold units (capped at ``produced``).
      * ``in_stock``      — ``produced - sold`` (held, whether listed or not).
      * ``listed``        — ``min(listed_units, in_stock)`` (can't have more on
                            the market than you hold from tracked builds).
      * ``unlisted``      — ``in_stock - listed`` (built, not yet on the market).
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
