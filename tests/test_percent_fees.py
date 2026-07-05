"""
Tests for the percent-based tax / broker fee inputs.

The UI shows Sales tax and Broker fee as percent (4.5, 1.5) in a global bar
while the backend still receives fractions (0.045, 0.015). Conversion happens
at the input boundary in the page JS via pctToFrac() / fracToPct(). These
tests pin the served HTML/JS so the conversion can't silently regress.
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
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01),
                     daemon=True).start()
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
    def test_global_sales_tax_default_is_percent(self, html):
        assert b'id="g-tax" type="number" step="0.1" value="7.5"' in html

    def test_global_broker_fee_default_is_percent(self, html):
        assert b'id="g-broker" type="number" step="0.1" value="3.0"' in html

    def test_labels_carry_percent_sign(self, html):
        assert b"Sales tax %" in html
        assert b"Broker fee %" in html

    def test_old_decimal_defaults_gone(self, html):
        assert b'value="0.045"' not in html
        assert b'value="0.015"' not in html
        assert b'value="0.075"' not in html


# ---------------------------------------------------------------------------
# Conversion helpers exist and are wired into save / restore / send
# ---------------------------------------------------------------------------

class TestConversionWiring:
    def test_helpers_defined(self):
        assert "function pctToFrac(" in lp_web.FRONTEND_SOURCE
        assert "function fracToPct(" in lp_web.FRONTEND_SOURCE

    def test_savels_converts_to_fraction(self):
        assert 'tax:pctToFrac($("#g-tax").value)' in lp_web.FRONTEND_SOURCE
        assert 'broker:pctToFrac($("#g-broker").value)' in lp_web.FRONTEND_SOURCE

    def test_scan_ctx_converts_to_fraction(self):
        assert 'tax:pctToFrac($("#g-tax").value), broker:pctToFrac($("#g-broker").value)' in lp_web.FRONTEND_SOURCE

    def test_restore_converts_to_percent(self):
        assert '$("#g-tax").value=fracToPct(s.tax)' in lp_web.FRONTEND_SOURCE
        assert '$("#g-broker").value=fracToPct(s.broker)' in lp_web.FRONTEND_SOURCE

    def test_arb_tax_uses_numeric_addition(self):
        assert 'parseFloat(pctToFrac($("#g-tax").value)||0)+parseFloat(pctToFrac($("#g-broker").value)||0)' in lp_web.FRONTEND_SOURCE
