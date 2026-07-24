"""Tests for the Industry 'tracked builds' feature (server-side persistence
and the save/list/delete/link API handlers)."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def _acct():
    """A minimal legacy Account (file-backed storage path, pg_store disabled)."""
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Tester"}
    a.active_char_id = 1
    return a


def _bind(monkeypatch, tmp_path, acct):
    """Point the file-backed store at tmp_path and make current_account() return
    the given account (or None) without a real HTTP request."""
    monkeypatch.setattr(lp_web, "IND_BUILDS_PATH", tmp_path / "builds.json")
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)


def _snapshot(**over):
    snap = {
        "blueprint_id": 999,
        "product": {"type_id": 587, "name": "Rifter", "quantity": 1},
        "total_cost": 100.0,
        "profit_patient": 40.0,
        "profit_instant": 25.0,
        "build_time": 3600,
        "material_cost": 90.0,
        "job_cost": 10.0,
        "ask": 150.0, "bid": 120.0,
        "required_items": [
            {"name": "Tritanium", "eff_qty": 100, "unit_price": 5.0,
             "line_cost": 500.0, "volume_each": 0.01},
        ],
    }
    snap.update(over)
    return snap


class TestStorage:
    def test_empty_when_no_account(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, None)
        assert lp_web.do_ind_builds_list({}) == {"builds": []}

    def test_save_then_list(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        import json
        res = lp_web.do_ind_builds_save({
            "runs": ["10"], "snapshot": [json.dumps(_snapshot())]})
        assert res["ok"] is True
        build = res["build"]
        assert build["runs"] == 10
        assert build["product_name"] == "Rifter"
        assert build["blueprint_id"] == 999
        assert "id" in build and "created_at" in build
        # It round-trips through the store.
        listed = lp_web.do_ind_builds_list({})["builds"]
        assert len(listed) == 1
        assert listed[0]["id"] == build["id"]
        # The frozen snapshot is preserved verbatim.
        assert listed[0]["snapshot"]["total_cost"] == 100.0

    def test_save_rejects_missing_snapshot(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert "error" in lp_web.do_ind_builds_save({"runs": ["1"]})

    def test_save_rejects_bad_snapshot_json(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert "error" in lp_web.do_ind_builds_save(
            {"snapshot": ["{not valid json"]})

    def test_runs_defaults_and_floors_to_one(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        r = lp_web.do_ind_builds_save({"snapshot": [json.dumps(_snapshot())]})
        assert r["build"]["runs"] == 1
        r2 = lp_web.do_ind_builds_save(
            {"runs": ["0"], "snapshot": [json.dumps(_snapshot())]})
        assert r2["build"]["runs"] == 1

    def test_newest_first(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        first = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot(blueprint_id=1))]})["build"]
        second = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot(blueprint_id=2))]})["build"]
        listed = lp_web.do_ind_builds_list({})["builds"]
        assert [b["id"] for b in listed] == [second["id"], first["id"]]

    def test_delete_removes_one(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b1 = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot(blueprint_id=1))]})["build"]
        lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot(blueprint_id=2))]})
        lp_web.do_ind_builds_delete({"id": [b1["id"]]})
        remaining = lp_web.do_ind_builds_list({})["builds"]
        assert len(remaining) == 1
        assert remaining[0]["id"] != b1["id"]

    def test_delete_missing_id_is_noop(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert "error" in lp_web.do_ind_builds_delete({})

    def test_max_builds_cap(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        monkeypatch.setattr(lp_web, "_MAX_TRACKED_BUILDS", 3)
        for i in range(5):
            lp_web.do_ind_builds_save(
                {"runs": ["1"], "snapshot": [json.dumps(_snapshot(blueprint_id=i))]})
        assert len(lp_web.do_ind_builds_list({})["builds"]) == 3


class TestLink:
    def test_link_sets_job_fields(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot())]})["build"]
        res = lp_web.do_ind_builds_link({
            "id": [b["id"]], "job_id": ["12345"],
            "job_end": ["2026-07-20T10:00:00Z"], "char_name": ["Tester"],
            "job_location": ["Jita IV - Moon 4 - Caldari Navy Assembly Plant"]})
        assert res["ok"] is True
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["job_id"] == "12345"
        assert stored["job_end"] == "2026-07-20T10:00:00Z"
        assert stored["char_name"] == "Tester"
        assert stored["job_location"] == "Jita IV - Moon 4 - Caldari Navy Assembly Plant"

    def test_link_done_at(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot())]})["build"]
        lp_web.do_ind_builds_link({"id": [b["id"]], "done_at": ["1700000000.0"]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["done_at"] == 1700000000.0

    def test_link_null_clears_field(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot())]})["build"]
        lp_web.do_ind_builds_link({"id": [b["id"]], "job_id": ["55"]})
        lp_web.do_ind_builds_link({"id": [b["id"]], "job_id": ["null"]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["job_id"] is None

    def test_link_unknown_id(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        res = lp_web.do_ind_builds_link({"id": ["nope"], "job_id": ["1"]})
        assert res["ok"] is False

    def test_link_rebases_runs_on_close_match(self, monkeypatch, tmp_path):
        """Accepting a close-match job (tracked 30×, started 32×) re-bases the
        build's run count onto the real number of runs started."""
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["30"], "snapshot": [json.dumps(_snapshot())]})["build"]
        lp_web.do_ind_builds_link({
            "id": [b["id"]], "job_id": ["999"], "runs": ["32"]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["job_id"] == "999"
        assert stored["runs"] == 32

    def test_link_runs_floors_to_one_and_ignores_bad(self, monkeypatch, tmp_path):
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["30"], "snapshot": [json.dumps(_snapshot())]})["build"]
        lp_web.do_ind_builds_link({"id": [b["id"]], "runs": ["0"]})
        assert lp_web.do_ind_builds_list({})["builds"][0]["runs"] == 1
        lp_web.do_ind_builds_link({"id": [b["id"]], "runs": ["oops"]})
        # A bad value leaves the prior run count untouched.
        assert lp_web.do_ind_builds_list({})["builds"][0]["runs"] == 1
        # A blank/null value is a no-op too.
        lp_web.do_ind_builds_link({"id": [b["id"]], "runs": ["null"]})
        assert lp_web.do_ind_builds_list({})["builds"][0]["runs"] == 1

    def test_link_clears_done_at(self, monkeypatch, tmp_path):
        """The self-heal path (a build wrongly marked done whose job is still
        running) patches done_at back to null — verify the server clears it."""
        import json
        _bind(monkeypatch, tmp_path, _acct())
        b = lp_web.do_ind_builds_save(
            {"runs": ["1"], "snapshot": [json.dumps(_snapshot())]})["build"]
        lp_web.do_ind_builds_link({"id": [b["id"]], "done_at": ["1700000000.0"]})
        assert lp_web.do_ind_builds_list({})["builds"][0]["done_at"] == 1700000000.0
        lp_web.do_ind_builds_link({"id": [b["id"]], "done_at": ["null"]})
        assert lp_web.do_ind_builds_list({})["builds"][0]["done_at"] is None
