"""Guards the tracked-build quick-look ("peek") modal — the 🔗 popup on the
Character overview — and specifically the Re-price decision tool.

The modal has one job: answer "how's this build doing, and should I drop my
sell price to climb the queue?" That means the re-price simulator MUST account
for a *fresh* broker fee (re-listing pays broker again), compute over the units
still unsold (with realized profit added for the batch total), and show the
"settle for less" floors — dump into buy orders now, or undercut the best ask —
so the user can see how much they actually give up. These are static-source
guards (no headless browser in CI) that lock those invariants in place.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHAR_JS = (_ROOT / "static" / "js" / "char.js").read_text()
_HTML = (_ROOT / "static" / "index.html").read_text()
_CSS = (_ROOT / "static" / "style.css").read_text()


def _sim_fn(name):
    """Slice out a named function body from char.js for focused assertions."""
    start = _CHAR_JS.index(f"function {name}(")
    # Crude but sufficient: read to the next top-level "\nfunction " marker.
    rest = _CHAR_JS[start + 1:]
    end = rest.find("\nfunction ")
    return rest if end < 0 else rest[:end]


class TestModalMarkup:
    def test_modal_has_two_tabs(self):
        # The peek splits into a high-level Overview and a detailed Re-price tab.
        assert 'id="buildPeekModal"' in _HTML
        assert 'id="build-peek-tabs"' in _HTML
        assert 'data-tab="overview"' in _HTML
        assert 'data-tab="reprice"' in _HTML

    def test_stepper_pinned_above_tabs(self):
        # The lifecycle stepper persists across both tabs, so it lives above the
        # tab switch (its own container), not inside a tab body.
        assert 'id="build-peek-stepper"' in _HTML
        assert _HTML.index('id="build-peek-stepper"') < _HTML.index('id="build-peek-tabs"')


class TestNameTrigger:
    def test_tracked_name_is_a_peek_trigger(self):
        # Clicking a tracked build's *name* opens the modal too — no behavioural
        # gap vs. the 🔗 icon. _peekName wraps it in a data-peek span.
        assert "function _peekName(" in _CHAR_JS
        fn = _sim_fn("_peekName")
        assert 'data-peek="${tb.id}"' in fn
        # All four render paths (single/multi-char jobs + orders) route names
        # through _peekName rather than a bare authEsc.
        assert _CHAR_JS.count("_peekName(") >= 4

    def test_peek_click_wiring_covers_all_data_peek_nodes(self):
        # The delegated click handler binds every [data-peek] node, so a name
        # span opens the modal exactly like the icon.
        assert 'querySelectorAll("[data-peek]")' in _CHAR_JS


class TestRepriceSimulator:
    def test_net_per_unit_charges_tax_and_a_fresh_broker_fee(self):
        # Re-listing pays broker AGAIN — net/unit must subtract both sales tax
        # and broker fee, not tax alone.
        fn = _sim_fn("_updateBuildPeekSim")
        assert "(1-stax-bfee)" in fn.replace(" ", "")

    def test_profit_scoped_to_remaining_plus_realized(self):
        # Emphasis on the unsold units; realized profit is added for the batch
        # total so the user still sees whether the whole run stays positive.
        fn = _sim_fn("_updateBuildPeekSim")
        assert "remaining" in fn
        assert "rz.profit" in fn          # realized-so-far folded into the total
        assert "totalProfit" in fn

    def test_break_even_warning_both_ways(self):
        # Above break-even reads as a win; below it must flag losing money.
        fn = _sim_fn("_updateBuildPeekSim")
        assert "above break-even" in fn
        assert "below break-even" in fn

    def test_shows_both_settle_for_less_floors(self):
        # Instant dump into buy orders (bid, tax only — NO broker) and the
        # optimistic undercut-best-ask case, plus the give-up delta.
        fn = _sim_fn("_updateBuildPeekSim")
        assert "buy orders" in fn                 # instant dump floor
        assert "Undercut best ask" in fn          # undercut floor
        assert "gives up" in fn                   # explicit give-up vs list price
        # The instant floor is bid × (1 - sales tax), with no broker fee applied.
        compact = fn.replace(" ", "")
        assert "bid*(1-stax)" in compact

    def test_simulator_seeds_reference_ticks(self):
        # The slider offers snap chips for break-even, live best ask, an
        # undercut, and the frozen price.
        fn = _sim_fn("_renderBuildPeekSim")
        assert "Break-even" in fn
        assert "Best ask" in fn
        assert "Undercut" in fn
        assert "Frozen" in fn


class TestOwnerFees:
    def test_fees_recomputed_from_owning_characters_skills(self):
        # The sim must use the OWNING character's live skill-derived rates, not
        # the frozen snapshot — resolved via _peekOwnerFees / _peekOwnerChar.
        assert "function _peekOwnerFees(" in _CHAR_JS
        assert "function _peekOwnerChar(" in _CHAR_JS
        fn = _sim_fn("_peekOwnerFees")
        # Same formulas as the auto-fill: tax 7.5%×(1−0.11×Accounting),
        # broker 3%−0.3%×Broker Relations.
        compact = fn.replace(" ", "")
        assert "7.5*(1-0.11*acc)" in compact
        assert "3.0-0.3*bro" in compact

    def test_owner_resolution_prefers_the_order_owner(self):
        # Resolve the owner by the linked order first, then the recorded
        # char_name, then the active character.
        fn = _sim_fn("_peekOwnerChar")
        assert "order_ids" in fn          # match the order's owner first
        assert "char_name" in fn          # then the char who ran the job
        assert "active_char_id" in fn     # else the active character

    def test_falls_back_to_snapshot_when_skills_unavailable(self):
        # No character / missing skill level → use the frozen snapshot rate so
        # the sim always has usable numbers.
        fn = _sim_fn("_peekOwnerFees")
        assert "snapTax" in fn and "snapBroker" in fn

    def test_open_uses_recomputed_fees_not_snapshot(self):
        # openBuildPeek must source stax/bfee from _peekOwnerFees, not straight
        # off the snapshot.
        fn = _sim_fn("openBuildPeek")
        assert "_peekOwnerFees(b)" in fn
        assert "fees.stax" in fn and "fees.bfee" in fn

    def test_reprice_tab_surfaces_whose_fees(self):
        # The user should see which character's fees the sim is using.
        assert "build-peek-fees" in _CHAR_JS
        assert "skills" in _CHAR_JS  # "from <name>'s skills"


class TestLiveFetch:
    def test_fetches_live_on_open_with_a_visible_indicator(self):
        # Prices are fetched live when the modal opens, and the user sees a
        # "fetching" state until the quote lands.
        assert "_fetchBuildPeekLive" in _CHAR_JS
        assert "refresh_prices" in _sim_fn("_fetchBuildPeekLive")
        assert "fetching" in _CHAR_JS.lower()
        # The drift table's "now" cells start in a loading state.
        assert 'bpr-load' in _CSS

    def test_stale_response_is_dropped(self):
        # A late response for a since-closed/swapped modal must be ignored.
        fn = _sim_fn("_fetchBuildPeekLive")
        assert "_buildPeekId!==id" in fn.replace(" ", "")
