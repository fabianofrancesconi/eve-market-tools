#!/usr/bin/env python3
"""
Industry (manufacturing / invention) data layer for the EVE Market Tools web UI
(`lp-web.py`). Like `lp_core.py` and `arb_core.py` this is pure data/logic -- no
printing, no HTML -- so the calculations can be unit-tested in isolation.

This module owns the *Static Data Export* (SDE) side of the Industry module: the
blueprint bill-of-materials, products, build times, required skills and invention
probabilities. Those don't change between game patches, aren't exposed by ESI in
bulk, and are far too large to fetch per-item at scan time -- so we download
Fuzzwork's per-table CSV dumps once and bulk-import them into a compact local
SQLite database (`sde_industry.sqlite`) that scans then query.

Live prices, market history and packaged volumes are NOT here -- those come from
the existing `lp_core` helpers (fetch_prices / fetch_history_volumes /
resolve_volumes) so the whole app computes identical numbers.

Profit/throughput evaluation (manufacturing_cost, build_time, evaluate_industry,
invention_cost_per_run, build_industry_detail) is added on top of this in later
milestones.
"""
import csv
import math
import os
import sqlite3
import time
from pathlib import Path

import requests

import lp_core
from lp_core import ESI, HEADERS, USER_AGENT, _best, load_json, save_json

# --- constants -------------------------------------------------------------
SDE_BASE_URL = "https://www.fuzzwork.co.uk/dump/latest/csv"
_SDE_HEADERS = {"User-Agent": USER_AGENT}
SDE_DB_NAME = "sde_industry.sqlite"
# The SDE only changes on game patches; a week between rebuilds is plenty.
SDE_TTL_SECONDS = 7 * 24 * 3600
# Adjusted prices (the EIV basis) are recomputed by CCP daily; cache a few hours.
ADJ_CACHE_NAME = "adjusted_prices.json"
ADJ_TTL_SECONDS = 6 * 3600

# EVE industry activity IDs (the only two this module models).
ACT_MANUFACTURING = 1
ACT_INVENTION = 8

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

# Rows inserted per executemany batch when importing a CSV.
_INSERT_BATCH = 5000


# --- CSV cell converters ---------------------------------------------------
def _to_int(v):
    """CSV cell -> int, or None for blank/unparseable (SDE leaves many blank)."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return None


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _to_str(v):
    return v if v else None


# --- table specs (header-driven so we're robust to column reordering) ------
# Each spec: target SQLite table, source CSV basename, and the columns we keep
# as (csv_column, db_column, sql_type, converter). The DB column order defines
# the INSERT order; values are pulled by CSV *name* so a column moving in the
# dump can't silently corrupt the import.
_TABLE_SPECS = [
    ("activity", "industryActivity", [
        ("typeID",       "blueprint_id", "INTEGER", _to_int),
        ("activityID",   "activity_id",  "INTEGER", _to_int),
        ("time",         "time",         "INTEGER", _to_int),
    ]),
    ("materials", "industryActivityMaterials", [
        ("typeID",         "blueprint_id", "INTEGER", _to_int),
        ("activityID",     "activity_id",  "INTEGER", _to_int),
        ("materialTypeID", "material_id",  "INTEGER", _to_int),
        ("quantity",       "quantity",     "INTEGER", _to_int),
    ]),
    ("products", "industryActivityProducts", [
        ("typeID",        "blueprint_id", "INTEGER", _to_int),
        ("activityID",    "activity_id",  "INTEGER", _to_int),
        ("productTypeID", "product_id",   "INTEGER", _to_int),
        ("quantity",      "quantity",     "INTEGER", _to_int),
    ]),
    ("probabilities", "industryActivityProbabilities", [
        ("typeID",        "blueprint_id", "INTEGER", _to_int),
        ("activityID",    "activity_id",  "INTEGER", _to_int),
        ("productTypeID", "product_id",   "INTEGER", _to_int),
        ("probability",   "probability",  "REAL",    _to_float),
    ]),
    ("skills", "industryActivitySkills", [
        ("typeID",     "blueprint_id", "INTEGER", _to_int),
        ("activityID", "activity_id",  "INTEGER", _to_int),
        ("skillID",    "skill_id",     "INTEGER", _to_int),
        ("level",      "level",        "INTEGER", _to_int),
    ]),
    ("blueprints", "industryBlueprints", [
        ("typeID",              "blueprint_id",         "INTEGER", _to_int),
        ("maxProductionLimit",  "max_production_limit",  "INTEGER", _to_int),
    ]),
    ("types", "invTypes", [
        ("typeID",         "type_id",         "INTEGER", _to_int),
        ("groupID",        "group_id",        "INTEGER", _to_int),
        ("typeName",       "type_name",       "TEXT",    _to_str),
        ("volume",         "volume",          "REAL",    _to_float),
        ("portionSize",    "portion_size",    "INTEGER", _to_int),
        ("marketGroupID",  "market_group_id", "INTEGER", _to_int),
        ("published",      "published",       "INTEGER", _to_int),
        ("techLevel",      "tech_level",      "INTEGER", _to_int),
    ]),
    ("market_groups", "invMarketGroups", [
        ("marketGroupID",   "market_group_id", "INTEGER", _to_int),
        ("parentGroupID",   "parent_group_id", "INTEGER", _to_int),
        ("marketGroupName", "name",            "TEXT",    _to_str),
    ]),
]

# Indexes that make the per-scan joins (blueprint->materials->types->groups) fast.
_INDEXES = [
    "CREATE INDEX idx_activity_bp     ON activity(blueprint_id, activity_id)",
    "CREATE INDEX idx_materials_bp    ON materials(blueprint_id, activity_id)",
    "CREATE INDEX idx_products_bp     ON products(blueprint_id, activity_id)",
    "CREATE INDEX idx_products_prod   ON products(product_id, activity_id)",
    "CREATE INDEX idx_prob_bp         ON probabilities(blueprint_id, activity_id)",
    "CREATE INDEX idx_skills_bp       ON skills(blueprint_id, activity_id)",
    "CREATE INDEX idx_types_group     ON types(market_group_id)",
]

# Attribute 275 = skillTimeConstant (the training rank multiplier).
_SKILL_RANK_ATTR_ID = 275
# Prerequisite skill attribute IDs (requiredSkill1..4 and their levels).
_PREREQ_SKILL_ATTRS = {182, 183, 184, 1285}    # skill type_id
_PREREQ_LEVEL_ATTRS = {277, 278, 279, 1286}    # required level
_PREREQ_ATTR_PAIRS = [(182, 277), (183, 278), (184, 279), (1285, 1286)]
# All attribute IDs we extract from dgmTypeAttributes in one pass.
_WANTED_ATTRS = {_SKILL_RANK_ATTR_ID} | _PREREQ_SKILL_ATTRS | _PREREQ_LEVEL_ATTRS
# SP thresholds per level (cumulative) = 250 × rank × sqrt(32)^(L-1).
# Precomputed multiplier for each level (relative to rank):
#   L1: 250, L2: 1414, L3: 8000, L4: 45255, L5: 256000
_SP_PER_LEVEL = [0, 250, 1414, 8000, 45255, 256000]
# Default training speed: 27 primary + 21/2 secondary = 37.5 SP/min = 2250 SP/hr.
# This is a reasonable optimized remap baseline.
_SP_PER_HOUR = 2250


# --- download + ingest -----------------------------------------------------
def _stream_csv_rows(session, basename):
    """Yield (header, row_dicts...) from a Fuzzwork SDE CSV, streamed line by
    line so we never hold a multi-MB file in memory. The dumps carry a UTF-8 BOM
    on the header, which we strip. Yields the header list first, then one list of
    string cells per data row."""
    url = f"{SDE_BASE_URL}/{basename}.csv"
    r = session.get(url, headers=_SDE_HEADERS, stream=True, timeout=120)
    r.raise_for_status()
    r.encoding = "utf-8"
    lines = r.iter_lines(decode_unicode=True)

    def _bom_stripped(src):
        # The dumps carry a UTF-8 BOM *before* the opening quote of the header
        # ("﻿\"typeID\"..."), which would otherwise break CSV quote-parsing
        # of the first field. Strip it at the line level, before csv.reader sees it.
        it = iter(src)
        first = next(it, None)
        if first is not None:
            yield first.lstrip("﻿")
            yield from it

    for row in csv.reader(_bom_stripped(lines)):
        if row:
            yield row


def _ingest_table(conn, session, table, basename, columns):
    """Create `table` and bulk-load the kept `columns` from the named CSV.
    Pulls each value by CSV column name (via the header), so column order in the
    dump doesn't matter."""
    db_cols = [c[1] for c in columns]
    col_types = ", ".join(f"{c[1]} {c[2]}" for c in columns)
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE TABLE {table} ({col_types})")
    placeholders = ", ".join("?" for _ in db_cols)
    insert_sql = f"INSERT INTO {table} ({', '.join(db_cols)}) VALUES ({placeholders})"

    rows = _stream_csv_rows(session, basename)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    convs = [(idx.get(c[0]), c[3]) for c in columns]

    batch, total = [], 0
    for raw in rows:
        batch.append(tuple(
            conv(raw[i]) if (i is not None and i < len(raw)) else None
            for i, conv in convs
        ))
        if len(batch) >= _INSERT_BATCH:
            conn.executemany(insert_sql, batch)
            total += len(batch)
            batch = []
    if batch:
        conn.executemany(insert_sql, batch)
        total += len(batch)
    return total


