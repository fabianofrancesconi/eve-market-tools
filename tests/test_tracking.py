"""Tests for the exploration journal: trail store, session records, the live
state machine, journal route handlers, and the location poll tick (with the
online auto-pause grace window + auto-resume).

All ESI HTTP is mocked; the file-backed (non-Postgres) storage path is exercised.
"""
import time
from unittest.mock import MagicMock

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
lp_web = importlib.import_module("lp-web")

import requests


def _acct(chars=None):
    a = lp_web.Account(1)
    for cid, name in (chars or {1: "Explorer"}).items():
        a.characters[cid] = {"character_id": cid, "name": name,
                             "scopes": ["esi-location.read_location.v1"],
                             "refresh_token": "RT", "access_token": "AT",
                             "expires_at": time.time() + 600}
    a.active_char_id = next(iter(a.characters), None)
    return a


def _isolate(monkeypatch, tmp_path):
    """Point all journal state at temp files and reset in-memory registries."""
    monkeypatch.setattr(lp_web, "LOCATION_TRAIL_PATH", tmp_path / "trail.json")
    monkeypatch.setattr(lp_web, "LOCATION_TRACK_PATH", tmp_path / "track.json")
    monkeypatch.setattr(lp_web, "EXPLORATION_SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(lp_web.pg_store, "enabled", lambda: False)
    monkeypatch.setattr(lp_web, "_TRACK_SESSIONS", {})
    monkeypatch.setattr(lp_web, "_TRACK_LOADED_ACCTS", set())
    # Never let a token refresh or an SSE bump hit anything real.
    monkeypatch.setattr(lp_web, "_access_token", lambda acct, cid=None: "AT")
    monkeypatch.setattr(lp_web._CHAR_PUBSUB, "bump", lambda key: None)


# ── trail storage ───────────────────────────────────────────────────────────

class TestTrailStore:
    def test_append_and_query(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 30000142, "Jita", 0.9)
        lp_web._append_trail(acct, 1, 200.0, "run1", 30000144, "Perimeter", 0.9)
        rows = lp_web._query_trail(acct, 1, run_id="run1")
        assert [r["system_name"] for r in rows] == ["Jita", "Perimeter"]
        assert rows[0]["entered_at"] == 100.0

    def test_query_filters_by_run(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 1, "A", 0.5)
        lp_web._append_trail(acct, 1, 200.0, "run2", 2, "B", 0.5)
        assert len(lp_web._query_trail(acct, 1, run_id="run1")) == 1
        assert len(lp_web._query_trail(acct, 1, run_id="run2")) == 1

    def test_annotate_scanned(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 1, "A", 0.5)
        lp_web._annotate_trail(acct, 1, 100.0, scanned=True)
        assert lp_web._query_trail(acct, 1, run_id="run1")[0]["scanned"] is True

    def test_delete_run_removes_trail(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 1, "A", 0.5)
        lp_web._append_trail(acct, 1, 200.0, "run2", 2, "B", 0.5)
        lp_web._delete_trail_run(acct, 1, "run1")
        assert lp_web._query_trail(acct, 1, run_id="run1") == []
        assert len(lp_web._query_trail(acct, 1, run_id="run2")) == 1


# ── session records (the journal store) ──────────────────────────────────────

class TestSessionRecords:
    def test_upsert_and_get(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._session_record_upsert(acct, 1, {
            "run_id": "r1", "name": "Placid roam", "started_at": 100.0,
            "ended_at": None, "notes": "", "cargo_value": None})
        rec = lp_web._session_record_get(acct, 1, "r1")
        assert rec["name"] == "Placid roam" and rec["started_at"] == 100.0

    def test_patch_fields(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._session_record_upsert(acct, 1, {
            "run_id": "r1", "name": "", "started_at": 100.0,
            "ended_at": None, "notes": "", "cargo_value": None})
        lp_web._session_record_patch(acct, 1, "r1", name="Renamed",
                                     notes="jumped in Rancer", cargo_value=204_000_000.0)
        rec = lp_web._session_record_get(acct, 1, "r1")
        assert rec["name"] == "Renamed"
        assert rec["notes"] == "jumped in Rancer"
        assert rec["cargo_value"] == 204_000_000.0

    def test_list_newest_first(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        for run, ts in (("old", 100.0), ("new", 300.0), ("mid", 200.0)):
            lp_web._session_record_upsert(acct, 1, {
                "run_id": run, "name": run, "started_at": ts,
                "ended_at": None, "notes": "", "cargo_value": None})
        assert [r["run_id"] for r in lp_web._session_records_list(acct, 1)] == ["new", "mid", "old"]

    def test_delete(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._session_record_upsert(acct, 1, {
            "run_id": "r1", "name": "", "started_at": 100.0,
            "ended_at": None, "notes": "", "cargo_value": None})
        lp_web._session_record_delete(acct, 1, "r1")
        assert lp_web._session_record_get(acct, 1, "r1") is None


# ── session lifecycle via the route handlers ────────────────────────────────

class TestSessionLifecycle:
    def test_start_creates_record_and_live_state(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        r = lp_web.do_track_start({})
        assert r["state"] == "active" and r["run_id"]
        assert lp_web._session_record_get(acct, 1, r["run_id"]) is not None

    def test_start_pause_resume_stop(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        assert lp_web.do_track_pause({})["state"] == "paused"
        r = lp_web.do_track_resume({})
        assert r["state"] == "active" and r["run_id"] == run_id
        assert lp_web.do_track_stop({})["state"] == "stopped"

    def test_stop_stamps_ended_and_autonames(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._append_trail(acct, 1, time.time(), run_id, 30000142, "Jita", 0.9)
        lp_web.do_track_stop({})
        rec = lp_web._session_record_get(acct, 1, run_id)
        assert rec["ended_at"] is not None
        assert "Jita" in rec["name"]

    def test_pause_reason_user(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        assert lp_web.do_track_pause({})["pause_reason"] == "user"

    def test_state_persists_and_rehydrates_paused(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        lp_web._TRACK_SESSIONS.clear()
        lp_web._TRACK_LOADED_ACCTS.clear()
        lp_web._ensure_track_loaded(acct)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "paused" and s["pause_reason"] == "auto"


# ── journal payload handlers ─────────────────────────────────────────────────

class TestJournalHandlers:
    def test_status_shape(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        p = lp_web.do_track_status({})
        assert set(p) >= {"char_id", "state", "run_id", "trail", "scope_ok"}
        assert p["scope_ok"] is True

    def test_sessions_list_marks_live(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        out = lp_web.do_track_sessions({})
        assert len(out["sessions"]) == 1
        assert out["sessions"][0]["run_id"] == run_id
        assert out["sessions"][0]["is_live"] is True

    def test_session_detail_has_trail_and_stats(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._append_trail(acct, 1, 100.0, run_id, 1, "A", 0.5)
        lp_web._append_trail(acct, 1, 200.0, run_id, 2, "B", 0.4)
        out = lp_web.do_track_session({"run_id": [run_id]})
        assert out["session"]["systems"] == 2
        assert out["session"]["jumps"] == 1
        assert len(out["trail"]) == 2

    def test_session_update_name_notes_cargo(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web.do_track_session_update({"run_id": [run_id], "name": ["My roam"],
                                        "notes": ["found a relic"], "cargo_value": ["120000000"]})
        rec = lp_web._session_record_get(acct, 1, run_id)
        assert rec["name"] == "My roam"
        assert rec["notes"] == "found a relic"
        assert rec["cargo_value"] == 120_000_000.0

    def test_cargo_value_cleared_by_blank(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web.do_track_session_update({"run_id": [run_id], "cargo_value": ["500"]})
        lp_web.do_track_session_update({"run_id": [run_id], "cargo_value": [""]})
        assert lp_web._session_record_get(acct, 1, run_id)["cargo_value"] is None

    def test_delete_live_session_refused(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        out = lp_web.do_track_session_delete({"run_id": [run_id]})
        assert "error" in out
        assert lp_web._session_record_get(acct, 1, run_id) is not None

    def test_delete_finished_session(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._append_trail(acct, 1, 100.0, run_id, 1, "A", 0.5)
        lp_web.do_track_stop({})
        out = lp_web.do_track_session_delete({"run_id": [run_id]})
        assert out.get("ok") is True
        assert lp_web._session_record_get(acct, 1, run_id) is None
        assert lp_web._query_trail(acct, 1, run_id=run_id) == []

    def test_scanned_route(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._append_trail(acct, 1, 500.0, run_id, 1, "A", 0.5)
        lp_web.do_track_scanned({"entered_at": ["500.0"], "scanned": ["true"]})
        assert lp_web._query_trail(acct, 1, run_id=run_id)[0]["scanned"] is True


# ── poll tick: system change + online grace / auto-pause / auto-resume ───────

class TestPollTick:
    def _mock_esi(self, monkeypatch, online, system_id):
        monkeypatch.setattr(lp_web.sso_core, "fetch_online",
                            lambda t, c, s: {"online": online})
        monkeypatch.setattr(lp_web.sso_core, "fetch_location",
                            lambda t, c, s: {"solar_system_id": system_id})
        monkeypatch.setattr(lp_web, "_resolve_track_system",
                            lambda sid: {"name": f"Sys{sid}", "sec": 0.5})

    def test_records_system_change(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._poll_location_once(acct, 1)
        rows = lp_web._query_trail(acct, 1, run_id=run_id)
        assert len(rows) == 1 and rows[0]["system_id"] == 30000142

    def test_no_duplicate_on_same_system(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        run_id = lp_web.do_track_start({})["run_id"]
        lp_web._poll_location_once(acct, 1)
        lp_web._poll_location_once(acct, 1)
        assert len(lp_web._query_trail(acct, 1, run_id=run_id)) == 1

    def test_offline_within_grace_stays_active(self, monkeypatch, tmp_path):
        """A single offline reading (ESI cache lag / transient) must NOT pause."""
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=False, system_id=30000142)
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "active"
        assert s.get("offline_since") is not None

    def test_auto_pause_after_sustained_offline(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=False, system_id=30000142)
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)  # first offline reading, within grace
        lp_web._TRACK_SESSIONS[1]["offline_since"] = time.time() - lp_web._ONLINE_OFFLINE_GRACE - 1
        lp_web._TRACK_SESSIONS[1]["online_checked_at"] = 0.0
        lp_web._poll_location_once(acct, 1)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "paused" and s["pause_reason"] == "auto"

    def test_offline_then_back_online_clears_streak(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=False, system_id=30000142)
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)
        assert lp_web._TRACK_SESSIONS[1].get("offline_since") is not None
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        lp_web._TRACK_SESSIONS[1]["online_checked_at"] = 0.0
        lp_web._poll_location_once(acct, 1)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "active" and s.get("offline_since") is None

    def test_auto_resume_when_back_online(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        lp_web._TRACK_SESSIONS[1].update(
            {"state": "paused", "pause_reason": "auto", "online_checked_at": 0.0})
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        lp_web._poll_location_once(acct, 1)
        assert lp_web._TRACK_SESSIONS[1]["state"] == "active"

    def test_user_pause_not_auto_resumed(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        lp_web._TRACK_SESSIONS[1].update(
            {"state": "paused", "pause_reason": "user", "online_checked_at": 0.0})
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        lp_web._poll_location_once(acct, 1)
        assert lp_web._TRACK_SESSIONS[1]["state"] == "paused"

    def test_403_sets_scope_error(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        resp = MagicMock(); resp.status_code = 403
        err = requests.HTTPError(response=resp)
        def _boom(t, c, s): raise err
        monkeypatch.setattr(lp_web.sso_core, "fetch_online", _boom)
        monkeypatch.setattr(lp_web.sso_core, "fetch_location", _boom)
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)
        assert "403" in (lp_web._TRACK_SESSIONS[1].get("error") or "")
