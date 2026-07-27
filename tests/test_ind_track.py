"""Tests for ind_track: the pure inventory + sales accounting core.

Covers wallet-ledger dedup, FIFO allocation across produced lots (incl. the
user's real 40/40/30 parallel-batch scenario), tax/cost handling, unallocated
(flipped) overflow, and the pipeline unit-flow aggregation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ind_track


def _ts(s):
    """Trivial deterministic 'parser': the fixtures use plain numbers as dates."""
    return None if s is None else float(s)


class TestMergeSellFills:
    def test_adds_new_sell_fills_keyed_by_product(self):
        ledger = {}
        txns = [
            {"transaction_id": 1, "date": "100", "type_id": 587,
             "quantity": 5, "unit_price": 150.0, "is_buy": False},
            {"transaction_id": 2, "date": "110", "type_id": 587,
             "quantity": 3, "unit_price": 155.0, "is_buy": False},
        ]
        ledger, changed = ind_track.merge_sell_fills(ledger, txns, _ts)
        assert changed is True
        assert len(ledger["587"]) == 2
        assert ledger["587"][0]["units"] == 5
        assert ledger["587"][0]["ts"] == 100.0

    def test_ignores_buys(self):
        ledger = {}
        txns = [{"transaction_id": 1, "date": "100", "type_id": 587,
                 "quantity": 5, "unit_price": 150.0, "is_buy": True}]
        ledger, changed = ind_track.merge_sell_fills(ledger, txns, _ts)
        assert changed is False
        assert ledger == {}

    def test_dedups_by_transaction_id_across_reruns(self):
        ledger = {}
        txns = [{"transaction_id": 1, "date": "100", "type_id": 587,
                 "quantity": 5, "unit_price": 150.0, "is_buy": False}]
        ind_track.merge_sell_fills(ledger, txns, _ts)
        # Same transaction seen again next sweep — must not double-book.
        ledger, changed = ind_track.merge_sell_fills(ledger, txns, _ts)
        assert changed is False
        assert len(ledger["587"]) == 1

    def test_skips_bad_rows(self):
        ledger = {}
        txns = [
            {"transaction_id": None, "date": "1", "type_id": 587,
             "quantity": 5, "unit_price": 1, "is_buy": False},
            {"transaction_id": 9, "date": "1", "type_id": None,
             "quantity": 5, "unit_price": 1, "is_buy": False},
            {"transaction_id": 10, "date": "1", "type_id": 587,
             "quantity": 0, "unit_price": 1, "is_buy": False},
        ]
        ledger, changed = ind_track.merge_sell_fills(ledger, txns, _ts)
        assert changed is False
        assert ledger == {}


class TestPruneLegacyDuplicates:
    def test_drops_legacy_fill_duplicated_by_real_wallet_txn(self):
        # The exact prod corruption: a migration-seeded order-diff fill and the
        # real wallet transaction for the same sale both counted, so 4200 units
        # were booked against a 4200-unit lot's worth of real sales — twice.
        ledger = {"587": [
            {"transaction_id": "6842019761", "units": 2200, "price": 14690.0,
             "ts": 1784929559.0},
            {"transaction_id": "legacy-b1-0", "units": 2200, "price": 14690.0,
             "ts": 1784930030.0},   # ~8 min later, same units+price → dup
        ]}
        ledger, removed = ind_track.prune_legacy_duplicates(ledger)
        assert removed == 1
        assert len(ledger["587"]) == 1
        assert ledger["587"][0]["transaction_id"] == "6842019761"

    def test_keeps_unmatched_legacy_fill(self):
        # A legacy sale older than the wallet window has no real twin — it's the
        # only record of that profit and must survive.
        ledger = {"587": [
            {"transaction_id": "legacy-b1-2", "units": 5000, "price": 15630.0,
             "ts": 1000.0},
        ]}
        ledger, removed = ind_track.prune_legacy_duplicates(ledger)
        assert removed == 0
        assert len(ledger["587"]) == 1

    def test_greedy_one_to_one_not_collapsed(self):
        # Two legacy fills, one real: only one legacy is a duplicate.
        ledger = {"587": [
            {"transaction_id": "9001", "units": 100, "price": 10.0, "ts": 100.0},
            {"transaction_id": "legacy-a-0", "units": 100, "price": 10.0,
             "ts": 150.0},
            {"transaction_id": "legacy-a-1", "units": 100, "price": 10.0,
             "ts": 200.0},
        ]}
        ledger, removed = ind_track.prune_legacy_duplicates(ledger)
        assert removed == 1
        ids = {f["transaction_id"] for f in ledger["587"]}
        assert "9001" in ids
        assert len([f for f in ledger["587"] if str(f["transaction_id"]).startswith("legacy-")]) == 1

    def test_price_closest_real_is_matched(self):
        # Order-diff recorded the listed price; the wallet the actual fill. When
        # several reals share the units, the price-closest one is the true twin.
        ledger = {"587": [
            {"transaction_id": "1", "units": 50, "price": 100.0, "ts": 10.0},
            {"transaction_id": "2", "units": 50, "price": 105.0, "ts": 12.0},
            {"transaction_id": "legacy-x-0", "units": 50, "price": 104.5,
             "ts": 11.0},
        ]}
        ledger, removed = ind_track.prune_legacy_duplicates(ledger)
        assert removed == 1
        # Real "2" (price 105, closest to 104.5) is the claimed twin, so both
        # reals survive and only the legacy is gone.
        ids = {f["transaction_id"] for f in ledger["587"]}
        assert ids == {"1", "2"}

    def test_outside_window_not_matched(self):
        ledger = {"587": [
            {"transaction_id": "1", "units": 50, "price": 100.0, "ts": 10.0},
            {"transaction_id": "legacy-x-0", "units": 50, "price": 100.0,
             "ts": 10.0 + 4 * 86400},   # 4 days apart > default 3-day window
        ]}
        ledger, removed = ind_track.prune_legacy_duplicates(ledger)
        assert removed == 0

    def test_idempotent(self):
        ledger = {"587": [
            {"transaction_id": "1", "units": 50, "price": 100.0, "ts": 10.0},
            {"transaction_id": "legacy-x-0", "units": 50, "price": 100.0,
             "ts": 20.0},
        ]}
        ledger, r1 = ind_track.prune_legacy_duplicates(ledger)
        ledger, r2 = ind_track.prune_legacy_duplicates(ledger)
        assert r1 == 1 and r2 == 0

    def test_restores_sold_below_produced(self):
        # End to end: the corrupt ledger over-allocates a lot to fully-sold;
        # after pruning the phantom, the lot is only partially sold again.
        lot = {"id": "L", "units": 4200, "cost_per_unit": 1.0, "sales_tax": 0.0,
               "done_at": 0}
        ledger = {"587": [
            {"transaction_id": "r1", "units": 2200, "price": 100.0, "ts": 100.0},
            {"transaction_id": "legacy-L-0", "units": 2200, "price": 100.0,
             "ts": 300.0},
        ]}
        _, before = ind_track.allocate_fifo([lot], ledger["587"])
        assert before["sold"] == 4200        # corrupt: whole lot "sold"
        ind_track.prune_legacy_duplicates(ledger)
        _, after = ind_track.allocate_fifo([lot], ledger["587"])
        assert after["sold"] == 2200         # healed: only the real sale counts


class TestAllocateFifo:
    def _lot(self, lid, units, cpu=100.0, tax=0.0, done_at=0):
        return {"id": lid, "units": units, "cost_per_unit": cpu,
                "sales_tax": tax, "done_at": done_at}

    def test_single_lot_partial(self):
        lots = [self._lot("A", 10, cpu=100.0)]
        fills = [{"units": 4, "price": 150.0, "ts": 1}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["sold"] == 4
        assert per_lot["A"]["net"] == 600.0        # 4×150, no tax
        assert per_lot["A"]["cost"] == 400.0       # 4×100
        assert per_lot["A"]["profit"] == 200.0
        assert summ == {"sold": 4, "net": 600.0, "cost": 400.0,
                        "profit": 200.0, "unallocated": 0}

    def test_sales_tax_applied_to_net(self):
        lots = [self._lot("A", 10, cpu=100.0, tax=0.05)]
        fills = [{"units": 10, "price": 200.0, "ts": 1}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["net"] == 10 * 200.0 * 0.95
        assert per_lot["A"]["cost"] == 1000.0

    def test_fifo_spills_oldest_lot_first(self):
        # Two lots of the same item; older one (done_at 0) fills first. The sale
        # (ts 100) is after both deliveries, so both lots are eligible.
        lots = [self._lot("OLD", 40, cpu=100.0, done_at=0),
                self._lot("NEW", 40, cpu=110.0, done_at=50)]
        fills = [{"units": 50, "price": 200.0, "ts": 100}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["OLD"]["sold"] == 40   # exhausted first
        assert per_lot["NEW"]["sold"] == 10   # remainder
        assert summ["sold"] == 50
        assert summ["unallocated"] == 0

    def test_user_scenario_40_40_30(self):
        """The exact reported flow: two 40-run batches then a 30-run batch of the
        same item, sold across parallel/overlapping orders at different prices.
        FIFO by production date must give each batch its own honest profit with
        no order-linking."""
        lots = [
            {"id": "b1", "units": 40, "cost_per_unit": 100.0, "sales_tax": 0.0,
             "done_at": 1000},
            {"id": "b2", "units": 40, "cost_per_unit": 100.0, "sales_tax": 0.0,
             "done_at": 2000},
            {"id": "b3", "units": 30, "cost_per_unit": 90.0, "sales_tax": 0.0,
             "done_at": 3000},
        ]
        # Sales trickle in over time at drifting prices (re-priced listings).
        fills = [
            {"transaction_id": 1, "units": 25, "price": 150.0, "ts": 1100},
            {"transaction_id": 2, "units": 30, "price": 140.0, "ts": 2100},  # price cut
            {"transaction_id": 3, "units": 20, "price": 160.0, "ts": 3100},  # price back up
        ]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        # 75 units sold, FIFO: b1 gets 40, b2 gets 35, b3 gets 0.
        assert per_lot["b1"]["sold"] == 40
        assert per_lot["b2"]["sold"] == 35
        assert per_lot["b3"]["sold"] == 0
        assert summ["sold"] == 75
        assert summ["unallocated"] == 0
        # Money is the sum of the real fills, independent of which order sold it.
        assert round(summ["net"], 2) == round(25 * 150 + 30 * 140 + 20 * 160, 2)

    def test_sale_before_delivery_is_not_allocated(self):
        # The reported bug: the cumulative wallet ledger holds surplus fills (old
        # flipped/untracked stock). When a brand-new lot is delivered, those
        # earlier sales must NOT spill into it — you can't sell a unit before you
        # produce it — so the fresh lot stays unsold and the surplus is flipped.
        lots = [self._lot("FRESH", 38, cpu=100.0, done_at=2000)]
        fills = [{"units": 38, "price": 150.0, "ts": 1000}]   # all sold earlier
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["FRESH"]["sold"] == 0
        assert summ["sold"] == 0
        assert summ["unallocated"] == 38

    def test_fill_skips_undelivered_lot_to_reach_older_one(self):
        # A single monotonic pointer would wrongly stall: the oldest free lot
        # (NEW) isn't yet delivered at sale time, so the fill must skip it and
        # draw from the older, already-delivered OLD lot.
        lots = [self._lot("OLD", 10, cpu=100.0, done_at=1000),
                self._lot("NEW", 10, cpu=100.0, done_at=3000)]
        # OLD delivered, sale at 2000 (before NEW), then a later sale at 4000.
        fills = [{"units": 5, "price": 150.0, "ts": 2000},
                 {"units": 12, "price": 150.0, "ts": 4000}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        # First fill: only OLD eligible → 5 to OLD. Second: OLD has 5 left, NEW
        # now delivered → 5 to OLD then 7 to NEW.
        assert per_lot["OLD"]["sold"] == 10
        assert per_lot["NEW"]["sold"] == 7
        assert summ["unallocated"] == 0

    def test_fill_missing_ts_lands_on_any_lot(self):
        # A legacy/migration fill with no ts is treated as newest and may still
        # allocate — the gate only rejects sales provably before delivery.
        lots = [self._lot("A", 10, cpu=100.0, done_at=2000)]
        fills = [{"units": 4, "price": 150.0, "ts": None}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["sold"] == 4
        assert summ["unallocated"] == 0

    def test_overflow_is_unallocated_not_over_attributed(self):
        # One 10-unit lot, but 15 units sold (5 flipped from untracked stock).
        lots = [self._lot("A", 10, cpu=100.0)]
        fills = [{"units": 15, "price": 150.0, "ts": 1}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["sold"] == 10
        assert summ["sold"] == 10
        assert summ["unallocated"] == 5
        # The 5 flipped units contribute no cost/profit to the batch.
        assert per_lot["A"]["cost"] == 1000.0

    def test_lot_missing_cost_reports_none_profit(self):
        lots = [{"id": "A", "units": 10, "cost_per_unit": None,
                 "sales_tax": 0.0, "done_at": 0}]
        fills = [{"units": 5, "price": 150.0, "ts": 1}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["sold"] == 5
        assert per_lot["A"]["profit"] is None
        # Uncostable units don't poison the summary money totals.
        assert summ["net"] == 0.0
        assert summ["cost"] == 0.0

    def test_unbuilt_lots_excluded_from_allocation(self):
        # A lot with 0 built units (or filtered out by caller) gets nothing.
        lots = [self._lot("A", 0, cpu=100.0)]
        fills = [{"units": 5, "price": 150.0, "ts": 1}]
        per_lot, summ = ind_track.allocate_fifo(lots, fills)
        assert per_lot["A"]["sold"] == 0
        assert summ["unallocated"] == 5

    def test_no_fills_zero_everything(self):
        lots = [self._lot("A", 10)]
        per_lot, summ = ind_track.allocate_fifo(lots, [])
        assert per_lot["A"] == {"sold": 0, "net": 0.0, "cost": 0.0, "profit": 0.0}
        assert summ["sold"] == 0


class TestAllocateListed:
    def test_single_order_lands_on_oldest_held_build_only(self):
        # Two delivered builds of the same item both hold unsold stock, one open
        # order of 5. It must attach to the oldest held build, NOT flag both.
        lots = [{"id": "OLD", "units": 10, "done_at": 1000},
                {"id": "NEW", "units": 10, "done_at": 2000}]
        per_lot = {"OLD": {"sold": 0}, "NEW": {"sold": 0}}
        out = ind_track.allocate_listed(lots, per_lot, 5)
        assert out == {"OLD": 5, "NEW": 0}

    def test_spills_to_next_build_when_oldest_exhausted(self):
        lots = [{"id": "OLD", "units": 10, "done_at": 1000},
                {"id": "NEW", "units": 10, "done_at": 2000}]
        per_lot = {"OLD": {"sold": 0}, "NEW": {"sold": 0}}
        out = ind_track.allocate_listed(lots, per_lot, 14)
        assert out == {"OLD": 10, "NEW": 4}

    def test_skips_sold_out_build(self):
        # OLD fully sold → its 0 unsold can't be listed; the order sits on NEW.
        lots = [{"id": "OLD", "units": 10, "done_at": 1000},
                {"id": "NEW", "units": 10, "done_at": 2000}]
        per_lot = {"OLD": {"sold": 10}, "NEW": {"sold": 0}}
        out = ind_track.allocate_listed(lots, per_lot, 3)
        assert out == {"OLD": 0, "NEW": 3}

    def test_listed_capped_at_held_stock(self):
        # More on the market than tracked builds hold (extra from flipped stock).
        lots = [{"id": "A", "units": 10, "done_at": 1000}]
        per_lot = {"A": {"sold": 8}}
        out = ind_track.allocate_listed(lots, per_lot, 50)
        assert out == {"A": 2}   # only the 2 unsold held units can be listed

    def test_no_orders_lists_nothing(self):
        lots = [{"id": "A", "units": 10, "done_at": 1000}]
        out = ind_track.allocate_listed(lots, {"A": {"sold": 0}}, 0)
        assert out == {"A": 0}


class TestProductPipeline:
    def test_unit_flow(self):
        lots = [
            {"id": "done", "units": 40, "done_at": 1000},
            {"id": "wip", "units": 30, "done_at": None},
        ]
        per_lot = {"done": {"sold": 25}, "wip": {"sold": 0}}
        flow = ind_track.product_pipeline(lots, per_lot, listed_units=10)
        assert flow["in_production"] == 30
        assert flow["produced"] == 40
        assert flow["sold"] == 25
        assert flow["in_stock"] == 15
        assert flow["listed"] == 10
        assert flow["unlisted"] == 5

    def test_listed_capped_at_in_stock(self):
        # More units on the market than tracked builds hold (extra from flipping).
        lots = [{"id": "d", "units": 10, "done_at": 1}]
        per_lot = {"d": {"sold": 8}}
        flow = ind_track.product_pipeline(lots, per_lot, listed_units=50)
        assert flow["in_stock"] == 2
        assert flow["listed"] == 2      # capped
        assert flow["unlisted"] == 0

    def test_sold_capped_at_produced(self):
        lots = [{"id": "d", "units": 10, "done_at": 1}]
        per_lot = {"d": {"sold": 14}}   # overflow shouldn't drive in_stock negative
        flow = ind_track.product_pipeline(lots, per_lot, listed_units=0)
        assert flow["sold"] == 10
        assert flow["in_stock"] == 0


# ── reconcile: the single authority the whole app reads ──────────────────────
def _lot(lid, done_at, produced, *, cap=None, cpu=100.0, tax=0.0,
         planned=None, abandoned=False, archived=False):
    """A rich reconcile lot (see lp-web._build_lot for the real builder)."""
    prod = 0 if done_at is None else produced
    return {"id": lid, "done_at": done_at, "planned_units": planned if planned
            is not None else produced, "produced": prod,
            "cap": (prod if cap is None else cap), "cost_per_unit": cpu,
            "sales_tax": tax, "abandoned": abandoned, "archived": archived}


class TestReconcile:
    def test_single_built_lot_no_orders(self):
        r = ind_track.reconcile([_lot("A", 1000, 10)], [], 0)
        assert r["lots"]["A"]["stage"] == "built"
        assert r["lots"]["A"]["held"] == 10
        assert r["lots"]["A"]["listed"] == 0
        assert r["listed_anchor"] is None

    def test_in_production_lot_has_none_stage(self):
        # An undelivered lot is planned/building — reconcile leaves stage None for
        # the caller to split; it holds nothing and can't be listed.
        r = ind_track.reconcile([_lot("A", None, 10, planned=10)], [], 5)
        assert r["lots"]["A"]["stage"] is None
        assert r["lots"]["A"]["held"] == 0
        assert r["lots"]["A"]["listed"] == 0
        assert r["listed_anchor"] is None
        assert r["flow"]["in_production"] == 10

    def test_listed_anchor_is_oldest_held_lot(self):
        # Two held lots, one order → only the oldest is the anchor / listed.
        lots = [_lot("OLD", 1000, 10), _lot("NEW", 2000, 10)]
        r = ind_track.reconcile(lots, [], 5)
        assert r["listed_anchor"] == "OLD"
        assert r["lots"]["OLD"]["stage"] == "listed"
        assert r["lots"]["OLD"]["listed"] == 5
        assert r["lots"]["NEW"]["stage"] == "built"
        assert r["lots"]["NEW"]["listed"] == 0

    def test_listed_spills_but_anchor_stays_oldest(self):
        lots = [_lot("OLD", 1000, 10), _lot("NEW", 2000, 10)]
        r = ind_track.reconcile(lots, [], 14)
        assert r["listed_anchor"] == "OLD"      # anchor is always the oldest
        assert r["lots"]["OLD"]["listed"] == 10
        assert r["lots"]["NEW"]["listed"] == 4
        assert r["lots"]["NEW"]["stage"] == "listed"

    def test_sold_out_oldest_lot_not_the_anchor(self):
        # OLD fully sold → held 0, can't be the anchor; the order sits on NEW.
        lots = [_lot("OLD", 1000, 10), _lot("NEW", 2000, 10)]
        fills = [{"transaction_id": 1, "units": 10, "price": 150.0, "ts": 1500}]
        r = ind_track.reconcile(lots, fills, 5)
        assert r["lots"]["OLD"]["stage"] == "sold"
        assert r["listed_anchor"] == "NEW"
        assert r["lots"]["NEW"]["stage"] == "listed"

    def test_abandoned_lot_is_sold_and_never_listed(self):
        lots = [_lot("A", 1000, 10, cap=4, abandoned=True)]
        r = ind_track.reconcile(lots, [], 99)
        assert r["lots"]["A"]["stage"] == "sold"
        assert r["lots"]["A"]["listed"] == 0
        assert r["listed_anchor"] is None

    def test_archived_lot_never_the_anchor(self):
        # THE FIELD BUG: an older ARCHIVED lot still holding stock must not grab
        # the order / anchor — the board hides it, so the badge would point at a
        # lot the user can't see while the visible newer lot reads Built. The
        # order must land on the visible held lot instead.
        lots = [_lot("ARCH", 1000, 10, archived=True), _lot("VIS", 2000, 10)]
        r = ind_track.reconcile(lots, [], 5)
        assert r["listed_anchor"] == "VIS"
        assert r["lots"]["VIS"]["stage"] == "listed"
        assert r["lots"]["VIS"]["listed"] == 5
        assert r["lots"]["ARCH"]["stage"] == "built"   # closed, but not listed
        assert r["lots"]["ARCH"]["listed"] == 0
        assert r["lots"]["ARCH"]["is_listed_anchor"] is False

    def test_archived_only_lot_is_never_listed(self):
        # A lone archived held lot with a live order: nothing visible to list, so
        # no anchor and no badge — flow.listed collapses to 0.
        lots = [_lot("ARCH", 1000, 10, archived=True)]
        r = ind_track.reconcile(lots, [], 5)
        assert r["listed_anchor"] is None
        assert r["lots"]["ARCH"]["listed"] == 0
        assert r["lots"]["ARCH"]["stage"] == "built"
        assert r["flow"]["listed"] == 0

    def test_archived_sales_still_counted(self):
        # Archive is a declutter, not a write-off: an archived lot's already-sold
        # units keep their realized profit even though it can't be listed.
        lots = [_lot("ARCH", 1000, 10, cpu=100.0, tax=0.0, archived=True)]
        fills = [{"transaction_id": 1, "units": 6, "price": 150.0, "ts": 1500}]
        r = ind_track.reconcile(lots, fills, 5)
        assert r["lots"]["ARCH"]["sold"] == 6
        assert r["lots"]["ARCH"]["profit"] == 6 * (150.0 - 100.0)
        assert r["summary"]["sold"] == 6
        assert r["listed_anchor"] is None            # still never listed

    def test_profit_flows_from_fills(self):
        lots = [_lot("A", 1000, 10, cpu=100.0, tax=0.05)]
        fills = [{"transaction_id": 1, "units": 4, "price": 200.0, "ts": 1500}]
        r = ind_track.reconcile(lots, fills, 0)
        assert r["lots"]["A"]["sold"] == 4
        assert r["lots"]["A"]["net"] == 4 * 200 * 0.95
        assert r["lots"]["A"]["cost"] == 400.0
        assert r["summary"]["sold"] == 4

    def test_pre_delivery_fill_not_allocated(self):
        # A sale before the lot was produced stays unallocated (can't sell early).
        lots = [_lot("A", 2000, 10)]
        fills = [{"transaction_id": 1, "units": 10, "price": 150.0, "ts": 1000}]
        r = ind_track.reconcile(lots, fills, 5)
        assert r["lots"]["A"]["sold"] == 0
        assert r["summary"]["unallocated"] == 10
        assert r["lots"]["A"]["stage"] == "listed"   # still held, order on it


class TestReconcileInvariants:
    """The guarantees the UI relies on, on a spread of hand-built shapes. The
    LINKED-badge theorem — order shows 🔗  ⟺  a lot is Listed  ⟺  flow.listed>0 —
    is checked on every one."""

    SCENARIOS = [
        ([_lot("A", 1000, 10)], [], 0),
        ([_lot("A", 1000, 10)], [], 5),
        ([_lot("A", 1000, 10)], [], 50),
        ([_lot("A", 1000, 10), _lot("B", 2000, 10)], [], 5),
        ([_lot("A", 1000, 10), _lot("B", 2000, 10)], [], 15),
        ([_lot("A", 1000, 10), _lot("B", 2000, 10)],
         [{"transaction_id": 1, "units": 10, "price": 150.0, "ts": 1500}], 5),
        ([_lot("A", 1000, 10), _lot("B", 2000, 10)],
         [{"transaction_id": 1, "units": 20, "price": 150.0, "ts": 2500}], 5),
        ([_lot("A", None, 10, planned=10)], [], 5),
        ([_lot("A", 1000, 10, cap=4, abandoned=True)], [], 5),
        ([_lot("A", 1000, 10, cap=4, abandoned=True), _lot("B", 2000, 10)], [], 5),
        # Archived lots: hidden closed positions — never listed, never the anchor.
        ([_lot("A", 1000, 10, archived=True)], [], 5),
        ([_lot("A", 1000, 10, archived=True), _lot("B", 2000, 10)], [], 5),
        ([_lot("A", 1000, 10, archived=True), _lot("B", 2000, 10)], [], 15),
        ([_lot("A", 1000, 10, archived=True)],
         [{"transaction_id": 1, "units": 6, "price": 150.0, "ts": 1500}], 5),
    ]

    def _check(self, lots, fills, listed_units):
        r = ind_track.reconcile(lots, fills, listed_units)
        L = r["lots"]
        produced = sum(l["produced"] for l in lots)
        # sold sums and is capped at production
        assert sum(v["sold"] for v in L.values()) == r["summary"]["sold"]
        assert r["summary"]["sold"] <= produced
        # listed sums to the flow, is capped at in-stock, and never exceeds held
        assert sum(v["listed"] for v in L.values()) == r["flow"]["listed"]
        assert r["flow"]["listed"] == min(listed_units, r["flow"]["in_stock"])
        for v in L.values():
            assert v["listed"] <= v["held"] or v["held"] == 0 and v["listed"] == 0
            assert v["listed"] >= 0 and v["sold"] >= 0 and v["held"] >= 0
        # stage ⟺ listed/sold definitions. Archived (closed, hidden) delivered
        # lots keep their sold count but are never listed — so a not-fully-sold
        # archived lot reads "built", never "listed".
        for l in lots:
            v = L[l["id"]]
            if l["done_at"] is None:
                assert v["stage"] is None
            elif l["abandoned"] or (l["produced"] > 0 and v["sold"] >= l["produced"]):
                assert v["stage"] == "sold"
            elif l.get("archived"):
                assert v["stage"] == "built" and v["listed"] == 0
            elif v["listed"] > 0:
                assert v["stage"] == "listed"
            else:
                assert v["stage"] == "built"
        # THE THEOREM: badge ⟺ some listed lot ⟺ flow.listed>0 ⟺ one anchor.
        any_listed = any(v["stage"] == "listed" for v in L.values())
        anchors = [k for k, v in L.items() if v["is_listed_anchor"]]
        assert (r["listed_anchor"] is not None) == any_listed
        assert (r["listed_anchor"] is not None) == (r["flow"]["listed"] > 0)
        assert len(anchors) == (1 if r["listed_anchor"] is not None else 0)
        if anchors:
            assert anchors[0] == r["listed_anchor"]
            assert L[r["listed_anchor"]]["stage"] == "listed"
        # idempotent
        r2 = ind_track.reconcile(lots, fills, listed_units)
        assert r2 == r

    def test_all_scenarios(self):
        for lots, fills, listed_units in self.SCENARIOS:
            self._check(lots, fills, listed_units)