def _ingest_skill_ranks(conn, session):
    """Single-pass extraction of skill-related attributes from dgmTypeAttributes:
    - attribute 275 (skillTimeConstant) -> skill_ranks table
    - attributes 182/183/184/1285 + 277/278/279/1286 -> skill_prereqs table
    The CSV is multi-million rows; we filter in-flight to keep only what we need."""
    conn.execute("DROP TABLE IF EXISTS skill_ranks")
    conn.execute("CREATE TABLE skill_ranks (type_id INTEGER PRIMARY KEY, rank REAL)")
    conn.execute("DROP TABLE IF EXISTS skill_prereqs")
    conn.execute("CREATE TABLE skill_prereqs "
                 "(type_id INTEGER, prereq_skill_id INTEGER, prereq_level INTEGER)")
    rows_iter = _stream_csv_rows(session, "dgmTypeAttributes")
    header = next(rows_iter)
    idx = {name: i for i, name in enumerate(header)}
    ti = idx.get("typeID")
    ai = idx.get("attributeID")
    vi = idx.get("valueInt")
    vf = idx.get("valueFloat")

    # Accumulate per-type attribute values, then flush prereqs when we have pairs.
    rank_batch, prereq_batch = [], []
    # {type_id: {attr_id: value}} — built up row by row
    attrs_by_type = {}
    for raw in rows_iter:
        attr_id = int(raw[ai])
        if attr_id not in _WANTED_ATTRS:
            continue
        type_id = int(raw[ti])
        val = float(raw[vf]) if (vf is not None and raw[vf]) else (
              float(raw[vi]) if (vi is not None and raw[vi]) else None)
        if val is None:
            continue
        if attr_id == _SKILL_RANK_ATTR_ID:
            rank_batch.append((type_id, val))
        else:
            attrs_by_type.setdefault(type_id, {})[attr_id] = val

    # Flush ranks
    if rank_batch:
        conn.executemany("INSERT INTO skill_ranks (type_id, rank) VALUES (?, ?)", rank_batch)

    # Build prereq rows from collected pairs
    for type_id, attrs in attrs_by_type.items():
        for skill_attr, level_attr in _PREREQ_ATTR_PAIRS:
            sid = attrs.get(skill_attr)
            lvl = attrs.get(level_attr)
            if sid and lvl:
                prereq_batch.append((type_id, int(sid), int(lvl)))
    if prereq_batch:
        conn.executemany(
            "INSERT INTO skill_prereqs (type_id, prereq_skill_id, prereq_level) VALUES (?, ?, ?)",
            prereq_batch)
    conn.execute("CREATE INDEX idx_prereqs_type ON skill_prereqs(type_id)")
    return len(rank_batch) + len(prereq_batch)


def build_sde_db(cache_dir, session=None, emit=None):
    """Download every required SDE table and (re)build `sde_industry.sqlite`.

    Builds into a temp file and atomically replaces the live DB, so an
    interrupted/failed rebuild never leaves a half-written database in place.
    `emit`, if given, is called with a short progress string per table. Returns
    the path to the database."""
    session = session or requests.Session()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = cache_dir / SDE_DB_NAME
    tmp_path = cache_dir / (SDE_DB_NAME + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(tmp_path)
    try:
        counts = {}
        for table, basename, columns in _TABLE_SPECS:
            if emit:
                emit(f"Downloading {basename}…")
            counts[table] = _ingest_table(conn, session, table, basename, columns)
        if emit:
            emit("Downloading skill training ranks…")
        counts["skill_ranks"] = _ingest_skill_ranks(conn, session)
        for stmt in _INDEXES:
            conn.execute(stmt)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
            ("built_at", str(int(time.time()))),
            ("source", SDE_BASE_URL),
        ] + [(f"rows_{t}", str(n)) for t, n in counts.items()])
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, final_path)
    return final_path


def sde_db_path(cache_dir):
    return Path(cache_dir) / SDE_DB_NAME


