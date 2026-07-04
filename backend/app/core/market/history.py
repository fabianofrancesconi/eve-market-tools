"""Market history fetching — daily volumes and median prices from ESI."""
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..shared.constants import ESI, HEADERS, HISTORY_DAYS, HISTORY_TTL_SECONDS
from ..shared.cache import load_json, save_json
from ..esi.client import check_esi_rate_limit


def _mean_daily_volume(history, days=HISTORY_DAYS):
    """Average daily volume over the last `days` CALENDAR days. ESI history
    omits days with zero trades, so we fill gaps with 0 to get the true daily
    rate.  Mean (not median) because median-with-zeros collapses to 0 for
    items that trade sporadically.  None when there's no usable history."""
    if not history:
        return None
    last_date = datetime.strptime(history[-1]["date"], "%Y-%m-%d")
    start_date = last_date - timedelta(days=days - 1)
    vol_by_date = {}
    for entry in history:
        d = datetime.strptime(entry["date"], "%Y-%m-%d")
        if d >= start_date:
            vol_by_date[d] = entry.get("volume") or 0
    total = sum(vol_by_date.values())
    if total == 0:
        return 0
    return total / days


def _median_daily_avg_price(history, days=HISTORY_DAYS):
    """Median of the last `days` daily *average* traded prices from an ESI
    history list. Median (not mean) so one fire-sale or gouge day doesn't skew
    the fair value. None when there's no usable history."""
    prices = [d.get("average") for d in history[-days:]]
    prices = [p for p in prices if p is not None]
    if not prices:
        return None
    return statistics.median(prices)


def _fetch_history_summary(type_ids, region_id, session, cache_dir, summarize,
                           refresh=False):
    """Shared per-type ESI market-history fetch + cache, reduced to one number
    per type by `summarize(history_list)`. One HTTP round-trip per uncached type
    -- the expensive call, so resolve it off the main scan path / in the
    background.

    Shares the `mhist_{region}_{type}.json` cache files the price-chart endpoint
    uses (same format, HISTORY_TTL_SECONDS window). Maps a type to None when it
    has no recorded history (the market never traded it) or on fetch failure."""
    out = {}
    now = time.time()
    for tid in sorted(set(type_ids)):
        path = Path(cache_dir) / f"mhist_{region_id}_{tid}.json"
        data = None
        if not refresh:
            cached = load_json(path, None)
            if cached and now - cached.get("_ts", 0) < HISTORY_TTL_SECONDS:
                data = cached["data"]
        if data is None:
            try:
                r = session.get(f"{ESI}/markets/{region_id}/history/",
                                params={"type_id": tid}, headers=HEADERS, timeout=20)
                check_esi_rate_limit(r)
                if r.status_code != 200:
                    out[tid] = None
                    continue
                data = sorted(r.json(), key=lambda x: x["date"])
                save_json(path, {"_ts": now, "data": data})
            except requests.RequestException:
                out[tid] = None
                continue
        out[tid] = summarize(data)
    return out


def fetch_history_volumes(type_ids, region_id, session, cache_dir, refresh=False):
    """type_id -> median daily traded volume (last HISTORY_DAYS) in `region_id`,
    via ESI market history. None for a type with no recorded history or on
    fetch failure. See _fetch_history_summary for caching."""
    return _fetch_history_summary(type_ids, region_id, session, cache_dir,
                                  _mean_daily_volume, refresh)


def fetch_history_prices(type_ids, region_id, session, cache_dir, refresh=False):
    """type_id -> median daily average traded price (last HISTORY_DAYS) in
    `region_id`, the "fair value" anchor for a suggested list price. None for a
    type with no recorded history or on fetch failure. Reuses the same cache
    files as fetch_history_volumes, so calling both costs no extra ESI calls."""
    return _fetch_history_summary(type_ids, region_id, session, cache_dir,
                                  _median_daily_avg_price, refresh)


def suggested_list_price(ask, fair):
    """Per-unit price to put on a sell order, anchored to history so a single
    lowball sell order doesn't drag the suggestion below fair value.

      ask  -- current lowest sell order at the hub (None if nothing is listed)
      fair -- 30-day median of the daily average traded price (None if no
              history); the fair-value anchor from fetch_history_prices.

    Returns the lowest current sell, UNLESS that sits below fair value (someone
    is dumping) -- then it holds at fair value rather than joining the race to
    the bottom. With only one signal available it returns that one; None when
    neither is."""
    if ask is None:
        return fair
    if fair is None:
        return ask
    return max(ask, fair)
