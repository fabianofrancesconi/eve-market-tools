"""Token exchange, refresh, JWT decode, persistence, and expiry check."""
import base64
import json
import time
from pathlib import Path

from ..shared.constants import USER_AGENT
from ..shared.cache import load_json, save_json
from .scopes import TOKEN_URL, AUTH_FILE

_TOKEN_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENT,
}


def exchange_code(client_id, redirect_uri, code, verifier, session):
    """Trade an authorization code for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    r = session.post(TOKEN_URL, data=data, headers=_TOKEN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def refresh_access_token(client_id, refresh_token, session):
    """Exchange a refresh token for a fresh access token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    r = session.post(TOKEN_URL, data=data, headers=_TOKEN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def decode_jwt_payload(access_token):
    """Decode the JWT payload (no signature check)."""
    parts = access_token.split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    sub = claims.get("sub", "")
    character_id = int(sub.split(":")[-1]) if sub.startswith("CHARACTER:EVE:") else None
    scp = claims.get("scp") or []
    if isinstance(scp, str):
        scp = [scp]
    return {
        "character_id": character_id,
        "name": claims.get("name"),
        "scopes": scp,
        "exp": claims.get("exp"),
    }


def _auth_path(cache_dir):
    return Path(cache_dir) / AUTH_FILE


def load_tokens(cache_dir):
    """Load the persisted auth blob or {} if none."""
    return load_json(_auth_path(cache_dir), {})


def save_tokens(cache_dir, data):
    save_json(_auth_path(cache_dir), data)


def clear_tokens(cache_dir):
    p = _auth_path(cache_dir)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def access_token_expired(expires_at, skew=60):
    """True if the access token should be refreshed now."""
    return not expires_at or time.time() >= (expires_at - skew)
