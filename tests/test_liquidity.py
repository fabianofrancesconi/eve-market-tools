"""
Tests for the market-saturation layer added in v1.4.0:

- _median_daily_volume   -- robust daily-volume summary from ESI history
- fetch_history_volumes  -- per-type history fetch + shared mhist cache
- enrich_liquidity       -- days-to-clear and crowding-capped profit math
"""
import json
import time
from unittest.mock import MagicMock, patch

import lp_core


# ---------------------------------------------------------------------------
# _median_daily_volume
# ---------------------------------------------------------------------------

class TestMedianDailyVolume:
    def test_median_of_volumes(self):
        hist = [{"volume": 100}, {"volume": 300}, {"volume": 200}]
        assert lp_core._median_daily_volume(hist) == 200

    def test_empty_history_is_none(self):
        assert lp_core._median_daily_volume([]) is None

    def test_skips_none_volumes(self):
        hist = [{"volume": None}, {"volume": 50}, {"volume": 150}]
        assert lp_core._median_daily_volume(hist) == 100

    def test_all_none_is_none(self):
        assert lp_core._median_daily_volume([{"volume": None}, {}]) is None

    def test_uses_only_last_n_days(self):
        # 40 days: first 10 are huge whale days that must be ignored when
        # HISTORY_DAYS=30 keeps only the tail of steady 10-unit days.
        hist = [{"volume": 1_000_000} for _ in range(10)] + \
               [{"volume": 10} for _ in range(30)]
        assert lp_core._median_daily_volume(hist, days=30) == 10


# ---------------------------------------------------------------------------
# fetch_history_volumes
# ---------------------------------------------------------------------------

_HIST = [{"date": "2024-01-01", "volume": 100},
         {"date": "2024-01-02", "volume": 300},
         {"date": "2024-01-03", "volume": 200}]


class TestFetchHistoryVolumes:
    def test_fetches_and_returns_median(self, tmp_path):
        resp = MagicMock(status_code=200)
        resp.json.return_value = _HIST
        with patch.object(lp_core.requests.Session, "get", return_value=resp):
            s = lp_core.requests.Session()
            out = lp_core.fetch_history_volumes({34}, 10000002, s, tmp_path)
        assert out == {34: 200}

    def test_uses_disk_cache_without_calling_esi(self, tmp_path):
        (tmp_path / "mhist_10000002_34.json").write_text(
            json.dumps({"_ts": time.time(), "data": _HIST}))
        s = MagicMock()
        s.get.side_effect = AssertionError("must not hit ESI when cached")
        out = lp_core.fetch_history_volumes({34}, 10000002, s, tmp_path)
        assert out == {34: 200}

    def test_refetches_when_cache_stale(self, tmp_path):
        (tmp_path / "mhist_10000002_34.json").write_text(
            json.dumps({"_ts": 0.0, "data": []}))
        resp = MagicMock(status_code=200)
        resp.json.return_value = _HIST
        s = MagicMock()
        s.get.return_value = resp
        out = lp_core.fetch_history_volumes({34}, 10000002, s, tmp_path)
        assert out == {34: 200}
        s.get.assert_called_once()

    def test_non_200_yields_none(self, tmp_path):
        s = MagicMock()
        s.get.return_value = MagicMock(status_code=404)
        out = lp_core.fetch_history_volumes({999}, 10000002, s, tmp_path)
        assert out == {999: None}

    def test_no_history_yields_none(self, tmp_path):
        resp = MagicMock(status_code=200)
        resp.json.return_value = []
        s = MagicMock()
        s.get.return_value = resp
        out = lp_core.fetch_history_volumes({34}, 10000002, s, tmp_path)
        assert out == {34: None}

    def test_writes_cache_file(self, tmp_path):
        resp = MagicMock(status_code=200)
        resp.json.return_value = _HIST
        s = MagicMock()
        s.get.return_value = resp
        lp_core.fetch_history_volumes({34}, 10000002, s, tmp_path)
        assert (tmp_path / "mhist_10000002_34.json").exists()


# ---------------------------------------------------------------------------
# enrich_liquidity
# ---------------------------------------------------------------------------

def _row(**kw):
    base = {"offer_id": 1, "name_id": 101, "qty": 5, "max_units": 10,
            "profit_per": 450.0, "sell_volume": 1000}
    base.update(kw)
    return base


