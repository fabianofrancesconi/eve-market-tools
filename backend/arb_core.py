"""Backward-compatibility shim: re-exports all public names from the refactored
arbitrage modules so that `import arb_core` continues to work."""

from app.core.shared.constants import HEADERS, USER_AGENT, JITA_STATION_ID, JITA_SYSTEM_ID
from app.core.shared.cache import load_json, save_json
from app.core.esi.client import check_esi_rate_limit

from app.core.arbitrage.scanner import (
    fetch_region_types, fetch_fuzzwork_region, arb_candidates,
    FUZZWORK_AGG, _FUZZWORK_HEADERS, _FUZZWORK_BATCH, _TYPES_CACHE_TTL,
)
from app.core.arbitrage.spreads import (
    fetch_type_orders, get_orders, find_spreads,
)
from app.core.arbitrage.enrichment import (
    load_lookup_cache, save_lookup_cache,
    resolve_station, resolve_system, route_info,
    enrich_locations, enrich_security, round_sec, sec_band, row_risk_sec,
    filter_from_jita, resolve_names, resolve_volume, build_shown,
)

ESI = "https://esi.evetech.net"
