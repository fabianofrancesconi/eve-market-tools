"""User settings API router."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import json

from ..database import get_db
from ..dependencies import get_current_user, get_optional_user
from ..models import User, UserSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])

_MAX_SETTINGS_SIZE = 65536  # 64KB


@router.get("")
async def get_settings(
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user settings."""
    if not user:
        return {}
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return {}
    return json.loads(row.settings_json)


@router.post("")
async def save_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save user settings."""
    raw_body = await request.body()
    if len(raw_body) > _MAX_SETTINGS_SIZE:
        return JSONResponse({"error": "Settings too large"}, status_code=413)
    body = json.loads(raw_body)
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if row:
        row.settings_json = json.dumps(body)
    else:
        row = UserSettings(user_id=user.id, settings_json=json.dumps(body))
        db.add(row)
    await db.commit()
    return {"ok": True}


@router.post("/sync")
async def sync_settings(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sync full client settings (merge)."""
    raw_body = await request.body()
    if len(raw_body) > _MAX_SETTINGS_SIZE:
        return JSONResponse({"error": "Settings too large"}, status_code=413)
    body = json.loads(raw_body)
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if row:
        existing = json.loads(row.settings_json)
        existing.update(body)
        row.settings_json = json.dumps(existing)
    else:
        row = UserSettings(user_id=user.id, settings_json=json.dumps(body))
        db.add(row)
    await db.commit()
    return {"ok": True}