class TestEnrichLiquidity:
    def test_days_to_clear_is_supply_over_daily_volume(self):
        out = lp_core.enrich_liquidity([_row()], {101: 200})
        assert out[1]["days_to_clear"] == 1000 / 200  # 5 days
        assert out[1]["daily_vol"] == 200

    def test_no_history_gives_no_days(self):
        out = lp_core.enrich_liquidity([_row()], {101: None})
        assert out[1]["daily_vol"] is None
        assert out[1]["days_to_clear"] is None

    def test_zero_volume_market_never_clears(self):
        out = lp_core.enrich_liquidity([_row()], {101: 0})
        assert out[1]["daily_vol"] == 0
        assert out[1]["days_to_clear"] is None

    def test_only_raw_signals_returned(self):
        # No invented constants leak through: just the two raw market signals.
        out = lp_core.enrich_liquidity([_row()], {101: 200})
        assert set(out[1]) == {"daily_vol", "days_to_clear"}

    def test_keyed_by_offer_id(self):
        rows = [_row(offer_id=7, name_id=1), _row(offer_id=8, name_id=2)]
        out = lp_core.enrich_liquidity(rows, {1: 100, 2: 100})
        assert set(out) == {7, 8}


# ---------------------------------------------------------------------------
# _median_daily_avg_price
# ---------------------------------------------------------------------------

class TestMedianDailyAvgPrice:
    def test_median_of_average_prices(self):
        hist = [{"average": 100.0}, {"average": 300.0}, {"average": 200.0}]
        assert lp_core._median_daily_avg_price(hist) == 200.0

    def test_empty_history_is_none(self):
        assert lp_core._median_daily_avg_price([]) is None

    def test_skips_missing_prices(self):
        hist = [{"average": None}, {"average": 50.0}, {"average": 150.0}]
        assert lp_core._median_daily_avg_price(hist) == 100.0

    def test_all_missing_is_none(self):
        assert lp_core._median_daily_avg_price([{"average": None}, {}]) is None

    def test_uses_only_last_n_days(self):
        # A 10-day fire sale at the front is ignored when the 30-day tail is
        # steady, so the median reflects the recent norm, not the dump.
        hist = [{"average": 1.0} for _ in range(10)] + \
               [{"average": 500.0} for _ in range(30)]
        assert lp_core._median_daily_avg_price(hist, days=30) == 500.0


# ---------------------------------------------------------------------------
# fetch_history_prices  (shares the mhist cache with fetch_history_volumes)
# ---------------------------------------------------------------------------

_HIST_PX = [{"date": "2024-01-01", "average": 100.0},
            {"date": "2024-01-02", "average": 300.0},
            {"date": "2024-01-03", "average": 200.0}]


class TestFetchHistoryPrices:
    def test_fetches_and_returns_median_price(self, tmp_path):
        resp = MagicMock(status_code=200)
        resp.json.return_value = _HIST_PX
        with patch.object(lp_core.requests.Session, "get", return_value=resp):
            s = lp_core.requests.Session()
            out = lp_core.fetch_history_prices({34}, 10000002, s, tmp_path)
        assert out == {34: 200.0}

    def test_uses_disk_cache_without_calling_esi(self, tmp_path):
        (tmp_path / "mhist_10000002_34.json").write_text(
            json.dumps({"_ts": time.time(), "data": _HIST_PX}))
        s = MagicMock()
        s.get.side_effect = AssertionError("must not hit ESI when cached")
        out = lp_core.fetch_history_prices({34}, 10000002, s, tmp_path)
        assert out == {34: 200.0}

    def test_non_200_yields_none(self, tmp_path):
        s = MagicMock()
        s.get.return_value = MagicMock(status_code=404)
        out = lp_core.fetch_history_prices({999}, 10000002, s, tmp_path)
        assert out == {999: None}


# ---------------------------------------------------------------------------
# suggested_list_price
# ---------------------------------------------------------------------------

class TestSuggestedListPrice:
    def test_lists_at_lowest_sell_in_a_healthy_market(self):
        # Lowest sell at/above fair value -> sit at the top of the book.
        assert lp_core.suggested_list_price(1000.0, 800.0) == 1000.0

    def test_holds_at_fair_value_when_market_is_dumped(self):
        # Lowest sell below fair value -> don't join the race to the bottom.
        assert lp_core.suggested_list_price(600.0, 800.0) == 800.0

    def test_no_history_falls_back_to_ask(self):
        assert lp_core.suggested_list_price(1000.0, None) == 1000.0

    def test_no_sell_orders_falls_back_to_fair(self):
        assert lp_core.suggested_list_price(None, 800.0) == 800.0

    def test_neither_signal_is_none(self):
        assert lp_core.suggested_list_price(None, None) is None
