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


def test_mode_button_renamed_to_tracker():
    src = lp_web.FRONTEND_SOURCE
    # The user-facing label is Tracker; the internal data-mode stays "summary"
    # so saved mode prefs keep resolving.
    assert ">Tracker<" in src
    assert 'data-mode="summary"' in src
    # A count badge rides on the Tracker button.
    assert 'id="ind-track-count"' in src


def test_tracked_build_cards_live_in_tracker():
    src = lp_web.FRONTEND_SOURCE
    # #ind-builds now sits inside the Tracker view, not the planner view, and the
    # Planner shows a link-across hint instead.
    sum_view = src.index('id="ind-summary-view"')
    plan_view = src.index('id="ind-planner-view"')
    builds = src.index('id="ind-builds"')
    assert plan_view < sum_view < builds  # #ind-builds comes after the summary view opens
    assert 'id="ind-planner-trackhint"' in src
    # The mode toggle renders cards + count on entry.
    assert "_updateTrackCount" in src


def test_tracker_dashboard_has_est_profit_and_capital_bar():
    src = lp_web.FRONTEND_SOURCE
    assert "Est. total profit" in src        # new headline KPI
    assert "sum-capbar" in src               # capital-by-stage stacked bar
    # The redundant "Tracked builds" count KPI is gone from the dashboard tiles
    # (the count now rides on the mode button + stage strip instead). The KPI
    # region runs from the first tile's label to the capital-bar comment.
    start = src.index('sum-kpi-label">Realized profit')
    kpi_html = src[start:src.index('"Where your capital sits"', start)]
    assert "Tracked builds" not in kpi_html
