"""Tests for the /api/ind/sell-analysis endpoint (do_ind_sell_analysis): the
sell-book + daily-volume fetch plumbing and the probability wiring that the
tracked-build modal's Market tab renders. The pure model itself
(units_ahead_in_queue / sell_through_probability) is covered in test_ind_core.py.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Tester"}
    a.active_char_id = 1
    return a


def _series_for(daily):
    """A 30-day history series whose per-day volume averages `daily`, trading in a
    wide 1..1e12 range so any realistic list price sits inside it (price filter is
    exercised separately). None `daily` → no history at all."""
    if daily is None:
        return None
    return [{"volume": daily, "low": 1.0, "high": 1e12, "average": daily}
            for _ in range(30)]


def _bind(monkeypatch, acct, book, daily, series="auto"):
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)
    monkeypatch.setattr(lp_web, "fetch_orderbook_jita",
                        lambda *a, **k: book)
    monkeypatch.setattr(lp_web, "fetch_history_volumes",
                        lambda type_ids, *a, **k: {list(type_ids)[0]: daily})
    ser = _series_for(daily) if series == "auto" else series
    monkeypatch.setattr(lp_web, "fetch_history_series",
                        lambda type_ids, *a, **k: {list(type_ids)[0]: ser})


class TestSellAnalysis:
    def test_returns_book_volume_and_curve(self, monkeypatch):
        book = [[100.0, 5], [101.0, 3], [102.0, 10]]
        _bind(monkeypatch, _acct(), book, daily=50.0)
        out = lp_web.do_ind_sell_analysis(
            {"type_id": ["587"], "price": ["101"], "qty": ["4"]})
        assert out["sell_book"] == book
        assert out["daily_volume"] == 50.0
        assert out["best_ask"] == 100.0
        assert out["sell_orders_total"] == 18
        assert out["units_ahead"] == 8          # units at/below 101
        # The whole EVE-duration curve comes back, ordered and bounded.
        assert [r["days"] for r in out["curve"]] == [1, 3, 7, 14, 30, 90]
        for r in out["curve"]:
            assert 0.0 <= r["all"] <= r["any"] <= 1.0
        # Price sits far below the wide range → ~full demand rate is credited.
        assert out["price_daily_rate"] == pytest.approx(50.0)

    def test_defaults_station_to_jita_and_qty_to_one(self, monkeypatch):
        _bind(monkeypatch, _acct(), [[10.0, 1]], daily=5.0)
        out = lp_web.do_ind_sell_analysis({"type_id": ["34"]})
        assert out["station_id"] == lp_web.JITA_STATION_ID
        assert out["qty"] == 1
        assert out["price"] is None
        # No candidate price → no queue position (units_ahead None) and the rate is
        # the unconditioned full rate; the curve still comes back populated.
        assert out["units_ahead"] is None
        assert out["curve"][0]["any"] is not None

    def test_unknown_history_yields_null_curve(self, monkeypatch):
        _bind(monkeypatch, _acct(), [[10.0, 1]], daily=None)
        out = lp_web.do_ind_sell_analysis({"type_id": ["34"], "price": ["10"]})
        assert out["daily_volume"] is None
        assert out["series"] is None
        assert out["price_daily_rate"] is None
        assert all(r["all"] is None and r["any"] is None for r in out["curve"])

    def test_high_price_drops_the_rate_and_odds(self, monkeypatch):
        # A price above everything the market recently paid → ~zero demand rate and
        # a near-zero curve even at the longest horizon. This is the whole point.
        series = [{"volume": 100, "low": 90.0, "high": 110.0, "average": 100.0}
                  for _ in range(30)]
        _bind(monkeypatch, _acct(), [[100.0, 1]], daily=100.0, series=series)
        out = lp_web.do_ind_sell_analysis(
            {"type_id": ["34"], "price": ["200"], "qty": ["1"]})
        assert out["price_daily_rate"] == 0.0
        assert out["curve"][-1]["all"] == 0.0   # 3 months, still ~never

    def test_bad_station_falls_back_to_jita(self, monkeypatch):
        _bind(monkeypatch, _acct(), [], daily=1.0)
        out = lp_web.do_ind_sell_analysis(
            {"type_id": ["34"], "station": ["99999999"]})
        assert out["station_id"] == lp_web.JITA_STATION_ID
        assert out["best_ask"] is None

    def test_requires_login(self, monkeypatch):
        monkeypatch.setattr(lp_web, "current_account", lambda: None)
        with pytest.raises(lp_web.LPError):
            lp_web.do_ind_sell_analysis({"type_id": ["34"]})
