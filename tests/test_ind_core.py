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

    # Skill 3380 (Industry): rank 1, no prereqs
    # Skill 11442: rank 3, requires skill 3380 at level 3
    # Skill 25000 (fake): rank 2, requires skill 11442 at level 2 (two-deep chain)
    "dgmTypeAttributes": BOM + '"typeID","attributeID","valueInt","valueFloat"\n'
    '"3380","275","","1.0"\n'
    '"11442","275","","3.0"\n'
    '"25000","275","","2.0"\n'
    '"11442","182","","3380.0"\n'
    '"11442","277","","3.0"\n'
    '"25000","182","","11442.0"\n'
    '"25000","277","","2.0"\n'
    '"99999","180","","500.0"\n',
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
        assert any("skill training ranks" in m for m in seen)
        assert len(seen) == len(ind_core._TABLE_SPECS) + 1  # +1 for skill_ranks


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

    def test_candidates_for_blueprints(self, tmp_path):
        # Always-include path for favourites: fetch specific blueprints by id,
        # regardless of category. 681 -> product 165 (manufacturing).
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        conn = ind_core.connect_sde(tmp_path)
        try:
            rows = ind_core.candidates_for_blueprints(conn, [681, 999999])
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["blueprint_id"] == 681
        assert rows[0]["product_id"] == 165
        assert ind_core.candidates_for_blueprints(conn, []) == []

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

    def test_batch_material_cost_uses_whole_job_me_rounding(self):
        # EVE rounds ME at the WHOLE-job level, not per run. With base_qty=7 and
        # ME=10%, one run consumes ceil(6.3)=7, but a 10-run job consumes
        # ceil(63)=63 — NOT 7*10=70. (base_qty=1 wouldn't show it: the per-run
        # floor max(runs, …) forces `runs` units either way.) material_cost_batch
        # must be 63-based, not 70.
        bp = _bp(materials=[(34, 7)])   # 7 units/run of material 34 @ 5.0
        c = ind_core.manufacturing_cost(bp, _prices(), _ADJUSTED,
                                        job_rate=0.06, me=10, runs=10)
        assert c["lines"][0]["eff_qty"] == 7            # per-run (unchanged)
        assert c["lines"][0]["eff_qty_batch"] == 63     # whole-job rounding
        assert c["material_cost"] == pytest.approx(35.0)         # per-run 7*5
        assert c["material_cost_batch"] == pytest.approx(315.0)  # 63*5, not 350
        assert c["lines"][0]["line_cost_batch"] == pytest.approx(315.0)

    def test_batch_defaults_to_single_run(self):
        # runs=1 (default): batch fields equal per-run fields.
        c = ind_core.manufacturing_cost(_bp(), _prices(), _ADJUSTED, job_rate=0.06, me=10)
        assert c["material_cost_batch"] == pytest.approx(c["material_cost"])
        assert c["lines"][0]["eff_qty_batch"] == c["lines"][0]["eff_qty"]

    def test_zero_price_is_a_real_price_not_missing(self):
        # A genuine sell_min of 0 is a real (if odd) price — it must be counted,
        # not treated as a missing order. Only an absent sell_min flips missing.
        c = ind_core.manufacturing_cost(
            _bp(), _prices({35: {"sell_min": 0.0, "buy_max": 9.0}}),
            _ADJUSTED, job_rate=0.06, me=0)
        assert c["missing_price"] is False           # 0 is priced, not missing
        assert c["material_cost"] == pytest.approx(500.0)   # 100*5 + 50*0
        # And its line cost is 0.0, not None.
        line35 = next(l for l in c["lines"] if l["type_id"] == 35)
        assert line35["line_cost"] == pytest.approx(0.0)

    def test_zero_adjusted_price_still_contributes_to_eiv(self):
        # adjusted price 0 must be added to EIV (0 contribution) without being
        # dropped as "falsy"; a non-zero one on the other material still counts.
        c = ind_core.manufacturing_cost(
            _bp(), _prices(), {34: 0.0, 35: 8.0}, job_rate=0.06, me=0)
        # EIV = 100*0 + 50*8 = 400
        assert c["eiv"] == pytest.approx(400.0)


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
        assert r["total_profit_patient"] == pytest.approx(81_200.0)
        assert r["total_profit_instant"] == pytest.approx(66_200.0)
        # input per run = 100*0.01 + 50*0.01 = 1.5 ; *100 = 150
        assert r["input_volume"] == pytest.approx(150.0)
        # output per run = 1*2500 ; *100 = 250000
        assert r["output_volume"] == pytest.approx(250_000.0)
        # 100 units / 50 per day = 2 days
        assert r["days_to_sell"] == pytest.approx(2.0)

    def test_instant_gated_when_buy_book_too_thin(self):
        # The output market has a bid (buy_max 1800) but the live buy book only
        # holds 0 units — dumping the batch into it isn't real, so the instant
        # figures must blank out (no phantom "instant" profit) while the patient
        # (list) side, which doesn't depend on buy depth, is untouched.
        prices = _prices({165: {"sell_min": 2000.0, "buy_max": 1800.0,
                                "buy_volume": 0.0}})
        r = ind_core.evaluate_industry([_bp()], prices, _ADJUSTED, _params())[0]
        assert r["profit_patient"] == pytest.approx(812.0)
        assert r["bid"] is None
        assert r["profit_instant"] is None
        assert r["profit_best"] == pytest.approx(812.0)   # falls back to patient

    def test_instant_gated_against_batch_size(self):
        # A book with 10 units on the bid is fine for 1 run but not for 100 runs
        # of a 1x blueprint (needs 100). Same item, different batch → gate flips.
        prices = _prices({165: {"sell_min": 2000.0, "buy_max": 1800.0,
                                "buy_volume": 10.0}})
        one = ind_core.evaluate_industry([_bp()], prices, _ADJUSTED, _params())[0]
        assert one["profit_instant"] is not None          # 1 unit fits in 10
        many = ind_core.evaluate_industry([_bp()], prices, _ADJUSTED,
                                          _params(runs=100))[0]
        assert many["profit_instant"] is None             # 100 > 10 on the book

    def test_instant_not_gated_when_depth_unknown(self):
        # No buy_volume in the price dict (e.g. pre-verify Fuzzwork miss) → we
        # don't know the depth, so we must NOT suppress a real-looking bid.
        r = self._row()
        assert r["profit_instant"] == pytest.approx(662.0)

    def test_buildable_gate(self):
        assert self._row(skill_profile={3380: 1})["buildable"] is True
        assert self._row(skill_profile={})["buildable"] is False

    def test_batch_profit_and_cargo_use_whole_job_me_rounding(self):
        # base_qty 7 @ 5.0, ME 10%, 10 runs. Whole-job materials = 63 units = 315
        # ISK (not 7*10*5 = 350). Job cost is linear: EIV = 7*4 = 28, job_cost =
        # 28*0.06 = 1.68, ×10 = 16.8. Batch operating cost = 315 + 16.8 = 331.8.
        bp = _bp(materials=[(34, 7)])
        params = _params(runs=10, me=10, volumes={34: 0.01, 165: 2500.0},
                         daily_vols={165: 50})
        r = ind_core.evaluate_industry([bp], _prices(), _ADJUSTED, params)[0]
        assert r["material_cost"] == pytest.approx(35.0)   # per-run 7*5
        # revenue/run patient = 2000*(1-0.05-0.02) = 1860 ; batch rev = 18600
        assert r["total_profit_patient"] == pytest.approx(18600.0 - 331.8)
        # instant rev/run = 1800*0.95 = 1710 ; batch = 17100 - 331.8
        assert r["total_profit_instant"] == pytest.approx(17100.0 - 331.8)
        # input cargo = 63 units * 0.01 = 0.63 m³ (not 70*0.01 = 0.7)
        assert r["input_volume"] == pytest.approx(0.63)

    def test_sorted_by_isk_per_hour_patient_none_last(self):
        cheap = _bp(blueprint_id=2, product_id=999, product_name="Junk")
        rows = ind_core.evaluate_industry(
            [_bp(), cheap],
            _prices({999: {"sell_min": None, "buy_max": None}}),
            _ADJUSTED, _params())
        assert rows[0]["product_id"] == 165          # profitable first
        assert rows[-1]["isk_per_hour_patient"] is None  # unpriced output last

    def test_owned_me_te_overrides_uniform_assumption_for_that_row_only(self):
        owned = _bp()               # blueprint_id 681
        not_owned = _bp(blueprint_id=2, product_id=999, product_name="Junk")
        rows = ind_core.evaluate_industry(
            [owned, not_owned], _prices({999: {"sell_min": 2000.0, "buy_max": 1800.0}}),
            _ADJUSTED, _params(owned_me_te={681: (10, 20)}))
        r_owned = next(r for r in rows if r["blueprint_id"] == 681)
        r_other = next(r for r in rows if r["blueprint_id"] == 2)
        # ME 10 -> 90/45 units instead of 100/50 -> material cost 900 not 1000.
        assert r_owned["material_cost"] == pytest.approx(900.0)
        assert r_owned["me_used"] == 10
        assert r_owned["te_used"] == 20
        assert r_owned["owned_bp_me_te"] is True
        # TE 20 + skills 5/5 -> 326.4s, matches TestBuildTime's te20/skill5-5 case.
        assert r_owned["build_time"] == pytest.approx(326.4)
        # Untouched row keeps the uniform me=0/te=0 from _params().
        assert r_other["material_cost"] == pytest.approx(1000.0)
        assert r_other["me_used"] == 0
        assert r_other["owned_bp_me_te"] is False


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

    def test_batch_shopping_list_uses_whole_job_me_rounding(self):
        # base_qty 7, ME 10%, 10 runs -> batch qty 63 (not 70), cost 315, vol 0.63.
        bp = _bp(materials=[(34, 7)])
        params = _params(runs=10, me=10, adjusted=_ADJUSTED)
        names = {34: "Tritanium", 165: "Test Frigate"}
        d = ind_core.build_industry_detail(bp, _prices(), names, _VOLUMES, params)
        line = next(x for x in d["required_items"] if x["type_id"] == 34)
        assert line["eff_qty"] == 7
        assert line["eff_qty_batch"] == 63
        assert line["line_cost_batch"] == pytest.approx(315.0)
        assert line["line_volume_batch"] == pytest.approx(0.63)
        assert d["input_volume_batch"] == pytest.approx(0.63)
        # Batch total cost = 315 materials + job (28*0.06=1.68)*10 = 16.8 -> 331.8
        assert d["material_cost_batch"] == pytest.approx(315.0)
        assert d["total_cost_batch"] == pytest.approx(331.8)
        assert d["profit_patient_batch"] == pytest.approx(2000 * (1 - 0.07) * 10 - 331.8)

    def test_detail_batch_matches_evaluate_batch(self):
        # The detail panel and the scan must agree on batch economics.
        bp = _bp(materials=[(34, 1)])
        params = _params(runs=37, me=7, adjusted=_ADJUSTED, volumes=_VOLUMES,
                         daily_vols={165: 50})
        d = ind_core.build_industry_detail(bp, _prices(), {}, _VOLUMES, params)
        row = ind_core.evaluate_industry([bp], _prices(), _ADJUSTED, params)[0]
        assert d["total_cost_batch"] == pytest.approx(
            row["material_cost"] and (d["material_cost_batch"] + d["job_cost"] * 37))
        assert d["profit_patient_batch"] == pytest.approx(row["total_profit_patient"])
        assert d["profit_instant_batch"] == pytest.approx(row["total_profit_instant"])

    def test_exposes_tax_and_broker_rates(self):
        # detail panel re-derives ISK fee/tax amounts client-side from these rates
        params = _params(adjusted=_ADJUSTED)
        d = ind_core.build_industry_detail(_bp(), _prices(), {}, _VOLUMES, params)
        assert d["sales_tax"] == pytest.approx(0.05)
        assert d["broker_fee"] == pytest.approx(0.02)

    def test_detail_instant_depth_gated(self):
        # The detail/peek panel must apply the same instant-sell depth gate as the
        # scan: an empty buy book blanks the instant figures (bid, revenue, profit)
        # while the patient/list side stays intact.
        params = _params(runs=10, adjusted=_ADJUSTED)
        prices = _prices({165: {"sell_min": 2000.0, "buy_max": 1800.0,
                                "buy_volume": 0.0}})
        d = ind_core.build_industry_detail(_bp(), prices, {}, _VOLUMES, params)
        assert d["bid"] is None
        assert d["revenue_instant"] is None
        assert d["profit_instant"] is None
        assert d["profit_instant_batch"] is None
        assert d["revenue_patient"] is not None      # list side untouched
        assert d["profit_patient"] is not None

    def test_matches_evaluate(self):
        # detail and evaluate must agree on the per-run economics
        params = _params(adjusted=_ADJUSTED)
        d = ind_core.build_industry_detail(_bp(), _prices(), {}, _VOLUMES, params)
        row = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, params)[0]
        assert d["total_cost"] == pytest.approx(row["total_cost"])
        assert d["profit_patient"] == pytest.approx(row["profit_patient"])
        assert d["job_cost"] == pytest.approx(row["job_cost"])


