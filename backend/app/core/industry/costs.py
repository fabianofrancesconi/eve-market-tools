"""
Material cost, job cost, EIV, build time, tradeability, and skill training
calculations for the Industry module.
"""
import math
import sqlite3

from .sde import _SP_PER_LEVEL, _SP_PER_HOUR

# Build-time reductions (material cost is unaffected by skills -- only ME is).
# Industry: -4%/level (manufacturing time). Advanced Industry: -3%/level.
# Skill type IDs verified against the SDE (types.type_name).
INDUSTRY_SKILL_ID = 3380          # "Industry"          -4%/level
ADV_INDUSTRY_SKILL_ID = 3388      # "Advanced Industry" -3%/level
INDUSTRY_TIME_PER_LEVEL = 0.04
ADV_INDUSTRY_TIME_PER_LEVEL = 0.03

# Tradeability: daily UNITS traded (ESI market-history volume) mapped to 0..100 on
# a log scale. What matters for a producer is how many units the market actually
# absorbs per day (not how many separate transactions). An item moving this many
# units/day scores ~100; the log curve spreads out the low end, which is exactly
# where "is there a market at all?" matters.
TRADEABILITY_FULL = 5000.0


def effective_qty(base_qty, me, runs=1):
    """Units of a material actually consumed, after Material Efficiency.

    EVE applies ME to the whole job and rounds up, with a floor of one unit per
    run: max(runs, ceil(base_qty * runs * (1 - ME/100))). The intermediate is
    rounded to 2 dp first to absorb float noise (matches the in-game numbers)."""
    raw = round(base_qty * runs * (1 - me / 100.0), 2)
    return max(runs, math.ceil(raw))


def manufacturing_cost(bp, prices, adjusted, job_rate, me):
    """Per-run input economics for a manufacturing blueprint.

      prices    {type_id: {"sell_min", "buy_max", ...}}  -- live market (lp_core)
      adjusted  {type_id: adjusted_price}                -- ESI /markets/prices/
      job_rate  installation-cost fraction of EIV (the user's manual rate)
      me        blueprint Material Efficiency 0..10

    Returns material_cost (ME-adjusted, bought at sell_min), EIV (BASE qty x
    adjusted_price -- NOT market, NOT ME-adjusted, per CCP's job-cost formula),
    job_cost (= EIV x job_rate), the per-line breakdown, and a missing_price flag
    when any input has no sell order to price against."""
    lines, material_cost, eiv, missing = [], 0.0, 0.0, False
    for mid, base_qty in bp.get("materials", []):
        eff = effective_qty(base_qty, me)
        unit = (prices.get(mid) or {}).get("sell_min")
        adj = adjusted.get(mid)
        line_cost = (eff * unit) if unit else None
        if line_cost is None:
            missing = True
        else:
            material_cost += line_cost
        if adj:
            eiv += base_qty * adj
        lines.append({
            "type_id": mid,
            "base_qty": base_qty,
            "eff_qty": eff,
            "unit_price": unit,
            "line_cost": line_cost,
        })
    return {
        "material_cost": material_cost,
        "eiv": eiv,
        "job_cost": eiv * job_rate,
        "lines": lines,
        "missing_price": missing,
    }


def build_time(base_time, te, skill_profile, default_level=0):
    """Seconds to run one manufacturing job, after Time Efficiency and the two
    time skills (Industry -4%/lvl, Advanced Industry -3%/lvl). Skills not named
    in `skill_profile` fall back to `default_level` (so "assume all skills at L5"
    is just default_level=5). None if the blueprint has no recorded time."""
    if not base_time:
        return None
    sp = skill_profile or {}
    ind = sp.get(INDUSTRY_SKILL_ID, default_level)
    adv = sp.get(ADV_INDUSTRY_SKILL_ID, default_level)
    return (base_time * (1 - te / 100.0)
            * (1 - INDUSTRY_TIME_PER_LEVEL * ind)
            * (1 - ADV_INDUSTRY_TIME_PER_LEVEL * adv))


