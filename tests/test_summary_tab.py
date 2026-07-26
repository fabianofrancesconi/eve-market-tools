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
    assert "_sumFigures" in src   # the strip's portfolio-figures helper


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
    # #ind-builds now sits inside the Tracker view, not the planner view.
    sum_view = src.index('id="ind-summary-view"')
    plan_view = src.index('id="ind-planner-view"')
    builds = src.index('id="ind-builds"')
    assert plan_view < sum_view < builds  # #ind-builds comes after the summary view opens
    # The mode toggle renders cards + count on entry.
    assert "_updateTrackCount" in src


def test_track_guards_against_duplicate_builds():
    src = lp_web.FRONTEND_SOURCE
    # trackThisBuild runs a duplicate guard before saving: exact (same bp + runs)
    # is a hard warning, same bp / different runs is a softer nudge. The premise is
    # one blueprint per player, so ANY active (not-yet-sold) build counts as a clash.
    assert "_confirmTrackNotDuplicate" in src
    guard = src.index("function _confirmTrackNotDuplicate")
    body = src[guard:src.index("function trackThisBuild", guard)]
    assert '_buildStage(b)!=="sold"' in body   # any active build, not just planned
    assert "⚠ Already tracking" in body        # hard, near-error warning (exact runs)
    assert "may be double-tracking" in body     # soft nudge (different run count)
    # trackThisBuild bails out when the guard is declined.
    assert "if(!_confirmTrackNotDuplicate(d, runs)) return;" in src


def test_track_button_shows_persistent_already_tracking_state():
    src = lp_web.FRONTEND_SOURCE
    # An active build of the open blueprint flips the action button to a
    # persistent "✓ Tracking" state (survives reopening the panel), instead of a
    # fresh "＋ Track this build" every time.
    assert "✓ Tracking" in src
    assert '.ind-track-btn.on' in src          # filled-green status styling
    # After a successful track the detail re-renders so the button reflects it.
    assert ("IND.openDetail && IND.openDetail.blueprint_id===d.blueprint_id) "
            "renderIndDetail(d)") in src


def test_automatic_sale_rendering_wired():
    src = lp_web.FRONTEND_SOURCE
    # Sales are fully automatic in the pooled model: money accrues from wallet
    # transactions with no order linking, so both instant and listed sales are
    # treated uniformly — the panel just says sales accrue from the wallet.
    assert "accrue from your wallet" in src
    # The only sell action is Abandon (write off the unsold remainder); the old
    # start/link/unlink/cancel/close/edit machinery is gone.
    assert "ind-sell-abandon" in src
    assert "sell/abandon" in src
    for gone in ("ind-sell-start", "ind-sell-cancel", "ind-sell-close",
                 "ind-sell-edit", "ind-sell-unlink", "ind-sell-pick",
                 'sell.mode==="instant"', "needs_pick"):
        assert gone not in src, gone


def test_tracker_readout_strip_shows_portfolio_figures():
    src = lp_web.FRONTEND_SOURCE
    # The heavy dashboard is now a single slim readout strip: four figures on one
    # ribbon — realized / capital / ready / estimate.
    assert 'class="sum-strip"' in src
    assert 'read("Realized"' in src
    assert 'read("Capital in flight"' in src
    assert 'read("Ready to realize"' in src
    assert 'read("Est. total"' in src
    # The old dashboard chrome (KPI grid, capital-by-stage bar, stage strip,
    # needs-action queue, by-item table) is gone.
    assert "sum-capbar" not in src
    assert "sum-stagestrip" not in src
    assert "sum-queue" not in src
    assert "sum-table" not in src


def test_tracker_renders_a_pipeline_board():
    src = lp_web.FRONTEND_SOURCE
    # Builds render as a pipeline board of lanes + compact tiles, with a focus
    # panel for the clicked build, instead of collapsible stage groups.
    assert 'class="ind-board"' in src
    assert "_buildTileHtml" in src
    assert 'class="ind-lane' in src
    assert "ind-focus" in src
    # A clicked tile focuses that build; the focus panel reuses the full card.
    assert "IND.focusedBuild" in src
