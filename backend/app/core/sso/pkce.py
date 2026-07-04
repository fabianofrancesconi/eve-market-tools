"""PKCE helpers and authorize URL construction."""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

from .scopes import AUTHORIZE_URL


def _b64url(raw: bytes) -> str:
    """base64url with no padding, per RFC 7636."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce():
    """Return (code_verifier, code_challenge)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(client_id, redirect_uri, scopes, state, challenge):
    """Full authorize URL for the PKCE flow."""
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTHORIZE_URL + "?" + urlencode(params)
