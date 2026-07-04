"""Shared constants used across all core modules."""

ESI = "https://esi.evetech.net"
FUZZWORK_AGG = "https://market.fuzzwork.co.uk/aggregates/"

JITA_STATION_ID = 60003760
JITA_REGION_ID = 10000002
JITA_SYSTEM_ID = 30000142

TRADE_HUBS = {
    60003760: {"name": "Jita 4-4",     "region_id": 10000002},
    60008494: {"name": "Amarr 8-20",   "region_id": 10000043},
    60004588: {"name": "Rens 6-8",     "region_id": 10000030},
    60011866: {"name": "Dodixie 9-20", "region_id": 10000032},
    60005686: {"name": "Hek 8-12",     "region_id": 10000042},
}

COMPAT_DATE = "2025-08-26"
USER_AGENT = "eve-market-tools/1.0 (fabiano.francesconi@gmail.com)"
HEADERS = {
    "X-Compatibility-Date": COMPAT_DATE,
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

OFFERS_TTL_SECONDS = 24 * 3600
HIGH_SPREAD_PCT = 25.0
HISTORY_DAYS = 30
HISTORY_TTL_SECONDS = 12 * 3600
