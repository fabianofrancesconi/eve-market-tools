"""
Unit tests for the ESI-direct pricing functions in lp_core:
fetch_prices_esi, _summarise_orders, and the caching behaviour.
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import lp_core
from lp_core import (
    JITA_STATION_ID, JITA_REGION_ID, PRICE_CACHE_TTL,
    _summarise_orders, fetch_prices_esi,
)


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

    def test_filters_to_station(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"price": 10.0, "volume_remain": 100, "is_buy_order": False,
             "location_id": JITA_STATION_ID},
            {"price": 8.0, "volume_remain": 50, "is_buy_order": False,
             "location_id": 99999999},  # different station
        ]
        session = MagicMock()
        session.get.return_value = mock_resp

        result = fetch_prices_esi(
            [34], session, station_id=JITA_STATION_ID,
            region_id=JITA_REGION_ID, cache_dir=tmp_path, refresh=True)

        assert result[34]["sell_min"] == 10.0
        assert result[34]["sell_volume"] == 100.0
