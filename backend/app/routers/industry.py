"""Industry scanner API router with SSE streaming."""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import requests as req_lib

from ..config import settings
from ..database import get_db
from ..dependencies import get_optional_user
from ..models import User
from ..services.auth_service import get_active_token
from ..core.shared.constants import JITA_STATION_ID, JITA_REGION_ID, TRADE_HUBS
from ..core.market.prices import fetch_prices
from ..core.market.history import fetch_history_volumes
from ..core.industry.sde import (
    load_sde_industry, connect_sde, top_market_groups,
    expand_market_groups, manufacturing_candidates, candidates_for_blueprints,
    fetch_adjusted_prices, volumes_for, market_group_names,
)
from ..core.industry.blueprints import assemble_blueprints, assemble_invention
from ..core.industry.evaluate import evaluate_industry
from ..core.industry.costs import tradeability, bulk_training_time, missing_skills
from ..core.industry.detail import build_industry_detail
from ..core.arbitrage.scanner import fetch_fuzzwork_region
from ..core.shared.names import resolve_names
from ..core.esi.character import (
    fetch_skills, fetch_character_blueprints, fetch_industry_jobs,
    skill_profile_from_skills, owned_blueprint_lookup,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/industry", tags=["industry"])
_session = req_lib.Session()

# Manufacturing activity id in the EVE SDE / ESI industry jobs.
_MANUFACTURING = 1


def _parse_ids(raw: str) -> list[int]:
    """Safely parse a comma-separated string of IDs."""
    result = []
    for t in raw.split(","):
        t = t.strip()
        if t.isdigit():
            result.append(int(t))
    return result


def _build_job_map(jobs) -> dict[int, dict]:
    """ESI industry jobs -> {blueprint_type_id: {end_date, runs, status}} for the
    soonest-ending active/paused manufacturing job of each blueprint."""
    out: dict[int, dict] = {}
    for j in jobs or []:
        if j.get("activity_id") != _MANUFACTURING:
            continue
        if j.get("status") not in ("active", "paused", "ready"):
            continue
        bpid = j.get("blueprint_type_id")
        if bpid is None:
            continue
        end = j.get("end_date")
        prev = out.get(bpid)
        if prev is None or (end and prev.get("end_date") and end < prev["end_date"]):
            out[bpid] = {"end_date": end, "runs": j.get("runs"), "status": j.get("status")}
    return out


async def _load_character_industry(user: Optional[User], db: AsyncSession):
    """Fetch the active character's skills, owned blueprints and running jobs.
    Returns (skill_profile, owned_me_te, job_map). Empty on any failure or when
    not logged in, so the caller falls back to the anonymous (assumed) path."""
    if not user:
        return {}, {}, {}
    try:
        tok = await get_active_token(user, db, _session)
        if not tok:
            return {}, {}, {}
        access_token, cid = tok
        skills = await asyncio.to_thread(fetch_skills, access_token, cid, _session)
        skill_profile = skill_profile_from_skills(skills)
        bps_resp = await asyncio.to_thread(fetch_character_blueprints, access_token, cid, _session)
        owned_me_te = owned_blueprint_lookup(bps_resp)
        jobs = await asyncio.to_thread(fetch_industry_jobs, access_token, cid, _session)
        return skill_profile, owned_me_te, _build_job_map(jobs)
    except Exception:
        logger.exception("Failed to load character industry data")
        return {}, {}, {}


@router.get("/groups")
async def groups():
    """Market group categories for the industry dropdown."""
    cache_dir = settings.cache_dir
    load_sde_industry(cache_dir, session=_session)
    conn = connect_sde(cache_dir)
    try:
        return top_market_groups(conn)
    finally:
        conn.close()


@router.get("/scan")
async def scan(
    group_ids: str = Query(""),
    station_id: int = Query(JITA_STATION_ID),
    me: int = Query(0),
    te: int = Query(0),
    job_rate: float = Query(0.06),
    sales_tax: float = Query(0.075),
    broker_fee: float = Query(0.03),
    runs: int = Query(1),
    skills_level: int = Query(0),
    favorites: str = Query(""),
    use_my_skills: bool = Query(False),
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE-streaming industry scan."""
    # When "My skills" is on and logged in, use the character's real trained
    # skills + owned blueprints (ME/TE) and annotate running manufacturing jobs.
    skill_profile, owned_me_te, job_map = {}, {}, {}
    if use_my_skills:
        skill_profile, owned_me_te, job_map = await _load_character_industry(user, db)

    def generate():
        cache_dir = settings.cache_dir
        hub = TRADE_HUBS.get(station_id, {})
        region_id = hub.get("region_id", JITA_REGION_ID)

        yield f"event: progress\ndata: {json.dumps({'pct': 5, 'msg': 'Loading SDE...'})}\n\n"
        load_sde_industry(cache_dir, session=_session)
        conn = connect_sde(cache_dir)

        try:
            gids = _parse_ids(group_ids)
            all_gids = expand_market_groups(conn, gids) if gids else None

            yield f"event: progress\ndata: {json.dumps({'pct': 15, 'msg': 'Finding candidates...'})}\n\n"
            candidates = manufacturing_candidates(conn, all_gids)

            # Add favorites
            fav_ids = _parse_ids(favorites)
            if fav_ids:
                fav_candidates = candidates_for_blueprints(conn, fav_ids)
                existing = {c["blueprint_id"] for c in candidates}
                candidates.extend(c for c in fav_candidates if c["blueprint_id"] not in existing)

            yield f"event: progress\ndata: {json.dumps({'pct': 25, 'msg': 'Assembling blueprints...'})}\n\n"
            bps = assemble_blueprints(conn, candidates)
            bps = assemble_invention(conn, bps)

            yield f"event: progress\ndata: {json.dumps({'pct': 40, 'msg': 'Fetching prices...'})}\n\n"
            all_type_ids = set()
            for bp in bps:
                all_type_ids.add(bp["product_id"])
                for mid, _ in bp.get("materials", []):
                    all_type_ids.add(mid)
                inv = bp.get("invention")
                if inv:
                    for dcid, _ in inv.get("datacores", []):
                        all_type_ids.add(dcid)

            prices = fetch_prices(list(all_type_ids), _session, station_id)

            yield f"event: progress\ndata: {json.dumps({'pct': 60, 'msg': 'Fetching adjusted prices...'})}\n\n"
            adjusted = fetch_adjusted_prices(_session, cache_dir)

            yield f"event: progress\ndata: {json.dumps({'pct': 65, 'msg': 'Fetching BPO prices...'})}\n\n"
            t1_bp_ids = [bp["blueprint_id"] for bp in bps if not bp.get("invention")]
            bpo_prices = {}
            if t1_bp_ids:
                bpo_region = fetch_fuzzwork_region(t1_bp_ids, region_id, _session)
                bpo_prices = {
                    bid: v["sell_min"]
                    for bid, v in bpo_region.items()
                    if v.get("sell_min")
                }

            yield f"event: progress\ndata: {json.dumps({'pct': 70, 'msg': 'Evaluating...'})}\n\n"
            vols = volumes_for(conn, list(all_type_ids))
            params = {
                "me": me, "te": te, "job_rate": job_rate,
                "sales_tax": sales_tax, "broker_fee": broker_fee,
                "runs": runs, "skills_level": skills_level,
                "volumes": vols,
                "bpo_prices": bpo_prices,
            }
            rows = evaluate_industry(bps, prices, adjusted, params)

            # Annotate training time for blueprints that need skills
            skill_profile = {}  # TODO: fetch from character when authenticated
            train_map = bulk_training_time(bps, skill_profile, conn, skills_level)
            for r in rows:
                r["train_hours"] = train_map.get(r["blueprint_id"])

            yield f"event: progress\ndata: {json.dumps({'pct': 85, 'msg': 'Resolving names...'})}\n\n"
            name_ids = list(set(r["product_id"] for r in rows[:200]))
            names = resolve_names(name_ids, _session, cache_dir)
            for r in rows:
                r["product_name"] = names.get(r["product_id"], r.get("product_name", ""))

            # Resolve market group names for subcategory labels
            gids_set = {r["market_group_id"] for r in rows if r.get("market_group_id")}
            gnames = market_group_names(conn, gids_set)
            for r in rows:
                r["group_name"] = gnames.get(r.get("market_group_id"), "")

            yield f"event: progress\ndata: {json.dumps({'pct': 100, 'msg': 'Done'})}\n\n"
            yield f"event: result\ndata: {json.dumps(rows)}\n\n"
        finally:
            conn.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/detail")
async def detail(
    blueprint_id: int = Query(...),
    station_id: int = Query(JITA_STATION_ID),
    me: int = Query(0),
    te: int = Query(0),
    job_rate: float = Query(0.06),
    sales_tax: float = Query(0.075),
    broker_fee: float = Query(0.03),
    runs: int = Query(1),
    skills_level: int = Query(0),
):
    """Full detail breakdown for one blueprint."""
    cache_dir = settings.cache_dir
    hub = TRADE_HUBS.get(station_id, {})
    region_id = hub.get("region_id", JITA_REGION_ID)
    load_sde_industry(cache_dir, session=_session)
    conn = connect_sde(cache_dir)
    try:
        candidates = candidates_for_blueprints(conn, [blueprint_id])
        if not candidates:
            return {"error": "Blueprint not found"}
        bps = assemble_blueprints(conn, candidates)
        bps = assemble_invention(conn, bps)
        bp = bps[0]

        all_type_ids = [bp["product_id"]] + [mid for mid, _ in bp.get("materials", [])]
        inv = bp.get("invention")
        if inv:
            all_type_ids.extend(dcid for dcid, _ in inv.get("datacores", []))

        prices = fetch_prices(all_type_ids, _session, station_id)
        adjusted = fetch_adjusted_prices(_session, cache_dir)
        names = resolve_names(all_type_ids, _session, cache_dir)
        vols = volumes_for(conn, all_type_ids)

        # T1 BPO buy-in price (region-wide Fuzzwork) so the detail panel can show
        # BP price + payback like the scan does. Was previously omitted → blank.
        bpo_prices = {}
        if not inv:
            bpo_region = fetch_fuzzwork_region([blueprint_id], region_id, _session)
            bpo_prices = {bid: v["sell_min"] for bid, v in bpo_region.items() if v.get("sell_min")}

        params = {
            "me": me, "te": te, "job_rate": job_rate,
            "sales_tax": sales_tax, "broker_fee": broker_fee,
            "runs": runs, "skills_level": skills_level, "adjusted": adjusted,
            "bpo_prices": bpo_prices,
        }
        result = build_industry_detail(bp, prices, names, vols, params)
        result["tech_level"] = bp.get("tech_level")
        result["missing_skills"] = missing_skills(bp, {}, conn, skills_level)
        result["owned_bp_me_te"] = None
        return result
    finally:
        conn.close()


@router.get("/refresh-sde")
async def refresh_sde():
    """Force re-download the SDE data."""
    cache_dir = settings.cache_dir
    load_sde_industry(cache_dir, session=_session, refresh=True)
    return {"status": "ok"}


@router.get("/liquidity")
async def ind_liquidity(
    type_ids: str = Query(""),
    station_id: int = Query(JITA_STATION_ID),
):
    """Background tradeability scoring."""
    cache_dir = settings.cache_dir
    hub = TRADE_HUBS.get(station_id, {})
    region_id = hub.get("region_id", JITA_REGION_ID)
    tids = _parse_ids(type_ids)
    if not tids:
        return {}
    vols = fetch_history_volumes(tids, region_id, _session, cache_dir)
    # Return raw mean daily volume + the absolute tradeability score. The client
    # blends daily-vol vs days-to-sell into a weight-driven tradeability (and
    # derives days-to-sell from the row's batch size), mirroring the LP tab.
    return {tid: {"daily_vol": v, "tradeability": tradeability(v)} for tid, v in vols.items()}
