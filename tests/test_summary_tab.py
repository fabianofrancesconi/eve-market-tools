"""Frontend-wiring checks for the Industry Summary tab. FRONTEND_SOURCE is the
concatenation of index.html + all JS, so these assert the tab is routed, gated
and bundled without needing a browser."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_summary_tab_routed():
    src = lp_web.FRONTEND_SOURCE
    # Clean-URL maps both directions (shared.js).
    assert 'sum:"/summary"' in src
    assert '"/summary":"sum"' in src
    # switchTab toggles the pane and loads the roll-up.
    assert 'tab==="sum"' in src
    assert "loadSummary" in src


def test_summary_backend_route_registered():
    assert "/api/ind/summary" in lp_web._GET_ROUTES
    assert "/summary" in lp_web.TAB_ROUTES


def test_summary_module_bundled():
    src = lp_web.FRONTEND_SOURCE
    # summary.js is included and its key render entry points are present.
    assert "/static/js/summary.js" in src
    assert "function renderSummary" in src
    assert "_sumNeedsAction" in src
