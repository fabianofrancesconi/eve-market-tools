"""Frontend-wiring checks for the industry portfolio Summary. It lives inside the
Industry tab as a Planner/Summary mode toggle (not a standalone tab).
FRONTEND_SOURCE is index.html + all JS, so these assert it's placed, wired and
bundled without needing a browser."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_summary_is_an_industry_mode_not_a_tab():
    src = lp_web.FRONTEND_SOURCE
    # The Planner/Summary switch and its two views live in the Industry tablewrap.
    assert 'class="ind-mode-btn active" data-mode="planner"' in src
    assert 'data-mode="summary"' in src
    assert 'id="ind-planner-view"' in src
    assert 'id="ind-summary-view"' in src
    # Mode toggling is driven by indSetMode/indApplyMode.
    assert "function indSetMode" in src
    assert "function indApplyMode" in src
    # No standalone Summary tab remains (nav button / clean-URL / pane).
    assert 'data-tab="sum"' not in src
    assert 'sum:"/summary"' not in src
    assert 'id="sum-tablewrap"' not in src


def test_summary_backend_route_registered():
    # The data endpoint stays; there is no /summary shell route (it's reached
    # inside /industry now).
    assert "/api/ind/summary" in lp_web._GET_ROUTES
    assert "/summary" not in lp_web.TAB_ROUTES


def test_summary_module_bundled():
    src = lp_web.FRONTEND_SOURCE
    assert "/static/js/summary.js" in src
    assert "function renderSummary" in src
    assert "_sumNeedsAction" in src
