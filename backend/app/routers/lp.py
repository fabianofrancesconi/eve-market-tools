"""LP Store API router."""
import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
import requests as req_lib

from ..config import settings
from ..database import get_db
from ..dependencies import get_optional_user
from ..models import User
from ..core.shared.constants import JITA_STATION_ID, JITA_REGION_ID, TRADE_HUBS
from ..core.lp.offers import resolve_corp_id, get_offers
from ..core.lp.evaluate import evaluate
from ..core.lp.liquidity import enrich_liquidity
from ..core.lp.detail import build_detail
from ..core.market.prices import fetch_prices, fetch_prices_esi
from ..core.market.history import fetch_history_volumes, fetch_history_prices
from ..core.shared.names import resolve_names, resolve_volumes
from ..core.esi.client import LPError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lp", tags=["lp"])
_session = req_lib.Session()


def _parse_ids(raw: str) -> list[int]:
    """Safely parse a comma-separated string of IDs."""
    result = []
    for t in raw.split(","):
        t = t.strip()
        if t.isdigit():
            result.append(int(t))
    return result


@router.get("/corps")
async def get_corps():
    """List NPC corporations with LP stores."""
    from ..core.shared.constants import ESI, HEADERS
    try:
        r = await asyncio.to_thread(
            _session.get, f"{ESI}/corporations/npccorps/", headers=HEADERS, timeout=30
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("Failed to fetch NPC corps")
        return []


@router.get("/scan")
async def scan(
    corp: str = Query(...),
    lp_budget: int = Query(100000),
    station_id: int = Query(JITA_STATION_ID),
    sales_tax: float = Query(0.08),
    broker_fee: float = Query(0.03),
    use_esi_prices: bool = Query(False),
):
    """Scan LP store offers and evaluate profitability."""
    cache_dir = settings.cache_dir
    try:
        corp_id, corp_name = await asyncio.to_thread(resolve_corp_id, corp, _session)
    except LPError as e:
        return {"error": str(e)}

    offers = await asyncio.to_thread(get_offers, corp_id, _session, cache_dir)
    type_ids = set()
    for o in offers:
        type_ids.add(o["type_id"])
        for req in o.get("required_items", []):
            type_ids.add(req["type_id"])

    if use_esi_prices:
        hub = TRADE_HUBS.get(station_id, {})
        region_id = hub.get("region_id", JITA_REGION_ID)
        prices = await asyncio.to_thread(
            fetch_prices_esi, list(type_ids), _session, station_id, region_id, cache_dir
        )
    else:
        prices = await asyncio.to_thread(fetch_prices, list(type_ids), _session, station_id)

    sellable, unsellable = evaluate(offers, prices, lp_budget, sales_tax, broker_fee)
    names = await asyncio.to_thread(
        resolve_names, [r["name_id"] for r in sellable + unsellable], _session, cache_dir
    )

    for r in sellable + unsellable:
        r["name"] = names.get(r["name_id"], str(r["name_id"]))

    return {
        "corp_id": corp_id,
        "corp_name": corp_name,
        "sellable": sellable,
        "unsellable": unsellable,
    }


@router.get("/liquidity")
async def liquidity(
    offer_ids: str = Query(""),
    type_ids: str = Query(""),
    station_id: int = Query(JITA_STATION_ID),
):
    """Background liquidity enrichment for scan results."""
    cache_dir = settings.cache_dir
    hub = TRADE_HUBS.get(station_id, {})
    region_id = hub.get("region_id", JITA_REGION_ID)

    tids = _parse_ids(type_ids)
    if not tids:
        return {}

    vols = await asyncio.to_thread(fetch_history_volumes, tids, region_id, _session, cache_dir)
    return vols


@router.get("/detail")
async def detail(
    offer_id: int = Query(...),
    corp_id: int = Query(...),
    lp_budget: int = Query(100000),
    station_id: int = Query(JITA_STATION_ID),
    sales_tax: float = Query(0.08),
    broker_fee: float = Query(0.03),
):
    """Full detail breakdown for one LP offer."""
    cache_dir = settings.cache_dir
    offers = await asyncio.to_thread(get_offers, corp_id, _session, cache_dir)
    offer = next((o for o in offers if o.get("offer_id") == offer_id), None)
    if not offer:
        return {"error": "Offer not found"}

    type_ids = [offer["type_id"]] + [r["type_id"] for r in offer.get("required_items", [])]
    prices = await asyncio.to_thread(fetch_prices, type_ids, _session, station_id)
    names = await asyncio.to_thread(resolve_names, type_ids, _session, cache_dir)
    volumes = await asyncio.to_thread(resolve_volumes, type_ids, _session, cache_dir)

    return build_detail(offer, prices, names, volumes, lp_budget, sales_tax, broker_fee)


@router.get("/history")
async def history(
    type_id: int = Query(...),
    region_id: int = Query(JITA_REGION_ID),
):
    """Market price history for a type."""
    from ..core.shared.constants import ESI, HEADERS
    from ..core.shared.cache import load_json, save_json
    from pathlib import Path
    import time

    cache_dir = settings.cache_dir
    path = Path(cache_dir) / f"mhist_{region_id}_{type_id}.json"
    now = time.time()
    cached = load_json(path, None)
    if cached and now - cached.get("_ts", 0) < 12 * 3600:
        return cached["data"]

    r = await asyncio.to_thread(
        _session.get, f"{ESI}/markets/{region_id}/history/",
        params={"type_id": type_id}, headers=HEADERS, timeout=20
    )
    if r.status_code != 200:
        return []
    data = sorted(r.json(), key=lambda x: x["date"])
    save_json(path, {"_ts": now, "data": data})
    return data
