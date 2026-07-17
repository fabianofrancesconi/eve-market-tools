"""Regression test for _persist_arb_caches under concurrent mutation.

Up to _MAX_CONCURRENT_SCANS arb scans share the module-global lookup dicts. One
scan serializing them (save_lookup_cache iterates via dict comprehensions) while
another's enrich_*/resolve_volume inserts into the same dict raised
"RuntimeError: dictionary changed size during iteration", aborting the scan with
an SSE error. _persist_arb_caches snapshots with a retry and never raises.
"""
import importlib
import json

lp_web = importlib.import_module("lp-web")


class _RaceyDict:
    """A mapping whose copy raises 'changed size during iteration' a fixed number
    of times before succeeding — deterministically simulating a concurrent
    mutator. NOT a dict subclass, so dict(this) goes through keys()/__getitem__
    (a dict subclass would hit CPython's C fast-copy and skip the override)."""
    def __init__(self, data, fail_times):
        self._data = dict(data)
        self._left = fail_times

    def keys(self):
        if self._left > 0:
            self._left -= 1
            raise RuntimeError("dictionary changed size during iteration")
        return self._data.keys()

    def __getitem__(self, k):
        return self._data[k]

    def __iter__(self):
        return iter(self._data)

    def items(self):
        return self._data.items()


def test_persist_retries_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
    # Fails its first two copy attempts, succeeds on the third (== _attempts=3).
    monkeypatch.setattr(lp_web, "_ARB_STATION_CACHE",
                        _RaceyDict({60003760: {"name": "Jita IV"}}, fail_times=2))
    monkeypatch.setattr(lp_web, "_ARB_VOLUME_CACHE", {34: 0.01})
    monkeypatch.setattr(lp_web, "_ARB_SYSTEM_CACHE", {})
    monkeypatch.setattr(lp_web, "_ARB_ROUTE_CACHE", {})

    assert lp_web._persist_arb_caches() is True
    doc = json.load(open(tmp_path / "lookups.json"))
    assert doc["stations"]["60003760"]["name"] == "Jita IV"


def test_persist_gives_up_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
    # Never stops racing -> returns False, but must NOT raise (best-effort cache).
    monkeypatch.setattr(lp_web, "_ARB_STATION_CACHE",
                        _RaceyDict({1: {"name": "X"}}, fail_times=999))
    monkeypatch.setattr(lp_web, "_ARB_VOLUME_CACHE", {})
    monkeypatch.setattr(lp_web, "_ARB_SYSTEM_CACHE", {})
    monkeypatch.setattr(lp_web, "_ARB_ROUTE_CACHE", {})

    assert lp_web._persist_arb_caches() is False
    assert not (tmp_path / "lookups.json").exists()


def test_persist_writes_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(lp_web, "_ARB_STATION_CACHE", {60003760: {"name": "Jita IV"}})
    monkeypatch.setattr(lp_web, "_ARB_VOLUME_CACHE", {34: 0.01})
    monkeypatch.setattr(lp_web, "_ARB_SYSTEM_CACHE", {30000142: {"sec": 0.9}})
    monkeypatch.setattr(lp_web, "_ARB_ROUTE_CACHE", {(30000142, 30002187, "shortest"): 7})
    assert lp_web._persist_arb_caches() is True
    doc = json.load(open(tmp_path / "lookups.json"))
    assert doc["stations"]["60003760"]["name"] == "Jita IV"
    assert doc["volumes"]["34"] == 0.01
    assert doc["routes"]["30000142:30002187:shortest"] == 7
