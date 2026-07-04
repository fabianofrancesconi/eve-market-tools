"""Character data API router."""
import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import requests as req_lib

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User, Character
from ..utils.encryption import decrypt_token, encrypt_token
from ..config import settings
from ..core.sso.tokens import refresh_access_token
from ..core.esi.character import (
    fetch_skills, fetch_skillqueue, fetch_wallet,
    fetch_loyalty_points, fetch_industry_jobs, fetch_market_orders,
    skill_profile_from_skills,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/character", tags=["character"])
_session = req_lib.Session()


async def _get_token(char: Character, db: AsyncSession) -> str:
    """Get a fresh access token for a character."""
    refresh_token = decrypt_token(char.refresh_token_encrypted)
    tokens = await asyncio.to_thread(
        refresh_access_token,
        client_id=settings.eve_client_id,
        refresh_token=refresh_token,
        session=_session,
    )
    new_refresh = tokens.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        char.refresh_token_encrypted = encrypt_token(new_refresh)
        await db.commit()
    return tokens["access_token"]


@router.get("/data")
async def character_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch all ESI data for linked characters."""
    result = await db.execute(
        select(Character).where(Character.user_id == user.id)
    )
    characters = list(result.scalars())

    char_data = []
    for char in characters:
        try:
            token = await _get_token(char, db)
            cid = char.character_id

            data = {"character_id": cid, "name": char.character_name, "is_active": char.is_active}

            try:
                data["wallet"] = await asyncio.to_thread(fetch_wallet, token, cid, _session)
            except Exception:
                logger.exception("Failed to fetch wallet for character %s", cid)
                data["wallet"] = None

            try:
                skills_resp = await asyncio.to_thread(fetch_skills, token, cid, _session)
                data["total_sp"] = skills_resp.get("total_sp")
                data["unallocated_sp"] = skills_resp.get("unallocated_sp")
            except Exception:
                logger.exception("Failed to fetch skills for character %s", cid)
                data["total_sp"] = None
                data["unallocated_sp"] = None

            try:
                data["skillqueue"] = await asyncio.to_thread(fetch_skillqueue, token, cid, _session)
            except Exception:
                logger.exception("Failed to fetch skillqueue for character %s", cid)
                data["skillqueue"] = []

            try:
                data["loyalty_points"] = await asyncio.to_thread(fetch_loyalty_points, token, cid, _session)
            except Exception:
                logger.exception("Failed to fetch loyalty points for character %s", cid)
                data["loyalty_points"] = []

            try:
                data["industry_jobs"] = await asyncio.to_thread(
                    fetch_industry_jobs, token, cid, _session, include_completed=True
                )
            except Exception:
                logger.exception("Failed to fetch industry jobs for character %s", cid)
                data["industry_jobs"] = []

            try:
                data["market_orders"] = await asyncio.to_thread(fetch_market_orders, token, cid, _session)
            except Exception:
                logger.exception("Failed to fetch market orders for character %s", cid)
                data["market_orders"] = []

            char_data.append(data)
        except Exception:
            logger.exception("Failed to refresh token for character %s", char.character_id)
            char_data.append({
                "character_id": char.character_id,
                "name": char.character_name,
                "is_active": char.is_active,
                "error": "Failed to refresh token",
            })

    return char_data
