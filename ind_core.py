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
# Skill type IDs verified against the SDE (types.type_name).
INDUSTRY_SKILL_ID = 3380          # "Industry"          -4%/level
ADV_INDUSTRY_SKILL_ID = 3388      # "Advanced Industry" -3%/level
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


# --- evaluation (pure: dicts in, dicts out -- no I/O) ----------------------
def _best(*vals):
    """Highest of the supplied values, ignoring None. None if all are None."""
    present = [v for v in vals if v is not None]
    return max(present) if present else None


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


def build_time(base_time, te, skill_profile):
    """Seconds to run one manufacturing job, after Time Efficiency and the two
    time skills (Industry -4%/lvl, Advanced Industry -3%/lvl). None if the
    blueprint has no recorded time."""
    if not base_time:
        return None
    ind = (skill_profile or {}).get(INDUSTRY_SKILL_ID, 0)
    adv = (skill_profile or {}).get(ADV_INDUSTRY_SKILL_ID, 0)
    return (base_time * (1 - te / 100.0)
            * (1 - INDUSTRY_TIME_PER_LEVEL * ind)
            * (1 - ADV_INDUSTRY_TIME_PER_LEVEL * adv))


def _buildable(bp, skill_profile):
    """True if the manual skill profile meets every skill the blueprint needs."""
    sp = skill_profile or {}
    return all(sp.get(sid, 0) >= lvl for sid, lvl in bp.get("skills", []))


def blueprint_cost_per_run(bp, params):
    """Per-run blueprint cost. Counted by default; the 'I own it' toggle
    (params['bp_owned']) zeroes it. For T1 this amortizes the BPO purchase price
    over params['amortize_runs']. (T2/invention overrides this in a later
    milestone via params['invention_costs'].)"""
    if params.get("bp_owned"):
        return 0.0
    inv = (params.get("invention_costs") or {}).get(bp["blueprint_id"])
    if inv is not None:
        return inv
    price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
    amort = params.get("amortize_runs") or 1
    return (price / amort) if price else 0.0


def evaluate_industry(candidates, prices, adjusted, params):
    """Rank assembled blueprints by ISK/hour. Mirrors lp_core.evaluate()'s
    dual-mode shape: every row carries patient (list at the ask, pay sales tax +
    broker) and instant (dump to the bid, pay sales tax only) figures plus a
    *_best convenience field, then batch totals for params['runs'] units.

    params keys: me, te, job_rate, sales_tax, broker_fee, runs (N), bp_owned,
    amortize_runs, bpo_prices, skill_profile, daily_vols (product_id -> median
    daily volume, for days-to-sell), volumes (type_id -> packaged m3, for cargo).

    Rows are sorted by isk_per_hour_best (None last)."""
    me = params.get("me", 0)
    te = params.get("te", 0)
    job_rate = params.get("job_rate", 0.0)
    sales_tax = params.get("sales_tax", 0.0)
    broker = params.get("broker_fee", 0.0)
    n = max(1, int(params.get("runs", 1)))
    skill_profile = params.get("skill_profile") or {}
    daily_vols = params.get("daily_vols") or {}
    volumes = params.get("volumes") or {}
    patient_factor = 1 - sales_tax - broker
    instant_factor = 1 - sales_tax

    rows = []
    for bp in candidates:
        pid = bp["product_id"]
        out_qty = bp.get("out_qty") or 1
        cost = manufacturing_cost(bp, prices, adjusted, job_rate, me)
        bp_cost = blueprint_cost_per_run(bp, params)
        total_cost = cost["material_cost"] + cost["job_cost"] + bp_cost

        p = prices.get(pid, {})
        ask, bid = p.get("sell_min"), p.get("buy_max")
        rev_patient = (out_qty * ask * patient_factor) if ask else None
        rev_instant = (out_qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - total_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - total_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
        margin = lambda pr: (pr / total_cost) if (pr is not None and total_cost > 0) else None

        secs = build_time(bp.get("base_time"), te, skill_profile)
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
            "tech_level": bp.get("tech_level"),
            "out_qty": out_qty,
            "material_cost": cost["material_cost"],
            "eiv": cost["eiv"],
            "job_cost": cost["job_cost"],
            "bp_cost": bp_cost,
            "total_cost": total_cost,
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
            "total_profit_best": None if profit_best is None else profit_best * n,
            "input_volume": in_vol * n,
            "output_volume": None if out_vol is None else out_vol * n,
            "daily_vol": dv,
            "days_to_sell": days_to_sell,
            "buildable": _buildable(bp, skill_profile),
        })

    rows.sort(key=lambda r: (r["isk_per_hour_best"] if r["isk_per_hour_best"] is not None
                             else float("-inf")), reverse=True)
    return rows


def build_industry_detail(bp, prices, names, volumes, params):
    """Full per-item breakdown for the detail panel: the material shopping list
    (qty, unit price, line cost and line m3 at batch N), the EIV/job-cost
    components, blueprint cost, output, and revenue/profit in both sell modes.
    All money/volume scale with params['runs'] (N) except bp_cost, which is a
    fixed per-run amortized add. Mirrors lp_core.build_detail()."""
    me = params.get("me", 0)
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

    bp_cost = blueprint_cost_per_run(bp, params)
    total_cost = cost["material_cost"] + cost["job_cost"] + bp_cost
    p = prices.get(pid, {})
    ask, bid = p.get("sell_min"), p.get("buy_max")
    rev_patient = (out_qty * ask * (1 - sales_tax - broker)) if ask else None
    rev_instant = (out_qty * bid * (1 - sales_tax)) if bid else None
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
        "bp_cost": bp_cost,
        "total_cost": total_cost,
        "missing_price": cost["missing_price"],
        "ask": ask,
        "bid": bid,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": None if rev_patient is None else rev_patient - total_cost,
        "profit_instant": None if rev_instant is None else rev_instant - total_cost,
        "build_time": build_time(bp.get("base_time"), params.get("te", 0),
                                 params.get("skill_profile")),
        "runs": n,
        # cargo for the whole batch
        "input_volume_batch": input_volume * n,
        "output_volume_batch": None if out_vol is None else out_vol * n,
    }
