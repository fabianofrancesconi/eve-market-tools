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
    assert ".ind-flag-pill.on-sale" in src
