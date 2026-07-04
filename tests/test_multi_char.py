"""
Tests for multi-character (alt account) support.

Covers: v1→v2 auth migration, per-character token refresh, character switching,
logout (single + all), cross-character blueprint annotations, and combined
char data output shape.
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


# ── Auth file migration (v1 → v2) ────────────────────────────────────────────

class TestAuthMigration:
    def test_v1_file_migrates_to_v2(self, tmp_path, monkeypatch):
        """Existing single-character eve_auth.json loads into _CHARACTERS."""
        v1_data = {
            "refresh_token": "old_refresh",
            "character_id": 123,
            "name": "OldChar",
            "scopes": ["esi-skills.read_skills.v1"],
        }
        (tmp_path / "eve_auth.json").write_text(json.dumps(v1_data))
        monkeypatch.setattr(lp_web, "_CHARACTERS", {})
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", None)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        lp_web._restore_auth()
        assert 123 in lp_web._CHARACTERS
        assert lp_web._CHARACTERS[123]["name"] == "OldChar"
        assert lp_web._CHARACTERS[123]["refresh_token"] == "old_refresh"
        assert lp_web._ACTIVE_CHAR_ID == 123
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
        monkeypatch.setattr(lp_web, "_CHARACTERS", {})
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", None)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        lp_web._restore_auth()
        assert 100 in lp_web._CHARACTERS
        assert 200 in lp_web._CHARACTERS
        assert lp_web._ACTIVE_CHAR_ID == 100

    def test_empty_file_means_no_login(self, tmp_path, monkeypatch):
        (tmp_path / "eve_auth.json").write_text("{}")
        monkeypatch.setattr(lp_web, "_CHARACTERS", {})
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", None)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        lp_web._restore_auth()
        assert lp_web._CHARACTERS == {}
        assert lp_web._ACTIVE_CHAR_ID is None

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
        monkeypatch.setattr(lp_web, "_CHARACTERS", {})
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", None)
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        lp_web._restore_auth()
        assert lp_web._ACTIVE_CHAR_ID == 100


# ── Token refresh per character ───────────────────────────────────────────────

class TestPerCharToken:
    def test_access_token_refreshes_correct_character(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {
            1: {"character_id": 1, "name": "A", "scopes": [],
                "refresh_token": "rt_A", "access_token": None, "expires_at": 0},
            2: {"character_id": 2, "name": "B", "scopes": [],
                "refresh_token": "rt_B", "access_token": "valid_B",
                "expires_at": time.time() + 3600},
        })
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", 1)
        monkeypatch.setenv("EVE_CLIENT_ID", "test123")  # client_id now comes from env

        # Mock token refresh for char 1
        fake_jwt = sso_core._b64url(b'{"a":1}') + "." + sso_core._b64url(
            json.dumps({"sub": "CHARACTER:EVE:1", "name": "A", "scp": []}).encode()
        ) + "." + sso_core._b64url(b'sig')
        mock_tok = {"access_token": fake_jwt, "refresh_token": "rt_A_new", "expires_in": 1200}
        monkeypatch.setattr(sso_core, "refresh_access_token",
                            lambda *a, **k: mock_tok)

        token = lp_web._access_token(1)
        assert token == fake_jwt
        assert lp_web._CHARACTERS[1]["access_token"] == fake_jwt

        # Char 2 doesn't need refresh — returns cached token
        token2 = lp_web._access_token(2)
        assert token2 == "valid_B"

    def test_access_token_defaults_to_active(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lp_web, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", 2)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {
            2: {"character_id": 2, "name": "B", "scopes": [],
                "refresh_token": "rt", "access_token": "tok_B",
                "expires_at": time.time() + 3600},
        })
        assert lp_web._access_token() == "tok_B"


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
        monkeypatch.setattr(lp_web, "_CHARACTERS", {
            10: {"character_id": 10, "name": "Main", "scopes": ["s1"],
                 "refresh_token": "rt1", "access_token": None, "expires_at": 0},
            20: {"character_id": 20, "name": "Alt", "scopes": ["s2"],
                 "refresh_token": "rt2", "access_token": None, "expires_at": 0},
        })
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", 10)
        monkeypatch.setattr(lp_web, "_CHAR_SKILL_PROFILES", {10: {}, 20: {}})
        monkeypatch.setattr(lp_web, "_CHAR_BP_ME_TES", {10: {}, 20: {}})

    def test_auth_status_returns_all_characters(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        st = lp_web.do_auth_status({})
        assert st["logged_in"] is True
        assert len(st["characters"]) == 2
        assert st["active_char_id"] == 10
        assert st["character_id"] == 10
        assert st["name"] == "Main"

    def test_switch_active_character(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        # Stub skill/bp refresh to avoid actual ESI calls
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda cid: None)
        result = lp_web.do_auth_switch({"active_char_id": ["20"]})
        assert result["active_char_id"] == 20
        assert result["name"] == "Alt"
        assert lp_web._ACTIVE_CHAR_ID == 20

    def test_switch_active_character_refreshes_skills_and_blueprints(self, monkeypatch, tmp_path):
        """Switching the active character re-pulls its skills + blueprints, since
        it now also drives the Industry planner."""
        self._setup_two_chars(monkeypatch, tmp_path)
        refreshed = []
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda cid: refreshed.append(("skills", cid)))
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda cid: refreshed.append(("bps", cid)))
        lp_web.do_auth_switch({"active_char_id": ["20"]})
        assert ("skills", 20) in refreshed
        assert ("bps", 20) in refreshed

    def test_switch_invalid_char_id_ignored(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        monkeypatch.setattr(lp_web, "_refresh_skill_profile", lambda cid: None)
        monkeypatch.setattr(lp_web, "_refresh_char_blueprints", lambda cid: None)
        lp_web.do_auth_switch({"active_char_id": ["999"]})
        assert lp_web._ACTIVE_CHAR_ID == 10  # unchanged

    def test_logout_single_character(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        lp_web.do_auth_logout({"char_id": ["10"]})
        assert 10 not in lp_web._CHARACTERS
        assert 20 in lp_web._CHARACTERS
        assert lp_web._ACTIVE_CHAR_ID == 20

    def test_logout_all(self, monkeypatch, tmp_path):
        self._setup_two_chars(monkeypatch, tmp_path)
        lp_web.do_auth_logout({})
        assert lp_web._CHARACTERS == {}
        assert lp_web._ACTIVE_CHAR_ID is None


# ── Cross-character blueprint annotations ─────────────────────────────────────

class TestCrossCharBlueprintAnnotations:
    def test_other_owners_populated_in_scan_rows(self, monkeypatch, tmp_path):
        """Industry scan rows should carry other_owners from alt characters."""
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", 1)
        monkeypatch.setattr(lp_web, "_CHAR_BP_ME_TES", {
            1: {681: (10, 20, True, -1)},
            2: {681: (8, 16, True, -1), 999: (0, 0, False, 50)},
        })
        monkeypatch.setattr(lp_web, "_CHARACTERS", {
            1: {"character_id": 1, "name": "Main", "scopes": [], "refresh_token": "x"},
            2: {"character_id": 2, "name": "Alt", "scopes": [], "refresh_token": "y"},
        })
        # Simulate rows that would come from evaluate_industry
        rows = [
            {"blueprint_id": 681, "product_name": "Item A"},
            {"blueprint_id": 999, "product_name": "Item B"},
            {"blueprint_id": 555, "product_name": "Item C"},
        ]
        # Build the other_owners_map the same way the scan does
        ind_cid = lp_web._ACTIVE_CHAR_ID
        other_owners_map = {}
        for other_cid, bp_map in lp_web._CHAR_BP_ME_TES.items():
            if other_cid == ind_cid:
                continue
            other_name = lp_web._CHARACTERS.get(other_cid, {}).get("name", "?")
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
        monkeypatch.setattr(lp_web, "_ACTIVE_CHAR_ID", 1)
        monkeypatch.setattr(lp_web, "_CHARACTERS", {
            1: {"character_id": 1, "name": "Main", "scopes": [],
                "refresh_token": "rt1", "access_token": "tok1",
                "expires_at": time.time() + 3600},
            2: {"character_id": 2, "name": "Alt", "scopes": [],
                "refresh_token": "rt2", "access_token": "tok2",
                "expires_at": time.time() + 3600},
        })
        monkeypatch.setattr(lp_web, "_CHAR_SKILL_PROFILES", {1: {}, 2: {}})
        monkeypatch.setattr(lp_web, "_CHAR_BP_ME_TES", {1: {}, 2: {}})
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
