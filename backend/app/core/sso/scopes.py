"""EVE SSO scope definitions and endpoint URLs."""

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize/"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

SCOPES = [
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-characters.read_loyalty.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-markets.read_character_orders.v1",
    "esi-characters.read_blueprints.v1",
]

AUTH_FILE = "eve_auth.json"