def sde_age_seconds(cache_dir, now=None):
    """Seconds since the local SDE DB was built, or None if it doesn't exist."""
    path = sde_db_path(cache_dir)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    now = time.time() if now is None else now
    return now - int(row[0])


def load_sde_industry(cache_dir, session=None, refresh=False, emit=None):
    """Ensure a fresh-enough `sde_industry.sqlite` exists and return its path.
    Rebuilds when missing, stale (older than SDE_TTL_SECONDS), or `refresh`."""
    age = sde_age_seconds(cache_dir)
    if refresh or age is None or age > SDE_TTL_SECONDS:
        return build_sde_db(cache_dir, session=session, emit=emit)
    return sde_db_path(cache_dir)


def connect_sde(cache_dir):
    """Read-only-ish connection to the SDE DB with dict-like rows. Caller closes."""
    conn = sqlite3.connect(sde_db_path(cache_dir))
    conn.row_factory = sqlite3.Row
    return conn


# --- query helpers ---------------------------------------------------------
def sde_meta(conn):
    """The meta table as a plain dict (built_at, source, per-table row counts)."""
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}


def top_market_groups(conn):
    """Top-level market groups (parent is null), for the category dropdown.
    Returns [{"id", "name"}] sorted by name."""
    rows = conn.execute(
        "SELECT market_group_id AS id, name FROM market_groups "
        "WHERE parent_group_id IS NULL ORDER BY name")
    return [dict(r) for r in rows]


def expand_market_groups(conn, root_ids):
    """All descendant market-group ids of the given roots (inclusive), so a
    user picking a top-level category ('Ships') captures every leaf group its
    manufacturable items actually live in. Iterative BFS over parent links."""
    children = {}
    for r in conn.execute("SELECT market_group_id, parent_group_id FROM market_groups"):
        children.setdefault(r["parent_group_id"], []).append(r["market_group_id"])
    out, stack = set(), list(root_ids)
    while stack:
        gid = stack.pop()
        if gid in out:
            continue
        out.add(gid)
        stack.extend(children.get(gid, []))
    return out


def market_group_names(conn, group_ids):
    """Resolve market_group_id → the second-level ancestor name (immediate child
    of a top-level group). E.g. for Ships>Frigates>Standard Frigates>Caldari,
    returns 'Frigates'. This is one level below what the category dropdown shows,
    giving consistent subcategory labels. Falls back to the group's own name when
    it IS at or near the top level. {id: name}."""
    if not group_ids:
        return {}
    all_groups = {}
    for r in conn.execute("SELECT market_group_id, parent_group_id, name FROM market_groups"):
        all_groups[r["market_group_id"]] = (r["name"], r["parent_group_id"])
    out = {}
    for gid in group_ids:
        if gid not in all_groups:
            continue
        # Walk up to find the second-level ancestor (whose parent is top-level,
        # i.e. whose parent's parent is NULL).
        cur = gid
        prev = gid
        while cur in all_groups:
            name, parent_id = all_groups[cur]
            if parent_id is None:
                # cur is top-level; prev is the second-level (or top if depth=1)
                out[gid] = all_groups[prev][0] if prev != cur else name
                break
            prev = cur
            cur = parent_id
        else:
            out[gid] = all_groups[gid][0]
    return out


def volumes_for(conn, type_ids):
    """SDE packaged-ish volume (m3) per type, from invTypes. Fast (already
    loaded) -- used for the scan's cargo columns. Note this is the SDE `volume`
    (assembled for ships); the detail panel can refine outputs via ESI's
    packaged volume. {type_id: m3}, missing types omitted."""
    if not type_ids:
        return {}
    ids = list(set(type_ids))
    out = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        marks = ", ".join("?" for _ in chunk)
        for r in conn.execute(
            f"SELECT type_id, volume FROM types WHERE type_id IN ({marks})", chunk):
            if r["volume"] is not None:
                out[r["type_id"]] = r["volume"]
    return out


