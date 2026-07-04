"""Backward-compatibility shim: re-exports all public names from the refactored
SSO modules so that `import sso_core` continues to work."""

from app.core.shared.constants import ESI, HEADERS, USER_AGENT
from app.core.shared.cache import load_json, save_json

from app.core.sso.scopes import AUTHORIZE_URL, TOKEN_URL, SCOPES, AUTH_FILE
from app.core.sso.pkce import _b64url, make_pkce, build_authorize_url
from app.core.sso.tokens import (
    exchange_code, refresh_access_token, decode_jwt_payload,
    load_tokens, save_tokens, clear_tokens, access_token_expired,
)
from app.core.esi.character import (
    fetch_public_char, fetch_skills, fetch_skillqueue, fetch_wallet,
    fetch_loyalty_points, fetch_market_orders, fetch_industry_jobs,
    fetch_character_blueprints, skill_profile_from_skills,
    owned_blueprint_lookup,
)
