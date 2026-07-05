"""
Tests for multi-character (alt account) support + the multi-user account model.

Covers: v1→v2 auth migration (legacy file mode), per-character token refresh,
character switching, logout (single + all), cross-character blueprint
annotations, and combined char data output shape.
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sso_core

# Import the web module for integration tests
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)
from lp_core import LPError


def _acct(chars=None, active=None):
    """Build an Account from {cid: name} (or richer {cid: dict})."""
    ids = list((chars or {}).keys())
    a = lp_web.Account(ids[0] if ids else 1)
    for cid, val in (chars or {}).items():
        if isinstance(val, dict):
            a.characters[cid] = {"character_id": cid, "scopes": [],
                                 "refresh_token": "x", **val}
        else:
            a.characters[cid] = {"character_id": cid, "name": val,
                                 "scopes": [], "refresh_token": "x"}
    a.active_char_id = active if (active in a.characters) else (ids[0] if ids else None)
    return a


def _use_account(acct):
    lp_web._REQUEST.account = acct


@pytest.fixture(autouse=True)
def _legacy_mode(monkeypatch):
    """These tests exercise the legacy (file-backed, single-account) path."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    lp_web._REQUEST.account = None
    yield
    lp_web._REQUEST.account = None


# ── Auth file migration (v1 → v2), legacy mode ───────────────────────────────

class TestAuthMigration:
    def test_v1_file_migrates_to_v2(self, tmp_path, monkeypatch):
        """Existing single-character eve_auth.json loads into the legacy account."""
        v1_data = {
            "refresh_token": "old_refresh",
            "character_id": 123,
            "name": "OldChar",
            "scopes": ["esi-skills.read_skills.v1"],
        }
        (tmp_path / "eve_auth.json").write_text(json.dumps(v1_data))
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "characters", {})
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "active_char_id", None)
        lp_web._startup_restore()
        acct = lp_web._LEGACY_ACCOUNT
        assert 123 in acct.characters
        assert acct.characters[123]["name"] == "OldChar"
        assert acct.characters[123]["refresh_token"] == "old_refresh"
        assert acct.active_char_id == 123
        # Verify it rewrote as v2
        saved = json.loads((tmp_path / "eve_auth.json").read_text())
        assert saved["version"] == 2
        assert len(saved["characters"]) == 1
        assert saved["characters"][0]["character_id"] == 123

    def test_v2_file_loads_multiple_characters(self, tmp_path, monkeypatch):
        v2_data = {
            "version": 2,
            "active_char_id": 100,
            "characters": [
                {"character_id": 100, "name": "Main", "scopes": [], "refresh_token": "rt1"},
                {"character_id": 200, "name": "Alt", "scopes": [], "refresh_token": "rt2"},
            ],
        }
        (tmp_path / "eve_auth.json").write_text(json.dumps(v2_data))
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "characters", {})
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "active_char_id", None)
        lp_web._startup_restore()
        acct = lp_web._LEGACY_ACCOUNT
        assert 100 in acct.characters
        assert 200 in acct.characters
        assert acct.active_char_id == 100

    def test_empty_file_means_no_login(self, tmp_path, monkeypatch):
        (tmp_path / "eve_auth.json").write_text("{}")
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "characters", {})
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "active_char_id", None)
        lp_web._startup_restore()
        assert lp_web._LEGACY_ACCOUNT.characters == {}
        assert lp_web._LEGACY_ACCOUNT.active_char_id is None

    def test_v2_with_deleted_active_char_falls_back(self, tmp_path, monkeypatch):
        """If active_char_id points to a character not in the list, pick first."""
        v2_data = {
            "version": 2,
            "active_char_id": 999,
            "characters": [
                {"character_id": 100, "name": "Main", "scopes": [], "refresh_token": "rt1"},
            ],
        }
        (tmp_path / "eve_auth.json").write_text(json.dumps(v2_data))
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "characters", {})
        monkeypatch.setattr(lp_web._LEGACY_ACCOUNT, "active_char_id", None)
        lp_web._startup_restore()
        assert lp_web._LEGACY_ACCOUNT.active_char_id == 100


# ── Token refresh per character ───────────────────────────────────────────────

