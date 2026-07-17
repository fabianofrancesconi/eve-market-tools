"""Tests for wallet balance history recording and API."""
import time
import json

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
lp_web = importlib.import_module("lp-web")


def _acct(chars=None):
    a = lp_web.Account(1)
    for cid, name in (chars or {1: "Tester"}).items():
        a.characters[cid] = {"character_id": cid, "name": name}
    a.active_char_id = next(iter(a.characters), None)
    return a


class TestRecordWalletSnapshot:
    def test_records_balance(self, monkeypatch, tmp_path):
        """A fresh call records the balance."""
        path = tmp_path / "wh.json"
        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web, "_WALLET_LAST_RECORDED", {})
        monkeypatch.setattr(lp_web, "_WALLET_PRUNE_LAST", time.time())
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        lp_web._record_wallet_snapshot(acct, 1, 1_500_000_000.0)

        store = json.loads(path.read_text())
        assert "1" in store
        assert len(store["1"]) == 1
        assert store["1"][0][1] == 1_500_000_000.0

    def test_dedup_within_60s(self, monkeypatch, tmp_path):
        """Calls within 60s of each other are deduplicated."""
        path = tmp_path / "wh.json"
        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web, "_WALLET_LAST_RECORDED", {})
        monkeypatch.setattr(lp_web, "_WALLET_PRUNE_LAST", time.time())
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        lp_web._record_wallet_snapshot(acct, 1, 100.0)
        lp_web._record_wallet_snapshot(acct, 1, 200.0)

        store = json.loads(path.read_text())
        assert len(store["1"]) == 1

    def test_none_balance_skipped(self, monkeypatch, tmp_path):
        """None balance (ESI error) is not recorded."""
        path = tmp_path / "wh.json"
        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web, "_WALLET_LAST_RECORDED", {})
        monkeypatch.setattr(lp_web, "_WALLET_PRUNE_LAST", time.time())
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        lp_web._record_wallet_snapshot(acct, 1, None)

        assert not path.exists()

    def test_multiple_characters(self, monkeypatch, tmp_path):
        """Each character gets its own series."""
        path = tmp_path / "wh.json"
        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web, "_WALLET_LAST_RECORDED", {})
        monkeypatch.setattr(lp_web, "_WALLET_PRUNE_LAST", time.time())
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct({1: "Alpha", 2: "Beta"})
        lp_web._record_wallet_snapshot(acct, 1, 100.0)
        lp_web._record_wallet_snapshot(acct, 2, 200.0)

        store = json.loads(path.read_text())
        assert len(store["1"]) == 1
        assert len(store["2"]) == 1
        assert store["1"][0][1] == 100.0
        assert store["2"][0][1] == 200.0


class TestDownsample:
    def test_no_change_if_under_limit(self):
        series = [[i, i * 10.0] for i in range(100)]
        result = lp_web._downsample(series, max_points=500)
        assert result == series

    def test_reduces_to_max_points(self):
        series = [[i, float(i)] for i in range(2000)]
        result = lp_web._downsample(series, max_points=500)
        assert len(result) <= 500

    def test_preserves_range(self):
        series = [[i, float(i * 100)] for i in range(1000)]
        result = lp_web._downsample(series, max_points=100)
        assert result[0][0] < result[-1][0]


class TestWalletHistoryEndpoint:
    def test_returns_series(self, monkeypatch, tmp_path):
        """The endpoint returns the correct shape."""
        path = tmp_path / "wh.json"
        now = time.time()
        data = {"1": [[now - 100, 500.0], [now - 50, 600.0]]}
        path.write_text(json.dumps(data))

        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)

        result = lp_web.do_wallet_history({"days": ["30"]})
        assert "series" in result
        assert "1" in result["series"]
        assert result["series"]["1"]["name"] == "Tester"
        assert len(result["series"]["1"]["data"]) == 2

    def test_filters_by_days(self, monkeypatch, tmp_path):
        """Only points within the time window are returned."""
        path = tmp_path / "wh.json"
        now = time.time()
        data = {"1": [
            [now - 86400 * 10, 100.0],  # 10 days ago
            [now - 86400 * 2, 200.0],   # 2 days ago
            [now - 100, 300.0],          # recent
        ]}
        path.write_text(json.dumps(data))

        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)

        result = lp_web.do_wallet_history({"days": ["7"]})
        assert len(result["series"]["1"]["data"]) == 2

    def test_empty_when_no_data(self, monkeypatch, tmp_path):
        """Returns empty series when no history exists."""
        path = tmp_path / "wh.json"
        monkeypatch.setattr(lp_web, "WALLET_HISTORY_PATH", path)
        monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)

        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)

        result = lp_web.do_wallet_history({"days": ["30"]})
        assert result["series"] == {}


class TestCompactSeries:
    def test_recent_data_unchanged(self):
        """Data within 7 days is kept at full resolution."""
        now = time.time()
        series = [[now - i * 300, 100.0 + i] for i in range(10)]
        result, changed = lp_web._compact_series(series, now)
        assert not changed
        assert result == series

    def test_old_data_compacted_hourly(self):
        """Data 7–90 days old is compacted to one point per hour."""
        now = time.time()
        # Align base to start of an hour so all 12 points land in one bucket
        base = (int((now - 10 * 86400) // 3600)) * 3600
        # 12 points within the same hour (every 5 min = 12*300 = 3600)
        series = [[base + i * 200, 1000.0 + i] for i in range(12)]
        result, changed = lp_web._compact_series(series, now)
        assert changed
        assert len(result) == 1
        assert abs(result[0][1] - sum(1000.0 + i for i in range(12)) / 12) < 0.01

    def test_very_old_data_compacted_daily(self):
        """Data 90–365 days old is compacted to one point per day."""
        now = time.time()
        # Align base to start of a UTC day so all points land in one bucket
        base = (int((now - 100 * 86400) // 86400)) * 86400
        # 12 points within the same day (every 2 hours)
        series = [[base + i * 7200, 5000.0 + i * 10] for i in range(12)]
        result, changed = lp_web._compact_series(series, now)
        assert changed
        assert len(result) == 1

    def test_ancient_data_discarded(self):
        """Data older than 365 days is removed."""
        now = time.time()
        series = [[now - 400 * 86400, 999.0], [now - 100, 500.0]]
        result, changed = lp_web._compact_series(series, now)
        assert changed
        assert len(result) == 1
        assert result[0][1] == 500.0

    def test_mixed_ages(self):
        """Series with points in all age buckets is properly segmented."""
        now = time.time()
        # The daily bucket keys on int(ts // 86400), so the two "same day" points
        # must land in the same epoch-day bucket regardless of now's time-of-day.
        # Anchor them to a shared day ~100 days back rather than offsetting from now
        # (an offset can straddle a UTC midnight and split the pair into two days).
        day_100_ago = int((now - 100 * 86400) // 86400)
        series = [
            [now - 400 * 86400, 1.0],        # >365d: discard
            [day_100_ago * 86400 + 100, 2.0],   # 90-365d: daily
            [day_100_ago * 86400 + 3700, 4.0],  # same day bucket as above
            [now - 10 * 86400, 3.0],         # 7-90d: hourly
            [now - 100, 4.0],                # <7d: keep
        ]
        result, changed = lp_web._compact_series(series, now)
        assert changed
        # 1 daily avg + 1 hourly + 1 recent = 3
        assert len(result) == 3