# ---------------------------------------------------------------------------
# invention_cost_per_run (Milestone 4 — T2)
# ---------------------------------------------------------------------------

def _inv(**kw):
    base = {"t1_blueprint_id": 600, "datacores": [(20410, 2), (20411, 4)],
            "probability": 0.30, "runs_per_bpc": 10}
    base.update(kw)
    return base


def _inv_prices():
    return {20410: {"sell_min": 1000.0}, 20411: {"sell_min": 500.0}}


class TestInventionCost:
    def test_attempt_cost_over_prob_and_runs(self):
        # attempt = 2*1000 + 4*500 = 4000 ; skills 0 -> p=0.30 ; runs 10
        # cost/run = 4000 / (0.30 * 10) = 1333.33
        c = ind_core.invention_cost_per_run(_inv(), _inv_prices(), {"skills_level": 0})
        assert c == pytest.approx(4000.0 / (0.30 * 10))

    def test_skills_raise_probability_lowering_cost(self):
        # skills_level 5: p = 0.30 * (1 + 5/40 + 2*5/30) = 0.30 * 1.4583 = 0.4375
        p = 0.30 * (1 + 5/40 + 2*5/30)
        c = ind_core.invention_cost_per_run(_inv(), _inv_prices(), {"skills_level": 5})
        assert c == pytest.approx(4000.0 / (p * 10))

    def test_decryptor_price_added(self):
        c0 = ind_core.invention_cost_per_run(_inv(), _inv_prices(), {"skills_level": 0})
        c1 = ind_core.invention_cost_per_run(
            _inv(), _inv_prices(), {"skills_level": 0, "decryptor_price": 2000.0})
        assert c1 > c0
        assert c1 == pytest.approx(6000.0 / (0.30 * 10))

    def test_zero_probability_is_zero(self):
        assert ind_core.invention_cost_per_run(_inv(probability=0), _inv_prices(), {}) == 0.0

    def test_unpriced_datacore_flagged_missing(self):
        # A datacore with no sell order is silently skipped from invention cost
        # (understating it). invention_datacores_missing surfaces that so callers
        # can flag the row instead of showing fake profit.
        assert ind_core.invention_datacores_missing(_inv(), _inv_prices()) is False
        # Drop 20411's price -> missing.
        assert ind_core.invention_datacores_missing(
            _inv(), {20410: {"sell_min": 1000.0}}) is True
        assert ind_core.invention_datacores_missing(None, {}) is False

    def test_zero_priced_datacore_is_not_missing(self):
        # A genuine 0 price is a real price, not a missing order.
        assert ind_core.invention_datacores_missing(
            _inv(), {20410: {"sell_min": 0.0}, 20411: {"sell_min": 0.0}}) is False

    def test_t2_row_missing_price_when_datacore_unpriced(self):
        # The scan row's missing_price must reflect an unpriced datacore, not
        # just unpriced materials.
        bp = _bp(invention=_inv())
        merged = _prices()
        merged.update({20410: {"sell_min": 1000.0}})   # 20411 deliberately unpriced
        r = ind_core.evaluate_industry([bp], merged, _ADJUSTED, _params())[0]
        assert r["missing_price"] is True
        # Detail panel agrees.
        d = ind_core.build_industry_detail(bp, merged, {}, _VOLUMES,
                                           _params(adjusted=_ADJUSTED))
        assert d["missing_price"] is True

    def test_t2_invention_cost_is_in_operating_profit(self):
        # For a T2 item the invention (datacore) cost is a recurring per-run cost
        # folded into the margin, not a separate buy-in.
        bp = _bp(invention=_inv())
        merged = _prices(); merged.update(_inv_prices())
        r = ind_core.evaluate_industry([bp], merged, _ADJUSTED, _params())[0]
        inv_cost = ind_core.invention_cost_per_run(_inv(), merged, _params())
        assert r["invention_cost"] == pytest.approx(inv_cost)
        assert r["bp_price"] is None          # no BPO to buy for T2
        # profit = revenue - (material + job + invention)
        expected = r["ask"] * (1 - 0.05 - 0.02) - r["total_cost"]
        assert r["profit_patient"] == pytest.approx(expected)

    def test_detail_includes_invention_block(self):
        bp = _bp(invention=_inv())
        params = _params(adjusted=_ADJUSTED)
        names = {20410: "Datacore A", 20411: "Datacore B"}
        merged = _prices(); merged.update(_inv_prices())
        d = ind_core.build_industry_detail(bp, merged, names, _VOLUMES, params)
        assert d["invention"] is not None
        assert d["invention"]["runs_per_bpc"] == 10
        assert len(d["invention"]["datacores"]) == 2
        assert d["invention"]["datacores"][0]["name"] == "Datacore A"

    def test_t1_has_no_invention_block(self):
        d = ind_core.build_industry_detail(_bp(), _prices(), {}, _VOLUMES,
                                           _params(adjusted=_ADJUSTED))
        assert d["invention"] is None


