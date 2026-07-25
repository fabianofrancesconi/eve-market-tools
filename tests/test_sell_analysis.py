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


def _bind(monkeypatch, acct, book, daily):
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)
    monkeypatch.setattr(lp_web, "fetch_orderbook_jita",
                        lambda *a, **k: book)
    monkeypatch.setattr(lp_web, "fetch_history_volumes",
                        lambda type_ids, *a, **k: {list(type_ids)[0]: daily})


class TestSellAnalysis:
    def test_returns_book_volume_and_probability(self, monkeypatch):
        book = [[100.0, 5], [101.0, 3], [102.0, 10]]
        _bind(monkeypatch, _acct(), book, daily=50.0)
        out = lp_web.do_ind_sell_analysis(
            {"type_id": ["587"], "price": ["101"], "qty": ["4"]})
        assert out["sell_book"] == book
        assert out["daily_volume"] == 50.0
        assert out["best_ask"] == 100.0
        assert out["sell_orders_total"] == 18
        assert out["units_ahead"] == 8          # units at/below 101
        assert 0.0 <= out["probability"]["all"] <= out["probability"]["any"] <= 1.0

    def test_defaults_station_to_jita_and_qty_to_one(self, monkeypatch):
        _bind(monkeypatch, _acct(), [[10.0, 1]], daily=5.0)
        out = lp_web.do_ind_sell_analysis({"type_id": ["34"]})
        assert out["station_id"] == lp_web.JITA_STATION_ID
        assert out["qty"] == 1
        assert out["price"] is None
        # No candidate price → no queue position to compute (units_ahead None),
        # but the model still returns odds (treating the queue as empty).
        assert out["units_ahead"] is None
        assert out["probability"]["any"] is not None

    def test_unknown_history_yields_null_probability(self, monkeypatch):
        _bind(monkeypatch, _acct(), [[10.0, 1]], daily=None)
        out = lp_web.do_ind_sell_analysis({"type_id": ["34"], "price": ["10"]})
        assert out["daily_volume"] is None
        assert out["probability"] == {"any": None, "all": None, "eta_days": None}

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
