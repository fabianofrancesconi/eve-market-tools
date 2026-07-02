"""
Regression tests covering bugs found during development.

Run:  pytest tests/ -v

Bugs covered:
- /api/corps returning 500 → JS crash (.filter is not a function)
- ALL_CORPS assigned non-array value from error response
- /api/scan with no corp returning 500 instead of 400
- Unknown routes returning wrong status
- HTML response missing version string
"""
import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Load lp-web.py (hyphen in filename requires importlib)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)

from lp_core import LPError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def http_get(url):
    """GET url → (parsed_body, status_code). Handles 4xx/5xx without raising."""
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_server(tmp_path):
    """Real HTTP server with an isolated temp cache directory."""
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
# get_npc_corps() — graceful fallback
# ---------------------------------------------------------------------------

class TestGetNpcCorps:
    """Bug: ESI failure caused 500 with {error:...}; JS then crashed on .filter()."""

    def setup_method(self):
        lp_web.NPC_CORPS.clear()

    def test_returns_empty_list_on_connection_error(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError("ESI down")):
            result = lp_web.get_npc_corps()
        assert result == []

    def test_returns_empty_list_on_http_error(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.get_npc_corps()
        assert result == []

    def test_return_type_is_always_list(self, tmp_path):
        """JS relies on Array.isArray() — server must always respond with a list."""
        lp_web.CACHE_DIR = tmp_path
        with patch.object(lp_web.SESSION, "get", side_effect=RuntimeError("unexpected")):
            result = lp_web.get_npc_corps()
        assert isinstance(result, list)

    def test_loads_from_disk_cache_without_esi(self, tmp_path):
        """Cached npc_corps.json must be returned with no ESI call at all."""
        lp_web.CACHE_DIR = tmp_path
        expected = [{"id": 1000001, "name": "Caldari Navy"}]
        (tmp_path / "npc_corps.json").write_text(json.dumps(expected))

        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI when cached")):
            result = lp_web.get_npc_corps()

        assert result == expected

    def test_in_memory_cache_prevents_second_esi_call(self, tmp_path):
        """Once loaded, subsequent calls must not hit ESI again."""
        lp_web.CACHE_DIR = tmp_path
        corps = [{"id": 1, "name": "Test Corp"}]
        (tmp_path / "npc_corps.json").write_text(json.dumps(corps))

        lp_web.get_npc_corps()  # prime in-memory cache

        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI for second call")):
            result = lp_web.get_npc_corps()

        assert result == corps


# ---------------------------------------------------------------------------
# _resolve_corp_names() — binary-split around bad IDs
# ---------------------------------------------------------------------------

def _fake_esi_post(valid_ids, corp_ids):
    """Build a SESSION.post replacement mimicking ESI /universe/names/.

    Mirrors real ESI: the whole batch 404s if ANY id is not resolvable.
    Resolvable ids return category 'corporation' if in corp_ids, else 'character'.
    """
    valid_ids = set(valid_ids)
    corp_ids = set(corp_ids)

    def _post(url, json=None, **kwargs):
        ids = json
        resp = MagicMock()
        if all(i in valid_ids for i in ids):
            resp.status_code = 200
            resp.json.return_value = [
                {"id": i, "name": f"Corp {i}",
                 "category": "corporation" if i in corp_ids else "character"}
                for i in ids
            ]
        else:
            resp.status_code = 404
            resp.json.return_value = {"error": "Ensure all IDs are valid before resolving."}
        return resp

    return _post


class TestResolveCorpNames:
    """Bug: a single unresolvable id 404'd the whole batch, the code then
    iterated over the error dict's keys and crashed → /api/corps returned []."""

    def test_all_valid_returns_all_corps(self):
        ids = [1, 2, 3, 4]
        post = _fake_esi_post(valid_ids=ids, corp_ids=ids)
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 2, 3, 4}

    def test_one_bad_id_does_not_lose_the_batch(self):
        """The key regression: id 3 is unresolvable; 1,2,4 must still come back."""
        ids = [1, 2, 3, 4]
        post = _fake_esi_post(valid_ids={1, 2, 4}, corp_ids={1, 2, 4})
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 2, 4}

    def test_filters_non_corporation_categories(self):
        ids = [1, 2, 3]
        post = _fake_esi_post(valid_ids=ids, corp_ids={1, 3})  # 2 is a character
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names(ids)
        assert {c["id"] for c in result} == {1, 3}

    def test_error_dict_response_does_not_crash(self):
        """Single bad id returns a 404 dict — must be skipped, not crash."""
        post = _fake_esi_post(valid_ids=set(), corp_ids=set())
        with patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._resolve_corp_names([999])
        assert result == []

    def test_empty_input_returns_empty(self):
        with patch.object(lp_web.SESSION, "post",
                          side_effect=AssertionError("must not POST for empty input")):
            assert lp_web._resolve_corp_names([]) == []

    def test_load_npc_corps_recovers_around_bad_id(self):
        """End-to-end: /npccorps/ lists a bad id; loader still returns the rest."""
        lp_web.NPC_CORPS.clear()
        all_ids = [1000180, 1000181, 9999999]  # last one is dead
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = all_ids
        post = _fake_esi_post(valid_ids={1000180, 1000181}, corp_ids={1000180, 1000181})
        import tempfile
        cache = Path(tempfile.mkdtemp())
        lp_web.CACHE_DIR = cache
        with patch.object(lp_web.SESSION, "get", return_value=get_resp), \
             patch.object(lp_web.SESSION, "post", side_effect=post):
            result = lp_web._load_npc_corps()
        assert {c["id"] for c in result} == {1000180, 1000181}
        # and the recovered list was cached to disk
        assert (cache / "npc_corps.json").exists()


# ---------------------------------------------------------------------------
# /api/corps HTTP endpoint
# ---------------------------------------------------------------------------