# ---------------------------------------------------------------------------
# assemble_invention (DB -> bp['invention'])
# ---------------------------------------------------------------------------

class TestAssembleInvention:
    def test_attaches_invention_to_t2(self, tmp_path):
        # Fixture: T1 bp 680 invents T2 bp 700 (runs 1, prob 0.30, datacore 20410 x2),
        # and 700 manufactures product 12005.
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        conn = ind_core.connect_sde(tmp_path)
        try:
            cands = ind_core.manufacturing_candidates(conn)
            bps = ind_core.assemble_blueprints(conn, cands)
            ind_core.assemble_invention(conn, bps)
        finally:
            conn.close()
        t2 = next(b for b in bps if b["blueprint_id"] == 700)
        t1 = next(b for b in bps if b["blueprint_id"] == 681)
        assert t2["invention"] is not None
        assert t2["invention"]["t1_blueprint_id"] == 680
        assert t2["invention"]["probability"] == pytest.approx(0.30)
        assert t2["invention"]["runs_per_bpc"] == 1
        assert t2["invention"]["datacores"] == [(20410, 2)]
        assert t1["invention"] is None


# ---------------------------------------------------------------------------
# Blueprint availability — hide items you can neither own nor obtain
# ---------------------------------------------------------------------------

class TestBlueprintAvailability:
    def test_t1_unobtainable_when_no_bpo_for_sale(self):
        r = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, _params())[0]
        assert r["bp_available"] is False
        assert r["bp_source"] == "none"
        assert r["bp_price"] is None

    def test_t1_buyin_and_payback_with_region_bpo_price(self):
        # T1 BPO is a one-time buy-in, NOT in the per-run margin; payback = runs
        # to recoup it from operating profit (812/run → ceil(1e6/812) = 1232).
        r = ind_core.evaluate_industry(
            [_bp()], _prices(), _ADJUSTED, _params(bpo_prices={681: 1_000_000.0}))[0]
        assert r["bp_available"] is True
        assert r["bp_source"] == "market"
        assert r["bp_price"] == pytest.approx(1_000_000.0)
        assert r["total_cost"] == pytest.approx(1048.0)   # buy-in excluded from cost
        assert r["profit_best"] == pytest.approx(812.0)
        assert r["payback_runs"] == 1232

    def test_t2_available_via_invention(self):
        bp = _bp(invention=_inv())
        merged = _prices(); merged.update(_inv_prices())
        r = ind_core.evaluate_industry([bp], merged, _ADJUSTED, _params())[0]
        assert r["bp_available"] is True and r["bp_source"] == "invention"

    def test_tradeability_none_and_zero(self):
        # tradeability is based on daily UNITS traded, not transaction count
        assert ind_core.tradeability(None) is None
        assert ind_core.tradeability(0) == 0
        assert ind_core.tradeability(-5) == 0

    def test_tradeability_full_caps_at_100(self):
        assert ind_core.tradeability(ind_core.TRADEABILITY_FULL) == 100
        assert ind_core.tradeability(10_000_000) == 100   # clamped

    def test_tradeability_monotonic_and_values(self):
        import math
        f = ind_core.TRADEABILITY_FULL
        exp10 = round(100 * math.log10(11) / math.log10(1 + f))
        assert ind_core.tradeability(10) == exp10          # ~35 units/day
        assert (ind_core.tradeability(10) < ind_core.tradeability(100)
                < ind_core.tradeability(1000))
        assert ind_core.tradeability(5) < 30               # a thin market scores low

    def test_tradeability_full_from_preset(self):
        # The preset sets the volume that scores 100 (Quiet 1 / Balanced 50 /
        # Liquidity 1000). A lower bar makes the same volume look more tradeable.
        assert ind_core.tradeability(50, full=50) == 100
        assert ind_core.tradeability(1000, full=1000) == 100
        # 50/day is fully tradeable on the Balanced bar but middling on Liquidity.
        assert ind_core.tradeability(50, full=1000) < 70
        # A tiny full bar never divides by zero and still caps at 100.
        assert ind_core.tradeability(5, full=1) == 100
        assert ind_core.tradeability(5, full=0) == 100

    def test_cheapest_sell_location(self):
        orders = [
            {"is_buy_order": True,  "price": 999.0, "location_id": 1},
            {"is_buy_order": False, "price": 1500.0, "location_id": 60003760},
            {"is_buy_order": False, "price": 1200.0, "location_id": 60008494},
        ]
        loc = ind_core.cheapest_sell_location(orders)
        assert loc["price"] == 1200.0
        assert loc["location_id"] == 60008494
        assert loc["orders"] == 2

    def test_cheapest_sell_location_none_when_no_sells(self):
        assert ind_core.cheapest_sell_location(
            [{"is_buy_order": True, "price": 5.0, "location_id": 1}]) is None

    # ── Sell-through probability model (Market tab) ──────────────────────────
    def test_units_ahead_counts_at_or_below_price(self):
        # Cheapest-first sell book; a fresh order at `price` sits behind every
        # unit priced at or below it (you join the back of a tie at your price).
        book = [[100.0, 5], [101.0, 3], [102.0, 10]]
        assert ind_core.units_ahead_in_queue(book, 100.0) == 5
        assert ind_core.units_ahead_in_queue(book, 101.0) == 8   # 5 + 3
        assert ind_core.units_ahead_in_queue(book, 101.5) == 8   # nothing at 101.5..102
        assert ind_core.units_ahead_in_queue(book, 102.0) == 18
        assert ind_core.units_ahead_in_queue(book, 99.0) == 0    # undercut everyone
        assert ind_core.units_ahead_in_queue([], 100.0) == 0
        assert ind_core.units_ahead_in_queue(book, None) is None

    def test_sell_through_unknown_and_dead_market(self):
        # No history → can't estimate (all None). Dead market → zero odds, no ETA.
        unknown = ind_core.sell_through_probability(0, None, qty=1)
        assert unknown == {"any": None, "all": None, "eta_days": None}
        dead = ind_core.sell_through_probability(0, 0, qty=5)
        assert dead["any"] == 0.0 and dead["all"] == 0.0 and dead["eta_days"] is None

    def test_sell_through_probabilities_bounded_and_ordered(self):
        # P(sell ≥1) is always ≥ P(sell all); both in [0,1]; ETA at the mean rate.
        p = ind_core.sell_through_probability(units_ahead=10, daily_volume=100,
                                              qty=5, horizon_days=1.0)
        assert 0.0 <= p["all"] <= p["any"] <= 1.0
        assert p["eta_days"] == (10 + 5) / 100

    def test_sell_through_empty_queue_high_odds(self):
        # Nothing ahead + brisk market → very likely to sell at least one unit.
        p = ind_core.sell_through_probability(units_ahead=0, daily_volume=50, qty=1)
        assert p["any"] > 0.99
        # A deep queue in a thin market → the batch almost certainly won't clear.
        q = ind_core.sell_through_probability(units_ahead=500, daily_volume=2, qty=10)
        assert q["all"] < 0.01
        assert q["any"] < 0.01

    def test_sell_through_monotonic_in_queue_depth(self):
        # More units queued ahead of you → lower odds of selling within the window.
        shallow = ind_core.sell_through_probability(5, 50, qty=1)["any"]
        deep = ind_core.sell_through_probability(80, 50, qty=1)["any"]
        assert shallow > deep

    def test_sell_through_rises_with_horizon(self):
        # The same order, listed for longer, has a higher chance of clearing —
        # the core of the multi-horizon read. Odds grow (never shrink) with time.
        thin = dict(units_ahead=0, daily_volume=3, qty=5)
        day = ind_core.sell_through_probability(**thin, horizon_days=1)["all"]
        week = ind_core.sell_through_probability(**thin, horizon_days=7)["all"]
        month = ind_core.sell_through_probability(**thin, horizon_days=30)["all"]
        assert day < week < month
        assert day < 0.5 and month > 0.95   # a frozen "24%" becomes ~100% given time

    def test_deep_queue_thin_market_is_not_always_100(self):
        # Regression: for a large demand mean the pmf seed (exp(-lam)/p^r)
        # underflows to 0, which used to leave every survival stuck at a bogus
        # 1.0 — a batch sitting behind 275k units at a ~2.5k/day rate wrongly read
        # "100% within a day". With ~110 days to clear, a day's odds must be ~0.
        p = ind_core.sell_through_probability(units_ahead=275_091,
                                              daily_volume=2518, qty=2700,
                                              horizon_days=1)
        assert p["all"] < 0.01
        # ...and the odds must agree with the ETA: ~110 days to clear means even a
        # month is hopeless, but the answer stays a real probability (never NaN).
        month = ind_core.sell_through_probability(275_091, 2518, 2700, 30)["all"]
        assert 0.0 <= month < 0.01
        # A brisk market with nothing ahead genuinely is ~100% — the fix must not
        # break the honest high case.
        easy = ind_core.sell_through_probability(0, 38619, 2700, 1)["all"]
        assert easy > 0.99

    def test_normal_approx_seam_is_continuous(self):
        # The exact-recurrence / normal-approximation switch (mean ~60) must not
        # jump: probabilities a hair below and above the seam stay within 1e-3.
        below = ind_core.sell_through_probability(0, 59, 1, 1)["all"]
        above = ind_core.sell_through_probability(0, 61, 1, 1)["all"]
        assert abs(below - above) < 1e-3

    def test_sell_through_graded_in_the_middle(self):
        # A batch that takes a few days to clear should read as a smooth ramp
        # across horizons (not a 0/100 cliff), and stay consistent with the ETA.
        mid = dict(units_ahead=5000, daily_volume=2518, qty=2700)  # eta ~3 days
        day = ind_core.sell_through_probability(**mid, horizon_days=1)["all"]
        three = ind_core.sell_through_probability(**mid, horizon_days=3)["all"]
        week = ind_core.sell_through_probability(**mid, horizon_days=7)["all"]
        assert day < 0.05 < three < 0.95 < week

    def test_overdispersion_softens_the_tails(self):
        # The negative-binomial model must be more forgiving than a bare Poisson at
        # a hard spot (batch >> daily rate): the fattened tail keeps a believable,
        # not-vanishing chance. Compare against DEMAND_DISPERSION turned off.
        args = dict(units_ahead=0, daily_volume=3, qty=8, horizon_days=1)
        nb = ind_core.sell_through_probability(**args)["all"]
        saved = ind_core.DEMAND_DISPERSION
        try:
            ind_core.DEMAND_DISPERSION = 1.0            # collapse to Poisson
            poisson = ind_core.sell_through_probability(**args)["all"]
        finally:
            ind_core.DEMAND_DISPERSION = saved
        assert nb > poisson


