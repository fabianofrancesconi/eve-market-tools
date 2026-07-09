"""
Regression tests for v1.91.11: saved build-location profiles must never be
silently wiped by a settings sync.

Build-location profiles live only inside the wholesale settings blob, which the
client snapshots from its live state and pushes to the account row. A boot /
cold-start race (e.g. the /api/settings fetch failing right after a deploy) could
leave the client holding its empty default (IND.profiles = []) and push that over
the durable copy — so saved build locations vanished after an update.

Two layers defend against this:
  * client: the saveLS persist gate only opens once a settings state was actually
    loaded, so a failed load can't push empty defaults;
  * server: _preserve_profiles refuses to overwrite non-empty stored profiles
    with an empty incoming list unless the client explicitly cleared them.
"""
import json
from pathlib import Path

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def _blob(profiles, cleared=None):
    ind = {"profiles": json.dumps(profiles)}
    if cleared is not None:
        ind["profiles_cleared"] = cleared
    return {"ind": ind}


class TestProfilesList:
    def test_parses_json_string(self):
        assert lp_web._profiles_list(_blob([{"name": "A"}])) == [{"name": "A"}]

    def test_empty_string_list(self):
        assert lp_web._profiles_list(_blob([])) == []

    def test_missing_ind(self):
        assert lp_web._profiles_list({}) == []

    def test_missing_profiles(self):
        assert lp_web._profiles_list({"ind": {}}) == []

    def test_raw_list_tolerated(self):
        assert lp_web._profiles_list({"ind": {"profiles": [{"name": "A"}]}}) == [{"name": "A"}]

    def test_garbage_string(self):
        assert lp_web._profiles_list({"ind": {"profiles": "not json"}}) == []


class TestPreserveProfiles:
    def test_empty_incoming_keeps_stored(self):
        """The core bug: an empty push must not wipe non-empty stored profiles."""
        stored = _blob([{"name": "Keepstar"}, {"name": "Raitaru"}])
        incoming = _blob([])
        out = lp_web._preserve_profiles(incoming, stored)
        assert lp_web._profiles_list(out) == [{"name": "Keepstar"}, {"name": "Raitaru"}]

    def test_missing_incoming_profiles_keeps_stored(self):
        stored = _blob([{"name": "Keepstar"}])
        out = lp_web._preserve_profiles({"ind": {}}, stored)
        assert lp_web._profiles_list(out) == [{"name": "Keepstar"}]

    def test_missing_incoming_ind_keeps_stored(self):
        stored = _blob([{"name": "Keepstar"}])
        out = lp_web._preserve_profiles({"corp": "x"}, stored)
        assert lp_web._profiles_list(out) == [{"name": "Keepstar"}]

    def test_non_empty_incoming_is_trusted(self):
        """A real edit (add / remove one of several) must go through untouched."""
        stored = _blob([{"name": "Old1"}, {"name": "Old2"}])
        incoming = _blob([{"name": "New"}])
        out = lp_web._preserve_profiles(incoming, stored)
        assert lp_web._profiles_list(out) == [{"name": "New"}]

    def test_explicit_clear_is_honoured(self):
        """Deleting the last build location (profiles_cleared='1') must persist."""
        stored = _blob([{"name": "Keepstar"}])
        incoming = _blob([], cleared="1")
        out = lp_web._preserve_profiles(incoming, stored)
        assert lp_web._profiles_list(out) == []

    def test_clear_flag_zero_does_not_honour_empty(self):
        stored = _blob([{"name": "Keepstar"}])
        incoming = _blob([], cleared="0")
        out = lp_web._preserve_profiles(incoming, stored)
        assert lp_web._profiles_list(out) == [{"name": "Keepstar"}]

    def test_empty_stored_leaves_incoming_untouched(self):
        incoming = _blob([])
        out = lp_web._preserve_profiles(incoming, _blob([]))
        assert lp_web._profiles_list(out) == []

    def test_none_stored_leaves_incoming_untouched(self):
        incoming = _blob([])
        out = lp_web._preserve_profiles(incoming, None)
        assert lp_web._profiles_list(out) == []


class TestClientGate:
    """The saveLS persist gate must only open after a settings state actually
    loaded — a failed /api/settings fetch with empty localStorage must NOT open
    it, or the next save pushes empty defaults over the durable copy."""

    def test_gate_is_conditional_on_load(self):
        src = lp_web.FRONTEND_SOURCE
        assert "const _settingsLoaded" in src
        assert "if(_settingsLoaded) markSettingsApplied();" in src
        # The unconditional call must be gone.
        assert "\n  markSettingsApplied();" not in src

    def test_blob_carries_clear_signal(self):
        src = lp_web.FRONTEND_SOURCE
        assert "profiles_cleared:" in src
        assert "IND.profilesCleared" in src
