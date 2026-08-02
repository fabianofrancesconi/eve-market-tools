"""Frontend-wiring checks for the Industry detail panel's live-price behaviour.

Two coupled changes:
 • The detail view always pulls live ESI prices on open (the old manual "Pull
   live prices" button is gone) — the table's row figures come from the laggy
   aggregate scan, so the panel refetches the real order book every time.
 • A discrepancy notice explains why the panel's INSTANT profit can differ from
   the table's: the table values instant off the aggregate top bid (ignoring each
   buy order's min_volume), while the panel walks the live book for the batch.

FRONTEND_SOURCE is index.html + all JS, so these assert the structure without a
browser."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_detail_open_always_refreshes_prices():
    src = lp_web.FRONTEND_SOURCE
    # openIndDetail's fetch now carries refresh_prices:"1".
    i = src.index("function openIndDetail(")
    body = src[i:i + 1200]
    assert 'refresh_prices:"1"' in body


def test_detail_open_shows_fetching_spinner():
    src = lp_web.FRONTEND_SOURCE
    # The loading placeholder spins and says prices are being fetched, since the
    # open now always waits on a live ESI order-book pull.
    i = src.index("function openIndDetail(")
    body = src[i:i + 1200]
    assert "_SPIN" in body
    assert "fetching live prices" in body


def test_pull_live_prices_button_is_gone():
    src = lp_web.FRONTEND_SOURCE
    # The manual button and its label are removed — pulling is automatic now.
    assert "ind-pull-prices" not in src
    assert "Pull live prices" not in src


def test_instant_discrepancy_warning_present():
    src = lp_web.FRONTEND_SOURCE
    # The gap is computed from reachable-vs-aggregate bid and rendered as a notice.
    assert "instBidWarn" in src
    assert "ind-instant-warn" in src
    # It contrasts the aggregate top bid against the reachable effBid.
    assert "differs from the table" in src


def test_instant_warning_styled():
    src = lp_web.FRONTEND_SOURCE
    assert ".ind-instant-warn" in src
