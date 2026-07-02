"""Tests for persisting and restoring last scan results."""
import json
import importlib
import sys
import time
from pathlib import Path
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


class TestLPScanPersistence:
    def test_save_and_load_lp_scan(self, tmp_path):
        """do_scan saves result to LP_LAST_SCAN_PATH."""
        lp_web = _import_lp_web()
        lp_web.LP_LAST_SCAN_PATH = tmp_path / "lp_last_scan.json"

        fake_result = {
            "corp_id": 123,
            "corp_name": "Test Corp",
            "rows": [{"offer_id": 1, "name": "Widget"}],
            "scanned_at": time.time(),
            "offers_fetched_at": time.time() - 3600,
        }
        lp_web._save_last_lp_scan(fake_result)

        loaded = lp_core.load_json(lp_web.LP_LAST_SCAN_PATH, None)
        assert loaded is not None
        assert loaded["corp_id"] == 123
        assert loaded["corp_name"] == "Test Corp"
        assert len(loaded["rows"]) == 1

    def test_save_and_load_ind_scan(self, tmp_path):
        """_save_last_ind_scan persists industry results."""
        lp_web = _import_lp_web()
        lp_web.IND_LAST_SCAN_PATH = tmp_path / "ind_last_scan.json"

        fake_result = {
            "station_id": 60003760,
            "station_name": "Jita",
            "market_group": "all",
            "runs": 1,
            "count": 5,
            "scanned_at": time.time(),
            "favorites_only": False,
            "owned_only": False,
            "rows": [{"blueprint_id": 1, "product_name": "Rifter"}],
        }
        lp_web._save_last_ind_scan(fake_result)

        loaded = lp_core.load_json(lp_web.IND_LAST_SCAN_PATH, None)
        assert loaded is not None
        assert loaded["station_name"] == "Jita"
        assert loaded["count"] == 5
        assert len(loaded["rows"]) == 1

    def test_missing_file_returns_none(self, tmp_path):
        """load_json returns None for missing cache files."""
        missing = tmp_path / "nonexistent.json"
        assert lp_core.load_json(missing, None) is None
