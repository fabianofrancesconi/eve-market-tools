"""Guards the tracker board's stage-specific detail panels and the inline price
decider docked in the Built/Listed cards.

The focused card leads with ONE small insight per lifecycle stage and hides the
nitty gritty behind a Details drawer:
  • planned  → "start N runs in EVE" (or link a close-match job) + the stake
  • building → the countdown + how the market moved since you started
  • built    → one copyable list price, both exits' profit, week odds
  • listed   → the hold / re-price / dump Call and its one-line reason
  • sold     → real profit and how it landed against the plan
Details holds the pricing decider, market watch, plan-vs-reality, the frozen
economics and every management action.

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
    """Every stage shares ONE anatomy: header → stepper → a single focused
    insight (eyebrow / headline / one line / ≤3 stats / ≤1 action) → a Details
    drawer, opened on demand, holding every number, tool and management action."""

    def test_card_anatomy_is_insight_then_details(self):
        fn = _sim_fn("_buildCardHtml")
        assert "_buildStepperHtml(b, stage)" in fn
        assert "_buildInsightHtml(b, stage, close)" in fn
        # The nitty gritty only renders once the user asks for it.
        assert "expanded?_buildDetailsHtml(b, stage)" in fn
        assert "ind-build-toggle" in fn and "Details ▾" in fn
        # The old stacked sell block is gone.
        assert "_buildSellHtml" not in _IND_JS

    def test_focus_panel_has_no_duplicate_head(self):
        # The card header carries the one close button; no separate panel head.
        assert "ind-focus-head" not in _IND_JS
        assert "ind-focus-close" in _sim_fn("_buildCardHtml")

    def test_each_stage_has_its_own_insight_branch(self):
        fn = _sim_fn("_insightInner")
        for stage in ("planned", "building", "built", "listed", "stopped"):
            assert f'stage==="{stage}"' in fn, stage
        # …and every branch goes through the same shell.
        assert fn.count("_insightShell({") >= 6

    def test_insight_shell_is_small_and_uniform(self):
        fn = _sim_fn("_insightShell")
        for cls in ("ind-ins-eyebrow", "ind-ins-title", "ind-ins-sub",
                    "ind-ins-bar", "ind-ins-stats", "ind-ins-acts"):
            assert cls in fn, cls

    def test_planned_asks_to_start_the_job(self):
        fn = _sim_fn("_insightInner")
        plan = fn[fn.index('stage==="planned"'):fn.index('stage==="building"')]
        assert "in EVE" in plan
        assert "econ.profitL" in plan
        # The close-match job link lives in the insight, where the decision is.
        assert "ind-build-linkclose" in plan

    def test_building_is_countdown_plus_drift(self):
        fn = _sim_fn("_insightInner")
        build = fn[fn.index('stage==="building"'):fn.index('stage==="built"')]
        assert "ind-live-timer" in build
        assert "Ready for delivery" in build
        assert "_buildWatchRead(b)" in build
        # No pricing tool before there's stock to price.
        assert "_buildDeciderHtml" not in build

    def test_building_watch_shows_drift_and_profit_impact(self):
        fn = _sim_fn("_renderBuildWatch")
        assert "Planned ask" in fn
        assert "_buildWatchRead(b)" in fn
        read = _sim_fn("_buildWatchRead")
        assert "st.live" in read
        assert "nowProfit" in read
        # It reuses the shared cached live quote (no separate fetch path).
        wire = _sim_fn("_wireBuildWatch")
        assert "_fetchDeciderLive(b)" in wire
        assert "_deciderState(b)" in wire

    def test_built_leads_with_one_copyable_price(self):
        fn = _sim_fn("_insightInner")
        built = fn[fn.index('stage==="built"'):fn.index('stage==="listed"')]
        assert "_builtRead(b)" in built
        assert "List at" in built
        assert "copyBtn(r.price)" in built
        # Both exits' profit as stats; flips to a dump when listing loses money.
        assert "Profit if listed" in built and "Profit if dumped now" in built
        assert "Dump into buy orders" in built
        read = _sim_fn("_builtRead")
        assert "0.9999" in read
        assert "_dumpQuote(" in read

    def test_listed_shows_the_call_from_the_shared_read(self):
        fn = _sim_fn("_insightInner")
        listed = fn[fn.index('stage==="listed"'):fn.index('stage==="stopped"')]
        assert "_listedRead(b)" in listed
        assert "title:v.rec" in listed
        assert "lr.queueShort" in listed
        assert "accrue from your wallet automatically" in listed

    def test_sold_insight_is_the_verdict(self):
        fn = _sim_fn("_insightInner")
        sold = fn[fn.index('stage==="stopped"'):]
        assert "_soldRead(b)" in sold
        assert "ind-sell-archive" in sold
        assert "ind-sell-resume" in sold

    def test_details_hold_the_nitty_gritty(self):
        fn = _sim_fn("_buildDetailsHtml")
        # Pricing tool (built/listed), market watch (building), plan vs reality (sold).
        assert "_buildDeciderHtml(b, stage)" in fn
        assert "ind-watch" in fn and "ind-sell-analyze" in fn
        for s in ("You predicted", "beat plan by", "missed plan by",
                  "Patience paid off", "dumped at the frozen bid"):
            assert s in fn, s
        # Frozen economics + every management action live here, not up front.
        assert "_buildDetailHtml(b)" in fn
        for cls in ("ind-sell-abandon", "ind-sell-edit", "ind-sell-stop",
                    "ind-sell-delete", "ind-build-del"):
            assert cls in fn, cls

    def test_insight_repaints_when_market_lands(self):
        assert "_renderInsight(b)" in _sim_fn("_fetchDeciderLive")
        assert "_renderInsight(b)" in _sim_fn("_fetchDeciderMarket")
        assert "_wireInsight(card, b)" in _sim_fn("_wireSellCard")


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
        # The "market moved under me" signal: frozen planned ask → live ask,
        # with a plain-language good/bad-surprise verdict.
        fn = _sim_fn("_renderDeciderDrift")
        assert "Planned ask" in fn
        assert "st.live" in fn
        assert "ind-dec-drift-verdict" in fn

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

    def test_listed_stage_adds_waiting_support(self):
        # "Keep waiting or re-price?" — the Listed stage gets queue depth (the
        # hidden reason nothing sells), a slow-vs-overpriced diagnosis from the
        # conditioned-vs-unconditioned demand rates, and a hold/re-price/dump call.
        upd = _sim_fn("_updateBuildDecider")
        # Only fires on the listed stage (built has no remainder-in-market yet).
        assert 'stage==="listed"' in upd
        assert "ind-wait" in upd and "_listedRead(b)" in upd
        fn = _sim_fn("_listedRead")
        # Queue position — units ahead at/below the chosen price.
        assert "_unitsAheadInQueue" in fn
        assert "Behind" in fn
        # The honest slow-vs-overpriced read (unconditioned baseline vs price).
        assert "baseRate" in fn
        assert "priced above market" in fn
        # A clear recommendation framing — the verdict itself is factored into the
        # shared _callVerdict helper (see test_call_verdict_is_a_shared_helper).
        assert "ind-wait-rec" in upd
        assert "_callVerdict({" in fn

    def test_waiting_support_uses_actual_listed_price(self):
        # The queue position / diagnosis / Call describe YOUR current listing, so
        # they must reason about the price you're actually listed at (your live
        # sell order), not the slider's exploratory undercut default — which had
        # claimed "you're at the front" while your real order sat mid-queue.
        helper = _sim_fn("_buildListedOrderPrice")
        assert "_peekLinkedOrder(b)" in helper
        fn = _sim_fn("_listedRead")
        assert "_buildListedOrderPrice(b)" in fn
        # Queue depth + demand are recomputed at the listed price, not `ahead`/`rate`
        # (those stay the slider-price odds read).
        assert "curPrice" in fn
        assert "_unitsAheadInQueue(m.sell_book, curPrice)" in fn
        # The Call reasons about the listed price too (profit at your real ask).
        assert "curListProfit" in fn
        # The board tile flag takes the very same read.
        assert "_listedRead(b)" in _sim_fn("_tileActionFlag")

    def test_queue_depth_reconciles_against_orders_real_market(self):
        # The decider fetches its sell book at the build snapshot's hub (the server
        # clamps a non-hub station to Jita). If your live order is actually listed
        # at a DIFFERENT market, walking that hub's book counts phantom competitors
        # — a genuinely-best order showed "Behind 1,395 units — re-price". The
        # order object carries its TRUE standing (is_best/queue_rank) computed at
        # its own location_id; defer to that when the markets differ.
        helper = _sim_fn("_linkedOrderStanding")
        # Reconciliation triggers only when the order's location ≠ the book's station.
        assert "o.location_id" in helper
        assert "bookStationId" in helper
        assert "return null" in helper  # same market / no rank ⇒ book wins
        assert "is_best" in helper and "queue_rank" in helper
        # The queue line consults it and, when best at its own market, says so
        # instead of inventing "Behind N units".
        fn = _sim_fn("_listedRead")
        assert "_linkedOrderStanding(b, m.station_id)" in fn
        assert "best ask at your market" in fn
        # And the Call doesn't tell you to re-price against a market you're not in.
        assert "standing&&standing.is_best" in fn.replace(" ", "")
        # The board tile flag uses the same read, so tile and panel agree.
        assert "_listedRead(b)" in _sim_fn("_tileActionFlag")

    def test_linked_order_carries_location_id(self):
        # For the reconciliation above the client needs the order's real market —
        # the backend must expose location_id on each order it returns.
        src = (_ROOT / "lp-web.py").read_text()
        assert '"location_id": loc,' in src

    def test_call_verdict_is_a_shared_helper(self):
        # The Listed-stage Call (dump / re-price / hold) is factored out so the
        # board tile reaches the SAME verdict as the decider from the same signals.
        # One read feeds the insight, the Details "why" and the tile flag.
        assert "_callVerdict({" in _sim_fn("_listedRead")
        assert "_listedRead(b)" in _sim_fn("_updateBuildDecider")
        assert "_listedRead(b)" in _sim_fn("_tileActionFlag")
        v = _sim_fn("_callVerdict")
        assert "Dump the remainder" in v
        assert "Re-price to move it" in v
        # Only the two act-now verdicts expose an `action`; both holds leave it null
        # so a caller can cheaply ask "does this need me?".
        assert 'action="dump"' in v
        assert 'action="reprice"' in v
        assert "action=null" in v.replace(" ", "")

    def test_reprice_is_gated_on_fee_aware_expected_value(self):
        # Re-pricing is NOT free: it burns a fresh broker fee and books less per
        # unit. The Call must only tilt to "re-price" when undercutting beats
        # holding in EXPECTED value (odds × profit), so a transient dip holds and
        # only a persistent shift (stop-loss) triggers a re-list.
        ev = _sim_fn("_repricePaysOff")
        # Hold pays NO fresh broker fee; re-price pays one (1-stax vs 1-stax-bfee).
        assert "curPrice*(1-stax)-cpu" in ev.replace(" ", "")
        assert "target*(1-stax-bfee)-cpu" in ev.replace(" ", "")
        # It's an expected-value comparison (odds × profit on each side).
        assert "holdEV" in ev and "repEV" in ev
        # Never re-price into a loss, and require a positive EV gain.
        assert "repNet>0" in ev.replace(" ", "")
        # The verdict gates the re-price branch on that test, not just "overpriced".
        v = _sim_fn("_callVerdict")
        assert "repriceWorthIt" in v
        assert "overpriced && repriceWorthIt" in v
        # Overpriced-but-not-worth-it becomes an explicit hold, not a re-price.
        assert "Hold — re-pricing won't pay" in v
        # The shared Listed read feeds the fee-aware gate in.
        assert "_repricePaysOff(" in _sim_fn("_listedRead")

    def test_listed_tile_shows_action_flag(self):
        # The kanban tile — not just the opened card — flags a lot that needs a
        # re-price/dump, so it's spottable across the board. Built from the shared
        # verdict over the prefetched market; only act-now verdicts render.
        tile = _sim_fn("_buildTileHtml")
        assert "_tileActionFlag(b)" in tile
        assert "ind-tile-action" in tile
        flag = _sim_fn("_tileActionFlag")
        # Needs the prefetched sell-analysis; silent (null) until it lands.
        assert 'st.marketState!=="done"' in _sim_fn("_listedRead")
        assert "if(!r) return null" in flag
        assert "return v.action ?" in flag
        # The board prefetches every listed build's market so tiles can flag without
        # the user opening each card, and repaints the tile when the fetch lands.
        assert "_prefetchListedFlags(box, (buckets.listed||[]))" in _IND_JS
        assert "_renderTileFlag(b)" in _sim_fn("_fetchDeciderMarket")
        # Tile carries data-stage so the async repaint can target listed tiles only.
        assert 'data-stage="${stage}"' in tile
        # And the flag has its own styling in the two act-now colours.
        assert ".ind-tile-action.reprice" in _CSS
        assert ".ind-tile-action.dump" in _CSS

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
        for cls in (".ind-insight", ".ind-ins-title", ".ind-ins-stats",
                    ".ind-ins-copy", ".ind-details", ".ind-dt-sec",
                    ".ind-dt-manage", ".ind-dec-routes",
                    ".ind-dec-route", ".ind-dec-route-profit",
                    ".ind-done-scenarios", ".ind-done-compare",
                    ".ind-decider", ".ind-dec-slider"):
            assert cls in _CSS, cls

    def test_old_stacked_panels_are_gone(self):
        for cls in (".ind-plan {", ".ind-sell-peek", ".ind-sell-live",
                    ".ind-listed-bar", ".ind-done-hero", ".ind-focus-head"):
            assert cls not in _CSS, cls

    def test_css_braces_balance(self):
        # A half-deleted rule silently unstyles everything after it.
        assert _CSS.count("{") == _CSS.count("}")

    def test_stage_colours_tint_the_rails(self):
        # Each stage's insight wears its lifecycle colour on the left rail.
        assert ".ind-insight.stage-listed" in _CSS
        assert "var(--stg-planned" in _CSS
        assert "var(--stg-built" in _CSS
        assert "var(--stg-listed" in _CSS
        assert "var(--stg-sold" in _CSS
