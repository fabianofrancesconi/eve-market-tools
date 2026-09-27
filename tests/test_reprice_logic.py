"""Behavioural tests for the Listed-stage Call (static/js/ind.js): hold / re-price /
dump, and break-even.

The Call is pure JS math over the decider's cached market read, so we extract the
functions from source and run them under node (skips cleanly if node isn't
installed). What these pin down — each was a real way the old Call misled:

  * Your OWN sell order sits in the station book the decider reads. Left in, it
    counted your units as "ahead of you" and made your own price the best ask, so
    the Call told you to undercut yourself.
  * The relist broker fee is charged up front on the whole order — it must be
    subtracted in full, not scaled by the odds of selling.
  * Undercutting below break-even (cost + both broker fees, after tax) is a loss:
    it's a hard stop, never a "re-price".
  * A thin spread (buy orders pay ~your price for the whole lot) → dump.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()
_CHAR_JS = (_ROOT / "static" / "js" / "char.js").read_text()
_LP_JS = (_ROOT / "static" / "js" / "lp.js").read_text()


def _extract_fn(src, name):
    """Brace-match `function <name>(...) { ... }` out of `src`."""
    start = src.index("function " + name + "(")
    # Skip the parameter list first — it may hold a destructuring `{...}`.
    depth, i = 0, src.index("(", start)
    while True:
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        if depth == 0:
            break
        i += 1
    depth, i = 0, src.index("{", i)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match %s" % name)


def _extract_const(src, name):
    start = src.index("const " + name + "=")
    return src[start:src.index(";", start) + 1]


_CHAR_FNS = ["_unitsAheadInQueue", "_priceConditionedDailyRate", "_sellThroughProb",
             "_demandSurvivals", "_erfc"]
_CHAR_CONSTS = ["_HISTORY_WINDOW_DAYS", "_DEMAND_DISPERSION", "_NORMAL_APPROX_MEAN"]
_IND_FNS = ["_bookWithoutOwn", "_expectedUnitsSold", "_repricePaysOff", "_callVerdict",
            "_deciderBook", "_linkedOrderStanding", "_buildListedOrderPrice",
            "_listedUnderBE", "_listedRead", "_tileActionFlag", "_dumpQuote"]


def _lib():
    parts = [_extract_const(_CHAR_JS, c) for c in _CHAR_CONSTS]
    parts += [_extract_fn(_CHAR_JS, f) for f in _CHAR_FNS]
    parts += [_extract_fn(_LP_JS, "walkBook")]
    parts += [_extract_fn(_IND_JS, f) for f in _IND_FNS]
    return "\n".join(parts)


def _run(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = _lib() + "\n" + body + "\n"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _call(fn, *args):
    return _run("process.stdout.write(JSON.stringify(%s(%s)));"
                % (fn, ", ".join(json.dumps(a) for a in args)))


# 30 days of history trading 100 units/day across [low, high].
def _series(low, high, vol=100, days=30):
    return [{"low": low, "high": high, "average": (low + high) / 2, "volume": vol}
            for _ in range(days)]


# ── pure helpers ─────────────────────────────────────────────────────────────

class TestBookWithoutOwn:
    def test_strips_own_units_at_own_price(self):
        book = [[100.0, 50], [101.0, 30]]
        assert _call("_bookWithoutOwn", book, 100.0, 50) == [[101.0, 30]]

    def test_leaves_other_sellers_at_the_same_price(self):
        book = [[100.0, 80], [101.0, 30]]
        assert _call("_bookWithoutOwn", book, 100.0, 50) == [[100.0, 30], [101.0, 30]]

    def test_other_prices_untouched(self):
        book = [[99.0, 10], [101.0, 30]]
        assert _call("_bookWithoutOwn", book, 100.0, 50) == book

    def test_no_order_passes_through(self):
        book = [[99.0, 10]]
        assert _call("_bookWithoutOwn", book, None, 50) == book


class TestExpectedUnitsSold:
    def test_bounded_by_qty(self):
        r = _call("_expectedUnitsSold", 0, 1000, 10, 7)
        assert 9.9 <= r <= 10

    def test_queue_ahead_reduces_sales(self):
        front = _call("_expectedUnitsSold", 0, 10, 50, 7)
        behind = _call("_expectedUnitsSold", 500, 10, 50, 7)
        assert behind < front

    def test_unknown_demand_is_null(self):
        assert _call("_expectedUnitsSold", 0, None, 10, 7) is None


def _ctx(**kw):
    base = dict(curPrice=1_700_000, compAsk=1_490_000, ahead=40, curRate=0.2,
                cpu=1_000_000, stax=0.036, bfee=0.015, series=_series(1_400_000, 1_600_000, 5),
                qty=20, horizon=7, residual=0)
    base.update(kw)
    return base


class TestRepricePaysOff:
    def test_overpriced_against_a_liquid_market_reprices(self):
        # Listed at 1.7M while the market trades 1.4–1.6M and a competitor asks
        # 1.49M: undercutting sells far more this week and stays above break-even.
        r = _call("_repricePaysOff", _ctx())
        assert r["candidate"] and r["worth"]
        assert r["target"] == pytest.approx(1_490_000 * 0.9999)
        assert not r["belowBE"]
        assert r["eRep"] > r["eHold"]

    def test_no_one_cheaper_is_not_a_candidate(self):
        # You're the cheapest ask: there is nobody to undercut.
        r = _call("_repricePaysOff", _ctx(compAsk=1_800_000))
        assert not r["candidate"] and not r["worth"]

    def test_below_break_even_is_a_hard_stop(self):
        # Cost 1.45M/unit: undercutting to 1.49M doesn't cover cost + two broker
        # fees after tax, so it's never "worth" it however fast it'd sell.
        r = _call("_repricePaysOff", _ctx(cpu=1_450_000))
        assert r["candidate"] and r["belowBE"] and not r["worth"]
        # repBE = (cpu + bfee·curPrice) / (1 − tax − bfee)
        assert r["repBE"] == pytest.approx((1_450_000 + 0.015 * 1_700_000) / (1 - 0.036 - 0.015))

    def test_relist_fee_is_charged_in_full(self):
        r = _call("_repricePaysOff", _ctx())
        assert r["fee"] == pytest.approx(0.015 * r["target"] * 20)

    def test_a_wash_holds(self):
        # A hair-cheaper competitor in a market that trades well above both prices:
        # both sell the same, so paying a new broker fee buys nothing.
        r = _call("_repricePaysOff", _ctx(curPrice=1_500_000, compAsk=1_499_000, ahead=0,
                                          curRate=50, series=_series(1_600_000, 1_700_000, 50)))
        assert r["candidate"] and not r["worth"]

    def test_unknown_demand_is_candidate_but_not_worth(self):
        r = _call("_repricePaysOff", _ctx(curRate=None))
        assert r["candidate"] and not r["worth"] and r["gain"] is None


class TestCallVerdict:
    def _v(self, **kw):
        base = dict(noOrder=False, reprice={"candidate": False}, offHub=None,
                    atFront=False, noHistory=False, thinSpread=False, weekAll=0.9)
        base.update(kw)
        return _call("_callVerdict", base)

    def test_no_order(self):
        assert self._v(noOrder=True)["kind"] == "noorder"

    def test_off_hub_uses_its_own_standing(self):
        assert self._v(offHub={"is_best": True})["kind"] == "front"
        v = self._v(offHub={"is_best": False, "rank": 3, "total": 5},
                    reprice={"candidate": True, "worth": True})
        assert v["kind"] == "offhub" and v["action"] is None

    def test_thin_spread_dumps(self):
        v = self._v(thinSpread=True)
        assert v["kind"] == "dump" and v["action"] == "dump"

    def test_reprice_branches(self):
        assert self._v(reprice={"candidate": True, "worth": True})["action"] == "reprice"
        assert self._v(reprice={"candidate": True, "worth": False})["kind"] == "fee"
        v = self._v(reprice={"candidate": True, "worth": False, "belowBE": True})
        assert v["kind"] == "underbe" and v["action"] is None

    def test_no_history_never_reprices(self):
        assert self._v(noHistory=True, reprice={"candidate": True, "worth": True})["kind"] == "nohistory"

    def test_holds(self):
        assert self._v(atFront=True)["kind"] == "front"
        assert self._v(weekAll=0.1)["kind"] == "slow"
        assert self._v()["kind"] == "hold"


# ── the full Listed read, with the decider's context stubbed ─────────────────

def _read(order, book, series, cpu=1_000_000, qty=20, bid=None, buy_book=None,
          other_orders=()):
    """Run _listedRead / _tileActionFlag for one listed build whose live order is
    `order` (or None), over the station `book` (which, like the server's, INCLUDES
    your own order)."""
    orders = ([order] if order else []) + list(other_orders)
    env = {
        "order": order, "orders": orders,
        "market": {"station_id": 60003760, "sell_book": book, "series": series},
        "live": {"ask": book[0][0] if book else None, "bid": bid,
                 "buy_book": buy_book if buy_book is not None else []},
        "cpu": cpu, "qty": qty, "bid": bid,
    }
    body = """
