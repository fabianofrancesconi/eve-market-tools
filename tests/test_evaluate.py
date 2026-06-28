"""
Unit tests for the core LP profit formulas in lp_core:

- _spread_pct  -- ask/bid spread percentage
- evaluate     -- per-offer profit, ISK/LP, budget projection, fee model

These pin the actual money math (revenue net of fees, required-input cost,
profit, isk_per_lp, max_units) so a future change to the formulas can't
silently shift the numbers shown to the user.
"""
import math

import pytest

import lp_core
from lp_core import evaluate, _spread_pct


# ---------------------------------------------------------------------------
# _spread_pct
# ---------------------------------------------------------------------------

class TestSpreadPct:
    def test_normal_spread(self):
        # (ask - bid) / ask * 100
        assert _spread_pct(1000.0, 900.0) == 10.0

    def test_zero_spread_when_ask_equals_bid(self):
        assert _spread_pct(1000.0, 1000.0) == 0.0

    def test_no_bids_is_one_hundred_pct(self):
        # Asks exist, nobody is buying -> treated as maximally illiquid.
        assert _spread_pct(1000.0, None) == 100.0

    def test_no_ask_is_none(self):
        assert _spread_pct(None, 900.0) is None

    def test_nothing_is_none(self):
        assert _spread_pct(None, None) is None

    def test_wide_spread(self):
        # ask 200, bid 50 -> 75%
        assert _spread_pct(200.0, 50.0) == 75.0


# ---------------------------------------------------------------------------
# evaluate -- shared fixtures
# ---------------------------------------------------------------------------

def _offer(**kw):
    base = {
        "offer_id": 1,
        "type_id": 10,
        "quantity": 2,
        "lp_cost": 100,
        "isk_cost": 500,
        "required_items": [{"type_id": 20, "quantity": 3}],
    }
    base.update(kw)
    return base


def _prices(overrides=None):
    base = {
        10: {"sell_min": 1000.0, "buy_max": 900.0, "sell_volume": 5, "buy_volume": 7},
        20: {"sell_min": 50.0, "buy_max": 40.0, "sell_volume": 9, "buy_volume": 1},
    }
    if overrides:
        base.update(overrides)
    return base


class TestEvaluateSellOrder:
    """instant=False: list a sell order -> pay sales tax + broker fee."""

    def _row(self):
        sellable, _ = evaluate([_offer()], _prices(),
                               lp_budget=1000, sales_tax=0.05, broker_fee=0.02,
                               instant=False)
        return sellable[0]

    def test_revenue_uses_ask_and_both_fees(self):
        # qty 2 * ask 1000 * (1 - 0.05 - 0.02) = 1860
        # profit = 1860 - isk_cost 500 - req 150 = 1210
        assert self._row()["profit_per"] == pytest.approx(1210.0)

    def test_isk_per_lp(self):
        # 1210 / 100 LP
        assert self._row()["isk_per_lp"] == pytest.approx(12.1)

    def test_required_input_cost_uses_ask(self):
        # 3 units * sell_min 50 = 150
        assert self._row()["req_cost"] == 150.0

    def test_max_units_floors_budget_over_lp_cost(self):
        # floor(1000 / 100) = 10
        assert self._row()["max_units"] == 10

    def test_total_profit_scales_by_max_units(self):
        assert self._row()["total_profit"] == pytest.approx(12100.0)

    def test_total_isk_in(self):
        # 10 runs * (isk_cost 500 + req 150)
        assert self._row()["total_isk_in"] == 6500.0

    def test_lp_used(self):
        assert self._row()["lp_used"] == 1000

    def test_spread_pct_recorded(self):
        assert self._row()["spread_pct"] == 10.0


class TestEvaluateInstant:
    """instant=True: sell to a buy order -> sales tax only, no broker fee."""

    def _row(self):
        sellable, _ = evaluate([_offer()], _prices(),
                               lp_budget=1000, sales_tax=0.05, broker_fee=0.02,
                               instant=True)
        return sellable[0]

    def test_revenue_uses_bid_and_tax_only(self):
        # qty 2 * bid 900 * (1 - 0.05) = 1710
        # profit = 1710 - 500 - 150 = 1060
        assert self._row()["profit_per"] == pytest.approx(1060.0)

    def test_broker_fee_not_applied_when_instant(self):
        # Recompute with a huge broker fee: instant profit must be unchanged.
        sellable, _ = evaluate([_offer()], _prices(),
                               lp_budget=1000, sales_tax=0.05, broker_fee=0.50,
                               instant=True)
        assert sellable[0]["profit_per"] == pytest.approx(1060.0)


