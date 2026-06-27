"""
Tests for the percent-based tax / broker fee inputs (v1.6.0).

The UI now shows Sales tax and Broker fee as percent (4.5, 1.5, 7.5) while the
backend still receives fractions (0.045, 0.015, 0.075). Conversion happens at
the input boundary in the page JS via pctToFrac() / fracToPct(). These tests
pin the served HTML/JS so the conversion can't silently regress.
"""
import importlib.util
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


@pytest.fixture()
def html(tmp_path):
    orig_cache = lp_web.CACHE_DIR
    orig_corps = lp_web.NPC_CORPS[:]
    lp_web.CACHE_DIR = tmp_path
    lp_web.NPC_CORPS.clear()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), lp_web.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            yield r.read()
    finally:
        srv.shutdown()
        lp_web.CACHE_DIR = orig_cache
        lp_web.NPC_CORPS.clear()
        lp_web.NPC_CORPS.extend(orig_corps)


# ---------------------------------------------------------------------------
# Input fields show percent values + percent labels
# ---------------------------------------------------------------------------

class TestPercentInputs:
    def test_lp_sales_tax_default_is_percent(self, html):
        assert b'id="tax" type="number" step="0.1" value="4.5"' in html

    def test_lp_broker_fee_default_is_percent(self, html):
        assert b'id="broker" type="number" step="0.1" value="1.5"' in html

    def test_arb_sales_tax_default_is_percent(self, html):
        assert b'id="arb-tax" type="number" step="0.1" value="7.5"' in html

    def test_labels_carry_percent_sign(self, html):
        assert b"Sales tax %" in html
        assert b"Broker fee %" in html

    def test_old_decimal_defaults_gone(self, html):
        # The previous fraction defaults must not linger in the inputs.
        assert b'value="0.045"' not in html
        assert b'value="0.015"' not in html
        assert b'value="0.075"' not in html


# ---------------------------------------------------------------------------
# Conversion helpers exist and are wired into save / restore / send
# ---------------------------------------------------------------------------

class TestConversionWiring:
    def test_helpers_defined(self, html):
        assert b"function pctToFrac(" in html
        assert b"function fracToPct(" in html

    def test_savels_converts_to_fraction(self, html):
        assert b'tax:pctToFrac($("#tax").value)' in html
        assert b'broker:pctToFrac($("#broker").value)' in html

    def test_scan_ctx_converts_to_fraction(self, html):
        assert b'tax:pctToFrac($("#tax").value), broker:pctToFrac($("#broker").value)' in html

    def test_restore_converts_to_percent(self, html):
        assert b'$("#tax").value=fracToPct(s.tax)' in html
        assert b'$("#broker").value=fracToPct(s.broker)' in html

    def test_arb_send_converts_to_fraction(self, html):
        assert b"sales_tax:    pctToFrac($(\"#arb-tax\").value)" in html

    def test_arb_restore_converts_to_percent(self, html):
        assert b'$("#arb-tax").value=fracToPct(a.sales_tax)' in html
