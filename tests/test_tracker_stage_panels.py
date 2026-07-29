"""Guards the tracker board's stage-specific detail panels and the inline price
decider docked in the Built/Listed cards.

The per-tile sell block was redesigned so each lifecycle stage answers ONE
question with minimal clutter (everything non-actionable lives behind "Full
detail"):
  • planned  → the shopping bill + a list-vs-dump profit hint
  • building → a quiet "waiting" note + a market look-ahead
  • built    → THE price decision: an inline decider (drift line, break-even-
               aware slider, sell-through odds, copy-to-the-cent price)
  • listed   → a sold-so-far bar + the same decider, scoped to the remainder
  • sold     → real profit vs the prediction, with a dump-now what-if

The inline decider reuses the peek modal's pure market math at runtime but draws
its own compact skeleton and caches state on IND.decider[id] so board re-renders
don't lose an in-flight fetch or a dialled-in price. These are static-source
guards (no headless browser in CI); _sim_fn slices a named JS function body.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()
_CSS = (_ROOT / "static" / "style.css").read_text()


def _sim_fn(name, src=_IND_JS):
    """Slice a named function body from JS source for focused assertions."""
    start = src.index(f"function {name}(")
    rest = src[start + 1:]
    end = rest.find("\nfunction ")
    return rest if end < 0 else rest[:end]


class TestStagePanels:
    def test_each_stage_has_its_own_branch(self):
        fn = _sim_fn("_buildSellHtml")
        for stage in ('stage==="planned"', 'stage==="building"',
                      'stage==="built"', 'stage==="listed"||stage==="sold"'):
            assert stage in fn, stage

    def test_planned_shows_shopping_bill_and_outcome(self):
        # "What do I buy, and is it worth it?" — the mat bill plus the two
        # outcomes (patient list vs dump now), no itemised material rows inline.
        fn = _sim_fn("_buildSellHtml")
        plan = fn[fn.index('stage==="planned"'):fn.index('stage==="building"')]
        assert "ind-plan" in plan
        assert "Shopping bill" in plan
        assert "Sell & wait" in plan
        assert "or dump now" in plan
        # The list-vs-instant profits come from the shared batch economics.
        assert "econ.profitL" in plan
        assert "econ.profitI" in plan

    def test_building_is_wait_plus_lookahead_only(self):
        # Nothing to act on — just a waiting note and the market look-ahead
        # button (the decider works pre-delivery, so the button lands usefully).
        fn = _sim_fn("_buildSellHtml")
        build = fn[fn.index('stage==="building"'):fn.index('stage==="built"')]
        assert "ind-sell-peek" in build
        assert "ind-sell-analyze" in build
        # No inline decider is drawn before there's stock to price.
        assert "_buildDeciderHtml" not in build

    def test_built_leads_with_inline_decider(self):
        # The decision stage embeds the decider — which now carries BOTH exit
        # routes (list + instant), so no separate instant row sits in the branch.
        fn = _sim_fn("_buildSellHtml")
        built = fn[fn.index('stage==="built"'):fn.index('stage==="listed"')]
        assert "_buildDeciderHtml(b, stage)" in built
        assert "Set your sell price" in built
        assert "ind-sell-instant" not in built

    def test_listed_shows_progress_and_decider(self):
        # Fine-tune to move the remainder: a sold-so-far bar + the same decider.
        fn = _sim_fn("_buildSellHtml")
        tail = fn[fn.index('stage==="listed"||stage==="sold"'):]
        assert "ind-listed-progress" in tail
        assert "ind-listed-bar" in tail
        assert "Fine-tune your price" in tail
        assert "_buildDeciderHtml(b, stage)" in tail

    def test_sold_compares_plan_vs_reality(self):
        # The reckoning: a real-profit hero, then predicted / delta / what-if.
        fn = _sim_fn("_buildSellHtml")
        tail = fn[fn.index('stage==="listed"||stage==="sold"'):]
        done = tail[tail.index("if(closed){"):]
        assert "ind-done-hero" in done
        assert "Real profit" in done
        assert "You predicted" in done
        assert "vs. reality" in done
        # The what-if dumps the whole lot at the frozen bid, not the live one.
        assert "What-if: dumped at frozen bid" in done
        assert "econ.profitI" in done


class TestInlineDecider:
    def test_state_cached_per_build(self):
        # IND carries a per-build decider cache so a re-render restores the panel
        # without refetching or dropping a dialled-in price.
        assert "decider:{}" in _IND_JS
        st = _sim_fn("_deciderState")
        assert "IND.decider[b.id]" in st
        for key in ("live", "liveState", "market", "marketState", "price"):
            assert key in st, key

    def test_fetches_live_quote_and_order_book(self):
        # Two fetches fill the decider: /api/ind/detail (live ask/bid) and
        # /api/ind/sell-analysis (order book + history for the odds).
        live = _sim_fn("_fetchDeciderLive")
        assert "/api/ind/detail?" in live
        assert "refresh_prices" in live
        book = _sim_fn("_fetchDeciderMarket")
        assert "/api/ind/sell-analysis?" in book

    def test_drift_line_compares_predicted_vs_now(self):
        # The "market moved under me" signal: frozen predicted ask → live ask.
        fn = _sim_fn("_renderDeciderDrift")
        assert "Predicted" in fn
        assert "st.live" in fn

    def test_slider_reuses_modal_rail_and_chips(self):
        # The slider reuses the peek modal's rail tint + chip styling and offers
        # the four snap targets.
        fn = _sim_fn("_renderDeciderBody")
        assert "bp-sim-slider" in fn
        assert "bp-sim-chips" in fn
        assert "_peekRailStyle" in fn
        for chip in ("Undercut", "Best ask", "Break-even", "Predicted"):
            assert chip in fn, chip

    def test_readout_uses_fresh_broker_and_sellthrough(self):
        # Re-listing pays broker again, so net is price*(1-stax-bfee); the odds
        # come from the price-conditioned demand model over the remainder.
        fn = _sim_fn("_updateBuildDecider")
        assert "(1-stax-bfee)" in fn.replace(" ", "")
        assert "_priceConditionedDailyRate" in fn
        assert "_sellThroughProb" in fn

    def test_both_exit_routes_show_profit(self):
        # The slider only prices the LIST route; the decider must ALSO show the
        # instant (dump-now) route with its own profit, so both exits compare.
        fn = _sim_fn("_updateBuildDecider")
        # List route: chosen price, sales tax + fresh broker.
        assert "listProfit" in fn
        assert "(1-stax-bfee)" in fn.replace(" ", "")
        # Instant route: live bid, sales tax only (no broker on an immediate sell).
        assert "instProfit" in fn
        assert "bid*(1-stax)" in fn.replace(" ", "")
        # And the delta between them — what patience buys.
        assert "gain" in fn
        assert "ind-dec-route" in fn

    def test_breakeven_is_only_a_warning_not_a_headline(self):
        # Break-even is NOT a margin readout; it only surfaces as a ⚠ flag when
        # the chosen list price is actually underwater.
        fn = _sim_fn("_updateBuildDecider")
        assert "/unit above break-even" not in fn
        assert "Below break-even" in fn
        assert "underBE" in fn

    def test_prices_shown_at_full_value_not_abbreviated(self):
        # EVE orders are to the cent — the decider's prices must use fmtISKFull
        # (14,589.99), never fmtISK's abbreviation (14.6K). The drift line, the
        # slider body/chips and the per-unit readout all format with fmtISKFull.
        for name in ("_renderDeciderDrift", "_renderDeciderBody",
                     "_updateBuildDecider"):
            fn = _sim_fn(name)
            assert "fmtISKFull" in fn, name
            # The isk helper in each is the full formatter, not the abbreviator.
            assert "fmtISK(v)" not in fn.replace("fmtISKFull(v)", ""), name

    def test_full_formatter_exists(self):
        shared = (_ROOT / "static" / "js" / "shared.js").read_text()
        assert "function fmtISKFull(" in shared
        assert "minimumFractionDigits:2" in shared.replace(" ", "")

    def test_full_market_link_opens_modal(self):
        # The deep-dive stays in the tested modal — one link opens it on Market.
        fn = _sim_fn("_wireBuildDecider")
        assert 'openBuildPeek(b.id, "market")' in fn

    def test_decider_wired_when_present(self):
        # _wireSellCard hooks the decider only when the card actually drew one.
        fn = _sim_fn("_wireSellCard")
        assert '.ind-decider' in fn
        assert "_wireBuildDecider(card, b)" in fn


class TestDeciderStyling:
    def test_new_panel_classes_are_styled(self):
        for cls in (".ind-plan", ".ind-plan-out", ".ind-dec-routes",
                    ".ind-dec-route", ".ind-dec-route-profit",
                    ".ind-listed-bar", ".ind-done-hero", ".ind-done-compare",
                    ".ind-decider", ".ind-dec-slider"):
            assert cls in _CSS, cls

    def test_stage_colours_tint_the_rails(self):
        # Each stage panel wears its lifecycle colour on the left rail.
        assert "var(--stg-planned" in _CSS
        assert "var(--stg-built" in _CSS
        assert "var(--stg-listed" in _CSS
        assert "var(--stg-sold" in _CSS
