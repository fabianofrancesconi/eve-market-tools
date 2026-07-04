"""Auth router: EVE SSO login flow, session management."""
from fastapi import APIRouter, Depends, Response, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import requests as req_lib

from ..config import settings
from ..database import get_db
from ..dependencies import get_current_user, get_optional_user
from ..models import User
from ..services.auth_service import start_login, complete_login, logout

router = APIRouter(prefix="/api/auth", tags=["auth"])

_http_session = req_lib.Session()


@router.get("/login")
async def login(user: User | None = Depends(get_optional_user)):
    """Start EVE SSO login flow. Returns the authorize URL."""
    url, state = start_login(user_id=user.id if user else None)
    return {"authorize_url": url}


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """EVE SSO callback — exchange code for tokens, create session."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        session_id, character_name = await complete_login(code, state, db, _http_session)
    except ValueError as e:
        logger.warning("Auth callback failed: %s", e)
        return RedirectResponse(url="/?auth_error=expired")
    except Exception as e:
        logger.exception("Auth callback error")
        return RedirectResponse(url="/?auth_error=failed")
    response = RedirectResponse(url="/character")
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=not settings.cors_origins.startswith("http://localhost"),
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/status")
async def status(user: User | None = Depends(get_optional_user), db: AsyncSession = Depends(get_db)):
    """Current auth status."""
    if not user:
        return {"logged_in": False}
    from sqlalchemy import select
    from ..models import Character
    result = await db.execute(
        select(Character).where(Character.user_id == user.id)
    )
    chars = [
        {"character_id": c.character_id, "name": c.character_name, "is_active": c.is_active}
        for c in result.scalars()
    ]
    return {"logged_in": True, "characters": chars}


@router.post("/logout")
async def do_logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    character_id: int | None = None,
):
    """Remove a character or all characters."""
    await logout(user, db, character_id)
    if not character_id:
        response.delete_cookie("session_id")
    return {"ok": True}


@router.post("/switch")
async def switch_character(
    character_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch active character."""
    from sqlalchemy import select
    from ..models import Character
    result = await db.execute(
        select(Character).where(Character.user_id == user.id)
    )
    found = False
    for char in result.scalars():
        char.is_active = (char.character_id == character_id)
        if char.is_active:
            found = True
    if not found:
        return {"ok": False, "error": "Character not found"}
    await db.commit()
    return {"ok": True}