class TestPriceConditionedRate:
    @staticmethod
    def _series(days):
        # days: list of (volume, low, high). average unused when range present.
        return [{"volume": v, "low": lo, "high": hi, "average": (lo + hi) / 2}
                for (v, lo, hi) in days]

    def test_none_series_is_unknown(self):
        assert ind_core.price_conditioned_daily_rate(None, 100.0) is None
        assert ind_core.price_conditioned_daily_rate([], 100.0) is None

    def test_none_price_is_full_rate(self):
        # No price filter → total volume / the full window (sparse days read low).
        s = self._series([(300, 90, 110)])   # one trading day in a 30-day window
        assert ind_core.price_conditioned_daily_rate(s, None) == 300 / 30

    def test_price_below_range_credits_full_volume(self):
        # Listing at/below where it trades → you capture the whole day's demand.
        s = self._series([(300, 90, 110)] * 30)   # every day trades 90..110
        assert ind_core.price_conditioned_daily_rate(s, 80.0) == 300.0

    def test_price_above_range_credits_nothing(self):
        # Listing above everything the market paid → ~zero demand at that price.
        s = self._series([(300, 90, 110)] * 30)
        assert ind_core.price_conditioned_daily_rate(s, 200.0) == 0.0

    def test_price_in_range_is_linear_share(self):
        # Straddling the day's [low, high]: credit (high - price)/(high - low).
        s = self._series([(300, 90, 110)] * 30)   # price 105 → (110-105)/20 = .25
        assert ind_core.price_conditioned_daily_rate(s, 105.0) == pytest.approx(75.0)

    def test_rate_falls_as_price_rises(self):
        # The monotone knob the UI leans on: higher list price → lower demand rate.
        s = self._series([(300, 90, 110)] * 30)
        rates = [ind_core.price_conditioned_daily_rate(s, p)
                 for p in (80, 95, 105, 120)]
        assert rates == sorted(rates, reverse=True)


