"""
Tests for the Fuzzwork-based arbitrage scan (v1.1.5).

Covers the three new functions in arb_core:
- fetch_region_types  (ESI /markets/{region}/types/ with disk caching)
- fetch_fuzzwork_region  (Fuzzwork aggregates with region parameter)
- arb_candidates  (spread filter — no false negatives by construction)
- fetch_type_orders  (per-type ESI orders)
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import arb_core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esi_page(data, pages=1, page=1):
    """Fake requests.Response for an ESI paginated list."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = data
    r.headers = {"X-Pages": str(pages)}
    return r


def _fuzzwork_response(mapping):
    """Fake requests.Response for Fuzzwork aggregates."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        str(tid): {
            "sell": {"min": str(p["sell_min"]) if p.get("sell_min") else "0"},
            "buy":  {"max": str(p["buy_max"])  if p.get("buy_max")  else "0"},
        }
        for tid, p in mapping.items()
    }
    return r


# ---------------------------------------------------------------------------
# fetch_region_types
# ---------------------------------------------------------------------------

class TestFetchRegionTypes:
    def test_single_page(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _esi_page([34, 35, 36], pages=1)

        result = arb_core.fetch_region_types(10000002, session, tmp_path)

        assert result == [34, 35, 36]
        session.get.assert_called_once()

    def test_multiple_pages(self, tmp_path):
        session = MagicMock()
        session.get.side_effect = [
            _esi_page([34, 35], pages=2, page=1),
            _esi_page([36, 37], pages=2, page=2),
        ]

        result = arb_core.fetch_region_types(10000002, session, tmp_path)

        assert result == [34, 35, 36, 37]
        assert session.get.call_count == 2

    def test_disk_cache_used_within_ttl(self, tmp_path):
        cache_file = tmp_path / "types_region_10000002.json"
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "types": [1, 2, 3]}))

        session = MagicMock()
        result = arb_core.fetch_region_types(10000002, session, tmp_path)

        assert result == [1, 2, 3]
        session.get.assert_not_called()

    def test_disk_cache_bypassed_when_refresh(self, tmp_path):
        cache_file = tmp_path / "types_region_10000002.json"
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "types": [1, 2, 3]}))

        session = MagicMock()
        session.get.return_value = _esi_page([99], pages=1)

        result = arb_core.fetch_region_types(10000002, session, tmp_path, refresh=True)

        assert result == [99]
        session.get.assert_called_once()

    def test_disk_cache_expired_re_fetches(self, tmp_path):
        cache_file = tmp_path / "types_region_10000002.json"
        old_ts = time.time() - arb_core._TYPES_CACHE_TTL - 1
        cache_file.write_text(json.dumps({"fetched_at": old_ts, "types": [1, 2, 3]}))

        session = MagicMock()
        session.get.return_value = _esi_page([42], pages=1)

        result = arb_core.fetch_region_types(10000002, session, tmp_path)

        assert result == [42]

    def test_result_written_to_disk(self, tmp_path):
        session = MagicMock()
        session.get.return_value = _esi_page([10, 20], pages=1)

        arb_core.fetch_region_types(10000002, session, tmp_path)

        cache_file = tmp_path / "types_region_10000002.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["types"] == [10, 20]
        assert "fetched_at" in data

    def test_progress_callback_cache_hit(self, tmp_path):
        cache_file = tmp_path / "types_region_10000002.json"
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "types": [1, 2]}))

        cb = MagicMock()
        session = MagicMock()
        arb_core.fetch_region_types(10000002, session, tmp_path, progress_cb=cb)

        cb.assert_called_once_with("cache", count=2)

    def test_progress_callback_pages(self, tmp_path):
        session = MagicMock()
        session.get.side_effect = [
            _esi_page([1], pages=2, page=1),
            _esi_page([2], pages=2, page=2),
        ]
        cb = MagicMock()
        arb_core.fetch_region_types(10000002, session, tmp_path, progress_cb=cb)

        calls = [c.args[0] for c in cb.call_args_list]
        assert calls == ["page", "page"]


# ---------------------------------------------------------------------------
# fetch_fuzzwork_region
# ---------------------------------------------------------------------------

class TestFetchFuzzworkRegion:
    def test_basic_mapping(self):
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({
            34: {"sell_min": 5.0, "buy_max": 4.5},
            35: {"sell_min": 100.0, "buy_max": 95.0},
        })

        result = arb_core.fetch_fuzzwork_region([34, 35], 10000002, session)

        assert result[34] == {"sell_min": 5.0, "buy_max": 4.5}
        assert result[35] == {"sell_min": 100.0, "buy_max": 95.0}

    def test_zero_prices_become_none(self):
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({
            34: {"sell_min": None, "buy_max": None},
        })

        result = arb_core.fetch_fuzzwork_region([34], 10000002, session)
        assert result[34]["sell_min"] is None
        assert result[34]["buy_max"] is None

    def test_batches_at_batch_size(self):
        # Create BATCH+1 type IDs to force two calls
        type_ids = list(range(arb_core._FUZZWORK_BATCH + 1))

        session = MagicMock()
        session.get.return_value = _fuzzwork_response({})

        arb_core.fetch_fuzzwork_region(type_ids, 10000002, session)

        assert session.get.call_count == 2

    def test_uses_region_param(self):
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({34: {"sell_min": 5.0, "buy_max": 4.0}})

        arb_core.fetch_fuzzwork_region([34], 10000002, session)

        _, kwargs = session.get.call_args
        assert kwargs["params"]["region"] == 10000002

    def test_progress_callback_called_per_chunk(self):
        type_ids = list(range(arb_core._FUZZWORK_BATCH + 1))
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({})
        cb = MagicMock()

        arb_core.fetch_fuzzwork_region(type_ids, 10000002, session, progress_cb=cb)

        assert cb.call_count == 2
        first = cb.call_args_list[0]
        assert first.kwargs["chunk"] == 1
        assert first.kwargs["total"] == 2


# ---------------------------------------------------------------------------
# arb_candidates
# ---------------------------------------------------------------------------

class TestArbCandidates:
    def test_spread_detected(self):
        prices = {34: {"sell_min": 5.0, "buy_max": 6.0}}
        # buy 6 * (1-0.075) = 5.55 > sell 5.0
        assert arb_core.arb_candidates(prices, 0.075) == [34]

    def test_no_spread(self):
        prices = {34: {"sell_min": 6.0, "buy_max": 6.0}}
        # buy 6 * 0.925 = 5.55 < sell 6.0 → no arb
        assert arb_core.arb_candidates(prices, 0.075) == []

    def test_missing_prices_excluded(self):
        prices = {
            34: {"sell_min": None, "buy_max": 10.0},
            35: {"sell_min": 5.0, "buy_max": None},
        }
        assert arb_core.arb_candidates(prices, 0.075) == []

    def test_no_false_negatives(self):
        """If same-station arb exists at any price pair, the region aggregate
        must also flag it (since region_max_buy >= station_buy and
        region_min_sell <= station_sell)."""
        station_sell = 50.0
        station_buy = 70.0
        tax = 0.075
        # Confirm there IS arb at station level
        assert station_buy * (1 - tax) > station_sell

        # Region aggregates are at least as extreme
        region_prices = {1: {"sell_min": station_sell, "buy_max": station_buy}}
        assert arb_core.arb_candidates(region_prices, tax) == [1]

    def test_tax_applied(self):
        # Exact boundary: buy * (1-tax) == sell → NOT an arb
        tax = 0.1
        sell = 90.0
        buy = 100.0  # 100 * 0.9 = 90 == sell → not profitable
        prices = {1: {"sell_min": sell, "buy_max": buy}}
        assert arb_core.arb_candidates(prices, tax) == []


# ---------------------------------------------------------------------------
# fetch_type_orders
# ---------------------------------------------------------------------------

class TestFetchTypeOrders:
    def _order(self, **kw):
        return {"type_id": 34, "location_id": 60003760, "price": 5.0,
                "is_buy_order": False, "volume_remain": 100, **kw}

    def test_returns_all_orders(self):
        session = MagicMock()
        session.get.return_value = _esi_page([self._order(), self._order()], pages=1)

        result = arb_core.fetch_type_orders(10000002, 34, session)

        assert len(result) == 2

    def test_paginates(self):
        session = MagicMock()
        session.get.side_effect = [
            _esi_page([self._order(price=5.0)], pages=2, page=1),
            _esi_page([self._order(price=6.0)], pages=2, page=2),
        ]

        result = arb_core.fetch_type_orders(10000002, 34, session)

        assert len(result) == 2
        assert session.get.call_count == 2

    def test_stops_on_non_200(self):
        bad = MagicMock()
        bad.status_code = 404

        session = MagicMock()
        session.get.return_value = bad

        result = arb_core.fetch_type_orders(10000002, 34, session)

        assert result == []

    def test_passes_type_id_and_both_sides(self):
        session = MagicMock()
        session.get.return_value = _esi_page([], pages=1)

        arb_core.fetch_type_orders(10000002, 34, session)

        _, kwargs = session.get.call_args
        assert kwargs["params"]["type_id"] == 34
        assert kwargs["params"]["order_type"] == "all"


# ---------------------------------------------------------------------------
# Regression: fuzzwork_progress callback signature (v1.1.5)
# ---------------------------------------------------------------------------

class TestFuzzworkProgressCallbackContract:
    """Regression for TypeError: got multiple values for argument 'chunk'.

    fetch_fuzzwork_region calls progress_cb("chunk", chunk=N, total=T, types_done=D).
    The handler must use (stage, **kw), not positional params like (chunk, total, types_done).
    """

    def test_callback_receives_stage_as_first_positional(self):
        """The first positional arg is always the stage string, not a numeric value."""
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({34: {"sell_min": 5.0, "buy_max": 4.0}})

        received = []
        def cb(stage, **kw):
            received.append((stage, kw))

        arb_core.fetch_fuzzwork_region([34], 10000002, session, progress_cb=cb)

        assert len(received) == 1
        stage, kw = received[0]
        assert stage == "chunk"
        assert isinstance(kw["chunk"], int)
        assert isinstance(kw["total"], int)
        assert "types_done" in kw

    def test_positional_signature_raises(self):
        """Using positional params (chunk, total, types_done) without a stage param
        would cause 'multiple values for argument chunk' — this documents the bug."""
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({34: {"sell_min": 5.0, "buy_max": 4.0}})

        def bad_cb(chunk, total, types_done):
            pass  # pragma: no cover

        with pytest.raises(TypeError, match="multiple values"):
            arb_core.fetch_fuzzwork_region([34], 10000002, session, progress_cb=bad_cb)

    def test_stage_kw_handler_does_not_raise(self):
        """The correct (stage, **kw) signature must not raise for any batch count."""
        type_ids = list(range(arb_core._FUZZWORK_BATCH + 5))
        session = MagicMock()
        session.get.return_value = _fuzzwork_response({})

        def good_cb(stage, **kw):
            _ = kw["chunk"] / kw["total"]  # would raise if wrong types

        arb_core.fetch_fuzzwork_region(type_ids, 10000002, session, progress_cb=good_cb)