class TestEvaluateUnsellable:
    def test_no_output_price_is_unsellable(self):
        prices = _prices({10: {"sell_min": None, "buy_max": None,
                               "sell_volume": 0, "buy_volume": 0}})
        sellable, unsellable = evaluate([_offer()], prices,
                                        lp_budget=1000, sales_tax=0.05,
                                        broker_fee=0.02, instant=False)
        assert sellable == []
        assert len(unsellable) == 1
        assert unsellable[0]["unsellable"] is True

    def test_instant_unsellable_when_no_bid(self):
        # No buy_max -> can't sell instantly even though an ask exists.
        prices = _prices({10: {"sell_min": 1000.0, "buy_max": None,
                               "sell_volume": 5, "buy_volume": 0}})
        sellable, unsellable = evaluate([_offer()], prices,
                                        lp_budget=1000, sales_tax=0.05,
                                        broker_fee=0.02, instant=True)
        assert sellable == []
        assert len(unsellable) == 1

    def test_zero_lp_cost_offer_skipped_entirely(self):
        offer = _offer(lp_cost=0)
        sellable, unsellable = evaluate([offer], _prices(),
                                        lp_budget=1000, sales_tax=0.05,
                                        broker_fee=0.02, instant=False)
        assert sellable == [] and unsellable == []


class TestEvaluateRequiredItems:
    def test_missing_required_price_flags_and_excludes_that_line(self):
        # Required item 20 has no price -> req_missing True, its cost is NOT
        # added (profit is therefore optimistic, surfaced via the flag).
        prices = _prices({20: {"sell_min": None, "buy_max": None,
                               "sell_volume": 0, "buy_volume": 0}})
        sellable, _ = evaluate([_offer()], prices,
                               lp_budget=1000, sales_tax=0.05,
                               broker_fee=0.02, instant=False)
        row = sellable[0]
        assert row["req_missing"] is True
        assert row["req_cost"] == 0.0
        # profit = 1860 - 500 - 0
        assert row["profit_per"] == pytest.approx(1360.0)

    def test_multiple_required_items_summed(self):
        offer = _offer(required_items=[
            {"type_id": 20, "quantity": 3},
            {"type_id": 30, "quantity": 2},
        ])
        prices = _prices()
        prices[30] = {"sell_min": 100.0, "buy_max": 90.0,
                      "sell_volume": 1, "buy_volume": 1}
        sellable, _ = evaluate([offer], prices,
                               lp_budget=1000, sales_tax=0.05,
                               broker_fee=0.02, instant=False)
        # 3*50 + 2*100 = 350
        assert sellable[0]["req_cost"] == 350.0


class TestEvaluateBudget:
    def test_zero_budget_gives_zero_runs(self):
        sellable, _ = evaluate([_offer()], _prices(),
                               lp_budget=0, sales_tax=0.05,
                               broker_fee=0.02, instant=False)
        row = sellable[0]
        assert row["max_units"] == 0
        assert row["total_profit"] == 0
        assert row["total_isk_in"] == 0

    def test_budget_not_a_multiple_floors_down(self):
        # budget 250 / lp_cost 100 -> 2 runs (floor)
        sellable, _ = evaluate([_offer()], _prices(),
                               lp_budget=250, sales_tax=0.05,
                               broker_fee=0.02, instant=False)
        assert sellable[0]["max_units"] == math.floor(250 / 100) == 2


class TestEvaluateSorting:
    def test_sellable_sorted_by_isk_per_lp_desc(self):
        good = _offer(offer_id=1, type_id=10, lp_cost=100)
        meh = _offer(offer_id=2, type_id=40, lp_cost=100,
                     required_items=[], isk_cost=0)
        prices = _prices()
        # type 40: cheap reward -> low isk/lp
        prices[40] = {"sell_min": 600.0, "buy_max": 550.0,
                      "sell_volume": 1, "buy_volume": 1}
        sellable, _ = evaluate([meh, good], prices,
                               lp_budget=1000, sales_tax=0.05,
                               broker_fee=0.02, instant=False)
        keys = [r["isk_per_lp"] for r in sellable]
        assert keys == sorted(keys, reverse=True)
        assert sellable[0]["offer_id"] == 1