const E=%s;
const IND={decider:{b1:{marketState:"done", market:E.market, live:E.live}}};
const fmtISKFull=v=>String(v);
const stax=0.036, bfee=0.015;
function _deciderCtx(b){ return {s:{ask:null, bid:E.bid}, fees:{stax, bfee}, cpu:E.cpu,
  remaining:E.qty, be:{list:E.cpu/(1-stax-bfee), instant:E.cpu/(1-stax)}}; }
function _peekLinkedOrder(b){ return E.order; }
function _peekChars(){ return [{market_orders:E.orders}]; }
const b={id:"b1", product_type_id:34};
const r=_listedRead(b);
process.stdout.write(JSON.stringify({r, flag:_tileActionFlag(b)}));
""" % json.dumps(env)
    return _run(body)


def _order(price, vol=20):
    return {"type_id": 34, "price": price, "volume_remain": vol,
            "location_id": 60003760, "is_buy_order": False}


class TestListedRead:
    def test_own_order_is_not_a_competitor(self):
        # You're the only cheap seller: the book's best ask IS your order. The old
        # Call counted your 20 units as "ahead" and told you to undercut yourself.
        book = [[1_500_000.0, 20], [1_600_000.0, 100]]
        out = _read(_order(1_500_000.0), book, _series(1_450_000, 1_550_000, 50))
        r = out["r"]
        assert r["compAsk"] == 1_600_000.0
        assert not r["reprice"]["candidate"]
        assert r["v"]["kind"] == "front"
        assert "cheapest listing" in r["queueShort"]
        assert out["flag"] is None

    def test_your_other_orders_are_stripped_too(self):
        # A second order of yours (another character) at 1.45M is not a competitor.
        book = [[1_450_000.0, 5], [1_500_000.0, 20], [1_600_000.0, 100]]
        other = dict(_order(1_450_000.0, 5))
        out = _read(_order(1_500_000.0), book, _series(1_450_000, 1_550_000, 50),
                    other_orders=[other])
        assert out["r"]["compAsk"] == 1_600_000.0
        assert not out["r"]["reprice"]["candidate"]

    def test_overpriced_listing_reprices_and_flags(self):
        book = [[1_490_000.0, 40], [1_700_000.0, 20]]
        out = _read(_order(1_700_000.0), book, _series(1_400_000, 1_600_000, 5))
        r = out["r"]
        assert r["compAsk"] == 1_490_000.0
        assert r["v"]["kind"] == "reprice"
        assert out["flag"]["action"] == "reprice"

    def test_reprice_below_break_even_holds(self):
        book = [[1_490_000.0, 40], [1_700_000.0, 20]]
        out = _read(_order(1_700_000.0), book, _series(1_400_000, 1_600_000, 5),
                    cpu=1_450_000)
        assert out["r"]["v"]["kind"] == "underbe"
        assert out["flag"] is None      # listed price itself is above break-even

    def test_listed_under_break_even_is_flagged(self):
        # Your order sits under cost + fees: every sale loses money, whatever the Call.
        book = [[900_000.0, 20], [1_600_000.0, 100]]
        out = _read(_order(900_000.0), book, _series(850_000, 950_000, 50))
        assert out["r"]["underBE"] == pytest.approx(1_000_000 / (1 - 0.036 - 0.015))
        assert out["flag"]["action"] == "underbe"
        assert "break-even" in out["flag"]["tip"]

    def test_thin_spread_dumps(self):
        book = [[1_500_000.0, 20], [1_600_000.0, 100]]
        out = _read(_order(1_500_000.0), book, _series(1_450_000, 1_550_000, 50),
                    bid=1_495_000.0, buy_book=[[1_495_000.0, 100, 1]])
        assert out["r"]["v"]["kind"] == "dump"
        assert out["flag"]["action"] == "dump"

    def test_no_order_makes_no_call(self):
        book = [[1_500_000.0, 20]]
        out = _read(None, book, _series(1_450_000, 1_550_000, 50))
        assert out["r"]["v"]["kind"] == "noorder"
        assert out["flag"] is None