class TestPerCharToken:
    def test_access_token_refreshes_correct_character(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        acct = _acct({
            1: {"name": "A", "refresh_token": "rt_A", "access_token": None, "expires_at": 0},
            2: {"name": "B", "refresh_token": "rt_B", "access_token": "valid_B",
                "expires_at": time.time() + 3600},
        }, active=1)
        monkeypatch.setenv("EVE_CLIENT_ID", "test123")  # client_id now comes from env

        # Mock token refresh for char 1
        fake_jwt = sso_core._b64url(b'{"a":1}') + "." + sso_core._b64url(
            json.dumps({"sub": "CHARACTER:EVE:1", "name": "A", "scp": []}).encode()
        ) + "." + sso_core._b64url(b'sig')
        mock_tok = {"access_token": fake_jwt, "refresh_token": "rt_A_new", "expires_in": 1200}
        monkeypatch.setattr(sso_core, "refresh_access_token", lambda *a, **k: mock_tok)

        token = lp_web._access_token(acct, 1)
        assert token == fake_jwt
        assert acct.characters[1]["access_token"] == fake_jwt

        # Char 2 doesn't need refresh — returns cached token
        assert lp_web._access_token(acct, 2) == "valid_B"

    def test_access_token_defaults_to_active(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        acct = _acct({
            2: {"name": "B", "refresh_token": "rt", "access_token": "tok_B",
                "expires_at": time.time() + 3600},
        }, active=2)
        assert lp_web._access_token(acct) == "tok_B"


# ── Env-based EVE login config (client_id + callback from env, not settings) ──

class TestEnvAuthConfig:
    def test_client_id_from_env(self, monkeypatch):
        monkeypatch.delenv("EVE_CLIENT_ID", raising=False)
        assert lp_web._eve_client_id() == ""
        monkeypatch.setenv("EVE_CLIENT_ID", "  cid-123  ")
        assert lp_web._eve_client_id() == "cid-123"  # trimmed

    def test_callback_from_env_overrides_localhost(self, monkeypatch):
        monkeypatch.setenv("EVE_CALLBACK_URL", "https://app.example/callback")
        assert lp_web._callback_url() == "https://app.example/callback"

    def test_callback_falls_back_to_localhost_when_unset(self, monkeypatch):
        monkeypatch.delenv("EVE_CALLBACK_URL", raising=False)
        monkeypatch.setattr(lp_web, "_SERVER_PORT", 8765)
        assert lp_web._callback_url() == "http://localhost:8765/callback"


# ── Auth status, switch, logout ───────────────────────────────────────────────

class TestAuthEndpoints:
    def _setup_two_chars(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        acct = _acct({
            10: {"name": "Main", "scopes": ["s1"], "refresh_token": "rt1",
                 "access_token": None, "expires_at": 0},
            20: {"name": "Alt", "scopes": ["s2"], "refresh_token": "rt2",
                 "access_token": None, "expires_at": 0},
        }, active=10)
        acct.skill_profiles = {10: {}, 20: {}}
        acct.bp_me_tes = {10: {}, 20: {}}
        _use_account(acct)
        return acct

    def test_auth_status_returns_all_characters(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        st = lp_web.do_auth_status({})
        assert st["logged_in"] is True
        assert len(st["characters"]) == 2
        assert st["active_char_id"] == 10
        assert st["character_id"] == 10
        assert st["name"] == "Main"

    def test_switch_active_character(self, monkeypatch, tmp_path):
        acct = self._setup_two_chars(monkeypatch, tmp_path)
        # Stub skill/bp refresh to avoid actual ESI calls
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        result = lp_web.do_auth_switch({"active_char_id": ["20"]})
        assert result["active_char_id"] == 20
        assert result["name"] == "Alt"
        assert acct.active_char_id == 20

    def test_switch_active_character_refreshes_skills_and_blueprints(self, monkeypatch, tmp_path):
        """Switching the active character re-pulls its skills + blueprints, since
        it now also drives the Industry planner."""
        self._setup_two_chars(monkeypatch, tmp_path)
        refreshed = []
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: refreshed.append(("skills", cid)))
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: refreshed.append(("bps", cid)))
        lp_web.do_auth_switch({"active_char_id": ["20"]})
        assert ("skills", 20) in refreshed
        assert ("bps", 20) in refreshed

    def test_switch_invalid_char_id_ignored(self, monkeypatch, tmp_path):
        acct = self._setup_two_chars(monkeypatch, tmp_path)
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda a, cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda a, cid: None)
        lp_web.do_auth_switch({"active_char_id": ["999"]})
        assert acct.active_char_id == 10  # unchanged

    def test_logout_single_character(self, monkeypatch, tmp_path):
        acct = self._setup_two_chars(monkeypatch, tmp_path)
        lp_web.do_auth_logout({"char_id": ["10"]})
        assert 10 not in acct.characters
        assert 20 in acct.characters
        assert acct.active_char_id == 20

    def test_logout_all(self, monkeypatch, tmp_path):
        acct = self._setup_two_chars(monkeypatch, tmp_path)
        lp_web.do_auth_logout({})
        assert acct.characters == {}
        assert acct.active_char_id is None


# ── Cross-character blueprint annotations ─────────────────────────────────────

class TestCrossCharBlueprintAnnotations:
    def test_other_owners_populated_in_scan_rows(self, monkeypatch, tmp_path):
        """Industry scan rows should carry other_owners from alt characters."""
        acct = _acct({
            1: {"name": "Main", "refresh_token": "x"},
            2: {"name": "Alt", "refresh_token": "y"},
        }, active=1)
        acct.bp_me_tes = {
            1: {681: (10, 20, True, -1)},
            2: {681: (8, 16, True, -1), 999: (0, 0, False, 50)},
        }
        rows = [
            {"blueprint_id": 681, "product_name": "Item A"},
            {"blueprint_id": 999, "product_name": "Item B"},
            {"blueprint_id": 555, "product_name": "Item C"},
        ]
        # Build the other_owners_map the same way do_ind_scan does now.
        ind_cid = acct.active_char_id
        other_owners_map = {}
        with acct.lock:
            bp_snapshot = [(ocid, acct.characters.get(ocid, {}).get("name", "?"),
                            dict(bp_map)) for ocid, bp_map in acct.bp_me_tes.items()]
        for other_cid, other_name, bp_map in bp_snapshot:
            if other_cid == ind_cid:
                continue
            for tid, entry in bp_map.items():
                other_owners_map.setdefault(tid, []).append({
                    "character_id": other_cid,
                    "name": other_name,
                    "me": entry[0], "te": entry[1],
                    "is_bpo": entry[2],
                })
        for r in rows:
            r["other_owners"] = other_owners_map.get(r["blueprint_id"], [])

        # Blueprint 681: Alt also owns it
        assert len(rows[0]["other_owners"]) == 1
        assert rows[0]["other_owners"][0]["name"] == "Alt"
        assert rows[0]["other_owners"][0]["me"] == 8
        # Blueprint 999: only Alt owns it (Main doesn't)
        assert len(rows[1]["other_owners"]) == 1
        assert rows[1]["other_owners"][0]["name"] == "Alt"
        # Blueprint 555: nobody else owns it
        assert rows[2]["other_owners"] == []


# ── Combined char data output ─────────────────────────────────────────────────

class TestCombinedCharData:
    def test_combined_output_shape(self, monkeypatch, tmp_path):
        """do_char_data returns combined_wallet, combined_jobs etc."""
        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(lp_web, "CACHE_DIR", cache)
        monkeypatch.setattr(lp_web, "JOBS_TRACK_PATH", cache / "jobs.json")
        monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", cache / "order_events.json")
        acct = _acct({
            1: {"name": "Main", "refresh_token": "rt1", "access_token": "tok1",
                "expires_at": time.time() + 3600},
            2: {"name": "Alt", "refresh_token": "rt2", "access_token": "tok2",
                "expires_at": time.time() + 3600},
        }, active=1)
        acct.skill_profiles = {1: {}, 2: {}}
        acct.bp_me_tes = {1: {}, 2: {}}
        _use_account(acct)
        # Mock all ESI fetchers
        monkeypatch.setattr(lp_web.sso_core, "fetch_wallet", lambda *a, **k: 500_000.0)
        monkeypatch.setattr(lp_web.sso_core, "fetch_skills",
                            lambda *a, **k: {"total_sp": 1_000_000, "skills": []})
        monkeypatch.setattr(lp_web.sso_core, "fetch_skillqueue", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_loyalty_points", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_industry_jobs", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_market_orders", lambda *a, **k: [])
        monkeypatch.setattr(lp_web.sso_core, "fetch_character_blueprints", lambda *a, **k: [])

        out = lp_web.do_char_data({})
        assert out["combined_wallet"] == 1_000_000.0  # 500k × 2 characters
        assert "characters" in out
        assert len(out["characters"]) == 2
        assert out["active_char_id"] == 1
        assert out["combined_jobs"] == []
        assert out["combined_orders"] == []
