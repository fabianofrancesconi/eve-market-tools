"""
Tests for the Industry data layer (`ind_core`).

Milestone 1 — SDE ingestion: download Fuzzwork's per-table CSV dumps and build a
local `sde_industry.sqlite`, then query it. Network is mocked: a fake session
serves small in-memory CSV fixtures (with the UTF-8 BOM the real dumps carry) so
the import + query path is exercised end to end without hitting Fuzzwork.
"""
import time
from unittest.mock import MagicMock

import pytest

import ind_core

BOM = "﻿"


# ---------------------------------------------------------------------------
# Factories for the pure-calculation tests (Milestone 2)
# ---------------------------------------------------------------------------

def _bp(**kw):
    base = {
        "blueprint_id": 681, "product_id": 165, "product_name": "Test Frigate",
        "out_qty": 1, "tech_level": 1,
        "materials": [(34, 100), (35, 50)],
        "base_time": 600, "skills": [(3380, 1)],
    }
    base.update(kw)
    return base


def _prices(overrides=None):
    base = {
        34: {"sell_min": 5.0, "buy_max": 4.0},
        35: {"sell_min": 10.0, "buy_max": 9.0},
        165: {"sell_min": 2000.0, "buy_max": 1800.0},
    }
    if overrides:
        base.update(overrides)
    return base


_ADJUSTED = {34: 4.0, 35: 8.0, 165: 1500.0}
_VOLUMES = {34: 0.01, 35: 0.01, 165: 2500.0}

# Small but coherent SDE slice. 681 = a T1 blueprint making product 165;
# 700 = a T2 blueprint making product 12005; 680 = a T1 blueprint whose
# invention (activity 8) yields the T2 blueprint 700. 999 is unpublished.
_FIXTURES = {
    "industryActivity": BOM + '"typeID","activityID","time"\n'
    '"681","1","600"\n'
    '"681","3","210"\n'
    '"700","1","1200"\n'
    '"680","8","3000"\n',

    "industryActivityMaterials": BOM + '"typeID","activityID","materialTypeID","quantity"\n'
    '"681","1","34","100"\n'
    '"681","1","35","50"\n'
    '"680","8","20410","2"\n',

    "industryActivityProducts": BOM + '"typeID","activityID","productTypeID","quantity"\n'
    '"681","1","165","1"\n'
    '"700","1","12005","1"\n'
    '"680","8","700","1"\n',

    "industryActivityProbabilities": BOM + '"typeID","activityID","productTypeID","probability"\n'
    '"680","8","700","0.30"\n',

    "industryActivitySkills": BOM + '"typeID","activityID","skillID","level"\n'
    '"681","1","3380","1"\n'
    '"700","1","3380","3"\n'
    '"680","8","11442","1"\n',

    "industryBlueprints": BOM + '"typeID","maxProductionLimit"\n'
    '"681","300"\n'
    '"700","10"\n',

    # Full 19-column invTypes header so name-based lookup is what's exercised.
    "invTypes": BOM + '"typeID","groupID","typeName","description","mass","volume",'
    '"capacity","portionSize","raceID","basePrice","published","marketGroupID",'
    '"iconID","soundID","graphicID","factionID","metaLevel","techLevel","shipTreeGroupID"\n'
    '"34","18","Tritanium","","0","0.01","0","1","","","1","1857","","","","","","1",""\n'
    '"35","18","Pyerite","","0","0.01","0","1","","","1","1857","","","","","","1",""\n'
    '"165","25","Test Frigate","","0","2500","0","1","","","1","61","","","","","","1",""\n'
    '"12005","324","Ishtar","","0","101000","0","1","","","1","61","","","","","","2",""\n'
    '"999","25","Unpublished","","0","100","0","1","","","0","61","","","","","","1",""\n',

    "invMarketGroups": BOM + '"marketGroupID","parentGroupID","marketGroupName","description","iconID","hasTypes"\n'
    '"61","","Test Group","","",""\n',
}


def _fake_session():
    """A requests-like session whose .get() serves the CSV fixture matching the
    requested table, mimicking a streamed response (iter_lines, no trailing \\n)."""
    def _get(url, **kw):
        base = url.rsplit("/", 1)[-1][:-len(".csv")]
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.iter_lines.return_value = _FIXTURES[base].splitlines()
        return resp
    s = MagicMock()
    s.get.side_effect = _get
    return s


