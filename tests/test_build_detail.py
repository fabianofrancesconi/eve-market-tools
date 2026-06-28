"""
Unit tests for lp_core.build_detail -- the per-offer breakdown used by the
detail panel. Pins the per-redemption money math (line costs, fee model,
revenue, profit) for BOTH sell modes (patient = list at ask; instant = dump
into a buy order) and the m3 haul figures, and verifies it stays consistent
with evaluate()'s headline numbers.
"""
import pytest

import lp_core
from lp_core import build_detail, evaluate


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


_NAMES = {10: "Reward Item", 20: "Input Item"}
_VOLUMES = {10: 12.0, 20: 0.5}


def _detail(prices=None, volumes=None, lp_budget=1000):
    return build_detail(_offer(), prices or _prices(), _NAMES,
                        volumes if volumes is not None else _VOLUMES,
                        lp_budget=lp_budget, sales_tax=0.05, broker_fee=0.02)


class TestBuildDetailMoney:
    def test_required_line_cost(self):
        d = _detail()
        line = d["required_items"][0]
        assert line["line_cost"] == 150.0  # 3 * 50
        assert line["unit_price"] == 50.0

    def test_total_cost_is_isk_fee_plus_inputs(self):
        d = _detail()
        assert d["req_cost"] == 150.0
        assert d["isk_fee"] == 500
        assert d["total_cost"] == 650.0

    def test_revenue_patient(self):
        # qty 2 * ask 1000 * (1 - 0.05 - 0.02)
        assert _detail()["revenue_patient"] == pytest.approx(1860.0)

    def test_revenue_instant(self):
        # qty 2 * bid 900 * (1 - 0.05)
        assert _detail()["revenue_instant"] == pytest.approx(1710.0)

    def test_profit_both_modes(self):
        d = _detail()
        assert d["profit_patient"] == pytest.approx(1210.0)  # 1860 - 650
        assert d["profit_instant"] == pytest.approx(1060.0)  # 1710 - 650
        assert d["profit_best"] == pytest.approx(1210.0)

    def test_isk_per_lp_both_modes(self):
        d = _detail()
        assert d["isk_per_lp_patient"] == pytest.approx(12.1)
        assert d["isk_per_lp_instant"] == pytest.approx(10.6)
        assert d["isk_per_lp_best"] == pytest.approx(12.1)

    def test_ask_and_bid_exposed(self):
        d = _detail()
        assert d["ask"] == 1000.0
        assert d["bid"] == 900.0

    def test_matches_evaluate(self):
        """build_detail and evaluate must agree on profit / isk_per_lp."""
        d = _detail()
        sellable, _ = evaluate([_offer()], _prices(), lp_budget=1000,
                               sales_tax=0.05, broker_fee=0.02)
        row = sellable[0]
        assert d["profit_patient"] == row["profit_patient"]
        assert d["profit_instant"] == row["profit_instant"]
        assert d["isk_per_lp_patient"] == row["isk_per_lp_patient"]
        assert d["isk_per_lp_instant"] == row["isk_per_lp_instant"]
        assert d["max_units"] == row["max_units"]


class TestBuildDetailVolumes:
    def test_input_volume_per_redemption(self):
        # 3 input units * 0.5 m3
        assert _detail()["input_volume_per_redemption"] == 1.5

    def test_output_volume_per_redemption(self):
        # qty 2 * 12.0 m3
        assert _detail()["output_volume_per_redemption"] == 24.0
        assert _detail()["output"]["volume_per_redemption"] == 24.0

    def test_missing_volume_is_none(self):
        d = _detail(volumes={20: 0.5})  # output volume (10) absent
        assert d["output_volume_per_redemption"] is None
        # an input with a known volume still contributes
        assert d["input_volume_per_redemption"] == 1.5


class TestBuildDetailEdgeCases:
    def test_missing_required_price_flags_and_optimistic_profit(self):
        prices = _prices({20: {"sell_min": None, "buy_max": None,
                               "sell_volume": 0, "buy_volume": 0}})
        d = _detail(prices=prices)
        assert d["req_missing_price"] is True
        assert d["required_items"][0]["line_cost"] is None
        assert d["req_cost"] == 0.0
        assert d["profit_patient"] == pytest.approx(1360.0)  # 1860 - 500 - 0

    def test_no_output_price_yields_none_revenue_and_profit(self):
        prices = _prices({10: {"sell_min": None, "buy_max": None,
                               "sell_volume": 0, "buy_volume": 0}})
        d = _detail(prices=prices)
        assert d["revenue_patient"] is None
        assert d["revenue_instant"] is None
        assert d["profit_patient"] is None
        assert d["profit_instant"] is None
        assert d["isk_per_lp_patient"] is None
        assert d["isk_per_lp_instant"] is None
        assert d["profit_best"] is None

    def test_one_sided_market_prices_only_that_mode(self):
        # No bid -> instant has no revenue, patient still computed.
        prices = _prices({10: {"sell_min": 1000.0, "buy_max": None,
                               "sell_volume": 5, "buy_volume": 0}})
        d = _detail(prices=prices)
        assert d["revenue_patient"] == pytest.approx(1860.0)
        assert d["revenue_instant"] is None
        assert d["profit_instant"] is None
        assert d["profit_best"] == pytest.approx(1210.0)

    def test_zero_budget_zero_runs(self):
        assert _detail(lp_budget=0)["max_units"] == 0

    def test_spread_pct_reported(self):
        assert _detail()["spread_pct"] == 10.0
