"""Arbitrage scanner API router with SSE streaming."""
import json
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import requests as req_lib

from ..config import settings
from ..core.shared.constants import JITA_REGION_ID
from ..core.arbitrage.scanner import fetch_region_types, fetch_fuzzwork_region, arb_candidates
from ..core.arbitrage.spreads import find_spreads, get_orders
from ..core.arbitrage.enrichment import (
    load_lookup_cache, save_lookup_cache, build_shown,
    resolve_names, resolve_volume, filter_from_jita,
)

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])
_session = req_lib.Session()


@router.get("/scan")
async def scan(
    region_id: int = Query(JITA_REGION_ID),
    sales_tax: float = Query(0.075),
    mode: str = Query("region"),
    min_isk: float = Query(1000000),
    max_jumps: int = Query(5),
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

        yield from emit_progress(5, "Loading region orders...")

        same_station = (mode == "station")
        orders, snap_meta = get_orders(region_id, _session, cache_path, refresh)
        yield from emit_progress(30, f"Loaded {len(orders)} orders")

        results = find_spreads(orders, sales_tax, same_station)
        yield from emit_progress(50, f"Found {len(results)} spreads")

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
        yield f"event: result\ndata: {json.dumps(shown)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
