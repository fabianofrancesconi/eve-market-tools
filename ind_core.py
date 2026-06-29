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
import os
import sqlite3
import time
from pathlib import Path

import requests

# --- constants -------------------------------------------------------------
SDE_BASE_URL = "https://www.fuzzwork.co.uk/dump/latest/csv"
USER_AGENT = "eve-industry-tools/1.0 (fabiano.francesconi@gmail.com)"
_SDE_HEADERS = {"User-Agent": USER_AGENT}
SDE_DB_NAME = "sde_industry.sqlite"
# The SDE only changes on game patches; a week between rebuilds is plenty.
SDE_TTL_SECONDS = 7 * 24 * 3600

# EVE industry activity IDs (the only two this module models).
ACT_MANUFACTURING = 1
ACT_INVENTION = 8

# Build-time reductions (material cost is unaffected by skills -- only ME is).
# Industry: -4%/level (manufacturing time). Advanced Industry: -3%/level.
INDUSTRY_TIME_PER_LEVEL = 0.04
ADV_INDUSTRY_TIME_PER_LEVEL = 0.03

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
