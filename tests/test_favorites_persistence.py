"""
Regression tests: Industry favorites (the blueprint watchlist) must never be
silently wiped by a settings sync.

Same failure mode as build-location profiles (see test_build_location_persistence):
favorites live only inside the wholesale settings blob the client pushes to the
account row. A cold-start race (server not yet synced + this browser has no local
cache) leaves the client holding the empty default IND.favorites=Set() and pushes
[] over the durable list — which is how a user's several favorites collapsed to
whatever they re-added afterwards.

The server-side guard _preserve_favorites refuses to overwrite a non-empty stored
favorites list with an empty incoming one unless the client explicitly signalled
a deliberate clear (ind.favorites_cleared == "1").
"""
import json
from pathlib import Path

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def _blob(favorites, cleared=None):
    ind = {"favorites": json.dumps(favorites)}
    if cleared is not None:
        ind["favorites_cleared"] = cleared
    return {"ind": ind}


class TestFavoritesList:
    def test_parses_json_string(self):
        assert lp_web._favorites_list(_blob([23560, 587])) == [23560, 587]

    def test_empty_string_list(self):
        assert lp_web._favorites_list(_blob([])) == []

    def test_missing_ind(self):
        assert lp_web._favorites_list({}) == []

    def test_missing_favorites(self):
        assert lp_web._favorites_list({"ind": {}}) == []

    def test_raw_list_tolerated(self):
        assert lp_web._favorites_list({"ind": {"favorites": [1, 2]}}) == [1, 2]

    def test_garbage_string(self):
        assert lp_web._favorites_list({"ind": {"favorites": "not json"}}) == []


class TestPreserveFavorites:
    def test_empty_incoming_keeps_stored(self):
        """The core bug: an empty push must not wipe non-empty stored favorites."""
        stored = _blob([23560, 587, 12005])
        out = lp_web._preserve_favorites(_blob([]), stored)
        assert lp_web._favorites_list(out) == [23560, 587, 12005]

    def test_missing_incoming_favorites_keeps_stored(self):
        stored = _blob([23560])
        out = lp_web._preserve_favorites({"ind": {}}, stored)
        assert lp_web._favorites_list(out) == [23560]

    def test_missing_incoming_ind_keeps_stored(self):
        stored = _blob([23560])
        out = lp_web._preserve_favorites({"corp": "x"}, stored)
        assert lp_web._favorites_list(out) == [23560]

    def test_non_empty_incoming_is_trusted(self):
        """A real edit (remove one of several) must go through untouched."""
        stored = _blob([1, 2, 3])
        out = lp_web._preserve_favorites(_blob([1, 2]), stored)
        assert lp_web._favorites_list(out) == [1, 2]

    def test_explicit_clear_is_honoured(self):
        """Removing the last favorite (favorites_cleared='1') must persist."""
        stored = _blob([23560])
        out = lp_web._preserve_favorites(_blob([], cleared="1"), stored)
        assert lp_web._favorites_list(out) == []

    def test_clear_flag_zero_does_not_honour_empty(self):
        stored = _blob([23560])
        out = lp_web._preserve_favorites(_blob([], cleared="0"), stored)
        assert lp_web._favorites_list(out) == [23560]

    def test_empty_stored_leaves_incoming_untouched(self):
        out = lp_web._preserve_favorites(_blob([]), _blob([]))
        assert lp_web._favorites_list(out) == []

    def test_none_stored_leaves_incoming_untouched(self):
        out = lp_web._preserve_favorites(_blob([]), None)
        assert lp_web._favorites_list(out) == []


class TestClientSignal:
    def test_blob_carries_clear_signal(self):
        src = lp_web.FRONTEND_SOURCE
        assert "favorites_cleared:" in src
        assert "IND.favoritesCleared" in src