class TestApiCorpsEndpoint:
    """The endpoint must always return HTTP 200 with a JSON array."""

    def test_returns_200_when_esi_is_down(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert isinstance(data, list)

    def test_returns_cached_corps(self, tmp_server):
        base, cache = tmp_server
        corps = [{"id": 1000180, "name": "State Protectorate"}]
        (cache / "npc_corps.json").write_text(json.dumps(corps))
        data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert data == corps

    def test_response_is_always_a_json_array(self, tmp_server):
        """Protects against the .filter-is-not-a-function bug on the client."""
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            data, status = http_get(f"{base}/api/corps")
        assert status == 200
        assert isinstance(data, list)

    def test_content_type_is_json(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web.SESSION, "get", side_effect=ConnectionError):
            with urllib.request.urlopen(f"{base}/api/corps") as r:
                ct = r.headers.get("Content-Type")
        assert "application/json" in ct


# ---------------------------------------------------------------------------
# /api/scan endpoint
# ---------------------------------------------------------------------------

class TestApiScanEndpoint:
    def test_missing_corp_returns_400(self, tmp_server):
        base, _ = tmp_server
        data, status = http_get(f"{base}/api/scan")
        assert status == 400
        assert "error" in data

    def test_empty_corp_returns_400(self, tmp_server):
        base, _ = tmp_server
        data, status = http_get(f"{base}/api/scan?corp=&corp_id=")
        assert status == 400
        assert "error" in data

    def test_do_scan_raises_lperror_with_no_corp(self):
        with pytest.raises(LPError):
            lp_web.do_scan({})

    def test_do_scan_raises_lperror_with_blank_corp(self):
        with pytest.raises(LPError):
            lp_web.do_scan({"corp": [""], "corp_id": [""]})

    def test_do_scan_includes_output_volume_in_rows(self, tmp_path):
        """output_volume = packaged m³/unit × qty is present in every scan row."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40, "isk_per_lp_best": 0.45,
            "max_units": 10,
            "total_profit_patient": 4500.0, "total_profit_instant": 4000.0,
            "total_profit_best": 4500.0, "buy_volume": 1000,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings"), \
             patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 10.0}):
            result = lp_web.do_scan(q)
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert "output_volume" in row
        assert row["output_volume"] == 50.0  # 10.0 m³/unit × 5 qty

    def test_do_scan_output_volume_none_when_unavailable(self, tmp_path):
        """output_volume is None when the ESI volume lookup fails."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 202, "quantity": 1, "lp_cost": 500}]
        fake_sellable = [{
            "offer_id": 2, "name_id": 202, "qty": 1, "lp_cost": 500,
            "isk_cost": 0, "req_cost": 0, "ask": 50.0, "bid": 45.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.09, "isk_per_lp_instant": 0.08, "isk_per_lp_best": 0.09,
            "max_units": 20,
            "total_profit_patient": 900.0, "total_profit_instant": 800.0,
            "total_profit_best": 900.0, "buy_volume": 500,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["2000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings"), \
             patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={202: "Other Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={202: None}):
            result = lp_web.do_scan(q)
        assert result["rows"][0]["output_volume"] is None

    def test_do_scan_rows_carry_liquidity_placeholders(self, tmp_path):
        """Scan rows expose type_id/sell_volume + null saturation fields so the
        background /api/liquidity fill can patch them in place."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40, "isk_per_lp_best": 0.45,
            "max_units": 10,
            "total_profit_patient": 4500.0, "total_profit_instant": 4000.0,
            "total_profit_best": 4500.0, "buy_volume": 1000,
            "sell_volume": 8000, "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "station": ["60003760"]}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings"), \
             patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 10.0}):
            row = lp_web.do_scan(q)["rows"][0]
        assert row["type_id"] == 101
        assert row["sell_volume"] == 8000
        assert row["daily_vol"] is None
        assert row["days_to_clear"] is None
        assert row["tradeability"] is None
        assert row["liq_loaded"] is False


# ---------------------------------------------------------------------------
# /api/liquidity endpoint (market-saturation background fill)
# ---------------------------------------------------------------------------

class TestDoLiquidity:
    def test_returns_saturation_keyed_by_offer(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 5, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 5, "max_units": 10,
            "profit_per": 450.0, "sell_volume": 1000, "ask": 100.0,
        }]
        q = {"corp_id": ["1000"], "lp": ["10000"], "tax": ["0.08"],
             "broker": ["0.03"], "instant": ["0"], "station": ["60003760"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_volumes", return_value={101: 200}), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: 120.0}), \
             patch.object(lp_web, "fetch_sell_order_stats",
                          return_value={"age_seconds": 28800.0}):
            result = lp_web.do_liquidity(q)
        assert "liquidity" in result
        entry = result["liquidity"][1]
        assert entry["daily_vol"] == 200
        assert entry["days_to_clear"] == 5.0
        # ask (100) below fair value (120) -> hold the list price at fair value
        assert entry["list_price"] == 120.0
        assert entry["floor_age"] == 28800.0  # 8h, from the order's issued stamp
        assert set(entry) == {"daily_vol", "days_to_clear", "list_price", "floor_age"}

    def test_history_fetched_for_hub_region(self, tmp_path):
        """Amarr station → daily volume pulled from the Domain region, not Forge."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [{"offer_id": 1, "name_id": 101, "qty": 1,
                          "max_units": 1, "profit_per": 1.0, "sell_volume": 0,
                          "ask": 100.0}]
        q = {"corp_id": ["1000"], "lp": ["1000"], "station": ["60008494"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: None}), \
             patch.object(lp_web, "fetch_sell_order_stats", return_value=None), \
             patch.object(lp_web, "fetch_history_volumes",
                          return_value={101: 5}) as m:
            lp_web.do_liquidity(q)
        assert m.call_args[0][1] == 10000043  # Domain region id

    def test_floor_age_deduped_per_reward_type(self, tmp_path):
        """Two offers rewarding the same type share one order-book call."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [
            {"offer_id": 1, "name_id": 101, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
            {"offer_id": 2, "name_id": 101, "qty": 1, "max_units": 1,
             "sell_volume": 0, "ask": 100.0},
        ]
        q = {"corp_id": ["1000"], "lp": ["1000"], "station": ["60003760"]}
        with patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "fetch_history_prices", return_value={101: None}), \
             patch.object(lp_web, "fetch_history_volumes", return_value={101: 5}), \
             patch.object(lp_web, "fetch_sell_order_stats",
                          return_value={"age_seconds": 3600.0}) as m:
            result = lp_web.do_liquidity(q)
        assert m.call_count == 1  # deduped: one call for the shared type 101
        assert result["liquidity"][1]["floor_age"] == 3600.0
        assert result["liquidity"][2]["floor_age"] == 3600.0


# ---------------------------------------------------------------------------
# SESSION retry on stale pooled connections
# ---------------------------------------------------------------------------

class TestSessionRetry:
    """Bug: leaving the page open for a while left SESSION holding a pooled
    keep-alive connection that ESI/Fuzzwork had since closed server-side. The
    next reused connection raised ConnectionError('RemoteDisconnected'),
    uncaught, surfacing a raw 500 in the UI. A mounted Retry adapter should
    retry transparently on a fresh connection instead."""

    def test_https_and_http_adapters_have_retry_mounted(self):
        for scheme in ("https://", "http://"):
            adapter = lp_web.SESSION.get_adapter(scheme + "example.com")
            assert adapter.max_retries.total >= 1
            assert adapter.max_retries.connect >= 1


# ---------------------------------------------------------------------------
# /api/settings endpoint
# ---------------------------------------------------------------------------

class TestApiSettingsEndpoint:
    def test_returns_200(self, tmp_server):
        base, _ = tmp_server
        data, status = http_get(f"{base}/api/settings")
        assert status == 200

    def test_returns_dict(self, tmp_server):
        base, _ = tmp_server
        data, _ = http_get(f"{base}/api/settings")
        assert isinstance(data, dict)

    def test_arb_key_present(self, tmp_server):
        """Merged settings must always include the arb sub-object."""
        base, _ = tmp_server
        data, _ = http_get(f"{base}/api/settings")
        assert "arb" in data

    def test_not_logged_in_reports_unsynced(self, tmp_server, monkeypatch):
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "_AUTH", {})
        data, _ = http_get(f"{base}/api/settings")
        assert data["_server_synced"] is False
        assert data["_logged_in"] is False

    def test_logged_in_without_prior_sync_falls_back_to_files(self, tmp_server, monkeypatch, tmp_path):
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        monkeypatch.setattr(lp_web, "_AUTH", {"character_id": 42})
        data, _ = http_get(f"{base}/api/settings")
        assert data["_server_synced"] is False
        assert data["_logged_in"] is True

    def test_logged_in_with_prior_sync_returns_synced_blob(self, tmp_server, monkeypatch, tmp_path):
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        monkeypatch.setattr(lp_web, "_AUTH", {"character_id": 42})
        lp_web.save_user_settings(42, {"active_tab": "ind"})
        data, _ = http_get(f"{base}/api/settings")
        assert data["_server_synced"] is True
        assert data["active_tab"] == "ind"


# ---------------------------------------------------------------------------
# Per-character settings sync (SQLite)
# ---------------------------------------------------------------------------

class TestUserSettingsSync:
    def test_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        lp_web.save_user_settings(7, {"col_order": ["name", "ask"]})
        assert lp_web.load_user_settings(7) == {"col_order": ["name", "ask"]}

    def test_missing_character_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        assert lp_web.load_user_settings(999) is None

    def test_save_overwrites_previous_blob(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        lp_web.save_user_settings(7, {"active_tab": "lp"})
        lp_web.save_user_settings(7, {"active_tab": "arb"})
        assert lp_web.load_user_settings(7) == {"active_tab": "arb"}

    def test_settings_are_isolated_per_character(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        lp_web.save_user_settings(1, {"active_tab": "lp"})
        lp_web.save_user_settings(2, {"active_tab": "ind"})
        assert lp_web.load_user_settings(1) == {"active_tab": "lp"}
        assert lp_web.load_user_settings(2) == {"active_tab": "ind"}

    def test_sync_without_login_is_a_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        monkeypatch.setattr(lp_web, "_AUTH", {})
        result = lp_web.do_settings_sync({"blob": ['{"active_tab":"lp"}']})
        assert result == {"ok": True, "synced": False}
        assert not (tmp_path / "user_settings.sqlite").exists()

    def test_sync_while_logged_in_persists_blob(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        monkeypatch.setattr(lp_web, "_AUTH", {"character_id": 42})
        result = lp_web.do_settings_sync({"blob": ['{"active_tab":"ind","col_widths":{"name":120}}']})
        assert result == {"ok": True, "synced": True}
        assert lp_web.load_user_settings(42) == {"active_tab": "ind", "col_widths": {"name": 120}}

    def test_sync_rejects_invalid_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "USER_SETTINGS_DB_PATH", tmp_path / "user_settings.sqlite")
        monkeypatch.setattr(lp_web, "_AUTH", {"character_id": 42})
        with pytest.raises(LPError):
            lp_web.do_settings_sync({"blob": ["not json"]})


# ---------------------------------------------------------------------------
# HTTP routing
# ---------------------------------------------------------------------------

class TestHttpRouting:
    def test_root_returns_200_html(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type")

    def test_root_contains_app_title(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            body = r.read()
        # Logo is split across elements: "EVE <span>MARKET TOOLS</span>"
        assert b"MARKET TOOLS" in body
        assert b"EVE Market Tools" in body  # <title> tag

    def test_root_contains_version(self, tmp_server):
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}/") as r:
            body = r.read()
        assert lp_web.__version__.encode() in body

    def test_unknown_path_returns_404(self, tmp_server):
        base, _ = tmp_server
        _, status = http_get(f"{base}/api/doesnotexist")
        assert status == 404

    def test_unknown_path_returns_json_error(self, tmp_server):
        base, _ = tmp_server
        data, _ = http_get(f"{base}/api/doesnotexist")
        assert "error" in data

    @pytest.mark.parametrize("path", sorted(lp_web.TAB_ROUTES))
    def test_tab_url_serves_app_shell(self, tmp_server, path):
        # Deep-linking / refreshing on a tab URL must serve the SPA shell so the
        # client can render that module — not 404.
        base, _ = tmp_server
        with urllib.request.urlopen(f"{base}{path}") as r:
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type")
            assert lp_web.__version__.encode() in r.read()

    def test_tab_routes_cover_every_tab(self):
        # The clean URL map in the front-end must have a matching server route
        # for each non-root tab, or a refresh there would 404.
        for path in ("/arbitrage", "/industry", "/character"):
            assert path in lp_web.TAB_ROUTES
        # Front-end path<->tab maps are present and consistent.
        assert 'const TAB_PATH = {' in lp_web.INDEX_HTML
        assert 'const PATH_TAB = {' in lp_web.INDEX_HTML
        assert 'history.pushState' in lp_web.INDEX_HTML
        assert '"popstate"' in lp_web.INDEX_HTML


# ---------------------------------------------------------------------------
# Custom tooltip system (data-tip + tooltip engine replaced native title=)
# ---------------------------------------------------------------------------

class TestTooltips:
    def test_tooltip_engine_present(self):
        # The themed tooltip element + mousemove engine must be wired up.
        assert 'id="tooltip"' in lp_web.INDEX_HTML
        assert "#tooltip.show" in lp_web.INDEX_HTML
        assert "[data-tip]" in lp_web.INDEX_HTML

    def test_uses_data_tip_not_native_title(self):
        # Column headers and controls now use data-tip, not title=.
        assert "data-tip=" in lp_web.INDEX_HTML
        assert 'c.tip?` data-tip=' in lp_web.INDEX_HTML

    def test_no_stale_native_title_on_controls(self):
        # The refresh/columns controls must not fall back to native title=.
        assert 'title="Re-fetch' not in lp_web.INDEX_HTML
        assert 'title="Choose visible columns"' not in lp_web.INDEX_HTML

    def test_sidebar_kpi_cards(self):
        # The detail-panel KPI grid lays out 3 per row and shows BOTH sell-mode
        # profits side by side instead of a single "Total profit" card.
        assert "repeat(3,1fr)" in lp_web.INDEX_HTML
        assert '<div class="l">List profit</div>' in lp_web.INDEX_HTML
        assert '<div class="l">Instant-sell profit</div>' in lp_web.INDEX_HTML
        # The old single-mode card is gone.
        assert '<div class="l">Total profit</div>' not in lp_web.INDEX_HTML
        # Revenue is covered by the profit-breakdown comparison, not a KPI card.
        assert '<div class="l">Revenue</div>' not in lp_web.INDEX_HTML
        # Item cost and redemption ISK are combined into one card; the separate
        # "Item cost" / "Redemption ISK" cards are gone (they live in the cost
        # breakdown table). A suggested-list-price card takes the freed slot.
        assert '<div class="l">Item + ISK cost</div>' in lp_web.INDEX_HTML
        assert '<div class="l">Suggested list / unit</div>' in lp_web.INDEX_HTML
        assert '<div class="l">Item cost</div>' not in lp_web.INDEX_HTML
        assert '<div class="l">Redemption ISK</div>' not in lp_web.INDEX_HTML
        # The store ISK charge is still labelled "Redemption ISK" (cost
        # breakdown / recipe), never the deprecated "ISK fee".
        assert "Redemption ISK" in lp_web.INDEX_HTML
        assert "ISK fee" not in lp_web.INDEX_HTML

    def test_profit_breakdown_waterfall(self):
        # The Sale section is a profit waterfall: gross sell value, the fee
        # deductions, net revenue subtotal, and the final profit line.
        html = lp_web.INDEX_HTML
        assert "Profit breakdown" in html
        assert "Sell value (walking buy orders)" in html
        assert "Sell value (listed at ask)" in html
        assert "− Sales tax" in html
        assert "− Broker fee" in html
        assert "Net revenue" in html
        assert "− Items cost" in html
        assert "− Redemption ISK" in html
        # The deprecated "Store ISK Fee"/"Store ISK fee" labels are gone.
        assert "Store ISK" not in html

    def test_detail_panel_uses_selected_hub_not_hardcoded_jita(self):
        # The detail panel must label prices with the chosen hub, not a
        # hardcoded "Jita" — the market is user-selectable.
        html = lp_web.INDEX_HTML
        assert "Jita ask / bid" not in html
        assert "Costs use the live ${hub} order book." in html
        assert "Reward (${fmtNum(d.output.quantity*n)}× ${d.output.name}) → ${hub}" in html

    def test_chart_stat_chips_have_labels_and_tooltips(self):
        # The Current / ATH / vs 30d MA chips use labelled k/v markup and
        # carry data-tip tooltips.
        assert '<span class="k">Current</span>' in lp_web.INDEX_HTML
        assert '<span class="k">ATH</span>' in lp_web.INDEX_HTML
        assert '<span class="k">vs 30d MA</span>' in lp_web.INDEX_HTML
        assert "All-time high daily average" in lp_web.INDEX_HTML
        assert "30-day moving average" in lp_web.INDEX_HTML
        assert ".chart-stats .k" in lp_web.INDEX_HTML


# ---------------------------------------------------------------------------
# Dual-mode redesign (v1.11.0): the Sell-mode toggle is gone; patient and
# instant figures are shown side by side everywhere.
# ---------------------------------------------------------------------------

class TestDualModeComparison:
    def test_sell_mode_dropdown_removed(self):
        # The single-mode <select id="instant"> control is gone.
        assert 'id="instant"' not in lp_web.INDEX_HTML
        assert ">Sell mode<" not in lp_web.INDEX_HTML

    def test_paired_isk_per_lp_columns(self):
        # The table exposes both sell-mode ISK/LP columns.
        assert '{k:"isk_per_lp_patient"' in lp_web.INDEX_HTML
        assert '{k:"isk_per_lp_instant"' in lp_web.INDEX_HTML
        # ...and the old single column is gone.
        assert '{k:"isk_per_lp",' not in lp_web.INDEX_HTML

    def test_paired_total_profit_columns(self):
        assert '{k:"total_profit_patient"' in lp_web.INDEX_HTML
        assert '{k:"total_profit_instant"' in lp_web.INDEX_HTML
        assert '{k:"total_profit",' not in lp_web.INDEX_HTML

    def test_mode_labels_are_list_and_instant_sell(self):
        html = lp_web.INDEX_HTML
        # Column headers and KPI cards use "List" / "Instant-sell" wording.
        assert 't:"List ISK/LP"' in html
        assert 't:"Instant-sell ISK/LP"' in html
        assert 't:"List profit"' in html
        assert 't:"Instant-sell profit"' in html
        # The earlier "· sell" / "· buy" shorthand is gone.
        assert "ISK/LP · sell" not in html
        assert "Profit · buy" not in html

    def test_default_sort_is_best_of_two(self):
        assert 'sort:{key:"isk_per_lp_best", dir:-1}' in lp_web.INDEX_HTML

    def test_winning_mode_highlight_styled(self):
        # The better of the two sell-mode cells gets a .win highlight.
        assert "td.win" in lp_web.INDEX_HTML
        assert 'cls+=" win"' in lp_web.INDEX_HTML

    def test_scan_response_carries_both_modes(self, tmp_path):
        """do_scan rows expose patient/instant/best ISK-per-LP and total profit."""
        lp_web.CACHE_DIR = tmp_path
        fake_offers = [{"type_id": 101, "quantity": 1, "lp_cost": 1000}]
        fake_sellable = [{
            "offer_id": 1, "name_id": 101, "qty": 1, "lp_cost": 1000,
            "isk_cost": 0, "req_cost": 0, "ask": 100.0, "bid": 90.0,
            "spread_pct": 10.0,
            "isk_per_lp_patient": 0.45, "isk_per_lp_instant": 0.40,
            "isk_per_lp_best": 0.45, "max_units": 5,
            "total_profit_patient": 2250.0, "total_profit_instant": 2000.0,
            "total_profit_best": 2250.0, "buy_volume": 1000,
            "req_missing": False, "ak_cost": 0,
        }]
        q = {"corp_id": ["1000"], "lp": ["5000"], "tax": ["0.045"],
             "broker": ["0.015"], "station": ["60003760"]}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings"), \
             patch.object(lp_web, "resolve_corp_name", return_value="Test Corp"), \
             patch.object(lp_web, "get_offers", return_value=fake_offers), \
             patch.object(lp_web, "load_json", return_value={}), \
             patch.object(lp_web, "fetch_prices", return_value={}), \
             patch.object(lp_web, "evaluate", return_value=(fake_sellable, [])), \
             patch.object(lp_web, "resolve_names", return_value={101: "Test Item"}), \
             patch.object(lp_web, "resolve_volumes", return_value={101: 1.0}):
            result = lp_web.do_scan(q)
        row = result["rows"][0]
        assert row["isk_per_lp_patient"] == 0.45
        assert row["isk_per_lp_instant"] == 0.40
        assert row["isk_per_lp_best"] == 0.45
        assert row["total_profit_patient"] == 2250.0
        assert row["total_profit_instant"] == 2000.0
        assert row["total_profit_best"] == 2250.0
        # The deprecated single-mode flag is no longer in the response.
        assert "instant" not in result


# ---------------------------------------------------------------------------
# Drag-to-reorder columns (v1.12.0): headers are draggable and the chosen
# order is persisted via /api/prefs alongside the column widths.
# ---------------------------------------------------------------------------

class TestColumnReorder:
    def test_do_prefs_persists_col_order(self):
        # col_order must be on the do_prefs whitelist so it survives a reload.
        saved = {}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings", side_effect=lambda d: saved.update(d)):
            lp_web.do_prefs({"col_order": ['["name","ask","bid"]'],
                             "col_layout_v": ["6"]})
        assert saved["col_order"] == '["name","ask","bid"]'
        assert saved["col_layout_v"] == "6"

    def test_do_prefs_ignores_unknown_keys(self):
        # The whitelist must not let arbitrary keys into settings.
        saved = {}
        with patch.object(lp_web, "load_settings", return_value={}), \
             patch.object(lp_web, "save_settings", side_effect=lambda d: saved.update(d)):
            lp_web.do_prefs({"col_order": ['["name"]'], "evil": ["1"]})
        assert "col_order" in saved
        assert "evil" not in saved

    def test_headers_are_draggable(self):
        # Each <th> opts into HTML5 drag-and-drop.
        assert '<th draggable="true" data-k="${c.k}"' in lp_web.INDEX_HTML

    def test_reorder_wiring_present(self):
        html = lp_web.INDEX_HTML
        # The drag helpers and per-header wiring must be hooked up.
        assert "function wireLPColDrag(" in html
        assert "function reorderLPCols(" in html
        assert "function orderedCols(" in html
        assert "wireLPColDrag(th);" in html
        # visCols now derives from the user order, not raw COLS.
        assert "function visCols(){ return orderedCols()" in html


# ---------------------------------------------------------------------------
# Column formatters that read the row object must declare rowCtx:true
# (v1.14.0 regression: the "List @" column used fmtListPrice — which reads
#  r.liq_loaded — without rowCtx, so the render loop passed `undefined` as the
#  row and threw "Cannot read properties of undefined (reading 'liq_loaded')").
#
# The render loop calls `c.rowCtx ? c.f(v,r) : c.f(v)`, so any formatter that
# touches the row argument MUST be on a column flagged rowCtx:true.
# ---------------------------------------------------------------------------

class TestColumnFormatterRowContext:
    def _cols_block(self):
        html = lp_web.INDEX_HTML
        start = html.index("const COLS = [")
        end = html.index("];", start)
        return html[start:end]

    def _col_lines(self):
        return [ln for ln in self._cols_block().splitlines() if "{k:" in ln]

    def _row_ctx_named_formatters(self):
        """Named formatters declared `function fmtX(v, r)` read the row arg."""
        return set(re.findall(r"function\s+(fmt\w+)\s*\(\s*v\s*,\s*r\s*\)",
                              lp_web.INDEX_HTML))

    def test_heuristics_find_the_expected_formatters(self):
        # Guard against the regexes silently matching nothing.
        assert len(self._col_lines()) >= 12
        assert {"fmtListPrice", "fmtVolPerDay", "fmtDays", "fmtTrade"} \
            <= self._row_ctx_named_formatters()

    def test_list_price_column_has_rowctx(self):
        line = next(ln for ln in self._col_lines() if '{k:"list_price"' in ln)
        assert "f:fmtListPrice" in line
        assert "rowCtx:true" in line

    def test_every_row_reading_column_declares_rowctx(self):
        row_named = self._row_ctx_named_formatters()
        offenders = []
        for ln in self._col_lines():
            key = re.search(r'\{k:"([^"]+)"', ln).group(1)
            inline_row = re.search(r"f:\s*\(\s*v\s*,\s*r\s*\)\s*=>", ln) is not None
            named_row = any(re.search(r"f:\s*" + re.escape(n) + r"\b", ln)
                            for n in row_named)
            if (inline_row or named_row) and "rowCtx:true" not in ln:
                offenders.append(key)
        assert not offenders, \
            f"columns read the row arg but lack rowCtx:true: {offenders}"

    def test_col_order_persisted_and_restored(self):
        html = lp_web.INDEX_HTML
        # Saved with the widths under the same layout version...
        assert "col_order=${encodeURIComponent(JSON.stringify(STATE.colOrder))}" in html
        # ...and restored on load, guarded by the layout version.
        assert "if(s.col_order && s.col_layout_v==COL_LAYOUT_VERSION){" in html

    def test_drag_does_not_trigger_sort(self):
        # A header click at the tail of a drag must not re-sort.
        assert "if(LP_DRAG_KEY){ return; }" in lp_web.INDEX_HTML


# ---------------------------------------------------------------------------
# /api/history endpoint
# ---------------------------------------------------------------------------

_FAKE_HISTORY = [
    {"date": "2024-01-01", "average": 100.0, "highest": 110.0, "lowest": 90.0,
     "order_count": 5, "volume": 200},
    {"date": "2024-01-03", "average": 105.0, "highest": 115.0, "lowest": 95.0,
     "order_count": 6, "volume": 300},
    {"date": "2024-01-02", "average": 102.0, "highest": 112.0, "lowest": 92.0,
     "order_count": 4, "volume": 150},
]


class TestDoHistory:
    def test_fetches_from_esi_and_returns_sorted(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert "history" in result
        dates = [d["date"] for d in result["history"]]
        assert dates == sorted(dates)

    def test_uses_disk_cache(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        import time as _t
        cache_data = {"_ts": _t.time(), "data": _FAKE_HISTORY[:2]}
        import json as _j
        (tmp_path / "mhist_10000002_34.json").write_text(_j.dumps(cache_data))
        with patch.object(lp_web.SESSION, "get",
                          side_effect=AssertionError("must not call ESI when cached")):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert len(result["history"]) == 2

    def test_refetches_when_cache_stale(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        import json as _j
        stale = {"_ts": 0.0, "data": []}
        (tmp_path / "mhist_10000002_34.json").write_text(_j.dumps(stale))
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            result = lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert len(result["history"]) == 3

    def test_defaults_to_forge_region(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp) as m:
            lp_web.do_history({"type_id": ["34"]})
        url = m.call_args[0][0]
        assert "10000002" in url

    def test_writes_cache_file(self, tmp_path):
        lp_web.CACHE_DIR = tmp_path
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            lp_web.do_history({"type_id": ["34"], "region_id": ["10000002"]})
        assert (tmp_path / "mhist_10000002_34.json").exists()


class TestApiHistoryEndpoint:
    def test_returns_200_with_history_key(self, tmp_server):
        base, _ = tmp_server
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            data, status = http_get(f"{base}/api/history?type_id=34&region_id=10000002")
        assert status == 200
        assert "history" in data
        assert isinstance(data["history"], list)

    def test_missing_type_id_returns_500(self, tmp_server):
        base, _ = tmp_server
        _, status = http_get(f"{base}/api/history")
        assert status == 500

    def test_history_sorted_by_date(self, tmp_server):
        base, _ = tmp_server
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _FAKE_HISTORY
        with patch.object(lp_web.SESSION, "get", return_value=mock_resp):
            data, _ = http_get(f"{base}/api/history?type_id=34&region_id=10000002")
        dates = [d["date"] for d in data["history"]]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Industry module routing (v1.19.0) — endpoints exist, isolate, and 404 right.
# The scan/detail data path is covered by ind_core unit tests; here we guard the
# web wiring (settings merge, prefs persistence, unknown-subpath status).
# ---------------------------------------------------------------------------

class TestIndustryLoginRequired:
    """The Industry planner has no manual ME/TE/skill inputs — it only means
    anything with a real character's owned blueprints and trained skills, so
    login is mandatory rather than an optional fallback."""

    def test_scan_without_login_raises(self, monkeypatch):
        monkeypatch.setattr(lp_web, "_AUTH", {})
        with pytest.raises(LPError):
            lp_web.do_ind_scan({})

    def test_detail_without_login_raises(self, monkeypatch):
        monkeypatch.setattr(lp_web, "_AUTH", {})
        with pytest.raises(LPError):
            lp_web.do_ind_detail({"blueprint_id": ["681"]})


class TestIndustryRoutes:
    def test_settings_includes_ind_key(self, tmp_server):
        base, _ = tmp_server
        body, status = http_get(f"{base}/api/settings")
        assert status == 200
        assert "ind" in body  # JS reads s.ind; missing key would break loadSettings

    def test_ind_prefs_roundtrip(self, tmp_server, tmp_path, monkeypatch):
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "IND_SETTINGS_PATH", tmp_path / "ind.json")
        body, status = http_get(
            f"{base}/api/ind/prefs?job_rate=6&profile=2&hide_t2=1")
        assert status == 200 and body["ok"] is True
        saved = json.loads((tmp_path / "ind.json").read_text())
        assert saved["job_rate"] == "6"
        assert saved["hide_t2"] == "1"

    def test_ind_prefs_me_te_skills_not_persisted(self, tmp_server, tmp_path, monkeypatch):
        # ME/TE/skills level are no longer user-settable — they're always the
        # real (0 = unresearched/untrained) baseline, overridden per-blueprint
        # with the logged-in character's own data. Nothing to persist.
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "IND_SETTINGS_PATH", tmp_path / "ind.json")
        body, status = http_get(f"{base}/api/ind/prefs?me=10&te=20&skills_level=5")
        assert status == 200 and body["ok"] is True
        saved = json.loads((tmp_path / "ind.json").read_text())
        assert "me" not in saved and "te" not in saved and "skills_level" not in saved

    def test_ind_prefs_persists_col_order(self, tmp_server, tmp_path, monkeypatch):
        # The industry column order must survive a reload, like the LP store.
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "IND_SETTINGS_PATH", tmp_path / "ind.json")
        order = '["_fav","product_name","_timer","tech_level"]'
        body, status = http_get(
            f"{base}/api/ind/prefs?col_order={urllib.parse.quote(order)}")
        assert status == 200 and body["ok"] is True
        saved = json.loads((tmp_path / "ind.json").read_text())
        assert saved["col_order"] == order

    def test_ind_prefs_persists_col_widths_and_vis(self, tmp_server, tmp_path, monkeypatch):
        base, _ = tmp_server
        monkeypatch.setattr(lp_web, "IND_SETTINGS_PATH", tmp_path / "ind.json")
        widths = '{"product_name":260}'
        vis = '{"ask":false}'
        body, status = http_get(
            f"{base}/api/ind/prefs?col_widths={urllib.parse.quote(widths)}"
            f"&col_vis={urllib.parse.quote(vis)}")
        assert status == 200 and body["ok"] is True
        saved = json.loads((tmp_path / "ind.json").read_text())
        assert saved["col_widths"] == widths
        assert saved["col_vis"] == vis

    def test_ind_columns_reorderable(self):
        html = lp_web.INDEX_HTML
        # Headers are draggable and the order is resolved through indOrderedCols().
        assert "function indOrderedCols(" in html
        assert "function reorderIndCols(" in html
        # Rendering is scoped to visible columns, which are built on the order.
        assert "function indVisCols(){ return indOrderedCols()" in html
        assert "thead.innerHTML=\"<tr>\"+vc.map" in html
        # Order is saved and restored.
        assert "col_order: JSON.stringify(IND.colOrder)" in html
        assert "if(ind.col_order){ try{" in html

    def test_ind_columns_resizable_and_toggleable(self):
        html = lp_web.INDEX_HTML
        # Resize: colgroup + resizer handle + drag wiring, mirroring the LP store.
        assert '<table id="ind-tbl"><colgroup id="ind-cg">' in html
        assert "function startIndResize(" in html
        assert '<span class="resizer"></span>' in html
        # Visibility: a Columns picker toggles IND.colVis and re-renders.
        assert 'id="indColPickerBtn"' in html
        assert 'id="indColPicker"' in html
        assert "IND.colVis[cb.dataset.k]=cb.checked" in html
        # Widths/visibility ride along with the rest of the industry prefs.
        assert "col_widths: JSON.stringify(IND.colw)" in html
        assert "col_vis: JSON.stringify(IND.colVis)" in html

    def test_ind_search_has_clear_button(self):
        html = lp_web.INDEX_HTML
        assert 'id="ind-search-clear" class="search-clear hidden"' in html
        assert "function updateIndSearchClear(){" in html
        assert '$("#ind-search-clear").addEventListener("click"' in html

    def test_unknown_ind_subpath_404(self, tmp_server):
        base, _ = tmp_server
        body, status = http_get(f"{base}/api/ind/bogus")
        assert status == 404


class TestIndustryTradeabilityFill:
    """v1.36.0: the scan scores only the top rows inline; a background fill
    (/api/ind/liquidity) scores every other item in chunks, with a spinner."""

    def test_liquidity_scores_each_type(self, tmp_server):
        base, _ = tmp_server
        fake = {34: 1000.0, 35: None, 36: 0.0}  # traded / never traded / zero vol
        with patch.object(lp_web, "fetch_history_volumes", return_value=fake):
            data, status = http_get(f"{base}/api/ind/liquidity?type_ids=34,35,36")
        assert status == 200
        liq = data["liquidity"]
        assert liq["34"]["daily_vol"] == 1000.0
        assert liq["34"]["tradeability"] > 0
        # Never traded -> no daily volume and no score (renders "—", not a 0).
        assert liq["35"]["daily_vol"] is None
        assert liq["35"]["tradeability"] is None
        # Traded but zero recent volume -> a real, lowest score.
        assert liq["36"]["tradeability"] == 0

    def test_liquidity_parses_only_integer_ids(self, tmp_server):
        base, _ = tmp_server
        seen = {}

        def fake(ids, region, sess, cache, **k):
            seen["ids"] = set(ids)
            return {}

        with patch.object(lp_web, "fetch_history_volumes", side_effect=fake):
            http_get(f"{base}/api/ind/liquidity?type_ids=34,abc,,36")
        assert seen["ids"] == {34, 36}

    def test_liquidity_empty_when_no_ids(self, tmp_server):
        base, _ = tmp_server
        with patch.object(lp_web, "fetch_history_volumes", return_value={}):
            data, status = http_get(f"{base}/api/ind/liquidity?type_ids=")
        assert status == 200
        assert data["liquidity"] == {}

    def test_scan_marks_scored_rows_loaded(self):
        # The scan must flag the rows it scored inline so the client only spins +
        # backfills the unscored remainder.
        src = Path(lp_web.__file__).read_text(encoding="utf-8")
        assert 'r["liq_loaded"] = True' in src

    def test_frontend_background_fill_wired(self):
        html = lp_web.INDEX_HTML
        assert "function fillIndTradeability(" in html
        assert "/api/ind/liquidity?" in html
        assert "fillIndTradeability();" in html        # kicked off after a scan
        assert "IND_FILL_TOKEN" in html                # stale-fill cancellation
        # Pending rows spin in both market-depth columns until their score lands.
        assert "!r.liq_loaded ? _SPIN" in html


# ---------------------------------------------------------------------------
# Delivered-runs counter (character tab) — cumulative, persisted, baseline on
# first sight so history before this feature existed is never counted.
# ---------------------------------------------------------------------------

class TestDeliveredRunsTracker:
    def _job(self, job_id, status="delivered", runs=10, product_type_id=165):
        return {"job_id": job_id, "status": status, "runs": runs,
                "product_type_id": product_type_id}

    def test_first_sight_is_baseline_not_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        jobs = [self._job(1), self._job(2, status="active", runs=5)]
        rt = lp_web._track_delivered_jobs(42, jobs, {165: "Test Frigate"})
        assert rt["total_runs"] == 0
        assert rt["total_jobs"] == 0

    def test_new_delivery_after_baseline_is_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(42, [self._job(1)], {165: "Test Frigate"})
        rt = lp_web._track_delivered_jobs(
            42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        assert rt["total_runs"] == 25
        assert rt["total_jobs"] == 1

    def test_same_job_not_double_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(42, [self._job(1)], {165: "Test Frigate"})
        lp_web._track_delivered_jobs(42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        rt = lp_web._track_delivered_jobs(42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        assert rt["total_runs"] == 25
        assert rt["total_jobs"] == 1

    def test_persists_across_calls(self, tmp_path, monkeypatch):
        path = tmp_path / "jobs.json"
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", path)
        lp_web._track_delivered_jobs(42, [self._job(1)], {165: "Test Frigate"})
        lp_web._track_delivered_jobs(42, [self._job(1), self._job(2, runs=25)], {165: "Test Frigate"})
        saved = json.loads(path.read_text())
        assert saved["42"]["total_runs"] == 25

    def test_separate_characters_tracked_independently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        lp_web._track_delivered_jobs(1, [self._job(1)], {})
        lp_web._track_delivered_jobs(2, [self._job(1)], {})  # same job_id, different char
        rt1 = lp_web._track_delivered_jobs(1, [self._job(1), self._job(2, runs=7)], {})
        rt2 = lp_web._track_delivered_jobs(2, [self._job(1), self._job(2, runs=9)], {})
        assert rt1["total_runs"] == 7
        assert rt2["total_runs"] == 9


# ---------------------------------------------------------------------------
# Character data bundle (v1.32.0) — a failing market-orders call (missing
# scope on the user's own EVE app, or stale token) must not take down the
# rest of the character tab (wallet, jobs, LP) with it.
# ---------------------------------------------------------------------------

class TestCharDataOrdersIsolation:
    def _login(self, monkeypatch):
        import time as _time
        monkeypatch.setattr(lp_web, "_AUTH", {
            "access_token": "TOK", "refresh_token": "RT", "character_id": 42,
            "name": "Test Char", "scopes": [], "expires_at": _time.time() + 3600,
        })

    def _http_error(self, status):
        resp = MagicMock(status_code=status)
        return requests.HTTPError(response=resp)

    def test_orders_failure_does_not_break_rest_of_bundle(self, tmp_path, monkeypatch):
        self._login(monkeypatch)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 1_000_000.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 5_000_000, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])

        def _raise_403(*a, **k):
            raise self._http_error(403)
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", _raise_403)

        out = lp_web.do_char_data({})
        assert out["wallet"] == 1_000_000.0
        assert out["total_sp"] == 5_000_000
        assert out["market_orders"] == []
        assert "esi-markets.read_character_orders.v1" in out["market_orders_error"]

    def test_orders_success_has_no_error(self, tmp_path, monkeypatch):
        self._login(monkeypatch)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", tmp_path / "jobs.json")
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 0.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 0, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: [])

        out = lp_web.do_char_data({})
        assert out["market_orders"] == []
        assert out["market_orders_error"] is None

    def test_orders_table_has_total_value_column(self):
        """Total value = remaining units x listed price, next to the Price column."""
        html = lp_web.INDEX_HTML
        assert ">Total value</th>" in html
        assert "fmtISK((o.volume_remain??0)*o.price)" in html


# ---------------------------------------------------------------------------
# SESSION retry policy — a stale pooled keep-alive connection to ESI/Fuzzwork
# (server closed it after being idle) surfaces as a ConnectionError on the
# next reused connection. urllib3's Retry only auto-retries that class of
# error ("read error") for methods it considers safe by default, which
# excludes POST — so bulk lookups like resolve_names()'s POST to
# /universe/names/ (used by every /api/char/data poll) went unretried while
# the equivalent GET calls were silently retried, causing a 500 on
# /api/char/data whenever it happened to land on a POST.
# ---------------------------------------------------------------------------

class TestSessionRetryCoversPost:
    def test_post_is_in_the_allowed_retry_methods(self):
        assert "POST" in lp_web._RETRY.allowed_methods

    def test_get_is_still_in_the_allowed_retry_methods(self):
        assert "GET" in lp_web._RETRY.allowed_methods

    def test_stale_connection_on_post_is_retried_transparently(self):
        """A ConnectionError on the first attempt of a POST must not surface —
        the mounted adapter should retry it on a fresh connection and return
        the eventual successful response, exactly like it already does for
        GET (see the SESSION.mount comment)."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        attempts = {"n": 0}

        class FlakyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                attempts["n"] += 1
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                if attempts["n"] == 1:
                    # Simulate the server closing a stale keep-alive connection:
                    # drop it without writing any response at all.
                    self.close_connection = True
                    return
                body = b"[]"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a, **k):
                pass

        srv = HTTPServer(("127.0.0.1", 0), FlakyHandler)
        port = srv.server_address[1]
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01),
                         daemon=True).start()
        try:
            r = lp_web.SESSION.post(f"http://127.0.0.1:{port}/universe/names/", json=[1])
            assert r.status_code == 200
            assert attempts["n"] == 2
        finally:
            srv.shutdown()
