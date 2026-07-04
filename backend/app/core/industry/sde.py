"""
SDE (Static Data Export) download, ingest, and query functions for the Industry module.

Downloads Fuzzwork's per-table CSV dumps once and bulk-imports them into a compact local
SQLite database (`sde_industry.sqlite`) that scans then query.
"""
import csv
import os
import sqlite3
import time
from pathlib import Path

import requests

from ..shared.constants import ESI, HEADERS, USER_AGENT
from ..shared.cache import load_json, save_json

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
# SP thresholds per level (cumulative) = 250 * rank * sqrt(32)^(L-1).
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
    # {type_id: {attr_id: value}} -- built up row by row
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
    """Resolve market_group_id -> the second-level ancestor name (immediate child
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
    blueprint ids regardless of category -- used to always include favourites."""
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
