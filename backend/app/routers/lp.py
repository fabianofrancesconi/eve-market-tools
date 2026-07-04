"""LP Store API router."""
import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
import requests as req_lib

from ..config import settings
from ..database import get_db
from ..dependencies import get_optional_user
from ..models import User
from ..core.shared.constants import JITA_STATION_ID, JITA_REGION_ID, TRADE_HUBS, HIGH_SPREAD_PCT
from ..core.lp.offers import resolve_corp_id, get_offers
from ..core.lp.evaluate import evaluate
from ..core.lp.liquidity import enrich_liquidity
from ..core.lp.detail import build_detail
from ..core.market.prices import fetch_prices, fetch_prices_esi
from ..core.market.history import fetch_history_volumes, fetch_history_prices, suggested_list_price
from ..core.market.orders import fetch_sell_order_stats, fetch_orderbook_jita
from ..core.shared.names import resolve_names, resolve_volumes
from ..core.industry.costs import tradeability as compute_tradeability
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
    """List NPC corporations with LP stores, resolved to names."""
    from ..core.shared.constants import ESI, HEADERS
    cache_dir = settings.cache_dir
    try:
        r = await asyncio.to_thread(
            _session.get, f"{ESI}/corporations/npccorps/", headers=HEADERS, timeout=30
        )
        r.raise_for_status()
        corp_ids = r.json()
        names = await asyncio.to_thread(resolve_names, corp_ids, _session, cache_dir)
        return [{"id": cid, "name": names.get(cid, str(cid))} for cid in corp_ids if cid in names]
    except Exception:
        logger.exception("Failed to fetch NPC corps")
        return []


@router.get("/scan")
async def scan(
    corp: str = Query(...),
    lp_budget: int = Query(100000),
    station_id: int = Query(JITA_STATION_ID),
    sales_tax: float = Query(0.075),
    broker_fee: float = Query(0.03),
    use_esi_prices: bool = Query(False),
    refresh: bool = Query(False),
):
    """Scan LP store offers and evaluate profitability."""
    cache_dir = settings.cache_dir
    try:
        corp_id, corp_name = await asyncio.to_thread(resolve_corp_id, corp, _session)
    except LPError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Failed to resolve corp")
        return {"error": f"Failed to resolve corporation: {e}"}

    try:
        offers = await asyncio.to_thread(get_offers, corp_id, _session, cache_dir, refresh)
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
        all_rows = sellable + unsellable
        names = await asyncio.to_thread(
            resolve_names, [r["name_id"] for r in all_rows], _session, cache_dir
        )
        vol_ids = [r["name_id"] for r in sellable]
        volumes = await asyncio.to_thread(resolve_volumes, vol_ids, _session, cache_dir)

        for r in all_rows:
            r["name"] = names.get(r["name_id"], str(r["name_id"]))

        for r in sellable:
            sp = r.get("spread_pct")
            r["illiquid"] = sp is None or sp >= HIGH_SPREAD_PCT
            vol = volumes.get(r["name_id"])
            r["output_volume"] = (vol * r["qty"]) if vol else None

        return {
            "corp_id": corp_id,
            "corp_name": corp_name,
            "sellable": sellable,
            "unsellable": unsellable,
        }
    except Exception as e:
        logger.exception("LP scan failed")
        return {"error": f"Scan failed: {e}"}


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

    async def _safe(fn, *args, default):
        """Run a blocking enrichment call, degrading to a default on any
        (often transient) network error instead of 500-ing the whole batch."""
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception:
            logger.warning("LP liquidity enrichment call %s failed", fn.__name__, exc_info=True)
            return default

    vols = await _safe(fetch_history_volumes, tids, region_id, _session, cache_dir, default={})
    fairs = await _safe(fetch_history_prices, tids, region_id, _session, cache_dir, default={})

    # Fetch current ask prices for suggested_list_price calculation
    prices = await _safe(fetch_prices, tids, _session, station_id, default={})

    result = {}
    for tid in tids:
        dv = vols.get(tid)
        fair = fairs.get(tid)
        ask = prices.get(tid, {}).get("sell_min")
        sell_vol = prices.get(tid, {}).get("sell_volume", 0)

        # Floor age from sell order stats
        stats = await _safe(
            fetch_sell_order_stats, tid, _session, station_id, region_id, default=None
        )
        floor_age = stats["age_seconds"] if stats else None

        result[tid] = {
            "daily_vol": dv,
            "days_to_clear": (sell_vol / dv) if (dv and dv > 0) else None,
            "list_price": suggested_list_price(ask, fair),
            "floor_age": floor_age,
            "tradeability": compute_tradeability(dv),
        }
    return result


