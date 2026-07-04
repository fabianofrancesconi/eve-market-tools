"""Tests for the auth service PKCE state management."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("EVE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EVE_CALLBACK_URL", "http://localhost/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-key-for-auth-tests-32bytes!")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///test.db")


def test_start_login_returns_url_and_state():
    from app.services.auth_service import start_login, _pkce_store
    url, state = start_login(user_id=None)
    assert "login.eveonline.com" in url
    assert state in _pkce_store
    assert "verifier" in _pkce_store[state]


def test_start_login_stores_user_id():
    from app.services.auth_service import start_login, _pkce_store
    url, state = start_login(user_id="user-123")
    assert _pkce_store[state]["user_id"] == "user-123"


def test_start_login_unique_states():
    from app.services.auth_service import start_login
    _, s1 = start_login()
    _, s2 = start_login()
    assert s1 != s2
