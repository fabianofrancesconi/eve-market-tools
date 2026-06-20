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
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    threading.Thread(target=srv.serve_forever, daemon=True).start()

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
