"""FastAPI dependency injection: auth, database session."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User, Session


async def get_current_user(
    session_id: Optional[str] = Cookie(None, alias="session_id"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the current user from the session cookie. Raises 401 if invalid."""
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.expires_at > datetime.now(timezone.utc),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    result = await db.execute(select(User).where(User.id == session.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_optional_user(
    session_id: Optional[str] = Cookie(None, alias="session_id"),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None instead of 401 for unauthenticated requests."""
    if not session_id:
        return None
    try:
        return await get_current_user(session_id=session_id, db=db)
    except HTTPException:
        return None
