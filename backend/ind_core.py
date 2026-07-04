"""Backward-compatibility shim: re-exports all public names from the refactored
industry modules so that `import ind_core` continues to work."""

from app.core.shared.constants import ESI, HEADERS, USER_AGENT
from app.core.shared.cache import load_json, save_json
from app.core.lp.evaluate import _best

import lp_core

from app.core.industry.sde import (
    SDE_BASE_URL, _SDE_HEADERS, SDE_DB_NAME, SDE_TTL_SECONDS,
    ADJ_CACHE_NAME, ADJ_TTL_SECONDS,
    ACT_MANUFACTURING, ACT_INVENTION,
    _INSERT_BATCH, _TABLE_SPECS, _INDEXES,
    _to_int, _to_float, _to_str,
    _SKILL_RANK_ATTR_ID, _PREREQ_SKILL_ATTRS, _PREREQ_LEVEL_ATTRS,
    _PREREQ_ATTR_PAIRS, _WANTED_ATTRS, _SP_PER_LEVEL, _SP_PER_HOUR,
    _stream_csv_rows, _ingest_table, _ingest_skill_ranks,
    build_sde_db, sde_db_path, sde_age_seconds, load_sde_industry, connect_sde,
    sde_meta, top_market_groups, expand_market_groups, market_group_names,
    volumes_for, fetch_adjusted_prices,
    manufacturing_candidates, candidates_for_blueprints,
    materials_for, activity_time, skills_for,
)
from app.core.industry.blueprints import assemble_blueprints, assemble_invention
from app.core.industry.costs import (
    INDUSTRY_SKILL_ID, ADV_INDUSTRY_SKILL_ID,
    INDUSTRY_TIME_PER_LEVEL, ADV_INDUSTRY_TIME_PER_LEVEL,
    TRADEABILITY_FULL,
    effective_qty, manufacturing_cost, build_time, _buildable,
    tradeability, cheapest_sell_location, training_time_hours,
    _load_prereqs, _walk_skill_tree, missing_skills, bulk_training_time,
)
from app.core.industry.invention import invention_cost_per_run
from app.core.industry.evaluate import evaluate_industry
from app.core.industry.detail import build_industry_detail, _invention_detail