class TestSellThroughCurve:
    def test_curve_covers_eve_durations_and_grows(self):
        # One row per EVE order duration, each carrying its label + odds; the
        # full-batch probability is non-decreasing as the duration lengthens.
        curve = ind_core.sell_through_curve(units_ahead=0, daily_volume=3, qty=5)
        assert [r["days"] for r in curve] == [1, 3, 7, 14, 30, 90]
        assert all("label" in r and "all" in r and "any" in r for r in curve)
        alls = [r["all"] for r in curve]
        assert alls == sorted(alls)

    def test_curve_unknown_history_all_none(self):
        curve = ind_core.sell_through_curve(units_ahead=0, daily_volume=None, qty=2)
        assert all(r["all"] is None and r["any"] is None for r in curve)

    def test_requires_invention_flag(self):
        # drives the "Hide T2 / invention" filter in the web layer
        t1 = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, _params())[0]
        assert t1["requires_invention"] is False
        bp = _bp(invention=_inv())
        merged = _prices(); merged.update(_inv_prices())
        t2 = ind_core.evaluate_industry([bp], merged, _ADJUSTED, _params())[0]
        assert t2["requires_invention"] is True

    def test_row_carries_client_filter_fields(self):
        # Buildable only / Include unobtainable / Hide T2 are applied CLIENT-side
        # over the scan's full superset (do_ind_scan no longer drops rows), so
        # every row must carry the fields those filters test. Guard the contract:
        # if any of these ever stops being emitted, the web filters silently break.
        r = ind_core.evaluate_industry([_bp()], _prices(), _ADJUSTED, _params())[0]
        for k in ("buildable", "bp_available", "tech_level",
                  "requires_invention", "owned_bp_me_te"):
            assert k in r, f"row missing client-filter field {k!r}"


