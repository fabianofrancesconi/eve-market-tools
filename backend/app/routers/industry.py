"""Industry scanner API router with SSE streaming."""
import json
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
import requests as req_lib

from ..config import settings
from ..core.shared.constants import JITA_STATION_ID, JITA_REGION_ID, TRADE_HUBS
from ..core.market.prices import fetch_prices
from ..core.market.history import fetch_history_volumes
from ..core.industry.sde import (
    load_sde_industry, connect_sde, top_market_groups,
    expand_market_groups, manufacturing_candidates, candidates_for_blueprints,
    fetch_adjusted_prices, volumes_for,
)
from ..core.industry.blueprints import assemble_blueprints, assemble_invention
from ..core.industry.evaluate import evaluate_industry
from ..core.industry.costs import tradeability
from ..core.industry.detail import build_industry_detail
from ..core.shared.names import resolve_names

router = APIRouter(prefix="/api/industry", tags=["industry"])
_session = req_lib.Session()


def _parse_ids(raw: str) -> list[int]:
    """Safely parse a comma-separated string of IDs."""
    result = []
    for t in raw.split(","):
        t = t.strip()
        if t.isdigit():
            result.append(int(t))
    return result


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
    job_rate: float = Query(0.0),
    sales_tax: float = Query(0.08),
    broker_fee: float = Query(0.03),
    runs: int = Query(1),
    skills_level: int = Query(0),
    favorites: str = Query(""),
):
    """SSE-streaming industry scan."""
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

            yield f"event: progress\ndata: {json.dumps({'pct': 70, 'msg': 'Evaluating...'})}\n\n"
            vols = volumes_for(conn, list(all_type_ids))
            params = {
                "me": me, "te": te, "job_rate": job_rate,
                "sales_tax": sales_tax, "broker_fee": broker_fee,
                "runs": runs, "skills_level": skills_level,
                "volumes": vols,
            }
            rows = evaluate_industry(bps, prices, adjusted, params)

            yield f"event: progress\ndata: {json.dumps({'pct': 90, 'msg': 'Resolving names...'})}\n\n"
            name_ids = list(set(r["product_id"] for r in rows[:200]))
            names = resolve_names(name_ids, _session, cache_dir)
            for r in rows:
                r["product_name"] = names.get(r["product_id"], r.get("product_name", ""))

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
    job_rate: float = Query(0.0),
    sales_tax: float = Query(0.08),
    broker_fee: float = Query(0.03),
    runs: int = Query(1),
    skills_level: int = Query(0),
):
    """Full detail breakdown for one blueprint."""
    cache_dir = settings.cache_dir
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

        params = {
            "me": me, "te": te, "job_rate": job_rate,
            "sales_tax": sales_tax, "broker_fee": broker_fee,
            "runs": runs, "skills_level": skills_level, "adjusted": adjusted,
        }
        return build_industry_detail(bp, prices, names, vols, params)
    finally:
        conn.close()


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
    return {tid: tradeability(v) for tid, v in vols.items()}
