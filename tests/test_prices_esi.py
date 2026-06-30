"""
Unit tests for the ESI-direct pricing functions in lp_core:
fetch_prices_esi, _summarise_orders, _esi_orders_for_type, and caching.
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import lp_core
from lp_core import (
    JITA_STATION_ID, JITA_REGION_ID, PRICE_CACHE_TTL,
    _summarise_orders, _esi_orders_for_type, fetch_prices_esi,
)


# ---------------------------------------------------------------------------
# _summarise_orders
# ---------------------------------------------------------------------------

class TestSummariseOrders:
    def test_basic_sell_and_buy(self):
        orders = [
            {"price": 100.0, "volume_remain": 50, "is_buy_order": False},
            {"price": 90.0, "volume_remain": 200, "is_buy_order": False},
            {"price": 80.0, "volume_remain": 100, "is_buy_order": True},
            {"price": 70.0, "volume_remain": 300, "is_buy_order": True},
        ]
        result = _summarise_orders(orders)
        assert result["sell_min"] == 90.0
        assert result["buy_max"] == 80.0
        assert result["sell_volume"] == 250.0
        assert result["buy_volume"] == 400.0

    def test_no_sell_orders(self):
        orders = [
            {"price": 80.0, "volume_remain": 100, "is_buy_order": True},
        ]
        result = _summarise_orders(orders)
        assert result["sell_min"] is None
        assert result["buy_max"] == 80.0

    def test_no_buy_orders(self):
        orders = [
            {"price": 100.0, "volume_remain": 50, "is_buy_order": False},
        ]
        result = _summarise_orders(orders)
        assert result["sell_min"] == 100.0
        assert result["buy_max"] is None

    def test_empty_orders(self):
        result = _summarise_orders([])
        assert result["sell_min"] is None
        assert result["buy_max"] is None
        assert result["sell_volume"] == 0.0
        assert result["buy_volume"] == 0.0

    def test_single_sell_at_zero_price_treated_as_none(self):
        """A price of 0.0 is treated as missing (converted to None via `or None`)."""
        orders = [
            {"price": 0.0, "volume_remain": 10, "is_buy_order": False},
        ]
        result = _summarise_orders(orders)
        assert result["sell_min"] is None

    def test_multiple_sells_picks_cheapest(self):
        orders = [
            {"price": 500.0, "volume_remain": 10, "is_buy_order": False},
            {"price": 300.0, "volume_remain": 20, "is_buy_order": False},
            {"price": 400.0, "volume_remain": 30, "is_buy_order": False},
        ]
        result = _summarise_orders(orders)
        assert result["sell_min"] == 300.0
        assert result["sell_volume"] == 60.0

    def test_multiple_buys_picks_highest(self):
        orders = [
            {"price": 10.0, "volume_remain": 100, "is_buy_order": True},
            {"price": 50.0, "volume_remain": 200, "is_buy_order": True},
            {"price": 30.0, "volume_remain": 300, "is_buy_order": True},
        ]
        result = _summarise_orders(orders)
        assert result["buy_max"] == 50.0
        assert result["buy_volume"] == 600.0


# ---------------------------------------------------------------------------
# _esi_orders_for_type
# ---------------------------------------------------------------------------

class TestEsiOrdersForType:
    def test_filters_to_station(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 100, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
            {"price": 8.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": 99999999},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = _esi_orders_for_type(34, session, JITA_STATION_ID, JITA_REGION_ID)
        assert len(result) == 1
        assert result[0]["location_id"] == JITA_STATION_ID

    def test_handles_non_200_gracefully(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        session = MagicMock()
        session.get.return_value = mock_resp

        result = _esi_orders_for_type(34, session, JITA_STATION_ID, JITA_REGION_ID)
        assert result == []

    def test_paginates_correctly(self):
        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.headers = {"X-Pages": "2"}
        page1_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 100, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.headers = {"X-Pages": "2"}
        page2_resp.json.return_value = [
            {"price": 12.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.side_effect = [page1_resp, page2_resp]

        result = _esi_orders_for_type(34, session, JITA_STATION_ID, JITA_REGION_ID)
        assert len(result) == 2
        assert session.get.call_count == 2

    def test_stops_at_page_limit(self):
        """Should stop at page 10 even if X-Pages says more exist."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "99"}
        mock_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 1, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = _esi_orders_for_type(34, session, JITA_STATION_ID, JITA_REGION_ID)
        assert session.get.call_count == 10

    def test_empty_first_page(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = []
        session = MagicMock()
        session.get.return_value = mock_resp

        result = _esi_orders_for_type(34, session, JITA_STATION_ID, JITA_REGION_ID)
        assert result == []


# ---------------------------------------------------------------------------
# fetch_prices_esi — caching
# ---------------------------------------------------------------------------

class TestFetchPricesEsiCaching:
    def test_returns_cached_data_within_ttl(self, tmp_path):
        cache_data = {
            "_ts": time.time(),
            "34": {"sell_min": 5.0, "buy_max": 4.0,
                   "sell_volume": 1000.0, "buy_volume": 500.0},
        }
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        cache_file.write_text(json.dumps(cache_data))

        session = MagicMock()
        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=False)

        assert result[34]["sell_min"] == 5.0
        assert result[34]["buy_max"] == 4.0
        session.get.assert_not_called()

    def test_fetches_fresh_when_cache_expired(self, tmp_path):
        cache_data = {
            "_ts": time.time() - PRICE_CACHE_TTL - 10,
            "34": {"sell_min": 5.0, "buy_max": 4.0,
                   "sell_volume": 1000.0, "buy_volume": 500.0},
        }
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        cache_file.write_text(json.dumps(cache_data))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 6.0, "volume_remain": 2000, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
            {"price": 4.5, "volume_remain": 800, "is_buy_order": True,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=False)

        assert result[34]["sell_min"] == 6.0
        assert result[34]["buy_max"] == 4.5
        session.get.assert_called()

    def test_refresh_bypasses_cache(self, tmp_path):
        cache_data = {
            "_ts": time.time(),
            "34": {"sell_min": 5.0, "buy_max": 4.0,
                   "sell_volume": 1000.0, "buy_volume": 500.0},
        }
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        cache_file.write_text(json.dumps(cache_data))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 7.0, "volume_remain": 500, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        assert result[34]["sell_min"] == 7.0
        session.get.assert_called()

    def test_refresh_preserves_other_cached_types(self, tmp_path):
        """Refreshing a subset of types must not destroy cached entries for other types."""
        cache_data = {
            "_ts": time.time(),
            "34": {"sell_min": 5.0, "buy_max": 4.0,
                   "sell_volume": 1000.0, "buy_volume": 500.0},
            "35": {"sell_min": 99.0, "buy_max": 88.0,
                   "sell_volume": 200.0, "buy_volume": 100.0},
        }
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        cache_file.write_text(json.dumps(cache_data))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 7.0, "volume_remain": 500, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        # Refresh only type 34
        fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        # Type 35 should still be in the cache file
        saved = json.loads(cache_file.read_text())
        assert saved["35"]["sell_min"] == 99.0
        assert saved["34"]["sell_min"] == 7.0

    def test_fetches_only_missing_types_from_cache(self, tmp_path):
        """When cache is valid but missing some requested types, only fetch those."""
        cache_data = {
            "_ts": time.time(),
            "34": {"sell_min": 5.0, "buy_max": 4.0,
                   "sell_volume": 1000.0, "buy_volume": 500.0},
        }
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        cache_file.write_text(json.dumps(cache_data))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 20.0, "volume_remain": 300, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34, 35], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=False)

        # Type 34 served from cache (no ESI call for it)
        assert result[34]["sell_min"] == 5.0
        # Type 35 fetched from ESI
        assert result[35]["sell_min"] == 20.0
        # Only one ESI call (for type 35)
        assert session.get.call_count == 1

    def test_handles_esi_error_for_one_type(self, tmp_path):
        """If ESI returns an error for one type, it gets None prices, others work."""
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {"X-Pages": "1"}
        ok_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        err_resp = MagicMock()
        err_resp.status_code = 500

        session = MagicMock()
        session.get.side_effect = [ok_resp, err_resp]

        result = fetch_prices_esi(
            [34, 35], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        assert result[34]["sell_min"] == 10.0
        # type 35 failed — should have None prices
        assert result[35]["sell_min"] is None
        assert result[35]["buy_max"] is None

    def test_handles_network_exception(self, tmp_path):
        """A network exception for one type doesn't crash the whole batch."""
        import requests
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {"X-Pages": "1"}
        ok_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]

        session = MagicMock()
        session.get.side_effect = [ok_resp, requests.ConnectionError("timeout")]

        result = fetch_prices_esi(
            [34, 35], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        assert result[34]["sell_min"] == 10.0
        assert result[35]["sell_min"] is None

    def test_deduplicates_type_ids(self, tmp_path):
        """Passing duplicate type_ids should only fetch once."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34, 34, 34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        assert session.get.call_count == 1
        assert 34 in result

    def test_emit_callback_fires(self, tmp_path):
        """The emit callback is invoked during fetching."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        calls = []
        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True,
            emit=lambda idx, total: calls.append((idx, total)))

        assert len(calls) == 1
        assert calls[0] == (0, 1)

    def test_no_cache_file_yet(self, tmp_path):
        """Works fine when the cache file doesn't exist yet."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 15.0, "volume_remain": 80, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=False)

        assert result[34]["sell_min"] == 15.0
        # Cache file should now exist
        cache_file = tmp_path / f"esi_prices_{JITA_STATION_ID}.json"
        assert cache_file.exists()