def tradeability(daily_volume):
    """A 0..100 score for how sellable a product is, from the daily UNITS traded
    on the market (not the transaction count). The point is to demote items that
    look profitable on paper but whose market can't absorb meaningful quantity.

      0 units/day              -> 0   (you can't realistically sell it)
      ~10/day                  -> ~28
      ~100/day                 -> ~54
      ~1000/day                -> ~81
      >= TRADEABILITY_FULL/day -> 100

    Log scale (traded volume spans orders of magnitude), clamped to [0, 100].
    None in -> None (history unknown)."""
    if daily_volume is None:
        return None
    if daily_volume <= 0:
        return 0
    score = math.log10(1 + daily_volume) / math.log10(1 + TRADEABILITY_FULL)
    return int(round(max(0.0, min(1.0, score)) * 100))


def cheapest_sell_location(orders):
    """From a list of ESI region orders for one type, the cheapest SELL order's
    price, location_id and how many sell orders exist -- i.e. where (and for how
    much) you can actually buy the item right now. None if nothing is on sale."""
    sells = [o for o in orders if not o.get("is_buy_order")]
    if not sells:
        return None
    cheapest = min(sells, key=lambda o: o["price"])
    return {"price": cheapest["price"],
            "location_id": cheapest["location_id"],
            "system_id": cheapest.get("system_id"),
            "orders": len(sells)}


def training_time_hours(from_level, to_level, rank):
    """Approximate hours to train a skill from from_level to to_level, given its
    training rank. Uses a fixed SP/hr rate (_SP_PER_HOUR) as a baseline."""
    if from_level >= to_level or rank is None:
        return 0.0
    sp_from = _SP_PER_LEVEL[from_level] * rank if from_level > 0 else 0
    sp_to = _SP_PER_LEVEL[to_level] * rank
    return (sp_to - sp_from) / _SP_PER_HOUR


def _load_prereqs(conn, skill_ids):
    """Load prerequisite tree for a set of skill IDs. Returns
    {skill_id: [(prereq_skill_id, level), ...]}."""
    prereqs = {}
    try:
        marks = ", ".join("?" for _ in skill_ids)
        for r in conn.execute(
                f"SELECT type_id, prereq_skill_id, prereq_level FROM skill_prereqs "
                f"WHERE type_id IN ({marks})", list(skill_ids)):
            prereqs.setdefault(r["type_id"], []).append(
                (r["prereq_skill_id"], r["prereq_level"]))
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet (old SDE build)
    return prereqs


def _walk_skill_tree(skill_id, required_level, sp, default_level, prereqs,
                     visited, result):
    """Recursively walk the prerequisite tree for one skill. Adds entries to
    `result` for any skill (including prerequisites) the character lacks.
    `visited` tracks {skill_id: level_already_required} to avoid duplicates
    and handle the same skill appearing at different levels (keep highest)."""
    current = sp.get(skill_id, default_level)
    prev_required = visited.get(skill_id, 0)
    if required_level <= prev_required:
        return  # already handled at this or higher level
    visited[skill_id] = required_level

    # Walk this skill's own prerequisites first (depth-first)
    for prereq_id, prereq_lvl in prereqs.get(skill_id, []):
        _walk_skill_tree(prereq_id, prereq_lvl, sp, default_level, prereqs,
                         visited, result)

    if current < required_level:
        # Update existing entry if we already added this skill at a lower level
        for entry in result:
            if entry["skill_id"] == skill_id:
                entry["required"] = required_level
                return
        result.append({
            "skill_id": skill_id,
            "required": required_level,
            "current": current,
        })


