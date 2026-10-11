"""Price history modal (static/js/pricehist.js), opened from the Industry
planner's detail header.

The analysis helpers are pure, so they run under node against hand-built ESI
history. Every expected value below is worked out from the definition (VWAP,
Wilder's RSI, calendar-window averages, ...) rather than read back from the
code. A final block checks the planner wiring without a browser."""
import datetime
import importlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")

_ROOT = Path(__file__).resolve().parent.parent
_PH_JS = (_ROOT / "static" / "js" / "pricehist.js").read_text()
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()


def _run(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", _PH_JS + "\n" + body + "\n"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _call(fn, *args):
    return _run("process.stdout.write(JSON.stringify(%s(%s)));"
                % (fn, ", ".join(json.dumps(a) for a in args)))


def _day(date, avg, vol=10, hi=None, lo=None, orders=1):
    return {"date": date, "average": avg, "highest": hi if hi is not None else avg,
            "lowest": lo if lo is not None else avg, "volume": vol, "order_count": orders}


def _days(start, prices, vol=10):
    d0 = datetime.date.fromisoformat(start)
    return [_day((d0 + datetime.timedelta(days=i)).isoformat(), p, vol)
            for i, p in enumerate(prices)]


# ── bucketing ────────────────────────────────────────────────────────────────

class TestBucket:
    def test_weekly_bars_start_on_monday_and_are_volume_weighted(self):
        hist = [_day("2026-10-05", 100, vol=10, hi=120, lo=90, orders=2),   # Monday
                _day("2026-10-07", 200, vol=30, hi=210, lo=80, orders=3),
                _day("2026-10-11", 150, vol=0, orders=0),                     # Sunday, same week
                _day("2026-10-12", 50, vol=5)]                                # next Monday
        bars = _call("_phBucket", hist, "week")
        assert [b["date"] for b in bars] == ["2026-10-05", "2026-10-12"]
        w = bars[0]
        # (100*10 + 200*30 + 150*0) / 40 units
        assert w["avg"] == pytest.approx(175.0)
        assert (w["open"], w["close"]) == (100, 150)
        assert (w["high"], w["low"]) == (210, 80)
        assert (w["volume"], w["orders"], w["days"]) == (40, 5, 3)

    def test_zero_volume_bucket_falls_back_to_plain_mean(self):
        hist = [_day("2026-10-05", 100, vol=0), _day("2026-10-06", 200, vol=0)]
        assert _call("_phBucket", hist, "week")[0]["avg"] == pytest.approx(150.0)

    def test_monthly_split_on_the_first(self):
        hist = [_day("2026-09-30", 10), _day("2026-10-01", 20)]
        bars = _call("_phBucket", hist, "month")
        assert [b["date"] for b in bars] == ["2026-09-01", "2026-10-01"]

    def test_daily_is_one_bar_per_day(self):
        hist = _days("2026-10-01", [1, 2, 3])
        bars = _call("_phBucket", hist, "day")
        assert [b["avg"] for b in bars] == [1, 2, 3]


# ── indicators ───────────────────────────────────────────────────────────────

class TestTimeSma:
    def test_three_day_average_needs_three_days_of_history(self):
        bars = _call("_phBucket", _days("2026-10-01", [1, 2, 3, 4, 5]), "day")
        assert _call("_phTimeSma", bars, 3) == [None, None, 2, 3, 4]

    def test_gap_days_do_not_stretch_the_window(self):
        # Trades on Oct 1, 2 and 10 only. The 7-day window ending Oct 10 covers
        # Oct 4–10, which holds just the Oct 10 trade.
        hist = [_day("2026-10-01", 10), _day("2026-10-02", 20), _day("2026-10-10", 30)]
        bars = _call("_phBucket", hist, "day")
        assert _call("_phTimeSma", bars, 7)[-1] == pytest.approx(30)


class TestRsi:
    def test_wilder_smoothing(self):
        # n=2: first moves +1, -1 -> avg gain .5, avg loss .5 -> RSI 50.
        # Next move +1: gain (.5*1+1)/2=.75, loss (.5*1+0)/2=.25 -> RS 3 -> RSI 75.
        assert _call("_phRsi", [1, 2, 1, 2], 2) == [None, None, 50, 75]

    def test_only_rising_is_100_and_flat_is_50(self):
        assert _call("_phRsi", list(range(1, 17)), 14)[-1] == 100
        assert _call("_phRsi", [5] * 16, 14)[-1] == 50

    def test_too_short_is_all_null(self):
        assert _call("_phRsi", [1, 2, 3], 14) == [None, None, None]


# ── range + interval ─────────────────────────────────────────────────────────

class TestSlice:
    def test_daily_range_keeps_last_n_calendar_days(self):
        bars = _call("_phBucket", _days("2026-09-01", list(range(1, 31))), "day")
        out = _call("_phSlice", bars, "7", "day")
        assert [b["date"] for b in out] == ["2026-09-%02d" % d for d in range(24, 31)]

    def test_daily_range_with_gap_does_not_reach_outside(self):
        # Trades Sep 1 and Sep 30. A 7-day range is Sep 24–30: Sep 1 is out.
        hist = [_day("2026-09-01", 1), _day("2026-09-30", 2)]
        bars = _call("_phBucket", hist, "day")
        assert [b["date"] for b in _call("_phSlice", bars, "7", "day")] == ["2026-09-30"]

    def test_weekly_keeps_the_bar_holding_the_cutoff(self):
        # Last day Sun Oct 11; a 30-day range starts Sat Sep 12, which falls in
        # the week of Mon Sep 7 — that week belongs, the one before does not.
        hist = _days("2026-08-31", [1] * 42)          # Aug 31 (Mon) .. Oct 11 (Sun)
        bars = _call("_phBucket", hist, "week")
        out = _call("_phSlice", bars, "30", "week")
        assert [b["date"] for b in out] == ["2026-09-07", "2026-09-14", "2026-09-21",
                                            "2026-09-28", "2026-10-05"]

    def test_all_keeps_everything(self):
        bars = _call("_phBucket", _days("2026-09-01", [1, 2, 3]), "day")
        assert len(_call("_phSlice", bars, "all", "day")) == 3


@pytest.mark.parametrize("rng,iv,want", [
    ("7", "week", "day"), ("30", "week", "week"), ("90", "month", "day"),
    ("180", "month", "month"), ("all", "month", "month"), ("7", "day", "day"),
])
def test_coarse_interval_needs_a_long_enough_range(rng, iv, want):
    assert _call("_phCoerceInterval", rng, iv) == want


def test_view_warms_up_averages_before_the_window():
    # 60 days of history, 7-day range: the 30d average already exists on the
    # first visible day because it is computed on the full series.
    v = _call("_phView", _days("2026-08-01", [100] * 60), "7", "day")
    assert len(v["bars"]) == 7
    assert v["maL"][0] == pytest.approx(100)
    assert v["prev"]["date"] == "2026-09-22"


# ── momentum read-out ────────────────────────────────────────────────────────

class TestMomentum:
    def test_jump_after_a_flat_year(self):
        hist = _days("2025-09-01", [100] * 399 + [110])
        m = _call("_phMomentum", hist)
        for n in ("1", "7", "30", "90", "365"):
            assert m["changes"][n] == pytest.approx(10.0)
        # Equal volume each day: 7-day VWAP = (6*100 + 110) / 7.
        avg7 = (6 * 100 + 110) / 7
        assert m["avg7"] == pytest.approx(avg7)
        assert m["vsAvg7"] == pytest.approx((110 - avg7) / avg7 * 100)
        assert m["range30"] == pytest.approx(100.0)       # sits at the 30-day high
        assert m["rsi"] == pytest.approx(100.0)           # only an up-move in the window
        assert m["vol7"] == pytest.approx(10) and m["volTrend"] == pytest.approx(0)
        assert m["iskDay30"] == pytest.approx((29 * 100 * 10 + 110 * 10) / 30)
        assert m["tradeDays30"] == 30

    def test_short_history_leaves_long_changes_empty(self):
        m = _call("_phMomentum", _days("2026-09-20", [100] * 20))
        assert m["changes"]["7"] == pytest.approx(0)
        assert m["changes"]["30"] is None and m["changes"]["365"] is None

    def test_trend_recovers_a_steady_daily_growth_rate(self):
        prices = [100 * 1.01 ** i for i in range(30)]
        m = _call("_phMomentum", _days("2026-09-01", prices))
        assert m["trendWeek"] == pytest.approx((1.01 ** 7 - 1) * 100)
        assert m["volatility"] == pytest.approx(0, abs=1e-9)  # identical moves

    def test_volatility_is_sample_stdev_of_log_returns(self):
        prices = [100 if i % 2 == 0 else 110 for i in range(30)]
        rets = [math.log(prices[i + 1] / prices[i]) for i in range(29)]
        m = _call("_phMomentum", _days("2026-09-01", prices))
        assert m["volatility"] == pytest.approx(statistics.stdev(rets) * 100)

    def test_volume_rates_use_calendar_days(self):
        # 30 days, 20 units/day for the first 23 then 0 for the last 7.
        hist = _days("2026-09-01", [100] * 30)
        for i, h in enumerate(hist):
            h["volume"] = 20 if i < 23 else 0
        m = _call("_phMomentum", hist)
        assert m["vol7"] == 0
        assert m["vol30"] == pytest.approx(23 * 20 / 30)
        assert m["volTrend"] == pytest.approx(-100)

    def test_empty_history(self):
        assert _call("_phMomentum", []) is None


# ── planner wiring ───────────────────────────────────────────────────────────

def test_detail_header_has_price_history_button():
    assert 'class="ind-hist-btn"' in _IND_JS
    assert "📈 Price history" in _IND_JS
    assert "openPriceHistory({" in _IND_JS


def test_price_history_script_is_loaded():
    assert '/static/js/pricehist.js' in lp_web.INDEX_HTML
    assert "function openPriceHistory(" in lp_web.FRONTEND_SOURCE


def test_open_modal_blocks_build_card_escape():
    assert "#phModal:not(.hidden)" in _IND_JS
