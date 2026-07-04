"""Authenticated ESI fetchers for character data."""
from ..shared.constants import ESI, HEADERS


def _auth_headers(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def fetch_public_char(character_id, session):
    """Public character sheet."""
    r = session.get(f"{ESI}/characters/{character_id}/", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_skills(token, character_id, session):
    r = session.get(f"{ESI}/characters/{character_id}/skills/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_skillqueue(token, character_id, session):
    r = session.get(f"{ESI}/characters/{character_id}/skillqueue/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_wallet(token, character_id, session):
    r = session.get(f"{ESI}/characters/{character_id}/wallet/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_loyalty_points(token, character_id, session):
    r = session.get(f"{ESI}/characters/{character_id}/loyalty/points/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_market_orders(token, character_id, session):
    r = session.get(f"{ESI}/characters/{character_id}/orders/",
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_industry_jobs(token, character_id, session, include_completed=False):
    r = session.get(f"{ESI}/characters/{character_id}/industry/jobs/",
                    params={"include_completed": str(include_completed).lower()},
                    headers=_auth_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_character_blueprints(token, character_id, session):
    out, page = [], 1
    while page <= 50:
        r = session.get(f"{ESI}/characters/{character_id}/blueprints/",
                        params={"page": page},
                        headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
    return out


def skill_profile_from_skills(skills_resp):
    """ESI skills response -> {skill_id: trained_level}."""
    out = {}
    for s in (skills_resp or {}).get("skills", []):
        sid = s.get("skill_id")
        lvl = s.get("trained_skill_level", s.get("active_skill_level", 0))
        if sid is not None:
            out[int(sid)] = int(lvl or 0)
    return out


def owned_blueprint_lookup(blueprints_resp):
    """ESI blueprints response -> {type_id: (me, te, is_bpo, max_runs)}."""
    best = {}
    for b in blueprints_resp or []:
        tid = b.get("type_id")
        if tid is None:
            continue
        me = int(b.get("material_efficiency") or 0)
        te = int(b.get("time_efficiency") or 0)
        qty = b.get("quantity", -1)
        is_bpo = (qty == -1)
        runs = -1 if is_bpo else int(b.get("runs") or 0)
        prev = best.get(tid)
        if prev is None:
            best[tid] = (me, te, is_bpo, runs)
        elif is_bpo and not prev[2]:
            best[tid] = (me, te, is_bpo, -1)
        elif is_bpo == prev[2] and (me, te) > (prev[0], prev[1]):
            best[tid] = (me, te, is_bpo, max(runs, prev[3]) if not is_bpo else -1)
        elif not is_bpo and not prev[2] and runs > prev[3]:
            best[tid] = (prev[0], prev[1], False, runs)
    return best