def missing_skills(bp, skill_profile, conn, default_level=0):
    """Return a list of skills the character lacks for this blueprint, including
    prerequisite skills walked recursively. Each entry: {skill_id, name,
    required, current, train_hours}. Ordered prerequisites-first."""
    sp = skill_profile or {}
    direct_skills = bp.get("skills", [])
    if not direct_skills:
        return []

    # Collect all skill IDs we might need prerequisites for (iterative BFS)
    to_check = set(sid for sid, _ in direct_skills)
    all_skill_ids = set(to_check)
    prereqs = {}
    for _ in range(10):  # max depth guard
        if not to_check:
            break
        loaded = _load_prereqs(conn, to_check)
        prereqs.update(loaded)
        to_check = set()
        for reqs in loaded.values():
            for psid, _ in reqs:
                if psid not in all_skill_ids:
                    all_skill_ids.add(psid)
                    to_check.add(psid)

    # Walk the tree for each direct requirement
    visited = {}
    result = []
    for sid, required_lvl in direct_skills:
        _walk_skill_tree(sid, required_lvl, sp, default_level, prereqs,
                         visited, result)

    if not result:
        return []

    # Resolve names and ranks for all missing skills
    missing_ids = [e["skill_id"] for e in result]
    marks = ", ".join("?" for _ in missing_ids)
    names = {}
    ranks = {}
    for r in conn.execute(
            f"SELECT type_id, type_name FROM types WHERE type_id IN ({marks})",
            missing_ids):
        names[r["type_id"]] = r["type_name"]
    try:
        for r in conn.execute(
                f"SELECT type_id, rank FROM skill_ranks WHERE type_id IN ({marks})",
                missing_ids):
            ranks[r["type_id"]] = r["rank"]
    except sqlite3.OperationalError:
        pass

    direct_ids = set(sid for sid, _ in direct_skills)
    for entry in result:
        sid = entry["skill_id"]
        entry["name"] = names.get(sid, f"Skill {sid}")
        entry["train_hours"] = training_time_hours(
            entry["current"], entry["required"], ranks.get(sid))
        entry["prereq"] = sid not in direct_ids

    return result


def bulk_training_time(bps, skill_profile, conn, default_level=0):
    """Return {blueprint_id: total_hours} for every blueprint whose skill
    requirements the profile does NOT meet. Includes prerequisite skills
    so the time reflects the full training queue, not just direct skills."""
    sp = skill_profile or {}
    all_direct_ids = set()
    needs = []
    for bp in bps:
        skills = bp.get("skills", [])
        missing = [(sid, lvl) for sid, lvl in skills
                   if sp.get(sid, default_level) < lvl]
        if missing:
            needs.append((bp["blueprint_id"], missing))
            all_direct_ids.update(sid for sid, _ in missing)
    if not all_direct_ids:
        return {}
    # Load prereq tree (BFS) for all missing skills across all blueprints
    prereqs = {}
    all_skill_ids = set(all_direct_ids)
    to_check = set(all_direct_ids)
    for _ in range(10):
        if not to_check:
            break
        loaded = _load_prereqs(conn, to_check)
        prereqs.update(loaded)
        to_check = set()
        for reqs in loaded.values():
            for psid, _ in reqs:
                if psid not in all_skill_ids:
                    all_skill_ids.add(psid)
                    to_check.add(psid)
    # Load ranks for all skills (direct + prereqs)
    ranks = {}
    if all_skill_ids:
        marks = ", ".join("?" for _ in all_skill_ids)
        try:
            for r in conn.execute(
                    f"SELECT type_id, rank FROM skill_ranks WHERE type_id IN ({marks})",
                    list(all_skill_ids)):
                ranks[r["type_id"]] = r["rank"]
        except Exception:
            pass
    result = {}
    for bp_id, missing_direct in needs:
        visited = {}
        skill_list = []
        for sid, required_lvl in missing_direct:
            _walk_skill_tree(sid, required_lvl, sp, default_level, prereqs,
                             visited, skill_list)
        total = 0.0
        for entry in skill_list:
            total += training_time_hours(
                entry["current"], entry["required"], ranks.get(entry["skill_id"]))
        result[bp_id] = total
    return result


def _buildable(bp, skill_profile, default_level=0):
    """True if the skill profile meets every skill the blueprint needs. Skills
    absent from the profile assume `default_level` (the "all skills at level X"
    convenience), so an empty profile + default_level=5 means "can I build it
    with everything at 5?"."""
    sp = skill_profile or {}
    return all(sp.get(sid, default_level) >= lvl for sid, lvl in bp.get("skills", []))
