"""Frontend-wiring checks for the Planner heads-up pills.

When a blueprint is currently occupied by an in-progress build ("in use") or you
still hold unsold stock of that item from an earlier build ("on sale"), the
planner shows a small pill after the item name (and in the detail header). These
warn you off starting a batch you can't start yet, or doubling down on stock you
haven't cleared. FRONTEND_SOURCE is index.html + all JS, so these assert the
structure without a browser."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_flag_helpers_exist():
    src = lp_web.FRONTEND_SOURCE
    assert "function indBlueprintFlags(" in src
    assert "function indFlagPills(" in src


def test_flags_derive_in_use_from_in_progress_builds():
    src = lp_web.FRONTEND_SOURCE
    # "in use" is an in-progress build still occupying the blueprint.
    assert "_isInProgressStage(_buildStage(b))" in src
    assert "inUse:occupied.length>0" in src


def test_flags_count_only_unsold_built_or_listed_stock():
    src = lp_web.FRONTEND_SOURCE
    # Only delivered-but-unsold stock (built/listed) counts as "on sale"; sold
    # and in-progress builds don't.
    assert 'st!=="built" && st!=="listed"' in src
    # Prefers the server's held_units, falling back to produced minus sold.
    assert "b.held_units" in src


def test_pills_rendered_in_row_and_detail():
    src = lp_web.FRONTEND_SOURCE
    # The row's item cell and the detail header both emit the pills.
    assert "indFlagPills(r.blueprint_id, false)" in src
    assert "indFlagPills(d.blueprint_id, true)" in src


def test_pill_styles_present():
    src = lp_web.FRONTEND_SOURCE
    assert ".ind-flag-pill.in-use" in src
    assert ".ind-flag-pill.tracked" in src
    assert ".ind-flag-pill.on-sale" in src


def test_occupied_pill_distinguishes_building_from_planned():
    src = lp_web.FRONTEND_SOURCE
    # A merely-planned build reads as "Tracked"; only an actually-running
    # ("building") build reads as "In use".
    assert 'const anyBuilding=f.occupied.some(b=>_buildStage(b)==="building");' in src
    assert 'const label=anyBuilding?"In use":"Tracked";' in src
    assert 'const cls=anyBuilding?"in-use":"tracked";' in src


# ── The "active job marked as built" guard ────────────────────────────────────
# A single transient jobs-fetch failure used to flip actively-building lots to
# "built" (permanent, since done_at is monotonic): the failing character dropped
# out of the combined bundle, its live jobs vanished, and the client read the
# missing job as "delivered". reconcileBuilds now demands two independent
# confirmations before stamping done_at.

def test_reconcile_requires_complete_jobs_before_delivering():
    src = lp_web.FRONTEND_SOURCE
    # The server's jobs_complete flag gates delivery; absent (older server) it
    # defaults true so behaviour is preserved, backstopped by the job_end check.
    assert "const jobsComplete = !AUTH.data || AUTH.data.jobs_complete !== false;" in src
    # A partial sweep holds the link instead of delivering.
    assert "if(!jobsComplete){" in src


def test_reconcile_requires_job_end_passed_before_delivering():
    src = lp_web.FRONTEND_SOURCE
    # A job whose clock hasn't run out cannot have been delivered, whatever the
    # active-jobs set says — the physical backstop.
    assert "const endTs=_jobEndTs(b);" in src
    assert "if(endTs!=null && endTs > Date.now()/1000 + 60){" in src
    # The end-time helper exists and parses the ISO job end.
    assert "function _jobEndTs(b){" in src
