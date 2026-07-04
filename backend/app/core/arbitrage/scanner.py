"""Arbitrage region scanning: type listing, Fuzzwork pricing, candidate filtering."""
import time
from pathlib import Path

from ..shared.constants import ESI, HEADERS, USER_AGENT
from ..shared.cache import load_json, save_json
from ..esi.client import check_esi_rate_limit

FUZZWORK_AGG = "https://market.fuzzwork.co.uk/aggregates/"
_FUZZWORK_HEADERS = {"User-Agent": USER_AGENT}
_FUZZWORK_BATCH = 200
_TYPES_CACHE_TTL = 600


def fetch_region_types(region_id, session, cache_dir, refresh=False, progress_cb=None):
    """All type_ids with active orders in the region from ESI.
    Cached to disk for _TYPES_CACHE_TTL seconds; refresh=True bypasses."""
    path = Path(cache_dir) / f"types_region_{region_id}.json"
    if not refresh:
        cached = load_json(path, None)
        if cached and time.time() - cached.get("fetched_at", 0) < _TYPES_CACHE_TTL:
            if progress_cb:
                progress_cb("cache", count=len(cached["types"]))
            return cached["types"]

    types, page = [], 1
    while True:
        r = session.get(f"{ESI}/markets/{region_id}/types/",
                        params={"page": page}, headers=HEADERS, timeout=30)
        check_esi_rate_limit(r)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        types.extend(batch)
        total_pages = int(r.headers.get("X-Pages", 1))
        if progress_cb:
            progress_cb("page", page=page, pages=total_pages, count=len(types))
        if page >= total_pages:
            break
        page += 1

    save_json(path, {"fetched_at": time.time(), "types": types})
    return types


def fetch_fuzzwork_region(type_ids, region_id, session, progress_cb=None):
    """Min sell / max buy per type_id for the region via Fuzzwork aggregates."""
    out = {}
    ids = sorted(set(type_ids))
    total = (len(ids) + _FUZZWORK_BATCH - 1) // _FUZZWORK_BATCH
    for i, start in enumerate(range(0, len(ids), _FUZZWORK_BATCH)):
        chunk = ids[start:start + _FUZZWORK_BATCH]
        r = session.get(FUZZWORK_AGG,
                        params={"region": region_id,
                                "types": ",".join(map(str, chunk))},
                        headers=_FUZZWORK_HEADERS, timeout=30)
        r.raise_for_status()
        for tid_str, d in r.json().items():
            sell = d.get("sell", {})
            buy = d.get("buy", {})
            out[int(tid_str)] = {
                "sell_min": float(sell.get("min") or 0) or None,
                "buy_max": float(buy.get("max") or 0) or None,
            }
        if progress_cb:
            progress_cb("chunk", chunk=i + 1, total=total,
                        types_done=start + len(chunk))
    return out


def arb_candidates(prices, sales_tax):
    """Type IDs where best-buy (after tax) beats best-sell."""
    return [
        tid for tid, p in prices.items()
        if p.get("sell_min") and p.get("buy_max")
        and p["buy_max"] * (1.0 - sales_tax) > p["sell_min"]
    ]
