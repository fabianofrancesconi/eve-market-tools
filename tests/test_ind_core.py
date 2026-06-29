"""
Tests for the Industry data layer (`ind_core`).

Milestone 1 — SDE ingestion: download Fuzzwork's per-table CSV dumps and build a
local `sde_industry.sqlite`, then query it. Network is mocked: a fake session
serves small in-memory CSV fixtures (with the UTF-8 BOM the real dumps carry) so
the import + query path is exercised end to end without hitting Fuzzwork.
"""
import time
from unittest.mock import MagicMock

import ind_core

BOM = "﻿"

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