# ---------------------------------------------------------------------------
# training_time_hours / missing_skills
# ---------------------------------------------------------------------------

class TestTrainingTime:
    def test_zero_when_already_trained(self):
        assert ind_core.training_time_hours(3, 3, 5.0) == 0.0
        assert ind_core.training_time_hours(5, 3, 5.0) == 0.0

    def test_level_0_to_1_rank_1(self):
        # SP needed: 250 * 1 = 250, at 2250 SP/hr => ~0.111 hours
        hours = ind_core.training_time_hours(0, 1, 1.0)
        assert abs(hours - 250 / 2250) < 0.001

    def test_level_0_to_5_rank_1(self):
        hours = ind_core.training_time_hours(0, 5, 1.0)
        assert abs(hours - 256000 / 2250) < 0.1

    def test_level_3_to_5_rank_3(self):
        # SP: (256000 - 8000) * 3 = 744000, at 2250/hr = 330.67 hrs
        hours = ind_core.training_time_hours(3, 5, 3.0)
        expected = (256000 - 8000) * 3.0 / 2250
        assert abs(hours - expected) < 0.1

    def test_none_rank_returns_zero(self):
        assert ind_core.training_time_hours(0, 5, None) == 0.0

    def test_higher_rank_scales_linearly(self):
        h1 = ind_core.training_time_hours(0, 3, 1.0)
        h5 = ind_core.training_time_hours(0, 3, 5.0)
        assert abs(h5 / h1 - 5.0) < 0.01


