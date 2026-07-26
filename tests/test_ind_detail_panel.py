"""Frontend-wiring checks for the redesigned Industry Planner detail panel.

The top of the panel was rebuilt around the sell decision: two side-by-side sell
paths (list vs instant), each stating net ISK, the price it uses and the margin,
with the more profitable path accented — plus a market-health strip (units
traded/day, days to offload, competition, buy-side depth, liquidity). The old
accounting waterfall ("build ledger") is gone. FRONTEND_SOURCE is index.html +
all JS, so these assert the structure without a browser."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_old_waterfall_ledger_is_gone():
    src = lp_web.FRONTEND_SOURCE
    # The single-column fee waterfall was replaced by the sell-decision hero.
    for gone in ('class="ind-led"', "ind-led-head", "ind-led-body",
                 "ind-led-foot", "Build ledger"):
        assert gone not in src, gone


def test_sell_decision_hero_has_both_paths():
    src = lp_web.FRONTEND_SOURCE
    # The signature is the list-vs-instant fork.
    assert 'class="ind-sell"' in src
    assert "ind-sell-paths" in src
    assert "ind-sell-path" in src
    # Both selling methods are present as paths.
    assert "List &amp; wait" in src
    assert "Dump now" in src
    # Each path states net ISK + margin (the profit answer, not a fee column).
    assert "ind-sell-net" in src
    assert "ind-sell-margin" in src
    # The price each path uses is shown (list at ask, instant at bid).
    assert "at ask" in src
    assert "at bid" in src


def test_winning_path_is_accented():
    src = lp_web.FRONTEND_SOURCE
    # The more profitable path is flagged so the eye lands on the answer.
    assert "listWins" in src
    assert "instWins" in src
    assert "▲ best" in src
    assert ".ind-sell-path.win" in src


def test_reference_rail_shows_breakeven_and_cost():
    src = lp_web.FRONTEND_SOURCE
    # Break-even price, build cost and build time sit on a reference rail.
    assert "ind-sell-rail" in src
    assert "Break-even" in src
    assert "Build cost" in src
    assert "Build time" in src


def test_market_strip_reports_liquidity_signals():
    src = lp_web.FRONTEND_SOURCE
    # The market strip answers "can the market take it": daily volume, days to
    # offload the batch, competing sell orders, buy-side depth, liquidity score.
    assert "ind-sell-market" in src
    assert "daysToOffload" in src
    assert "to offload batch" in src
    assert "listed vs you" in src     # competition (sell_volume)
    assert "wanted now" in src        # instant-exit depth (buy_volume)
    assert "liquidity" in src         # tradeability score
    # It reads the live book depth fields the backend now exposes.
    assert "d.sell_volume" in src
    assert "d.buy_volume" in src


def test_vol_per_day_column_in_scan_table():
    src = lp_web.FRONTEND_SOURCE
    # The big table gets a per-day traded-volume column (the market's appetite).
    assert 'k:"daily_vol"' in src
    assert '"Vol/day"' in src