def fetch_adjusted_prices(session, cache_dir, refresh=False):
    """{type_id: adjusted_price} from ESI /markets/prices/ -- the CCP-published
    per-type value that the job-cost EIV is computed against (NOT the market
    price). One bulk call, cached for ADJ_TTL_SECONDS."""
    path = Path(cache_dir) / ADJ_CACHE_NAME
    now = time.time()
    cached = load_json(path, None)
    if not refresh and cached and now - cached.get("_ts", 0) < ADJ_TTL_SECONDS:
        return {int(k): v for k, v in cached["data"].items()}
    r = session.get(f"{ESI}/markets/prices/", headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = {}
    for e in r.json():
        ap = e.get("adjusted_price")
        if ap:
            data[e["type_id"]] = ap
    save_json(path, {"_ts": now, "data": {str(k): v for k, v in data.items()}})
    return data


def manufacturing_candidates(conn, market_group_ids=None):
    """Every manufacturable product: the blueprint, its output type + quantity,
    the product's name, market group and tech level. Optionally restricted to a
    set of market_group_ids (the category filter). Sorted by product name."""
    sql = (
        "SELECT p.blueprint_id, p.product_id, p.quantity AS out_qty, "
        "       t.type_name, t.market_group_id, t.tech_level, t.volume AS out_volume "
        "FROM products p JOIN types t ON t.type_id = p.product_id "
        "WHERE p.activity_id = ? AND t.published = 1"
    )
    params = [ACT_MANUFACTURING]
    if market_group_ids:
        marks = ", ".join("?" for _ in market_group_ids)
        sql += f" AND t.market_group_id IN ({marks})"
        params.extend(market_group_ids)
    sql += " ORDER BY t.type_name"
    return [dict(r) for r in conn.execute(sql, params)]


def candidates_for_blueprints(conn, blueprint_ids):
    """Candidate rows (same shape as manufacturing_candidates) for specific
    blueprint ids regardless of category — used to always include favourites."""
    ids = [int(b) for b in blueprint_ids]
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    sql = (
        "SELECT p.blueprint_id, p.product_id, p.quantity AS out_qty, "
        "       t.type_name, t.market_group_id, t.tech_level, t.volume AS out_volume "
        "FROM products p JOIN types t ON t.type_id = p.product_id "
        f"WHERE p.activity_id = ? AND p.blueprint_id IN ({marks})"
    )
    return [dict(r) for r in conn.execute(sql, [ACT_MANUFACTURING, *ids])]


def materials_for(conn, blueprint_id, activity_id=ACT_MANUFACTURING):
    """Bill of materials for a blueprint activity: [(material_id, base_qty), ...]
    at ME0 (the raw SDE quantities, before any efficiency adjustment)."""
    rows = conn.execute(
        "SELECT material_id, quantity FROM materials "
        "WHERE blueprint_id = ? AND activity_id = ?",
        (blueprint_id, activity_id),
    )
    return [(r["material_id"], r["quantity"]) for r in rows]


def activity_time(conn, blueprint_id, activity_id=ACT_MANUFACTURING):
    """Base time (seconds) for a blueprint activity, or None if not defined."""
    row = conn.execute(
        "SELECT time FROM activity WHERE blueprint_id = ? AND activity_id = ?",
        (blueprint_id, activity_id),
    ).fetchone()
    return row["time"] if row else None


def skills_for(conn, blueprint_id, activity_id=ACT_MANUFACTURING):
    """Required skills for a blueprint activity: [(skill_id, level), ...]."""
    rows = conn.execute(
        "SELECT skill_id, level FROM skills "
        "WHERE blueprint_id = ? AND activity_id = ?",
        (blueprint_id, activity_id),
    )
    return [(r["skill_id"], r["level"]) for r in rows]


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


# --- evaluation (pure: dicts in, dicts out -- no I/O) ----------------------


def tradeability(daily_volume, full=TRADEABILITY_FULL):
    """A 0..100 score for how sellable a product is, from the daily UNITS traded
    on the market (not the transaction count). The point is to demote items that
    look profitable on paper but whose market can't absorb meaningful quantity.

    ``full`` is the daily volume that scores 100 — the "fully tradeable" bar. It
    comes from the user's preset (Quiet/Balanced/Liquidity → 1/50/1000): ammo is
    only impressive when the market moves thousands, a capital component when it
    moves a handful. With the default bar:

      0 units/day              -> 0   (you can't realistically sell it)
      ~10/day                  -> ~28
      ~100/day                 -> ~54
      ~1000/day                -> ~81
      >= full/day              -> 100

    Log scale (traded volume spans orders of magnitude), clamped to [0, 100].
    None in -> None (history unknown)."""
    if daily_volume is None:
        return None
    if daily_volume <= 0:
        return 0
    score = math.log10(1 + daily_volume) / math.log10(1 + max(1.0, full))
    return int(round(max(0.0, min(1.0, score)) * 100))


def units_ahead_in_queue(sell_levels, price):
    """How many units are listed at or below `price` on the sell side -- the
    queue that must clear (or be undercut) before a fresh order at `price` starts
    filling. `sell_levels` is [[price, volume], ...] cheapest-first (the shape
    fetch_orderbook_jita returns). A buyer sweeps the book bottom-up, so every
    unit priced strictly below yours, plus everything already sitting AT your
    price (you'd join the back of that tie), sits ahead of you.

    None price -> None (can't place a queue)."""
    if price is None or not sell_levels:
        return 0 if price is not None else None
    ahead = 0
    for lvl_price, vol in sell_levels:
        if lvl_price <= price:
            ahead += vol
        else:
            break  # levels are cheapest-first; nothing past here is <= price
    return ahead


# Demand is bursty: a handful of buyers each sweep several units, so real daily
# trade counts scatter far wider than a pure Poisson (whose variance equals its
# mean) would predict. We model the window's demand as negative-binomial with a
# variance-to-mean ratio of DEMAND_DISPERSION, which fattens the tails: the odds
# then ease across a price range instead of snapping 100%->0% at the mean, and a
# thin-but-lucky market keeps a believable (not vanishing) chance of clearing.
DEMAND_DISPERSION = 3.0


def price_conditioned_daily_rate(series, price):
    """Units/day that recent history shows trading AT OR ABOVE `price` -- the
    data-driven demand rate for a sell order listed at that price. This is what
    captures "does it actually clear at my price": list at or below where it
    normally trades and you get the full rate; list above the usual range and the
    rate shrinks toward zero, because history shows few buyers ever paid that much.

    `series` is the compact per-day history from lp_core.fetch_history_series:
    ``[{"volume", "low", "high", "average"}, ...]`` over the recent window (ESI
    omits zero-trade days, so len(series) may be < window; we still divide by the
    full window so sparse trading reads as a low rate). For each day we credit the
    share of that day's volume plausibly transacted at/above `price`, approximated
    from where `price` sits in the day's [low, high] range:
        frac = clamp((high - price) / (high - low), 0, 1)
    (a day trading entirely above `price` credits its full volume; entirely below,
    none; straddling, the linear share). `price` None -> the full unconditioned
    rate (no price filter). Returns None when there's no usable history, so the
    caller can distinguish "unknown" from a genuine zero.

    window_days is taken from the series' own coverage but floored at len(series)
    -- we can't see the calendar here, so we assume the caller passed a full window
    and only shorten it if the series is somehow longer (it never is)."""
    if not series:
        return None
    window = max(len(series), HISTORY_WINDOW_DAYS)
    if price is None:
        return sum(d.get("volume") or 0 for d in series) / window
    total = 0.0
    for d in series:
        vol = d.get("volume") or 0
        if not vol:
            continue
        lo, hi = d.get("low"), d.get("high")
        if hi is None or lo is None:
            # No range recorded -- fall back to the day's average as a point price.
            avg = d.get("average")
            frac = 1.0 if (avg is not None and avg >= price) else 0.0
        elif hi <= lo:
            frac = 1.0 if hi >= price else 0.0
        else:
            frac = (hi - price) / (hi - lo)
            frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        total += vol * frac
    return total / window


# The recent-history window (calendar days) the price-conditioned rate normalises
# against. Mirrors lp_core.HISTORY_DAYS; kept here so ind_core has no import of it.
HISTORY_WINDOW_DAYS = 30


def sell_through_probability(units_ahead, daily_volume, qty=1, horizon_days=1.0):
    """How a sell order at a given price is expected to fill within `horizon_days`,
    modelled as bursty buy demand clearing the queue ahead of you.

    `daily_volume` is the demand rate in units/day -- either the raw ~30-day mean
    or, better, the price-conditioned rate from price_conditioned_daily_rate (so a
    price the market rarely pays yields a low rate and low odds). Over
    `horizon_days` the expected demand is ``lam = daily_volume * horizon_days``
    units, treated as overdispersed (negative-binomial, see DEMAND_DISPERSION) so
    the odds ease across price/time instead of snapping at the mean. Your first
    unit fills once demand exceeds `units_ahead`; the whole batch once it exceeds
    ``units_ahead + qty``.

    Returns a dict:
      any       P(at least 1 of your units sells) = P(demand > units_ahead)
      all       P(all `qty` of your units sell)    = P(demand >= units_ahead + qty)
      eta_days  expected days for the queue+batch to clear at the mean rate
                (units_ahead + qty) / daily_volume, or None if the market is dead.

    None daily_volume (history unknown) -> all-None (we can't estimate). A dead
    market (daily_volume == 0) -> zero everything, eta None (never clears)."""
    if daily_volume is None:
        return {"any": None, "all": None, "eta_days": None}
    if units_ahead is None:
        units_ahead = 0
    qty = max(1, int(qty))
    if daily_volume <= 0:
        return {"any": 0.0, "all": 0.0, "eta_days": None}
    lam = daily_volume * max(0.0, horizon_days)
    # units_ahead can be fractional (aggregated float volumes); you fill once
    # STRICTLY more than the whole units ahead of you have been bought, so floor.
    a = math.floor(units_ahead)
    # S(j) = P(demand > j). Your 1st unit needs S(a); the whole batch S(a+qty-1).
    survivals = _demand_survivals(a, qty, lam)
    eta = (units_ahead + qty) / daily_volume
    return {"any": max(0.0, min(1.0, survivals[0])),
            "all": max(0.0, min(1.0, survivals[-1])),
            "eta_days": eta}


# EVE's sell-order durations, in days -- the horizons the sell-through curve is
# quoted across (1d, 3d, 1w, 2w, 1mo, 3mo). A longer listing has more days to
# clear (odds rise), but if the price-conditioned rate is near zero because
# history shows the item rarely sells that high, even 3 months stays low.
SELL_HORIZONS = (
    {"days": 1, "label": "1 day"},
    {"days": 3, "label": "3 days"},
    {"days": 7, "label": "1 week"},
    {"days": 14, "label": "2 weeks"},
    {"days": 30, "label": "1 month"},
    {"days": 90, "label": "3 months"},
)


def sell_through_curve(units_ahead, daily_volume, qty=1, horizons=SELL_HORIZONS):
    """The full-batch sell probability across each duration in `horizons` (default
    SELL_HORIZONS -- EVE's order durations). Returns a list of
    ``{"days", "label", "all", "any", "eta_days"}`` so the UI can show the odds
    growing as the listing runs longer. `daily_volume` should be the
    price-conditioned rate (price_conditioned_daily_rate) so a too-high price keeps
    the whole curve low. None daily_volume -> every row's odds are None."""
    return [
        {"days": h["days"], "label": h["label"],
         **sell_through_probability(units_ahead, daily_volume, qty, h["days"])}
        for h in horizons
    ]


# Above this mean we switch from the exact pmf recurrence to a normal
# approximation. Two reasons: (1) the pmf seed (exp(-lam), or p^r) underflows to
# 0.0 for large means, which would leave the accumulated CDF stuck at 0 and make
# EVERY survival read as a bogus 1.0 (the "always 100%" bug); (2) the exact loop
# is bounded by units_ahead+qty, which can be hundreds of thousands in a deep
# book. The normal approximation is already accurate by mean ~30, so switching at
# 60 keeps both branches in close agreement across the seam.
_NORMAL_APPROX_MEAN = 60.0


def _demand_survivals(a, qty, lam):
    """The list [S(a), S(a+1), ..., S(a+qty-1)] where S(j) = P(demand > j) for the
    window's demand ~ negative-binomial with mean `lam` and variance-to-mean ratio
    DEMAND_DISPERSION (Poisson when the ratio collapses to 1).

    For a modest mean this is exact: accumulate the pmf via its recurrence in a
    single pass (no factorials/special functions). For a large mean we use a
    normal approximation with a continuity correction -- both because the pmf seed
    underflows to 0 for large means (which would wrongly peg every survival at 1)
    and because the exact loop would otherwise run a+qty times."""
    a = max(0, int(a))
    qty = max(1, int(qty))
    od = DEMAND_DISPERSION
    if not od or od <= 1.0:
        mean, var = lam, lam                       # Poisson: var == mean
    else:
        mean, var = lam, od * lam                  # NB: var/mean == od

    if mean >= _NORMAL_APPROX_MEAN:
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd <= 0:
            return [1.0 if a + k < mean else 0.0 for k in range(qty)]
        # S(j) = P(X > j) = P(X >= j+1) ~= P(Z > (j + 0.5 - mean)/sd).
        inv = 1.0 / (sd * math.sqrt(2.0))
        out = []
        for k in range(qty):
            z = (a + k + 0.5 - mean) * inv
            out.append(max(0.0, min(1.0, 0.5 * math.erfc(z))))
        return out

    if not od or od <= 1.0:
        pmf_iter = _poisson_pmf_iter(lam)          # dispersion off -> Poisson
    else:
        # NB(r, p): mean = r(1-p)/p = lam, var = mean/p, so var/mean = 1/p = od.
        p = 1.0 / od
        r = lam * p / (1.0 - p)                     # = lam / (od - 1)
        pmf_iter = _nbinom_pmf_iter(r, p)
    out = []
    cdf = 0.0
    top = a + qty                                  # need S(a) .. S(a+qty-1)
    j = 0
    for pmf in pmf_iter:
        cdf += pmf
        if j >= a:
            out.append(max(0.0, 1.0 - cdf))        # S(j) = 1 - P(X <= j)
        j += 1
        if j >= top:
            break
    # pmf underflowed to 0 before reaching top (huge lam vs small j): remaining
    # survivals are ~0. Pad so the list is always length qty.
    while len(out) < qty:
        out.append(0.0)
    return out


def _poisson_pmf_iter(lam):
    """Yield Poisson(lam) pmf values P(X=0), P(X=1), ... via p_i = p_{i-1}*lam/i.
    e^{-lam} underflows to 0 for very large lam, correctly zeroing small-k mass."""
    term = math.exp(-lam)
    i = 0
    while True:
        yield term
        i += 1
        term *= lam / i


def _nbinom_pmf_iter(r, p):
    """Yield negative-binomial pmf values P(X=0), P(X=1), ... for real r>0 and
    success prob p in (0,1), via P(k) = P(k-1) * (k-1+r)/k * (1-p) with
    P(0) = p^r. No special functions; same shape as the Poisson recurrence."""
    q = 1.0 - p
    term = p ** r
    k = 0
    while True:
        yield term
        k += 1
        term *= (k - 1 + r) / k * q


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


def effective_qty(base_qty, me, runs=1):
    """Units of a material actually consumed, after Material Efficiency.

    EVE applies ME to the whole job and rounds up, with a floor of one unit per
    run: max(runs, ceil(base_qty * runs * (1 - ME/100))). The intermediate is
    rounded to 2 dp first to absorb float noise (matches the in-game numbers)."""
    raw = round(base_qty * runs * (1 - me / 100.0), 2)
    return max(runs, math.ceil(raw))


def manufacturing_cost(bp, prices, adjusted, job_rate, me, runs=1):
    """Per-run input economics for a manufacturing blueprint.

      prices    {type_id: {"sell_min", "buy_max", ...}}  -- live market (lp_core)
      adjusted  {type_id: adjusted_price}                -- ESI /markets/prices/
      job_rate  installation-cost fraction of EIV (the user's manual rate)
      me        blueprint Material Efficiency 0..10
      runs      batch size N (for the *_batch aggregates; per-run figures ignore it)

    Returns material_cost (ME-adjusted, bought at sell_min), EIV (BASE qty x
    adjusted_price -- NOT market, NOT ME-adjusted, per CCP's job-cost formula),
    job_cost (= EIV x job_rate), the per-line breakdown, and a missing_price flag
    when any input has no sell order to price against.

    EVE applies ME to the WHOLE job and rounds ONCE, so an N-run batch consumes
    ceil(base*N*(1-ME)) of each material -- NOT the per-run ceil times N. The
    *_batch fields carry that job-level rounding; multiplying the per-run
    material_cost by N would over-buy (e.g. base 1, ME 10%, 100 runs: per-run
    ceil(0.9)=1 -> 100 units, but the job really eats ceil(90)=90)."""
    n = max(1, int(runs))
    lines = []
    material_cost = material_cost_batch = eiv = 0.0
    missing = False
    for mid, base_qty in bp.get("materials", []):
        eff = effective_qty(base_qty, me)
        eff_batch = effective_qty(base_qty, me, runs=n)
        unit = (prices.get(mid) or {}).get("sell_min")
        adj = adjusted.get(mid)
        # `is not None`, not truthiness: a genuine price of 0 is a real price, not
        # a missing one. Only an absent sell order flips missing_price / drops the
        # EIV contribution.
        line_cost = (eff * unit) if unit is not None else None
        line_cost_batch = (eff_batch * unit) if unit is not None else None
        if line_cost is None:
            missing = True
        else:
            material_cost += line_cost
            material_cost_batch += line_cost_batch
        if adj is not None:
            eiv += base_qty * adj
        lines.append({
            "type_id": mid,
            "base_qty": base_qty,
            "eff_qty": eff,
            "eff_qty_batch": eff_batch,
            "unit_price": unit,
            "line_cost": line_cost,
            "line_cost_batch": line_cost_batch,
        })
    return {
        "material_cost": material_cost,
        "material_cost_batch": material_cost_batch,
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


def _buildable(bp, skill_profile, default_level=0):
    """True if the skill profile meets every skill the blueprint needs. Skills
    absent from the profile assume `default_level` (the "all skills at level X"
    convenience), so an empty profile + default_level=5 means "can I build it
    with everything at 5?"."""
    sp = skill_profile or {}
    return all(sp.get(sid, default_level) >= lvl for sid, lvl in bp.get("skills", []))


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


def invention_cost_per_run(inv, prices, params):
    """Effective blueprint cost for one T2 manufacturing run, sourced from
    invention rather than a bought BPO.

      inv     {datacores:[(id,qty)], probability (base), runs_per_bpc, ...}
      prices  live prices (datacores priced at sell_min)
      params  carries skills_level (the science-skill assumption) and an optional
              decryptor_price.

    Each invention attempt costs the datacores (+ optional decryptor) and yields,
    on success, a BPC good for `runs_per_bpc` manufacturing runs. So the cost
    charged to ONE T2 run is attempt_cost / (success_prob * runs_per_bpc). The
    base success probability is lifted by the three invention skills, here
    approximated with the flat skills_level (encryption /40, two datacore /30).
    The consumed T1 BPC (the invention input) is assumed owned/free -- modelling
    a copied T1 original you already run."""
    runs = inv.get("runs_per_bpc") or 1
    p_base = inv.get("probability") or 0.0
    if p_base <= 0 or runs <= 0:
        return 0.0
    lvl = params.get("skills_level", 0)
    p = min(1.0, p_base * (1 + lvl / 40.0 + 2 * lvl / 30.0))
    attempt = 0.0
    for dcid, qty in inv.get("datacores", []):
        unit = (prices.get(dcid) or {}).get("sell_min")
        if unit is not None:   # a genuine 0 counts; only an absent order is skipped
            attempt += qty * unit
    if params.get("decryptor_price"):
        attempt += params["decryptor_price"]
    return attempt / (p * runs)


def invention_datacores_missing(inv, prices):
    """True when any datacore for this invention has no sell order to price
    against. invention_cost_per_run silently omits such a datacore (understating
    T2 cost/inflating profit with no visible warning), so callers OR this into
    their missing_price flag the same way manufacturing does for materials."""
    if not inv:
        return False
    return any((prices.get(dcid) or {}).get("sell_min") is None
               for dcid, _qty in inv.get("datacores", []))


def evaluate_industry(candidates, prices, adjusted, params):
    """Rank assembled blueprints by ISK/hour. Mirrors lp_core.evaluate()'s
    dual-mode shape: every row carries patient (list at the ask, pay sales tax +
    broker) and instant (dump to the bid, pay sales tax only) figures plus a
    *_best convenience field, then batch totals for params['runs'] units.

    Assumes you own no blueprints: a T1 item's BPO is a one-time buy-in (reported
    as bp_price, with payback_runs) kept OUT of the per-craft margin; a T2 item's
    invention datacores are a recurring cost folded INTO it.

    params keys: me, te, job_rate, sales_tax, broker_fee, runs (N), bpo_prices
    (blueprint_id -> region BPO sell price), skill_profile, skills_level,
    daily_vols (product_id -> median daily volume, for days-to-sell), volumes
    (type_id -> packaged m3, for cargo), owned_me_te (blueprint_id -> (me, te)
    of a blueprint you actually own, overriding the uniform me/te for that row
    only).

    Rows are sorted by isk_per_hour_patient (None last)."""
    me = params.get("me", 0)
    te = params.get("te", 0)
    job_rate = params.get("job_rate", 0.0)
    sales_tax = params.get("sales_tax", 0.0)
    broker = params.get("broker_fee", 0.0)
    n = max(1, int(params.get("runs", 1)))
    skill_profile = params.get("skill_profile") or {}
    default_level = params.get("skills_level", 0)
    daily_vols = params.get("daily_vols") or {}
    volumes = params.get("volumes") or {}
    owned_me_te = params.get("owned_me_te") or {}
    patient_factor = 1 - sales_tax - broker
    instant_factor = 1 - sales_tax

    rows = []
    for bp in candidates:
        pid = bp["product_id"]
        out_qty = bp.get("out_qty") or 1
        owned_entry = owned_me_te.get(bp["blueprint_id"])
        if owned_entry:
            bp_me, bp_te = owned_entry[0], owned_entry[1]
            bp_is_bpo = owned_entry[2] if len(owned_entry) > 2 else True
            bp_max_runs = owned_entry[3] if len(owned_entry) > 3 else -1
        else:
            bp_me, bp_te = me, te
            bp_is_bpo, bp_max_runs = False, 0
        cost = manufacturing_cost(bp, prices, adjusted, job_rate, bp_me, runs=n)

        # Blueprint economics differ by tech tier (assuming you own nothing):
        #   T2 — you can't buy the blueprint (BPCs are contract-only); you invent
        #        it, and the datacores are a RECURRING per-run cost that belongs
        #        in the per-craft margin.
        #   T1 — you buy a reusable BPO: a one-time CAPITAL buy-in, kept out of
        #        the per-craft margin and reported separately with a payback.
        inv = bp.get("invention")
        bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
        invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
        # An unpriced datacore silently understates invention cost — flag it like
        # a missing material so the row is marked, not shown as cheaper-than-real.
        missing_price = cost["missing_price"] or invention_datacores_missing(inv, prices)
        bp_buyin = None if inv else bpo_price          # T1 BPO purchase price
        bp_available = bool(inv or bpo_price)          # obtainable: inventable or BPO on sale

        # Operating (per-craft) cost = materials + job (+ invention for T2). The
        # BPO buy-in is NOT here — that's capital, recovered via payback.
        operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
        total_cost = operating_cost
        # Batch operating cost uses the job-level ME rounding (material_cost_batch),
        # not material_cost*N — job cost and invention are linear so scale by N.
        operating_cost_batch = (cost["material_cost_batch"]
                                + cost["job_cost"] * n + invention_cost * n)

        p = prices.get(pid, {})
        ask, bid = p.get("sell_min"), p.get("buy_max")
        buy_vol = p.get("buy_volume")
        sell_vol = p.get("sell_volume")
        # Depth gate for the INSTANT sell: dumping the batch into buy orders is
        # only real if the current buy book can actually absorb what you'd make
        # (out_qty x runs). A single stale/tiny bid — or a bid that's really in
        # another region and only lingers in the aggregate — otherwise mints a
        # phantom "instant" profit for a market that's empty. When we don't know
        # the depth (None) we don't gate; a zero/thin book suppresses the bid so
        # the row reads "no market" instead of a made-up number.
        need_qty = out_qty * n
        if bid is not None and buy_vol is not None and buy_vol < need_qty:
            bid = None
        rev_patient = (out_qty * ask * patient_factor) if ask else None
        rev_instant = (out_qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - operating_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - operating_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
        # Batch profit off the batch cost (revenue is linear in N).
        batch_profit_patient = (None if rev_patient is None
                                else rev_patient * n - operating_cost_batch)
        batch_profit_instant = (None if rev_instant is None
                                else rev_instant * n - operating_cost_batch)
        margin = lambda pr: (pr / operating_cost) if (pr is not None and operating_cost > 0) else None
        # Runs of profit needed to recoup the BPO purchase (T1 only).
        payback_runs = (math.ceil(bp_buyin / profit_best)
                        if (bp_buyin and profit_best and profit_best > 0) else None)
        payback_runs_patient = (math.ceil(bp_buyin / profit_patient)
                                if (bp_buyin and profit_patient and profit_patient > 0) else None)
        payback_runs_instant = (math.ceil(bp_buyin / profit_instant)
                                if (bp_buyin and profit_instant and profit_instant > 0) else None)

        secs = build_time(bp.get("base_time"), bp_te, skill_profile, default_level)
        hours = (secs / 3600.0) if secs else None
        iph = lambda pr: (pr / hours) if (pr is not None and hours) else None

        in_vol = sum(line["eff_qty"] * volumes[line["type_id"]]
                     for line in cost["lines"] if volumes.get(line["type_id"]) is not None)
        # Batch input cargo uses job-level ME rounding, not in_vol*N.
        in_vol_batch = sum(line["eff_qty_batch"] * volumes[line["type_id"]]
                           for line in cost["lines"] if volumes.get(line["type_id"]) is not None)
        out_vol_each = volumes.get(pid)
        out_vol = (out_qty * out_vol_each) if out_vol_each is not None else None
        dv = daily_vols.get(pid)
        days_to_sell = ((out_qty * n) / dv) if dv else None

        rows.append({
            "blueprint_id": bp["blueprint_id"],
            "product_id": pid,
            "product_name": bp.get("product_name"),
            "market_group_id": bp.get("market_group_id"),
            "tech_level": bp.get("tech_level"),
            "out_qty": out_qty,
            "material_cost": cost["material_cost"],
            "eiv": cost["eiv"],
            "job_cost": cost["job_cost"],
            "invention_cost": invention_cost,   # recurring per-run cost (T2 only)
            "bp_price": bp_buyin,               # one-time BPO buy-in (T1; None for T2)
            "bp_source": "invention" if inv else ("market" if bpo_price else "none"),
            "bp_available": bp_available,
            "payback_runs": payback_runs,
            "payback_runs_patient": payback_runs_patient,
            "payback_runs_instant": payback_runs_instant,
            "requires_invention": bool(inv),
            "total_cost": operating_cost,
            "missing_price": missing_price,
            "ask": ask,
            "bid": bid,
            "profit_patient": profit_patient,
            "profit_instant": profit_instant,
            "profit_best": profit_best,
            "margin_patient": margin(profit_patient),
            "margin_instant": margin(profit_instant),
            "margin_best": margin(profit_best),
            "build_time": secs,
            "isk_per_hour_patient": iph(profit_patient),
            "isk_per_hour_instant": iph(profit_instant),
            "isk_per_hour_best": iph(profit_best),
            "runs": n,
            "total_profit_patient": batch_profit_patient,
            "total_profit_instant": batch_profit_instant,
            "input_volume": in_vol_batch,
            "output_volume": None if out_vol is None else out_vol * n,
            # Per-run building blocks so the UI can rescale batch columns live
            # (profit×N, cargo, days-to-sell) when the run count changes.
            "in_vol_run": in_vol,
            "out_vol_run": out_vol,
            "daily_vol": dv,
            "days_to_sell": days_to_sell,
            "buy_volume": buy_vol,     # live buy-book depth (units) at the hub
            "sell_volume": sell_vol,   # live sell-book depth (units) at the hub
            "tradeability": None,   # patched for the top rows by the web layer
            "buildable": _buildable(bp, skill_profile, default_level),
            "me_used": bp_me,
            "te_used": bp_te,
            "owned_bp_me_te": bp["blueprint_id"] in owned_me_te,
            "owned_is_bpo": bp_is_bpo,
            "owned_max_runs": bp_max_runs,
        })

    rows.sort(key=lambda r: (r["isk_per_hour_patient"] if r["isk_per_hour_patient"] is not None
                             else float("-inf")), reverse=True)
    return rows


def build_industry_detail(bp, prices, names, volumes, params):
    """Full per-item breakdown for the detail panel: the material shopping list
    (qty, unit price, line cost and line m3 at batch N), the EIV/job-cost
    components, the blueprint buy-in / invention cost, output, and revenue/profit
    in both sell modes. Mirrors lp_core.build_detail()."""
    me = params.get("me", 0)
    te = params.get("te", 0)
    n = max(1, int(params.get("runs", 1)))
    job_rate = params.get("job_rate", 0.0)
    sales_tax = params.get("sales_tax", 0.0)
    broker = params.get("broker_fee", 0.0)
    volumes = volumes or {}
    names = names or {}

    # runs=n so the *_batch fields carry EVE's job-level ME rounding (see
    # manufacturing_cost) rather than a per-run figure the client would ×N.
    cost = manufacturing_cost(bp, prices, adjusted=params.get("adjusted", {}),
                              job_rate=job_rate, me=me, runs=n)
    pid = bp["product_id"]
    out_qty = bp.get("out_qty") or 1

    required = []
    input_volume = 0.0
    input_volume_batch = 0.0
    for line in cost["lines"]:
        tid = line["type_id"]
        vol_each = volumes.get(tid)
        line_vol = (line["eff_qty"] * vol_each) if vol_each is not None else None
        if line_vol is not None:
            input_volume += line_vol
        eff_qty_batch = line["eff_qty_batch"]
        line_cost_batch = line["line_cost_batch"]
        line_vol_batch = (eff_qty_batch * vol_each) if vol_each is not None else None
        if line_vol_batch is not None:
            input_volume_batch += line_vol_batch
        required.append({
            "type_id": tid,
            "name": names.get(tid, str(tid)),
            "base_qty": line["base_qty"],
            "eff_qty": line["eff_qty"],
            "eff_qty_batch": eff_qty_batch,
            "unit_price": line["unit_price"],
            "line_cost": line["line_cost"],
            "line_cost_batch": line_cost_batch,
            "volume_each": vol_each,
            "line_volume_batch": line_vol_batch,
        })

    inv = bp.get("invention")
    bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
    invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
    bp_buyin = None if inv else bpo_price
    bp_source = "invention" if inv else ("market" if bpo_price else "none")
    operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
    # Batch cost uses job-level ME rounding (material_cost_batch); job + invention
    # are linear in N. Batch profit is off this, not per-run profit × N.
    operating_cost_batch = (cost["material_cost_batch"]
                            + cost["job_cost"] * n + invention_cost * n)
    p = prices.get(pid, {})
    ask, bid = p.get("sell_min"), p.get("buy_max")
    buy_vol = p.get("buy_volume")
    sell_vol = p.get("sell_volume")
    # Depth gate for the INSTANT sell — identical to evaluate_industry: only treat
    # the bid as real if the current buy book can absorb the batch (out_qty × n).
    # Keeps the detail/peek panel honest instead of showing a phantom instant
    # profit for an empty market. Unknown depth (None) → don't gate.
    if bid is not None and buy_vol is not None and buy_vol < out_qty * n:
        bid = None
    rev_patient = (out_qty * ask * (1 - sales_tax - broker)) if ask else None
    rev_instant = (out_qty * bid * (1 - sales_tax)) if bid else None
    profit_patient = None if rev_patient is None else rev_patient - operating_cost
    profit_instant = None if rev_instant is None else rev_instant - operating_cost
    profit_best = _best(profit_patient, profit_instant)
    batch_profit_patient = (None if rev_patient is None
                            else rev_patient * n - operating_cost_batch)
    batch_profit_instant = (None if rev_instant is None
                            else rev_instant * n - operating_cost_batch)
    payback_runs = (math.ceil(bp_buyin / profit_best)
                    if (bp_buyin and profit_best and profit_best > 0) else None)
    payback_runs_patient = (math.ceil(bp_buyin / profit_patient)
                            if (bp_buyin and profit_patient and profit_patient > 0) else None)
    payback_runs_instant = (math.ceil(bp_buyin / profit_instant)
                            if (bp_buyin and profit_instant and profit_instant > 0) else None)
    out_vol_each = volumes.get(pid)
    out_vol = (out_qty * out_vol_each) if out_vol_each is not None else None

    return {
        "blueprint_id": bp["blueprint_id"],
        "product": {
            "type_id": pid,
            "name": names.get(pid, bp.get("product_name") or str(pid)),
            "quantity": out_qty,
            "volume_each": out_vol_each,
        },
        "required_items": required,
        "material_cost": cost["material_cost"],
        "eiv": cost["eiv"],
        "job_rate": job_rate,
        "job_cost": cost["job_cost"],
        "invention_cost": invention_cost,
        "bp_price": bp_buyin,
        "bp_source": bp_source,
        "bp_available": bool(inv or bpo_price),
        "payback_runs": payback_runs,
        "payback_runs_patient": payback_runs_patient,
        "payback_runs_instant": payback_runs_instant,
        "total_cost": operating_cost,
        "total_cost_batch": operating_cost_batch,
        "material_cost_batch": cost["material_cost_batch"],
        "missing_price": cost["missing_price"] or invention_datacores_missing(inv, prices),
        "ask": ask,
        "bid": bid,
        "sales_tax": sales_tax,
        "broker_fee": broker,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": profit_patient,
        "profit_instant": profit_instant,
        "profit_patient_batch": batch_profit_patient,
        "profit_instant_batch": batch_profit_instant,
        "build_time": build_time(bp.get("base_time"), te,
                                 params.get("skill_profile"),
                                 params.get("skills_level", 0)),
        "me_used": me,
        "te_used": te,
        "runs": n,
        # cargo for the whole batch (job-level ME rounding, not per-run × N)
        "input_volume_batch": input_volume_batch,
        "output_volume_batch": None if out_vol is None else out_vol * n,
        "invention": _invention_detail(bp, prices, names, params),
    }


def _invention_detail(bp, prices, names, params):
    """The invention breakdown for the detail panel (None for T1 items): the
    datacore shopping list, the skill-adjusted success probability, runs per
    invented BPC and the resulting per-run blueprint cost."""
    inv = bp.get("invention")
    if not inv:
        return None
    lvl = params.get("skills_level", 0)
    p = min(1.0, (inv.get("probability") or 0.0) * (1 + lvl / 40.0 + 2 * lvl / 30.0))
    datacores = []
    for dcid, qty in inv.get("datacores", []):
        unit = (prices.get(dcid) or {}).get("sell_min")
        datacores.append({
            "type_id": dcid,
            "name": (names or {}).get(dcid, str(dcid)),
            "quantity": qty,
            "unit_price": unit,
            "line_cost": (qty * unit) if unit else None,
        })
    return {
        "datacores": datacores,
        "base_probability": inv.get("probability"),
        "probability": p,
        "runs_per_bpc": inv.get("runs_per_bpc"),
        "cost_per_run": invention_cost_per_run(inv, prices, params),
    }
