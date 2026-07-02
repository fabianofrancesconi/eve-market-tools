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
from lp_core import ESI, HEADERS, load_json, save_json

# --- constants -------------------------------------------------------------
SDE_BASE_URL = "https://www.fuzzwork.co.uk/dump/latest/csv"
USER_AGENT = "eve-industry-tools/1.0 (fabiano.francesconi@gmail.com)"
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
def _best(*vals):
    """Highest of the supplied values, ignoring None. None if all are None."""
    present = [v for v in vals if v is not None]
    return max(present) if present else None


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
        if unit:
            attempt += qty * unit
    if params.get("decryptor_price"):
        attempt += params["decryptor_price"]
    return attempt / (p * runs)


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
        cost = manufacturing_cost(bp, prices, adjusted, job_rate, bp_me)

        # Blueprint economics differ by tech tier (assuming you own nothing):
        #   T2 — you can't buy the blueprint (BPCs are contract-only); you invent
        #        it, and the datacores are a RECURRING per-run cost that belongs
        #        in the per-craft margin.
        #   T1 — you buy a reusable BPO: a one-time CAPITAL buy-in, kept out of
        #        the per-craft margin and reported separately with a payback.
        inv = bp.get("invention")
        bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
        invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
        bp_buyin = None if inv else bpo_price          # T1 BPO purchase price
        bp_available = bool(inv or bpo_price)          # obtainable: inventable or BPO on sale

        # Operating (per-craft) cost = materials + job (+ invention for T2). The
        # BPO buy-in is NOT here — that's capital, recovered via payback.
        operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
        total_cost = operating_cost

        p = prices.get(pid, {})
        ask, bid = p.get("sell_min"), p.get("buy_max")
        rev_patient = (out_qty * ask * patient_factor) if ask else None
        rev_instant = (out_qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - operating_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - operating_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
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
            "missing_price": cost["missing_price"],
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
            "total_profit_patient": None if profit_patient is None else profit_patient * n,
            "total_profit_instant": None if profit_instant is None else profit_instant * n,
            "input_volume": in_vol * n,
            "output_volume": None if out_vol is None else out_vol * n,
            # Per-run building blocks so the UI can rescale batch columns live
            # (profit×N, cargo, days-to-sell) when the run count changes.
            "in_vol_run": in_vol,
            "out_vol_run": out_vol,
            "daily_vol": dv,
            "days_to_sell": days_to_sell,
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

    cost = manufacturing_cost(bp, prices, adjusted=params.get("adjusted", {}),
                              job_rate=job_rate, me=me)
    pid = bp["product_id"]
    out_qty = bp.get("out_qty") or 1

    required = []
    input_volume = 0.0
    for line in cost["lines"]:
        tid = line["type_id"]
        vol_each = volumes.get(tid)
        line_vol = (line["eff_qty"] * vol_each) if vol_each is not None else None
        if line_vol is not None:
            input_volume += line_vol
        required.append({
            "type_id": tid,
            "name": names.get(tid, str(tid)),
            "base_qty": line["base_qty"],
            "eff_qty": line["eff_qty"],
            "unit_price": line["unit_price"],
            "line_cost": line["line_cost"],
            "line_cost_batch": None if line["line_cost"] is None else line["line_cost"] * n,
            "volume_each": vol_each,
            "line_volume_batch": None if line_vol is None else line_vol * n,
        })

    inv = bp.get("invention")
    bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
    invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
    bp_buyin = None if inv else bpo_price
    bp_source = "invention" if inv else ("market" if bpo_price else "none")
    operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
    p = prices.get(pid, {})
    ask, bid = p.get("sell_min"), p.get("buy_max")
    rev_patient = (out_qty * ask * (1 - sales_tax - broker)) if ask else None
    rev_instant = (out_qty * bid * (1 - sales_tax)) if bid else None
    profit_patient = None if rev_patient is None else rev_patient - operating_cost
    profit_instant = None if rev_instant is None else rev_instant - operating_cost
    profit_best = _best(profit_patient, profit_instant)
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
        "missing_price": cost["missing_price"],
        "ask": ask,
        "bid": bid,
        "sales_tax": sales_tax,
        "broker_fee": broker,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": profit_patient,
        "profit_instant": profit_instant,
        "build_time": build_time(bp.get("base_time"), te,
                                 params.get("skill_profile"),
                                 params.get("skills_level", 0)),
        "me_used": me,
        "te_used": te,
        "runs": n,
        # cargo for the whole batch
        "input_volume_batch": input_volume * n,
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