class TestMissingSkills:
    def _conn(self, tmp_path):
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        return ind_core.connect_sde(tmp_path)

    def test_no_missing_when_all_trained(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 1)])
            result = ind_core.missing_skills(bp, {3380: 5}, conn)
            assert result == []
        finally:
            conn.close()

    def test_missing_when_undertrained(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 3)])
            result = ind_core.missing_skills(bp, {3380: 1}, conn)
            assert len(result) == 1
            assert result[0]["skill_id"] == 3380
            assert result[0]["required"] == 3
            assert result[0]["current"] == 1
            assert result[0]["train_hours"] > 0
        finally:
            conn.close()

    def test_missing_uses_default_level(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 3)])
            # default_level=5 means character "has" level 5 in everything
            result = ind_core.missing_skills(bp, {}, conn, default_level=5)
            assert result == []
            # default_level=0 means character has nothing
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            assert len(result) == 1
            assert result[0]["current"] == 0
        finally:
            conn.close()

    def test_missing_includes_skill_name(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            # skill 3380 is not in our tiny invTypes fixture as a type,
            # so it falls back to "Skill 3380" — but let's check the logic
            bp = _bp(skills=[(34, 3)])  # type 34 = "Tritanium" in our fixture
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            assert result[0]["name"] == "Tritanium"
        finally:
            conn.close()

    def test_missing_with_no_skills_table(self, tmp_path):
        """Gracefully handles old SDE without skill_ranks table."""
        conn = self._conn(tmp_path)
        try:
            conn.execute("DROP TABLE IF EXISTS skill_ranks")
            conn.execute("DROP TABLE IF EXISTS skill_prereqs")
            bp = _bp(skills=[(3380, 3)])
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            assert len(result) == 1
            # train_hours should be 0 because rank is unknown
            assert result[0]["train_hours"] == 0.0
        finally:
            conn.close()

    def test_walks_prerequisite_chain(self, tmp_path):
        """Skill 11442 requires 3380 at L3. If character has neither,
        missing_skills should return both, prerequisites first."""
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(11442, 1)])
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            ids = [e["skill_id"] for e in result]
            # 3380 is a prereq of 11442, so should appear before 11442
            assert 3380 in ids
            assert 11442 in ids
            assert ids.index(3380) < ids.index(11442)
            # 3380 needed at level 3 (as prereq of 11442)
            entry_3380 = next(e for e in result if e["skill_id"] == 3380)
            assert entry_3380["required"] == 3
        finally:
            conn.close()

    def test_deep_prerequisite_chain(self, tmp_path):
        """Skill 25000 requires 11442 L2, which requires 3380 L3.
        All three should appear if character has none."""
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(25000, 1)])
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            ids = [e["skill_id"] for e in result]
            assert 3380 in ids
            assert 11442 in ids
            assert 25000 in ids
            # Order: 3380 before 11442 before 25000
            assert ids.index(3380) < ids.index(11442) < ids.index(25000)
        finally:
            conn.close()

    def test_prereq_already_trained_not_shown(self, tmp_path):
        """If character already has the prereq trained, it's not listed."""
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(11442, 1)])
            # Character has 3380 at L5 (prereq met), but not 11442
            result = ind_core.missing_skills(bp, {3380: 5}, conn, default_level=0)
            ids = [e["skill_id"] for e in result]
            assert 3380 not in ids
            assert 11442 in ids
        finally:
            conn.close()

    def test_no_duplicate_skills_in_result(self, tmp_path):
        """If the same skill appears as both direct and prereq, show once at max level."""
        conn = self._conn(tmp_path)
        try:
            # bp requires 3380 at L1 directly AND 11442 L1 (whose prereq is 3380 L3)
            bp = _bp(skills=[(3380, 1), (11442, 1)])
            result = ind_core.missing_skills(bp, {}, conn, default_level=0)
            ids = [e["skill_id"] for e in result]
            assert ids.count(3380) == 1
            # Required level should be 3 (from prerequisite, higher than direct L1)
            entry = next(e for e in result if e["skill_id"] == 3380)
            assert entry["required"] == 3
        finally:
            conn.close()


