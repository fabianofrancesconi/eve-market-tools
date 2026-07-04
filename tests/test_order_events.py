"""Tests for market order change tracking (sale/fill events)."""
import time

import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
lp_web = importlib.import_module("lp-web")


class TestTrackOrderChanges:
    """Unit tests for _track_order_changes."""

    def test_first_sync_no_events(self, monkeypatch, tmp_path):
        """First sync (no previous orders) should produce zero events."""
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", tmp_path / "ev.json")
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})
        orders = [
            {"order_id": 100, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 1000, "volume_total": 1000, "price": 5.0,
             "is_buy_order": False},
        ]
        events, last_sales = lp_web._track_order_changes(1, orders, {})
        assert events == []
        assert last_sales == {}

    def test_partial_sell_creates_event(self, monkeypatch, tmp_path):
        """When volume_remain decreases, a partial-sell event is recorded."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        # First sync: seed the baseline
        orders_t0 = [
            {"order_id": 100, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 1000, "volume_total": 1000, "price": 5.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t0, {})

        # Second sync: 200 units sold
        orders_t1 = [
            {"order_id": 100, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 800, "volume_total": 1000, "price": 5.0,
             "is_buy_order": False},
        ]
        events, last_sales = lp_web._track_order_changes(1, orders_t1, {})
        assert len(events) == 1
        assert events[0]["sold"] == 200
        assert events[0]["filled"] is False
        assert events[0]["type_name"] == "Tritanium"
        assert events[0]["price"] == 5.0
        # last_sales tracks the active order
        assert "100" in last_sales
        assert last_sales["100"]["sold"] == 200

    def test_fully_filled_creates_event(self, monkeypatch, tmp_path):
        """When an order disappears entirely, a filled event is recorded."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        # Seed
        orders_t0 = [
            {"order_id": 200, "type_id": 35, "type_name": "Pyerite",
             "volume_remain": 50, "volume_total": 100, "price": 10.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t0, {})

        # Order gone
        events, last_sales = lp_web._track_order_changes(1, [], {})
        assert len(events) == 1
        assert events[0]["sold"] == 50
        assert events[0]["filled"] is True
        # last_sales should NOT include a filled order
        assert "200" not in last_sales

    def test_no_change_no_event(self, monkeypatch, tmp_path):
        """If volume_remain doesn't change, no event is created."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        orders = [
            {"order_id": 300, "type_id": 36, "type_name": "Mexallon",
             "volume_remain": 500, "volume_total": 500, "price": 50.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders, {})
        events, _ = lp_web._track_order_changes(1, orders, {})
        assert events == []

    def test_volume_increase_no_event(self, monkeypatch, tmp_path):
        """If volume_remain increases (e.g. order modified), no sale event."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        orders_t0 = [
            {"order_id": 400, "type_id": 37, "type_name": "Isogen",
             "volume_remain": 100, "volume_total": 100, "price": 70.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t0, {})

        orders_t1 = [
            {"order_id": 400, "type_id": 37, "type_name": "Isogen",
             "volume_remain": 200, "volume_total": 200, "price": 70.0,
             "is_buy_order": False},
        ]
        events, _ = lp_web._track_order_changes(1, orders_t1, {})
        assert events == []

    def test_multiple_orders_tracked(self, monkeypatch, tmp_path):
        """Multiple orders can generate events in one sync."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        orders_t0 = [
            {"order_id": 500, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 100, "volume_total": 100, "price": 5.0,
             "is_buy_order": False},
            {"order_id": 501, "type_id": 35, "type_name": "Pyerite",
             "volume_remain": 50, "volume_total": 50, "price": 10.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t0, {})

        # Both sell some
        orders_t1 = [
            {"order_id": 500, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 80, "volume_total": 100, "price": 5.0,
             "is_buy_order": False},
            {"order_id": 501, "type_id": 35, "type_name": "Pyerite",
             "volume_remain": 30, "volume_total": 50, "price": 10.0,
             "is_buy_order": False},
        ]
        events, _ = lp_web._track_order_changes(1, orders_t1, {})
        assert len(events) == 2
        sold_amounts = sorted(e["sold"] for e in events)
        assert sold_amounts == [20, 20]

    def test_events_accumulate_across_syncs(self, monkeypatch, tmp_path):
        """Events from prior syncs persist (not overwritten)."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        orders_t0 = [
            {"order_id": 600, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 100, "volume_total": 100, "price": 5.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t0, {})

        # First sale
        orders_t1 = [
            {"order_id": 600, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 80, "volume_total": 100, "price": 5.0,
             "is_buy_order": False},
        ]
        lp_web._track_order_changes(1, orders_t1, {})

        # Second sale
        orders_t2 = [
            {"order_id": 600, "type_id": 34, "type_name": "Tritanium",
             "volume_remain": 50, "volume_total": 100, "price": 5.0,
             "is_buy_order": False},
        ]
        events, _ = lp_web._track_order_changes(1, orders_t2, {})
        assert len(events) == 2
        assert events[0]["sold"] == 20
        assert events[1]["sold"] == 30

    def test_expired_events_cleaned(self, monkeypatch, tmp_path):
        """Events older than ORDER_EVENT_EXPIRY are dropped."""
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {1: {"name": "Tester"}})

        # Manually seed an old event
        old_event = {
            "id": "999_old", "ts": time.time() - 8 * 86400,
            "order_id": 999, "type_name": "Old Item", "sold": 10,
            "price": 1.0, "is_buy_order": False, "filled": True,
            "character_name": "Tester", "dismissed": False,
        }
        lp_web.save_json(evpath, {
            "1": [old_event],
            "_prev_1": {},
        })

        events, _ = lp_web._track_order_changes(1, [], {})
        assert len(events) == 0  # old event was cleaned


