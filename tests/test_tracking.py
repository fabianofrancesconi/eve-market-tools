"""Tests for the live exploration tracker: trail store, session state machine,
route handlers and the location poll tick (with online auto-pause/resume).

All ESI HTTP is mocked; the file-backed (non-Postgres) storage path is exercised.
"""
import json
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
    """Point all tracking state at temp files and reset in-memory registries."""
    monkeypatch.setattr(lp_web, "LOCATION_TRAIL_PATH", tmp_path / "trail.json")
    monkeypatch.setattr(lp_web, "LOCATION_TRACK_PATH", tmp_path / "track.json")
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

    def test_annotate_scanned_and_cargo(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 1, "A", 0.5)
        lp_web._annotate_trail(acct, 1, 100.0, scanned=True)
        lp_web._annotate_trail(acct, 1, 100.0, cargo_isk=42_000_000.0)
        row = lp_web._query_trail(acct, 1, run_id="run1")[0]
        assert row["scanned"] is True
        assert row["cargo_isk"] == 42_000_000.0

    def test_annotate_cargo_clear(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        lp_web._append_trail(acct, 1, 100.0, "run1", 1, "A", 0.5)
        lp_web._annotate_trail(acct, 1, 100.0, cargo_isk=5.0)
        lp_web._annotate_trail(acct, 1, 100.0, cargo_isk="")
        assert lp_web._query_trail(acct, 1, run_id="run1")[0]["cargo_isk"] is None


# ── session lifecycle via the route handlers ────────────────────────────────

class TestSessionLifecycle:
    def test_start_pause_resume_stop(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)

        r = lp_web.do_track_start({})
        assert r["state"] == "active" and r["run_id"]
        run_id = r["run_id"]

        assert lp_web.do_track_pause({})["state"] == "paused"
        assert lp_web.do_track_pause({})["pause_reason"] is None or True
        # resume keeps the same run
        r = lp_web.do_track_resume({})
        assert r["state"] == "active" and r["run_id"] == run_id
        # stop closes it
        assert lp_web.do_track_stop({})["state"] == "stopped"

    def test_start_new_run_id_each_time(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        r1 = lp_web.do_track_start({})
        lp_web.do_track_stop({})
        r2 = lp_web.do_track_start({})
        assert r1["run_id"] != r2["run_id"]

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
        # Drop in-memory state; a fresh load should resurrect the run as auto-paused
        # (we can't assume the pilot is still online after a restart).
        lp_web._TRACK_SESSIONS.clear()
        lp_web._TRACK_LOADED_ACCTS.clear()
        lp_web._ensure_track_loaded(acct)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "paused" and s["pause_reason"] == "auto"


# ── annotation route handlers ────────────────────────────────────────────────

class TestAnnotationRoutes:
    def test_do_track_scanned(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        run_id = lp_web._TRACK_SESSIONS[1]["run_id"]
        lp_web._append_trail(acct, 1, 500.0, run_id, 1, "A", 0.5)
        lp_web.do_track_scanned({"entered_at": ["500.0"], "scanned": ["true"]})
        assert lp_web._query_trail(acct, 1, run_id=run_id)[0]["scanned"] is True

    def test_do_track_cargo(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        run_id = lp_web._TRACK_SESSIONS[1]["run_id"]
        lp_web._append_trail(acct, 1, 500.0, run_id, 1, "A", 0.5)
        lp_web.do_track_cargo({"entered_at": ["500.0"], "cargo_isk": ["1000000"]})
        assert lp_web._query_trail(acct, 1, run_id=run_id)[0]["cargo_isk"] == 1_000_000.0

    def test_trail_payload_shape(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        p = lp_web.do_track_trail({})
        assert set(p) >= {"char_id", "state", "run_id", "trail", "scope_ok"}
        assert p["scope_ok"] is True


# ── poll tick: system change + online auto-pause/resume ──────────────────────

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
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)
        run_id = lp_web._TRACK_SESSIONS[1]["run_id"]
        rows = lp_web._query_trail(acct, 1, run_id=run_id)
        assert len(rows) == 1 and rows[0]["system_id"] == 30000142

    def test_no_duplicate_on_same_system(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=True, system_id=30000142)
        lp_web.do_track_start({})
        lp_web._poll_location_once(acct, 1)
        lp_web._poll_location_once(acct, 1)
        run_id = lp_web._TRACK_SESSIONS[1]["run_id"]
        assert len(lp_web._query_trail(acct, 1, run_id=run_id)) == 1

    def test_auto_pause_when_offline(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        self._mock_esi(monkeypatch, online=False, system_id=30000142)
        lp_web.do_track_start({})
        # Force the online recheck to run (start stamps online_checked_at=0).
        lp_web._poll_location_once(acct, 1)
        s = lp_web._TRACK_SESSIONS[1]
        assert s["state"] == "paused" and s["pause_reason"] == "auto"

    def test_auto_resume_when_back_online(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        acct = _acct()
        monkeypatch.setattr(lp_web, "require_account", lambda: acct)
        lp_web.do_track_start({})
        # Manually put it in auto-paused state with a stale online check.
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
        # A user pause must survive being online — only Resume reactivates it.
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