class TestBulkTrainingTime:
    def _conn(self, tmp_path):
        ind_core.build_sde_db(tmp_path, session=_fake_session())
        return ind_core.connect_sde(tmp_path)

    def test_returns_hours_for_unbuildable(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 3)])
            result = ind_core.bulk_training_time([bp], {3380: 1}, conn)
            assert bp["blueprint_id"] in result
            assert result[bp["blueprint_id"]] > 0
        finally:
            conn.close()

    def test_empty_for_fully_trained(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 3)])
            result = ind_core.bulk_training_time([bp], {3380: 5}, conn)
            assert result == {}
        finally:
            conn.close()

    def test_respects_default_level(self, tmp_path):
        conn = self._conn(tmp_path)
        try:
            bp = _bp(skills=[(3380, 3)])
            result = ind_core.bulk_training_time([bp], {}, conn, default_level=5)
            assert result == {}
        finally:
            conn.close()

    def test_includes_prerequisite_training(self, tmp_path):
        """Training time includes prereqs (e.g. 11442 requires 3380 L3)."""
        conn = self._conn(tmp_path)
        try:
            # 11442 requires 3380 at L3 as prereq; character has neither
            bp = _bp(skills=[(11442, 1)])
            result = ind_core.bulk_training_time([bp], {}, conn, default_level=0)
            # Should include time for BOTH 3380 (0→3) and 11442 (0→1)
            direct_only = ind_core.training_time_hours(0, 1, None)
            assert result[bp["blueprint_id"]] > direct_only
        finally:
            conn.close()
