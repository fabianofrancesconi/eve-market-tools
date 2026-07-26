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
        # Two lots of the same item; older one (done_at 0) fills first.
        lots = [self._lot("OLD", 40, cpu=100.0, done_at=0),
                self._lot("NEW", 40, cpu=110.0, done_at=50)]
        fills = [{"units": 50, "price": 200.0, "ts": 10}]
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