@router.get("/detail")
async def detail(
    offer_id: int = Query(...),
    corp_id: int = Query(...),
    lp_budget: int = Query(100000),
    station_id: int = Query(JITA_STATION_ID),
    sales_tax: float = Query(0.075),
    broker_fee: float = Query(0.03),
):
    """Full detail breakdown for one LP offer."""
    cache_dir = settings.cache_dir
    hub = TRADE_HUBS.get(station_id, {})
    region_id = hub.get("region_id", JITA_REGION_ID)

    offers = await asyncio.to_thread(get_offers, corp_id, _session, cache_dir)
    offer = next((o for o in offers if o.get("offer_id") == offer_id), None)
    if not offer:
        return {"error": "Offer not found"}

    out_tid = offer["type_id"]
    type_ids = [out_tid] + [r["type_id"] for r in offer.get("required_items", [])]
    prices = await asyncio.to_thread(fetch_prices, type_ids, _session, station_id)
    names = await asyncio.to_thread(resolve_names, type_ids, _session, cache_dir)
    volumes = await asyncio.to_thread(resolve_volumes, type_ids, _session, cache_dir)

    result = build_detail(offer, prices, names, volumes, lp_budget, sales_tax, broker_fee)

    # Enrich with market intelligence
    daily_vols = await asyncio.to_thread(
        fetch_history_volumes, [out_tid], region_id, _session, cache_dir
    )
    fair_prices = await asyncio.to_thread(
        fetch_history_prices, [out_tid], region_id, _session, cache_dir
    )
    dv = daily_vols.get(out_tid)
    fair = fair_prices.get(out_tid)
    ask = prices.get(out_tid, {}).get("sell_min")
    sell_vol = prices.get(out_tid, {}).get("sell_volume", 0)

    result["daily_vol"] = dv
    result["days_to_clear"] = (sell_vol / dv) if (dv and dv > 0) else None
    result["fair_price"] = fair
    result["suggested_list"] = suggested_list_price(ask, fair)
    result["high_spread_pct"] = HIGH_SPREAD_PCT

    # Sell order freshness
    sell_stats = await asyncio.to_thread(
        fetch_sell_order_stats, out_tid, _session, station_id, region_id
    )
    result["sell_order_stats"] = sell_stats

    # Order books for required items (sell-side) and output (buy-side for instant)
    req_books = {}
    for req in offer.get("required_items", []):
        rtid = req["type_id"]
        book = await asyncio.to_thread(
            fetch_orderbook_jita, rtid, "sell", _session, station_id, region_id
        )
        req_books[rtid] = book
    result["required_books"] = req_books

    out_buy_book = await asyncio.to_thread(
        fetch_orderbook_jita, out_tid, "buy", _session, station_id, region_id
    )
    result["output_buy_book"] = out_buy_book

    return result


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


@router.get("/last-scan")
async def last_scan():
    """Restore the last LP scan results from disk."""
    from ..core.shared.cache import load_json
    cache_dir = settings.cache_dir
    lp_data = load_json(Path(cache_dir) / "lp_last_scan.json", None)
    ind_data = load_json(Path(cache_dir) / "ind_last_scan.json", None)
    return {"lp": lp_data, "ind": ind_data}


@router.post("/save-scan")
async def save_scan(request: Request):
    """Persist scan results to disk for restore on next page load."""
    from ..core.shared.cache import save_json
    cache_dir = settings.cache_dir
    body = await request.json()
    tab = body.get("tab")
    data = body.get("data")
    if tab == "lp" and data:
        save_json(Path(cache_dir) / "lp_last_scan.json", data)
    elif tab == "ind" and data:
        save_json(Path(cache_dir) / "ind_last_scan.json", data)
    return {"ok": True}
