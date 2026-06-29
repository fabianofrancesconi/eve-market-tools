"""
Tests for the sell-order freshness layer (v1.14.0):

- _issued_age_seconds   -- ESI 'issued' ISO-8601 stamp -> age in seconds
- _fetch_station_orders -- raw paged orders filtered to one station
- fetch_sell_order_stats-- freshness of the current cheapest sell order
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import lp_core


def _now(iso):
    """Epoch for an ISO-8601 'Z' stamp, for deterministic age math."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# _issued_age_seconds
# ---------------------------------------------------------------------------

class TestIssuedAgeSeconds:
    def test_eight_hours_ago(self):
        now = _now("2024-01-01T12:00:00Z")
        assert lp_core._issued_age_seconds("2024-01-01T04:00:00Z", now) == 8 * 3600

    def test_missing_is_none(self):
        assert lp_core._issued_age_seconds(None, 0.0) is None

    def test_unparseable_is_none(self):
        assert lp_core._issued_age_seconds("not-a-date", 0.0) is None


# ---------------------------------------------------------------------------
# fetch_sell_order_stats
# ---------------------------------------------------------------------------

STATION = lp_core.JITA_STATION_ID
REGION = lp_core.JITA_REGION_ID


def _session(orders):
    resp = MagicMock(status_code=200)
    resp.headers = {"X-Pages": "1"}
    resp.json.return_value = orders
    s = MagicMock()
    s.get.return_value = resp
    return s


def _order(price, issued, vol=10, duration=90, location=STATION):
    return {"location_id": location, "price": price, "volume_remain": vol,
            "issued": issued, "duration": duration, "is_buy_order": False}


class TestFetchSellOrderStats:
    def test_reports_floor_price_and_age(self):
        now = _now("2024-01-01T12:00:00Z")
        s = _session([
            _order(37_100_000.0, "2024-01-01T11:00:00Z"),   # 1h old
            _order(36_970_000.0, "2024-01-01T04:00:00Z"),   # cheapest, 8h old
            _order(38_000_000.0, "2023-10-01T00:00:00Z"),
        ])
        out = lp_core.fetch_sell_order_stats(34, s, STATION, REGION, now=now)
        assert out["best_price"] == 36_970_000.0
        assert out["age_seconds"] == 8 * 3600
        assert out["duration_days"] == 90
        assert out["orders_at_best"] == 1
        assert out["sell_orders_total"] == 3

    def test_floor_age_is_oldest_of_tied_orders(self):
        # Two orders share the lowest price; the floor has stood since the older.
        now = _now("2024-01-01T12:00:00Z")
        s = _session([
            _order(100.0, "2024-01-01T10:00:00Z"),   # 2h old
            _order(100.0, "2024-01-01T06:00:00Z"),   # 6h old -> floor age
        ])
        out = lp_core.fetch_sell_order_stats(34, s, STATION, REGION, now=now)
        assert out["orders_at_best"] == 2
        assert out["age_seconds"] == 6 * 3600

    def test_none_when_no_orders_at_station(self):
        # Orders exist in the region but at a different station.
        s = _session([_order(100.0, "2024-01-01T00:00:00Z", location=60000001)])
        assert lp_core.fetch_sell_order_stats(34, s, STATION, REGION) is None

    def test_missing_issued_yields_none_age_but_still_counts(self):
        now = _now("2024-01-01T12:00:00Z")
        s = _session([{"location_id": STATION, "price": 100.0,
                       "volume_remain": 5, "is_buy_order": False}])
        out = lp_core.fetch_sell_order_stats(34, s, STATION, REGION, now=now)
        assert out["best_price"] == 100.0
        assert out["age_seconds"] is None
        assert out["sell_orders_total"] == 1


# ---------------------------------------------------------------------------
# fetch_orderbook_jita still aggregates correctly after the refactor
# ---------------------------------------------------------------------------

class TestOrderbookStillAggregates:
    def test_aggregates_volume_per_price_level(self):
        s = _session([
            _order(100.0, "2024-01-01T00:00:00Z", vol=5),
            _order(100.0, "2024-01-01T00:00:00Z", vol=7),
            _order(110.0, "2024-01-01T00:00:00Z", vol=3),
        ])
        book = lp_core.fetch_orderbook_jita(34, "sell", s,
                                            station_id=STATION, region_id=REGION)
        assert book == [[100.0, 12], [110.0, 3]]  # cheapest first, levels merged
