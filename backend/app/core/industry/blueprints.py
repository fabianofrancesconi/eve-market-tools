"""
Blueprint assembly + skills: attach bill-of-materials, base time, required skills
and invention data to candidate rows for bulk evaluation.
"""
from .sde import ACT_MANUFACTURING, ACT_INVENTION


def assemble_blueprints(conn, candidates, activity_id=ACT_MANUFACTURING):
    """Attach the per-activity bill-of-materials, base time and required skills to
    each candidate row from `manufacturing_candidates`, returning the `bp` dicts
    the evaluation functions below consume. Bulk-loads (one query per relation,
    not per candidate) so it scales to thousands of items.

    Each returned dict carries: blueprint_id, product_id, product_name (alias of
    type_name), out_qty, tech_level, market_group_id, materials [(id, base_qty)],
    base_time (s), skills [(id, level)]."""
    bps = []
    by_bp = {}
    for c in candidates:
        bp = dict(c)
        bp["product_name"] = c.get("type_name")
        bp["materials"] = []
        bp["skills"] = []
        bp["base_time"] = None
        bps.append(bp)
        by_bp.setdefault(c["blueprint_id"], []).append(bp)
    if not by_bp:
        return bps
    ids = list(by_bp)
    marks = ", ".join("?" for _ in ids)

    for r in conn.execute(
        f"SELECT blueprint_id, material_id, quantity FROM materials "
        f"WHERE activity_id = ? AND blueprint_id IN ({marks})", [activity_id, *ids]):
        for bp in by_bp[r["blueprint_id"]]:
            bp["materials"].append((r["material_id"], r["quantity"]))
    for r in conn.execute(
        f"SELECT blueprint_id, skill_id, level FROM skills "
        f"WHERE activity_id = ? AND blueprint_id IN ({marks})", [activity_id, *ids]):
        for bp in by_bp[r["blueprint_id"]]:
            bp["skills"].append((r["skill_id"], r["level"]))
    for r in conn.execute(
        f"SELECT blueprint_id, time FROM activity "
        f"WHERE activity_id = ? AND blueprint_id IN ({marks})", [activity_id, *ids]):
        for bp in by_bp[r["blueprint_id"]]:
            bp["base_time"] = r["time"]
    return bps


def assemble_invention(conn, bps):
    """Attach bp['invention'] to every T2 blueprint among `bps` (in place, and
    returns `bps`). A T2 manufacturing blueprint is the product of some T1
    blueprint's invention activity; we look up that inventor, its datacores
    (activity-8 materials), the base success probability and the runs the
    invented BPC carries. T1 blueprints get bp['invention'] = None."""
    t2_ids = [bp["blueprint_id"] for bp in bps]
    by_t2 = {}
    for bp in bps:
        bp["invention"] = None
        by_t2.setdefault(bp["blueprint_id"], []).append(bp)
    if not t2_ids:
        return bps
    marks = ", ".join("?" for _ in t2_ids)

    inventor, t1_ids = {}, set()  # t2_bp -> (t1_bp, runs_per_bpc)
    for r in conn.execute(
        f"SELECT blueprint_id AS t1, product_id AS t2, quantity AS runs FROM products "
        f"WHERE activity_id = ? AND product_id IN ({marks})",
        [ACT_INVENTION, *t2_ids]):
        inventor[r["t2"]] = (r["t1"], r["runs"])
        t1_ids.add(r["t1"])
    if not inventor:
        return bps

    prob = {}  # t2_bp -> base probability
    for r in conn.execute(
        f"SELECT product_id AS t2, probability FROM probabilities "
        f"WHERE activity_id = ? AND product_id IN ({marks})",
        [ACT_INVENTION, *t2_ids]):
        prob[r["t2"]] = r["probability"]

    t1_marks = ", ".join("?" for _ in t1_ids)
    datacores = {}  # t1_bp -> [(datacore_id, qty)]
    for r in conn.execute(
        f"SELECT blueprint_id AS t1, material_id, quantity FROM materials "
        f"WHERE activity_id = ? AND blueprint_id IN ({t1_marks})",
        [ACT_INVENTION, *t1_ids]):
        datacores.setdefault(r["t1"], []).append((r["material_id"], r["quantity"]))

    for t2, (t1, runs) in inventor.items():
        info = {
            "t1_blueprint_id": t1,
            "datacores": datacores.get(t1, []),
            "probability": prob.get(t2),
            "runs_per_bpc": runs,
        }
        for bp in by_t2.get(t2, []):
            bp["invention"] = info
    return bps
