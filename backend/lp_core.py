"""Backward-compatibility shim: re-exports all public names from the refactored
core modules so that existing code and tests using `import lp_core` or
`from lp_core import ...` continues to work unchanged."""

# Shared constants
from app.core.shared.constants import (
    ESI, FUZZWORK_AGG, JITA_STATION_ID, JITA_REGION_ID, TRADE_HUBS,
    COMPAT_DATE, USER_AGENT, HEADERS, OFFERS_TTL_SECONDS, HIGH_SPREAD_PCT,
    HISTORY_DAYS, HISTORY_TTL_SECONDS,
)
from app.core.shared.cache import default_cache_dir, load_json, save_json
from app.core.shared.names import resolve_names, resolve_volumes, resolve_station_region

# ESI client
from app.core.esi.client import LPError, ESIRateLimited, check_esi_rate_limit

# Market
from app.core.market.prices import (
    PRICE_CACHE_TTL, fetch_prices, fetch_prices_esi,
    _esi_orders_for_type, _summarise_orders,
)
from app.core.market.history import (
    _mean_daily_volume, _median_daily_avg_price,
    fetch_history_volumes, fetch_history_prices, suggested_list_price,
)
from app.core.market.orders import (
    _issued_age_seconds, fetch_sell_order_stats, fetch_orderbook_jita,
    fetch_order_rank,
)

# LP
from app.core.lp.offers import resolve_corp_id, resolve_corp_name, get_offers
from app.core.lp.evaluate import _spread_pct, _best, evaluate
from app.core.lp.liquidity import enrich_liquidity
from app.core.lp.detail import build_detail