# ---------------------------------------------------------------------------
# build_sde_db / ingestion
# ---------------------------------------------------------------------------

class TestBuildSdeDb:
    def test_builds_db_with_meta(self, tmp_path):
        path = ind_core.build_sde_db(tmp_path, session=_fake_session())
        assert path.exists()
        conn = ind_core.connect_sde(tmp_path)
        try:
            meta = ind_core.sde_meta(conn)
        finally:
            conn.close()
        assert "built_at" in meta
        assert meta["source"] == ind_core.SDE_BASE_URL
        # row counts recorded per table
        assert meta["rows_products"] == "3"
        assert meta["rows_types"] == "5"

    def test_bom_is_stripped_so_first_column_imports(self, tmp_path):
        # The BOM sits before the header's opening quote; if it isn't stripped at
        # the line level the first column ("typeID") never matches and every
        # blueprint_id imports as NULL. Prove the first column actually loaded.
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        conn = ind_core.connect_sde(tmp_path)
        try:
            row = conn.execute(
                "SELECT time FROM activity WHERE blueprint_id=681 AND activity_id=1"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row["time"] == 600

    def test_emit_called_per_table(self, tmp_path):
        seen = []
        ind_core.build_sde_db(tmp_path, session=_fake_session(), emit=seen.append)
        assert any("invTypes" in m for m in seen)
        assert len(seen) == len(ind_core._TABLE_SPECS)


# ---------------------------------------------------------------------------
# query helpers
# ---------------------------------------------------------------------------

class TestQueries:
    def _conn(self, tmp_path):
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        return ind_core.connect_sde(tmp_path)

    def test_manufacturing_candidates_only_activity1_and_published(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            cands = ind_core.manufacturing_candidates(conn)
        finally:
            conn.close()
        ids = {c["product_id"] for c in cands}
        assert ids == {165, 12005}          # product 700 is invention-only; 999 unpublished
        ishtar = next(c for c in cands if c["product_id"] == 12005)
        assert ishtar["tech_level"] == 2
        assert ishtar["out_qty"] == 1

    def test_manufacturing_candidates_market_group_filter(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            none = ind_core.manufacturing_candidates(conn, market_group_ids=[999999])
            some = ind_core.manufacturing_candidates(conn, market_group_ids=[61])
        finally:
            conn.close()
        assert none == []
        assert {c["product_id"] for c in some} == {165, 12005}

    def test_materials_for(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            mats = ind_core.materials_for(conn, 681)
        finally:
            conn.close()
        assert sorted(mats) == [(34, 100), (35, 50)]

    def test_activity_time(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            assert ind_core.activity_time(conn, 681) == 600
            assert ind_core.activity_time(conn, 680, ind_core.ACT_INVENTION) == 3000
            assert ind_core.activity_time(conn, 424242) is None
        finally:
            conn.close()

    def test_skills_for(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            assert ind_core.skills_for(conn, 681) == [(3380, 1)]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# freshness / TTL
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_age_none_when_missing(self, tmp_path):
        assert ind_core.sde_age_seconds(tmp_path) is None

    def test_load_uses_cache_when_fresh(self, tmp_path):
        ind_core.load_sde_industry(tmp_path, session=_fake_session())
        # A second load with a session that explodes if used proves the cached
        # DB is reused rather than rebuilt.
        boom = MagicMock()
        boom.get.side_effect = AssertionError("must not re-download when fresh")
        path = ind_core.load_sde_industry(tmp_path, session=boom)
        assert path == ind_core.sde_db_path(tmp_path)

    def test_load_rebuilds_when_stale(self, tmp_path):
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        # Backdate built_at well past the TTL.
        import sqlite3
        conn = sqlite3.connect(ind_core.sde_db_path(tmp_path))
        old = int(time.time()) - ind_core.SDE_TTL_SECONDS - 10
        conn.execute("UPDATE meta SET value=? WHERE key='built_at'", (str(old),))
        conn.commit()
        conn.close()
        s = _fake_session()
        ind_core.load_sde_industry(tmp_path, session=s)
        assert s.get.called

    def test_refresh_forces_rebuild(self, tmp_path):
        ind_core.load_sde_industry(tmp_path, session=_fake_session())
        s = _fake_session()
        ind_core.load_sde_industry(tmp_path, session=s, refresh=True)
        assert s.get.called


# ---------------------------------------------------------------------------
# assemble_blueprints (DB -> bp dicts the calc functions consume)
# ---------------------------------------------------------------------------

class TestAssembleBlueprints:
    def test_attaches_materials_time_skills(self, tmp_path):
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        conn = ind_core.connect_sde(tmp_path)
        try:
            cands = ind_core.manufacturing_candidates(conn)
            bps = ind_core.assemble_blueprints(conn, cands)
        finally:
            conn.close()
        frig = next(b for b in bps if b["product_id"] == 165)
        assert sorted(frig["materials"]) == [(34, 100), (35, 50)]
        assert frig["base_time"] == 600
        assert frig["skills"] == [(3380, 1)]
        assert frig["product_name"] == "Test Frigate"


# ---------------------------------------------------------------------------
# effective_qty (Material Efficiency)
# ---------------------------------------------------------------------------

class TestEffectiveQty:
    def test_me0_is_base(self):
        assert ind_core.effective_qty(100, 0) == 100

    def test_me10_rounds_up(self):
        # 100 * 0.90 = 90 exactly
        assert ind_core.effective_qty(100, 10) == 90
        # 7 * 0.90 = 6.3 -> ceil 7
        assert ind_core.effective_qty(7, 10) == 7

    def test_floor_of_one_per_run(self):
        assert ind_core.effective_qty(1, 10) == 1

    def test_scales_with_runs(self):
        # 100 * 10 runs * 0.90 = 900
        assert ind_core.effective_qty(100, 10, runs=10) == 900


# ---------------------------------------------------------------------------
# manufacturing_cost
# ---------------------------------------------------------------------------

class TestManufacturingCost:
    def test_material_cost_me0(self):
        c = ind_core.manufacturing_cost(_bp(), _prices(), _ADJUSTED, job_rate=0.06, me=0)
        # 100*5 + 50*10 = 1000
        assert c["material_cost"] == pytest.approx(1000.0)

    def test_material_cost_me10(self):
        c = ind_core.manufacturing_cost(_bp(), _prices(), _ADJUSTED, job_rate=0.06, me=10)
        # 90*5 + 45*10 = 900
        assert c["material_cost"] == pytest.approx(900.0)

    def test_eiv_uses_base_qty_and_adjusted_not_market(self):
        # EIV must ignore ME and market price: 100*4 + 50*8 = 800
        c = ind_core.manufacturing_cost(_bp(), _prices(), _ADJUSTED, job_rate=0.06, me=10)
        assert c["eiv"] == pytest.approx(800.0)
        assert c["job_cost"] == pytest.approx(48.0)  # 800 * 0.06

    def test_missing_price_flagged(self):
        c = ind_core.manufacturing_cost(
            _bp(), _prices({35: {"buy_max": 9.0}}), _ADJUSTED, job_rate=0.06, me=0)
        assert c["missing_price"] is True
        assert c["material_cost"] == pytest.approx(500.0)  # only material 34 priced


# ---------------------------------------------------------------------------
# build_time
# ---------------------------------------------------------------------------

class TestBuildTime:
    def test_no_skills_no_te(self):
        assert ind_core.build_time(600, te=0, skill_profile={}) == pytest.approx(600.0)

    def test_te_and_both_skills_stack(self):
        # 600 * 0.80 * (1-0.20) * (1-0.15) = 326.4
        secs = ind_core.build_time(600, te=20, skill_profile={3380: 5, 3388: 5})
        assert secs == pytest.approx(326.4)

    def test_none_base_time(self):
        assert ind_core.build_time(None, te=10, skill_profile={}) is None


# ---------------------------------------------------------------------------
# blueprint_cost_per_run
# ---------------------------------------------------------------------------

class TestBlueprintCost:
    def test_owned_is_zero(self):
        assert ind_core.blueprint_cost_per_run(_bp(), {"bp_owned": True}) == 0.0

    def test_amortized_bpo_price(self):
        params = {"bpo_prices": {681: 1_000_000.0}, "amortize_runs": 100}
        assert ind_core.blueprint_cost_per_run(_bp(), params) == pytest.approx(10_000.0)

    def test_invention_cost_overrides(self):
        params = {"invention_costs": {681: 5_000.0}, "bpo_prices": {681: 9e9}}
        assert ind_core.blueprint_cost_per_run(_bp(), params) == pytest.approx(5_000.0)

    def test_no_price_is_zero(self):
        assert ind_core.blueprint_cost_per_run(_bp(), {}) == 0.0


# ---------------------------------------------------------------------------
# evaluate_industry
# ---------------------------------------------------------------------------

def _params(**kw):
    base = {"me": 0, "te": 0, "job_rate": 0.06, "sales_tax": 0.05,
            "broker_fee": 0.02, "runs": 1, "skill_profile": {3380: 5, 3388: 5}}
    base.update(kw)
    return base


class TestEvaluateIndustry:
    def _row(self, **pkw):
        rows = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, _params(**pkw))
        return rows[0]

    def test_dual_mode_profit(self):
        r = self._row()
        # total = material 1000 + job 48 + bp 0 = 1048
        # patient = 1*2000*(1-0.05-0.02) - 1048 = 1860 - 1048 = 812
        # instant = 1*1800*(1-0.05)     - 1048 = 1710 - 1048 = 662
        assert r["total_cost"] == pytest.approx(1048.0)
        assert r["profit_patient"] == pytest.approx(812.0)
        assert r["profit_instant"] == pytest.approx(662.0)
        assert r["profit_best"] == pytest.approx(812.0)

    def test_margin_and_isk_per_hour(self):
        r = self._row()
        assert r["margin_best"] == pytest.approx(812.0 / 1048.0)
        # build_time at te0/no-skill-effect-on-600? skills 5/5 reduce it:
        # 600*(1-0.20)*(1-0.15)=408 -> hours 0.11333 -> 812/0.11333
        assert r["build_time"] == pytest.approx(408.0)
        assert r["isk_per_hour_best"] == pytest.approx(812.0 / (408.0 / 3600.0))

    def test_batch_scaling_cargo_and_days(self):
        r = self._row(runs=100, volumes=_VOLUMES, daily_vols={165: 50})
        assert r["total_profit_best"] == pytest.approx(81_200.0)
        # input per run = 100*0.01 + 50*0.01 = 1.5 ; *100 = 150
        assert r["input_volume"] == pytest.approx(150.0)
        # output per run = 1*2500 ; *100 = 250000
        assert r["output_volume"] == pytest.approx(250_000.0)
        # 100 units / 50 per day = 2 days
        assert r["days_to_sell"] == pytest.approx(2.0)

    def test_buildable_gate(self):
        assert self._row(skill_profile={3380: 1})["buildable"] is True
        assert self._row(skill_profile={})["buildable"] is False

    def test_sorted_by_isk_per_hour_best_none_last(self):
        cheap = _bp(blueprint_id=2, product_id=999, product_name="Junk")
        rows = ind_core.evaluate_industry(
            [_bp(), cheap],
            _prices({999: {"sell_min": None, "buy_max": None}}),
            _ADJUSTED, _params())
        assert rows[0]["product_id"] == 165          # profitable first
        assert rows[-1]["isk_per_hour_best"] is None  # unpriced output last


# ---------------------------------------------------------------------------
# build_industry_detail
# ---------------------------------------------------------------------------

class TestBuildIndustryDetail:
    def test_shopping_list_and_cargo_batch(self):
        params = _params(runs=100, adjusted=_ADJUSTED)
        names = {34: "Tritanium", 35: "Pyerite", 165: "Test Frigate"}
        d = ind_core.build_industry_detail(_bp(), _prices(), names, _VOLUMES, params)
        trit = next(x for x in d["required_items"] if x["type_id"] == 34)
        assert trit["eff_qty"] == 100
        assert trit["line_cost"] == pytest.approx(500.0)
        assert trit["line_cost_batch"] == pytest.approx(50_000.0)
        assert trit["line_volume_batch"] == pytest.approx(100.0)  # 100*0.01*100
        assert d["output_volume_batch"] == pytest.approx(250_000.0)

    def test_matches_evaluate(self):
        # detail and evaluate must agree on the per-run economics
        params = _params(adjusted=_ADJUSTED)
        d = ind_core.build_industry_detail(_bp(), _prices(), {}, _VOLUMES, params)
        row = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, params)[0]
        assert d["total_cost"] == pytest.approx(row["total_cost"])
        assert d["profit_patient"] == pytest.approx(row["profit_patient"])
        assert d["job_cost"] == pytest.approx(row["job_cost"])