class TestGetOrderEvents:
    """Tests for _get_order_events."""

    def test_returns_non_dismissed_events(self, monkeypatch, tmp_path):
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        now = time.time()
        lp_web.save_json(evpath, {
            "1": [
                {"id": "a", "ts": now, "dismissed": False, "sold": 5},
                {"id": "b", "ts": now, "dismissed": True, "sold": 3},
            ],
            "_prev_1": {},
        })
        events = lp_web._get_order_events()
        assert len(events) == 1
        assert events[0]["id"] == "a"

    def test_excludes_expired(self, monkeypatch, tmp_path):
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        lp_web.save_json(evpath, {
            "1": [
                {"id": "old", "ts": time.time() - 8 * 86400, "dismissed": False, "sold": 1},
            ],
            "_prev_1": {},
        })
        assert lp_web._get_order_events() == []

    def test_aggregates_multiple_characters(self, monkeypatch, tmp_path):
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        now = time.time()
        lp_web.save_json(evpath, {
            "1": [{"id": "x", "ts": now - 10, "dismissed": False, "sold": 1}],
            "2": [{"id": "y", "ts": now - 5, "dismissed": False, "sold": 2}],
            "_prev_1": {}, "_prev_2": {},
        })
        events = lp_web._get_order_events()
        assert len(events) == 2
        # Sorted by ts descending (most recent first)
        assert events[0]["id"] == "y"
        assert events[1]["id"] == "x"


class TestDismissOrderEvent:
    """Tests for _dismiss_order_event."""

    def test_dismiss_single(self, monkeypatch, tmp_path):
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        now = time.time()
        lp_web.save_json(evpath, {
            "1": [
                {"id": "a", "ts": now, "dismissed": False, "sold": 5},
                {"id": "b", "ts": now, "dismissed": False, "sold": 3},
            ],
        })
        lp_web._dismiss_order_event("a")
        events = lp_web._get_order_events()
        assert len(events) == 1
        assert events[0]["id"] == "b"

    def test_dismiss_all(self, monkeypatch, tmp_path):
        evpath = tmp_path / "ev.json"
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", evpath)
        now = time.time()
        lp_web.save_json(evpath, {
            "1": [
                {"id": "a", "ts": now, "dismissed": False, "sold": 5},
                {"id": "b", "ts": now, "dismissed": False, "sold": 3},
            ],
            "2": [
                {"id": "c", "ts": now, "dismissed": False, "sold": 1},
            ],
        })
        lp_web._dismiss_order_event("all")
        assert lp_web._get_order_events() == []


class TestMaxSpreadClientSide:
    """The max_spread filter was moved from backend to frontend (v1.72.0).
    Verify the backend no longer filters by max_spread."""

    def test_high_spread_items_returned(self, monkeypatch, tmp_path):
        """Items with spread > max_spread should still be in the scan result."""
        from lp_core import evaluate

        offers = [
            {"type_id": 73227, "lp_cost": 100, "quantity": 1,
             "isk_cost": 0, "offer_id": 17404, "required_items": []},
        ]
        # Output item: sell 10M, buy 10K => 99.9% spread
        prices = {
            73227: {"sell_min": 10_000_000, "buy_max": 10_500, "sell_volume": 10, "buy_volume": 5},
        }
        sellable, unsellable = evaluate(offers, prices, 1000, 0.045, 0.015)
        assert len(sellable) == 1
        assert sellable[0]["spread_pct"] > 90
