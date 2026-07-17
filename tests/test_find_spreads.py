"""
Unit tests for arb_core.find_spreads -- the negative-spread (instant-flip)
detector. Pins the spread math (net proceeds after sales tax, per-unit and
total ISK opportunity, margin %), the best-order selection (cheapest sell vs
highest buy), the min_volume gate, and same-station vs cross-station keying.
"""
import pytest

from arb_core import find_spreads


def _order(type_id=34, location_id=60003760, price=100.0, is_buy=False,
           volume_remain=100, min_volume=1):
    return {
        "type_id": type_id,
        "location_id": location_id,
        "price": price,
        "is_buy_order": is_buy,
        "volume_remain": volume_remain,
        "min_volume": min_volume,
    }


class TestProfitableSpread:
    def _result(self):
        orders = [
            _order(price=100.0, is_buy=False, volume_remain=50),
            _order(price=130.0, is_buy=True, volume_remain=40),
        ]
        res = find_spreads(orders, sales_tax=0.05, same_station_only=True)
        assert len(res) == 1
        return res[0]

    def test_net_per_unit_after_tax(self):
        # buy 130 * (1 - 0.05) = 123.5 ; minus sell 100 = 23.5
        assert self._result()["net_per_unit"] == 23.5

    def test_flippable_qty_is_min_of_remains(self):
        assert self._result()["flippable_qty"] == 40

    def test_isk_opportunity(self):
        assert self._result()["isk_opportunity"] == 23.5 * 40

    def test_margin_pct_relative_to_cost(self):
        assert self._result()["margin_pct"] == 23.5 / 100.0 * 100.0

    def test_only_sales_tax_deducted_no_broker_fee(self):
        # The arb model dumps instantly into a buy order, which pays sales tax
        # ONLY (no broker fee — no order is placed). find_spreads must therefore
        # take a single sales_tax rate; if a caller folds a broker fee into it,
        # the effective deduction is larger and profitable flips vanish. Prove
        # that a flip which clears on tax-only would be wrongly killed by a
        # combined tax+broker rate — so the client must pass tax alone.
        orders = [
            _order(price=1_000_000.0, is_buy=False, volume_remain=10),
            _order(price=1_048_000.0, is_buy=True, volume_remain=10),
        ]
        # sales tax 4.5% only: 1_048_000 * 0.955 - 1_000_000 = +840 -> a deal
        ok = find_spreads(orders, sales_tax=0.045, same_station_only=True)
        assert len(ok) == 1
        assert ok[0]["net_per_unit"] == pytest.approx(840.0)
        # tax 4.5% + broker 3% folded together = 7.5%: 1_048_000*0.925 -
        # 1_000_000 = -30_600 -> the deal vanishes (the bug this fix prevents)
        killed = find_spreads(orders, sales_tax=0.075, same_station_only=True)
        assert killed == []

    def test_records_prices_and_locations(self):
        r = self._result()
        assert r["sell_price"] == 100.0
        assert r["buy_price"] == 130.0
        assert r["sell_location"] == 60003760
        assert r["buy_location"] == 60003760


class TestNoSpread:
    def test_tax_eats_the_margin(self):
        # buy 105 * 0.95 = 99.75 < sell 100 -> no arb
        orders = [_order(price=100.0, is_buy=False),
                  _order(price=105.0, is_buy=True)]
        assert find_spreads(orders, 0.05, True) == []

    def test_exact_breakeven_excluded(self):
        # buy 100 * 0.9 = 90 == sell 90 -> strictly-greater test fails
        orders = [_order(price=90.0, is_buy=False),
                  _order(price=100.0, is_buy=True)]
        assert find_spreads(orders, 0.10, True) == []

    def test_only_sell_orders_no_result(self):
        orders = [_order(price=100.0, is_buy=False),
                  _order(price=90.0, is_buy=False)]
        assert find_spreads(orders, 0.05, True) == []


class TestBestOrderSelection:
    def test_picks_cheapest_sell_and_highest_buy(self):
        orders = [
            _order(price=120.0, is_buy=False, volume_remain=10),
            _order(price=100.0, is_buy=False, volume_remain=10),  # cheapest
            _order(price=130.0, is_buy=True, volume_remain=10),
            _order(price=150.0, is_buy=True, volume_remain=10),   # highest
        ]
        r = find_spreads(orders, 0.05, True)[0]
        assert r["sell_price"] == 100.0
        assert r["buy_price"] == 150.0


class TestMinVolumeGate:
    def test_qty_below_buy_min_volume_is_skipped(self):
        # Only 5 units available to flip but the buy order demands >= 20.
        orders = [
            _order(price=100.0, is_buy=False, volume_remain=5),
            _order(price=200.0, is_buy=True, volume_remain=100, min_volume=20),
        ]
        assert find_spreads(orders, 0.05, True) == []

    def test_qty_meets_min_volume_is_kept(self):
        orders = [
            _order(price=100.0, is_buy=False, volume_remain=25),
            _order(price=200.0, is_buy=True, volume_remain=100, min_volume=20),
        ]
        res = find_spreads(orders, 0.05, True)
        assert len(res) == 1
        assert res[0]["flippable_qty"] == 25


class TestStationKeying:
    def test_same_station_only_requires_matching_location(self):
        # Cheap sell at station A, juicy buy at station B -> NOT a same-station flip.
        orders = [
            _order(price=100.0, is_buy=False, location_id=1),
            _order(price=200.0, is_buy=True, location_id=2),
        ]
        assert find_spreads(orders, 0.05, same_station_only=True) == []

    def test_cross_station_pairs_across_locations(self):
        orders = [
            _order(price=100.0, is_buy=False, location_id=1),
            _order(price=200.0, is_buy=True, location_id=2),
        ]
        res = find_spreads(orders, 0.05, same_station_only=False)
        assert len(res) == 1
        assert res[0]["sell_location"] == 1
        assert res[0]["buy_location"] == 2

    def test_results_sorted_by_isk_opportunity_desc(self):
        orders = [
            # small opportunity for type 34
            _order(type_id=34, price=100.0, is_buy=False, volume_remain=2),
            _order(type_id=34, price=130.0, is_buy=True, volume_remain=2),
            # large opportunity for type 35
            _order(type_id=35, price=100.0, is_buy=False, volume_remain=100),
            _order(type_id=35, price=200.0, is_buy=True, volume_remain=100),
        ]
        res = find_spreads(orders, 0.05, same_station_only=True)
        opps = [r["isk_opportunity"] for r in res]
        assert opps == sorted(opps, reverse=True)
        assert res[0]["type_id"] == 35
