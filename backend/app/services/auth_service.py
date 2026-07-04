"""Authentication service: EVE SSO flow, session management, token storage."""
import asyncio
import secrets
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import User, Character, Session
from ..utils.encryption import encrypt_token, decrypt_token
from ..core.sso.pkce import make_pkce, build_authorize_url
from ..core.sso.tokens import exchange_code, refresh_access_token, decode_jwt_payload
from ..core.sso.scopes import SCOPES


# In-memory PKCE state store (for production, use Redis or DB with TTL)
_PKCE_TTL = 300  # 5 minutes
_PKCE_MAX_SIZE = 1000

_pkce_store: dict[str, dict] = {}


def _cleanup_pkce():
    """Remove expired entries."""
    now = time.time()
    expired = [k for k, v in _pkce_store.items() if now - v.get("created_at", 0) > _PKCE_TTL]
    for k in expired:
        del _pkce_store[k]


def start_login(user_id: str | None = None) -> tuple[str, str]:
    """Begin SSO flow: generate PKCE, build authorize URL, store state.
    Returns (authorize_url, state)."""
    _cleanup_pkce()
    if len(_pkce_store) >= _PKCE_MAX_SIZE:
        # Evict oldest
        oldest = min(_pkce_store, key=lambda k: _pkce_store[k].get("created_at", 0))
        del _pkce_store[oldest]

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(32)
    _pkce_store[state] = {
        "verifier": verifier,
        "user_id": user_id,
        "created_at": time.time(),
    }
    url = build_authorize_url(
        client_id=settings.eve_client_id,
        redirect_uri=settings.eve_callback_url,
        scopes=SCOPES,
        state=state,
        challenge=challenge,
    )
    return url, state


async def complete_login(code: str, state: str, db: AsyncSession, session) -> tuple[str, str]:
    """Complete SSO flow: exchange code, create/update user and character.
    Returns (session_cookie_value, character_name)."""
    pkce = _pkce_store.pop(state, None)
    if not pkce or time.time() - pkce.get("created_at", 0) > _PKCE_TTL:
        raise ValueError("Invalid or expired state")

    tokens = await asyncio.to_thread(
        exchange_code,
        client_id=settings.eve_client_id,
        redirect_uri=settings.eve_callback_url,
        code=code,
        verifier=pkce["verifier"],
        session=session,
    )

    jwt_info = decode_jwt_payload(tokens["access_token"])
    character_id = jwt_info["character_id"]
    character_name = jwt_info["name"]
    refresh_token = tokens.get("refresh_token", "")

    # Find or create user
    user_id = pkce.get("user_id")
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    else:
        # Check if this character already has a user
        result = await db.execute(
            select(Character).where(Character.character_id == character_id)
        )
        existing_char = result.scalar_one_or_none()
        if existing_char:
            result = await db.execute(select(User).where(User.id == existing_char.user_id))
            user = result.scalar_one_or_none()
        else:
            user = None

    if not user:
        user = User()
        db.add(user)
        await db.flush()

    user.last_login = datetime.now(timezone.utc)

    # Upsert character
    result = await db.execute(
        select(Character).where(
            Character.user_id == user.id,
            Character.character_id == character_id,
        )
    )
    char = result.scalar_one_or_none()
    if char:
        char.refresh_token_encrypted = encrypt_token(refresh_token)
        char.character_name = character_name
        char.scopes = " ".join(jwt_info.get("scopes", []))
        char.last_used = datetime.now(timezone.utc)
    else:
        # Deactivate other characters, set this one active
        result = await db.execute(
            select(Character).where(Character.user_id == user.id)
        )
        for c in result.scalars():
            c.is_active = False

        char = Character(
            user_id=user.id,
            character_id=character_id,
            character_name=character_name,
            refresh_token_encrypted=encrypt_token(refresh_token),
            scopes=" ".join(jwt_info.get("scopes", [])),
            is_active=True,
        )
        db.add(char)

    # Create session
    app_session = Session(user_id=user.id)
    db.add(app_session)
    await db.commit()

    return app_session.id, character_name


async def logout(user: User, db: AsyncSession, character_id: int | None = None):
    """Remove a character (or all) and clean up sessions if no characters remain."""
    if character_id:
        result = await db.execute(
            select(Character).where(
                Character.user_id == user.id,
                Character.character_id == character_id,
            )
        )
        char = result.scalar_one_or_none()
        if char:
            await db.delete(char)
    else:
        result = await db.execute(
            select(Character).where(Character.user_id == user.id)
        )
        for char in result.scalars():
            await db.delete(char)

    # Always invalidate sessions when logging out completely
    if not character_id:
        await db.execute(delete(Session).where(Session.user_id == user.id))

    await db.commit()


async def get_active_token(user: User, db: AsyncSession, http_session) -> tuple[str, int] | None:
    """Get a fresh access token for the user's active character.
    Returns (access_token, character_id) or None."""
    result = await db.execute(
        select(Character).where(
            Character.user_id == user.id,
            Character.is_active == True,
        )
    )
    char = result.scalar_one_or_none()
    if not char:
        return None

    refresh_token = decrypt_token(char.refresh_token_encrypted)
    tokens = await asyncio.to_thread(
        refresh_access_token,
        client_id=settings.eve_client_id,
        refresh_token=refresh_token,
        session=http_session,
    )

    # Update stored refresh token if rotated
    new_refresh = tokens.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        char.refresh_token_encrypted = encrypt_token(new_refresh)
        await db.commit()

    return tokens["access_token"], char.character_id
