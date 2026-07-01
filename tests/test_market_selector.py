"""
Tests for the configurable market station feature (v1.1.0).

Bugs covered:
- JITA_STATION_ID not imported in lp-web.py → NameError on every scan
- Unknown station_id not falling back to Jita
- Market station not passed to fetch_prices / fetch_orderbook_jita
- Market dropdown missing from HTML
- saveLS/loadSettings not including market field
"""
import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)

import lp_core


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def http_get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


@pytest.fixture()
def tmp_server(tmp_path):
    orig_cache = lp_web.CACHE_DIR
    orig_corps = lp_web.NPC_CORPS[:]
    lp_web.CACHE_DIR = tmp_path
    lp_web.NPC_CORPS.clear()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), lp_web.Handler)
    port = srv.server_address[1]
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01),
                     daemon=True).start()

    yield f"http://127.0.0.1:{port}", tmp_path

    srv.shutdown()
    lp_web.CACHE_DIR = orig_cache
    lp_web.NPC_CORPS.clear()
    lp_web.NPC_CORPS.extend(orig_corps)


# ---------------------------------------------------------------------------
# Import sanity — the NameError that broke every scan
# ---------------------------------------------------------------------------

class TestImports:
    def test_jita_station_id_importable_from_lp_web(self):
        """JITA_STATION_ID must be importable in lp_web's namespace (was missing)."""
        assert hasattr(lp_web, "JITA_STATION_ID")
        assert lp_web.JITA_STATION_ID == 60003760

    def test_trade_hubs_importable_from_lp_web(self):
        assert hasattr(lp_web, "TRADE_HUBS")
        assert isinstance(lp_web.TRADE_HUBS, dict)

    def test_trade_hubs_contains_jita(self):
        assert 60003760 in lp_web.TRADE_HUBS
        assert lp_web.TRADE_HUBS[60003760]["name"] == "Jita 4-4"

    def test_all_hubs_have_region_id(self):
        for sid, hub in lp_web.TRADE_HUBS.items():
            assert "region_id" in hub, f"station {sid} missing region_id"
            assert isinstance(hub["region_id"], int)


# ---------------------------------------------------------------------------
# lp_core.fetch_prices — station_id param
# ---------------------------------------------------------------------------

class TestFetchPricesStationParam:
    def _make_session(self, station_id):
        """Returns a mock session that asserts the correct station was requested."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}

        def _get(url, params=None, **kwargs):
            assert params["station"] == station_id, (
                f"expected station {station_id}, got {params.get('station')}")
            return mock_resp

        sess = MagicMock()
        sess.get.side_effect = _get
        return sess

    def test_default_is_jita(self):
        sess = self._make_session(lp_core.JITA_STATION_ID)
        lp_core.fetch_prices([34], sess)  # no station_id → must use Jita

    def test_amarr_station_passed_through(self):
        amarr = 60008494
        sess = self._make_session(amarr)
        lp_core.fetch_prices([34], sess, station_id=amarr)

    def test_rens_station_passed_through(self):
        rens = 60004588
        sess = self._make_session(rens)
        lp_core.fetch_prices([34], sess, station_id=rens)


# ---------------------------------------------------------------------------
# lp_core.fetch_orderbook_jita — station_id / region_id params
# ---------------------------------------------------------------------------

class TestFetchOrderbookParams:
    def _make_session(self, region_id, station_id):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"location_id": station_id, "price": 100.0, "volume_remain": 10,
             "is_buy_order": False},
        ]
        sess = MagicMock()

        def _get(url, params=None, **kwargs):
            assert str(region_id) in url, f"expected region {region_id} in URL {url}"
            return mock_resp

        sess.get.side_effect = _get
        return sess

    def test_default_uses_jita_region(self):
        sess = self._make_session(lp_core.JITA_REGION_ID, lp_core.JITA_STATION_ID)
        lp_core.fetch_orderbook_jita(34, "sell", sess)

    def test_amarr_region_used_when_specified(self):
        amarr_station, amarr_region = 60008494, 10000043
        sess = self._make_session(amarr_region, amarr_station)
        lp_core.fetch_orderbook_jita(34, "sell", sess,
                                     station_id=amarr_station, region_id=amarr_region)

    def test_filters_to_specified_station(self):
        """Orders from other stations in the region must be excluded."""
        other_station = 60000001
        target_station = 60008494
        region = 10000043

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Pages": "1"}
        mock_resp.json.return_value = [
            {"location_id": target_station, "price": 100.0, "volume_remain": 5,
             "is_buy_order": False},
            {"location_id": other_station, "price": 90.0, "volume_remain": 99,
             "is_buy_order": False},
        ]
        sess = MagicMock()
        sess.get.return_value = mock_resp

        book = lp_core.fetch_orderbook_jita(34, "sell", sess,
                                             station_id=target_station, region_id=region)
        prices = [entry[0] for entry in book]
        assert 90.0 not in prices, "order from wrong station must be excluded"
        assert 100.0 in prices


# ---------------------------------------------------------------------------
# do_scan — unknown station falls back to Jita
# ---------------------------------------------------------------------------

class TestDoScanStationFallback:
    def test_invalid_station_falls_back_to_jita(self, tmp_path):
        """An unrecognised station_id must silently fall back to Jita, not crash."""
        lp_web.CACHE_DIR = tmp_path

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}

        price_calls = []

        def _get(url, params=None, **kwargs):
            if "aggregates" in url:
                price_calls.append(params.get("station"))
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {}
            return r

        with patch.object(lp_web.SESSION, "get", side_effect=_get), \
             patch.object(lp_web, "resolve_corp_id", return_value=(1000001, "Test Corp")), \
             patch.object(lp_web, "get_offers", return_value=[]), \
             patch.object(lp_web, "resolve_names", return_value={}):
            result = lp_web.do_scan({"corp": ["Test Corp"], "station": ["9999999"]})

        assert result["station_id"] == lp_core.JITA_STATION_ID

    def test_valid_station_returned_in_response(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        amarr = 60008494

        with patch.object(lp_web, "resolve_corp_id", return_value=(1000001, "Test Corp")), \
             patch.object(lp_web, "get_offers", return_value=[]), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "resolve_names", return_value={}):
            result = lp_web.do_scan({"corp": ["Test Corp"], "station": [str(amarr)]})

        assert result["station_id"] == amarr
        assert result["station_name"] == "Amarr 8-20"


# ---------------------------------------------------------------------------
# HTML — market dropdown present with correct options
# ---------------------------------------------------------------------------

class TestHtmlMarketDropdown:
    def _get_html(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            return r.read()

    def test_market_select_present(self, tmp_server):
        body = self._get_html(tmp_server)
        assert b'id="market"' in body

    def test_jita_option_present_and_default(self, tmp_server):
        body = self._get_html(tmp_server)
        assert b"Jita 4-4" in body
        assert b'value="60003760"' in body

    def test_all_five_hubs_present(self, tmp_server):
        body = self._get_html(tmp_server)
        for sid in [60003760, 60008494, 60004588, 60011866, 60005686]:
            assert str(sid).encode() in body, f"station {sid} missing from HTML"

    def test_market_in_savels(self, tmp_server):
        """saveLS() must snapshot the market field so it persists across reloads."""
        body = self._get_html(tmp_server)
        assert b'market:$("#market").value' in body

    def test_market_in_loadsettings(self, tmp_server):
        """loadSettings() must restore the market field on page load."""
        body = self._get_html(tmp_server)
        assert b'if(s.market)' in body
