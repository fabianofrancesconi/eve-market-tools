"""Tests for persisting and restoring last scan results."""
import json
import time
from pathlib import Path
from io import BytesIO
from unittest.mock import patch, MagicMock

import lp_core


def _import_lp_web():
    """Import lp-web.py (has a hyphen in the name)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestScanPersistence:
    def test_save_and_load_lp_scan(self, tmp_path):
        """save_json persists LP scan data that load_json can retrieve."""
        path = tmp_path / "lp_last_scan.json"
        fake_result = {
            "corp_id": 123,
            "corp_name": "Test Corp",
            "rows": [{"offer_id": 1, "name": "Widget", "liq_loaded": True,
                      "daily_vol": 50, "days_to_clear": 2.5}],
            "scanned_at": time.time(),
            "offers_fetched_at": time.time() - 3600,
        }
        lp_core.save_json(path, fake_result)

        loaded = lp_core.load_json(path, None)
        assert loaded is not None
        assert loaded["corp_id"] == 123
        assert loaded["corp_name"] == "Test Corp"
        assert len(loaded["rows"]) == 1
        assert loaded["rows"][0]["liq_loaded"] is True

    def test_save_and_load_ind_scan(self, tmp_path):
        """save_json persists industry results with tradeability."""
        path = tmp_path / "ind_last_scan.json"
        fake_result = {
            "station_id": 60003760,
            "station_name": "Jita",
            "market_group": "all",
            "runs": 1,
            "count": 5,
            "scanned_at": time.time(),
            "favorites_only": False,
            "owned_only": False,
            "rows": [{"blueprint_id": 1, "product_name": "Rifter",
                      "liq_loaded": True, "daily_vol": 120, "tradeability": 75}],
        }
        lp_core.save_json(path, fake_result)

        loaded = lp_core.load_json(path, None)
        assert loaded is not None
        assert loaded["station_name"] == "Jita"
        assert loaded["rows"][0]["tradeability"] == 75
        assert loaded["rows"][0]["liq_loaded"] is True

    def test_missing_file_returns_none(self, tmp_path):
        """load_json returns None for missing cache files."""
        missing = tmp_path / "nonexistent.json"
        assert lp_core.load_json(missing, None) is None

    def test_post_save_scan_endpoint(self, tmp_path):
        """The /api/save-scan POST handler writes to the correct path."""
        lp_web = _import_lp_web()
        lp_web.LP_LAST_SCAN_PATH = tmp_path / "lp_last_scan.json"
        lp_web.IND_LAST_SCAN_PATH = tmp_path / "ind_last_scan.json"

        fake_data = {"corp_id": 99, "rows": [{"offer_id": 1}], "scanned_at": 12345}
        body = json.dumps({"tab": "lp", "blob": fake_data}).encode()

        handler = MagicMock()
        handler.path = "/api/save-scan"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = BytesIO()

        lp_web.Handler.do_POST(handler)

        loaded = lp_core.load_json(tmp_path / "lp_last_scan.json", None)
        assert loaded is not None
        assert loaded["corp_id"] == 99
