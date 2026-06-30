"""
Tests for EVE Online SSO (sso_core.py) — PKCE flow, token exchange/refresh,
JWT decoding, token persistence, and authenticated ESI fetchers.

All HTTP is mocked; no real network calls.
"""
import base64
import hashlib
import json
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

import sso_core


# ── PKCE ──────────────────────────────────────────────────────────────────────

def test_make_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = sso_core.make_pkce()
    # URL-safe, unpadded.
    assert "=" not in verifier and "+" not in verifier and "/" not in verifier
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_make_pkce_is_random():
    assert sso_core.make_pkce()[0] != sso_core.make_pkce()[0]


# ── authorize URL ─────────────────────────────────────────────────────────────

def test_build_authorize_url_has_all_required_params():
    url = sso_core.build_authorize_url(
        "client123", "http://localhost:8765/callback",
        sso_core.SCOPES, "state-xyz", "chal-abc")
    parsed = urlparse(url)
    assert parsed.netloc == "login.eveonline.com"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["client123"]
    assert q["redirect_uri"] == ["http://localhost:8765/callback"]
    assert q["code_challenge"] == ["chal-abc"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["state-xyz"]
    assert q["scope"] == [" ".join(sso_core.SCOPES)]


# ── JWT decode ────────────────────────────────────────────────────────────────

def _make_jwt(payload):
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.signature"


def test_decode_jwt_payload_extracts_character_id_and_name():
    token = _make_jwt({
        "sub": "CHARACTER:EVE:95465499",
        "name": "Some Capsuleer",
        "scp": ["esi-skills.read_skills.v1", "esi-wallet.read_character_wallet.v1"],
        "exp": 1700000000,
    })
    claims = sso_core.decode_jwt_payload(token)
    assert claims["character_id"] == 95465499
    assert claims["name"] == "Some Capsuleer"
    assert claims["scopes"] == [
        "esi-skills.read_skills.v1", "esi-wallet.read_character_wallet.v1"]
    assert claims["exp"] == 1700000000


def test_decode_jwt_payload_scp_string_is_listified():
    token = _make_jwt({"sub": "CHARACTER:EVE:1", "name": "x", "scp": "one-scope"})
    assert sso_core.decode_jwt_payload(token)["scopes"] == ["one-scope"]


# ── token exchange / refresh ──────────────────────────────────────────────────

def _session_returning(payload):
    sess = MagicMock()
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    sess.post.return_value = resp
    return sess


def test_exchange_code_posts_pkce_fields():
    sess = _session_returning({"access_token": "AT", "refresh_token": "RT", "expires_in": 1199})
    out = sso_core.exchange_code("cid", "http://cb", "the-code", "the-verifier", sess)
    assert out["access_token"] == "AT"
    url = sess.post.call_args[0][0]
    data = sess.post.call_args[1]["data"]
    assert url == sso_core.TOKEN_URL
    assert data["grant_type"] == "authorization_code"
    assert data["client_id"] == "cid"
    assert data["code"] == "the-code"
    assert data["code_verifier"] == "the-verifier"


def test_refresh_access_token_posts_refresh_grant():
    sess = _session_returning({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 1199})
    out = sso_core.refresh_access_token("cid", "old-refresh", sess)
    assert out["refresh_token"] == "RT2"
    data = sess.post.call_args[1]["data"]
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "old-refresh"
    assert data["client_id"] == "cid"


# ── token persistence ─────────────────────────────────────────────────────────

def test_token_roundtrip_and_clear(tmp_path):
    assert sso_core.load_tokens(tmp_path) == {}
    blob = {"refresh_token": "RT", "character_id": 7, "name": "Pilot", "scopes": ["a"]}
    sso_core.save_tokens(tmp_path, blob)
    assert sso_core.load_tokens(tmp_path) == blob
    sso_core.clear_tokens(tmp_path)
    assert sso_core.load_tokens(tmp_path) == {}
    sso_core.clear_tokens(tmp_path)  # idempotent — no error when already gone


# ── mapping helpers ───────────────────────────────────────────────────────────

def test_skill_profile_from_skills():
    resp = {"skills": [
        {"skill_id": 3380, "trained_skill_level": 5, "active_skill_level": 5},
        {"skill_id": 3388, "trained_skill_level": 4, "active_skill_level": 3},
        {"skill_id": 9999, "active_skill_level": 2},  # no trained -> falls back to active
    ], "total_sp": 123}
    assert sso_core.skill_profile_from_skills(resp) == {3380: 5, 3388: 4, 9999: 2}


def test_skill_profile_from_empty():
    assert sso_core.skill_profile_from_skills({}) == {}
    assert sso_core.skill_profile_from_skills(None) == {}


def test_access_token_expired():
    import time
    assert sso_core.access_token_expired(0) is True
    assert sso_core.access_token_expired(None) is True
    assert sso_core.access_token_expired(time.time() + 600) is False
    assert sso_core.access_token_expired(time.time() + 30) is True  # within skew


# ── authenticated fetchers ────────────────────────────────────────────────────

def _get_session(payload):
    sess = MagicMock()
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    sess.get.return_value = resp
    return sess


def test_fetch_industry_jobs_url_headers_and_params():
    jobs = [{"job_id": 1, "activity_id": 1, "blueprint_type_id": 2047,
             "product_type_id": 587, "runs": 3, "status": "active",
             "end_date": "2026-07-01T00:00:00Z"}]
    sess = _get_session(jobs)
    out = sso_core.fetch_industry_jobs("TOKEN", 42, sess)
    assert out == jobs
    url = sess.get.call_args[0][0]
    headers = sess.get.call_args[1]["headers"]
    params = sess.get.call_args[1]["params"]
    assert url.endswith("/characters/42/industry/jobs/")
    assert headers["Authorization"] == "Bearer TOKEN"
    assert params["include_completed"] == "false"


def test_fetch_loyalty_points_authorized():
    sess = _get_session([{"corporation_id": 1000035, "loyalty_points": 50000}])
    out = sso_core.fetch_loyalty_points("TOKEN", 42, sess)
    assert out[0]["loyalty_points"] == 50000
    assert sess.get.call_args[1]["headers"]["Authorization"] == "Bearer TOKEN"
    assert sess.get.call_args[0][0].endswith("/characters/42/loyalty/points/")


def test_fetch_wallet_authorized():
    sess = _get_session(1234567.89)
    assert sso_core.fetch_wallet("TOKEN", 42, sess) == 1234567.89
    assert sess.get.call_args[0][0].endswith("/characters/42/wallet/")
