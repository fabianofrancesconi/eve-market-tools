"""LP-store offer fetching and corporation resolution."""
import time
from pathlib import Path

from ..shared.constants import ESI, HEADERS, OFFERS_TTL_SECONDS
from ..shared.cache import load_json, save_json
from ..esi.client import LPError


def resolve_corp_id(name, session):
    """NPC corporation name -> (corp_id, canonical_name). Raises LPError."""
    r = session.post(f"{ESI}/universe/ids/", json=[name], headers=HEADERS, timeout=30)
    r.raise_for_status()
    corps = r.json().get("corporations") or []
    if not corps:
        raise LPError(f"No corporation matched '{name}'. Check the exact NPC corp name "
                      f"(e.g. 'Serpentis Inquest', 'State Protectorate').")
    exact = [x for x in corps if x["name"].lower() == name.lower()]
    chosen = exact[0] if exact else corps[0]
    return chosen["id"], chosen["name"]


def resolve_corp_name(corp_id, session):
    """corporation_id -> name via /corporations/{id}/."""
    try:
        r = session.get(f"{ESI}/corporations/{corp_id}/", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json().get("name") or f"corp {corp_id}"
    except Exception:
        pass
    return f"corp {corp_id}"


def get_offers(corp_id, session, cache_dir, refresh=False):
    """LP-store offers for a corporation, cached for OFFERS_TTL_SECONDS."""
    path = Path(cache_dir) / f"lpstore_{corp_id}.json"
    now = time.time()
    if not refresh:
        cached = load_json(path, None)
        if cached and now - cached.get("fetched_at", 0) < OFFERS_TTL_SECONDS:
            return cached["offers"]
    r = session.get(f"{ESI}/loyalty/stores/{corp_id}/offers/", headers=HEADERS, timeout=30)
    if r.status_code == 404:
        raise LPError(f"Corp {corp_id} has no LP store (ESI 404).")
    r.raise_for_status()
    offers = r.json()
    save_json(path, {"fetched_at": now, "offers": offers})
    return offers
