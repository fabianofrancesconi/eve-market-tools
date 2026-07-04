"""Arbitrage scanner API router with SSE streaming."""
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import requests as req_lib

from ..config import settings
from ..core.shared.constants import JITA_REGION_ID
from ..core.arbitrage.spreads import find_spreads, get_orders
from ..core.arbitrage.enrichment import (
    load_lookup_cache, save_lookup_cache, build_shown,
    resolve_names, resolve_volume, filter_from_jita,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])
_session = req_lib.Session()


@router.get("/scan")
async def scan(
    region_id: int = Query(JITA_REGION_ID),
    sales_tax: float = Query(0.075),
    mode: str = Query("region"),
    min_isk: float = Query(0),
    max_jumps: int = Query(6),
    avoid_lowsec: bool = Query(True),
    route_flag: str = Query("shortest"),
    refresh: bool = Query(False),
    top: int = Query(100),
):
    """SSE-streaming arbitrage scan."""
    def generate():
        cache_dir = settings.cache_dir
        from pathlib import Path
        cache_path = Path(cache_dir)

        def emit_progress(pct, msg=""):
            yield f"event: progress\ndata: {json.dumps({'pct': pct, 'msg': msg})}\n\n"

        try:
            yield from emit_progress(5, "Loading region orders...")

            same_station = (mode == "station")
            orders, snap_meta = get_orders(region_id, _session, cache_path, refresh)
            yield from emit_progress(30, f"Loaded {len(orders)} orders")

            results = find_spreads(orders, sales_tax, same_station)
            total_spreads = len(results)
            yield from emit_progress(50, f"Found {total_spreads} spreads")

            # Filter by min ISK
            results = [r for r in results if r["isk_opportunity"] >= min_isk]

            # Enrich with locations
            stations, volumes, systems, routes = load_lookup_cache(cache_path)

            if not same_station:
                # Cross-station: enrich all, then filter to Jita-leg within max_jumps
                shown = build_shown(
                    results, len(results), False, avoid_lowsec, True, route_flag,
                    _session, stations, routes, systems,
                )
                shown = filter_from_jita(shown, max_jumps)
                shown = shown[:top]
            else:
                shown = build_shown(
                    results, top, False, avoid_lowsec, False, route_flag,
                    _session, stations, routes, systems,
                )

            save_lookup_cache(cache_path, stations, volumes, systems, routes)
            yield from emit_progress(80, "Resolving names...")

            # Resolve names
            type_ids = list(set(r["type_id"] for r in shown))
            names = resolve_names(type_ids, _session) if type_ids else {}
            for r in shown:
                r["name"] = names.get(r["type_id"], str(r["type_id"]))
                vol = resolve_volume(r["type_id"], volumes, _session)
                r["volume"] = vol

            yield from emit_progress(100, "Done")
            payload = {
                "rows": shown,
                "snapshot": snap_meta,
                "total_spreads": total_spreads,
                "total_orders": len(orders),
            }
            yield f"event: result\ndata: {json.dumps(payload)}\n\n"
        except Exception as e:
            logger.exception("Arbitrage scan failed")
            yield f"event: error\ndata: {json.dumps({'message': f'Scan failed: {e}'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
