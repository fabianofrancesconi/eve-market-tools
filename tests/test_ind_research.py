"""Industry → Research: the ME/TE job ledger, level inference and the benefit
maths behind the Research view, plus its wiring."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")
import ind_research as R

H = 3600


def _ts(s):
    return {"a": 1000, "b": 2000, "c": 3000, "d": 4000, "e": 5000}.get(s) if s else None


def _job(jid, act, runs, status, start="a", end="b", item=77, tid=900, cost=1000.0):
    return {"job_id": jid, "activity_id": act, "blueprint_id": item,
            "blueprint_type_id": tid, "runs": runs, "status": status,
            "start_date": start, "end_date": end, "cost": cost, "facility_id": 1}


def _bp(item, tid, me, te, qty=-1):
    return {"item_id": item, "type_id": tid, "material_efficiency": me,
            "time_efficiency": te, "quantity": qty, "runs": -1}


# ── ledgers ──────────────────────────────────────────────────────────────────

def test_merge_bp_levels_keeps_items_missing_from_latest_pull():
    store = {}
    R.merge_bp_levels(store, 1, [_bp(77, 900, 4, 8), _bp(78, 901, 0, 0, qty=-2)], now=10)
    R.merge_bp_levels(store, 1, [_bp(78, 901, 0, 0, qty=-2)], now=20)   # 77 went into a lab
    items = store["1"]["items"]
    assert items["77"]["me"] == 4 and items["77"]["seen"] == 10        # last-known kept
    owned = R.owned_items(store)
    assert [o["item_id"] for o in owned] == [78]                        # only current
    assert owned[0]["bpo"] is False


def test_merge_jobs_stamps_from_level_once_and_ignores_other_activities():
    levels = {"1": {"refreshed_at": 10, "items": {
        "77": {"type_id": 900, "me": 4, "te": 8, "bpo": True, "seen": 10}}}}
    store, changed = R.merge_jobs({}, [_job(5, R.ACT_ME, 3, "active"),
                                       _job(6, 1, 10, "active")],          # manufacturing
                                  1, "Tester", {900: "Rifter Blueprint"}, {1: "Lab"},
                                  levels, _ts)
    assert changed and list(store) == ["5"]
    rec = store["5"]
    assert rec["from_level"] == 4 and rec["blueprint_name"] == "Rifter Blueprint"
    assert rec["start"] == 1000 and rec["location"] == "Lab"
    # Delivered later, and the blueprint now reads ME7: from_level is NOT restamped.
    levels["1"]["items"]["77"]["me"] = 7
    store, changed = R.merge_jobs(store, [_job(5, R.ACT_ME, 3, "delivered")], 1, "Tester",
                                  {}, {}, levels, _ts)
    assert changed and store["5"]["from_level"] == 4 and store["5"]["status"] == "delivered"
    assert store["5"]["blueprint_name"] == "Rifter Blueprint"     # name survives
    _, changed = R.merge_jobs(store, [_job(5, R.ACT_ME, 3, "delivered")], 1, "Tester",
                              {}, {}, levels, _ts)
    assert not changed


# ── level inference ──────────────────────────────────────────────────────────

def _levels(me, te, item=77, tid=900):
    return {"1": {"refreshed_at": 10, "items": {
        str(item): {"type_id": tid, "me": me, "te": te, "bpo": True, "seen": 10}}}}


def test_infer_chains_back_from_current_level():
    jobs = [{"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 2, "status": "delivered", "end": 100},
            {"job_id": 2, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 3, "status": "delivered", "end": 200},
            {"job_id": 3, "activity_id": R.ACT_TE, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 4, "status": "delivered", "end": 150}]
    lv = R.infer_levels(jobs, _levels(me=9, te=10))
    assert lv[2] == (6, 9) and lv[1] == (4, 6)
    assert lv[3] == (2, 10)                              # TE: 2 per run


def test_infer_active_job_anchors_and_stamp_wins():
    jobs = [{"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 2, "status": "delivered", "end": 100},
            {"job_id": 2, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 3, "status": "active", "end": 300, "from_level": 5}]
    lv = R.infer_levels(jobs, _levels(me=0, te=0))     # stale level ignored: stamp wins
    assert lv[2] == (5, 8) and lv[1] == (3, 5)


def test_infer_unknowable_is_none():
    jobs = [{"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 5, "status": "delivered", "end": 100}]
    assert R.infer_levels(jobs, {})[1] == (None, None)
    assert R.infer_levels(jobs, _levels(me=2, te=0))[1] == (None, None)   # would be −3


# ── benefit maths ────────────────────────────────────────────────────────────

def test_me_cost_per_level():
    per, me0, priced = R.me_cost_per_level([(34, 1000), (35, 200)], {34: 5.0, 35: 10.0})
    assert me0 == 7000.0 and per == 70.0 and priced
    assert R.me_cost_per_level([(34, 1000), (99, 5)], {34: 5.0})[2] is False


def test_build_me_savings_uses_frozen_prices_and_rounding():
    snap = {"me_used": 10, "required_items": [
        {"base_qty": 100, "unit_price": 2.0}, {"base_qty": 1, "unit_price": 1000.0}]}
    # 10 runs: 1000 → 900 of the first (×2 = 200 ISK); the 1-unit item can't drop
    # below one per run, so it saves nothing.
    assert R.build_me_savings(snap, 10) == 200.0
    assert R.build_me_savings({"me_used": 10, "required_items": [{"eff_qty": 5}]}, 10) == 0.0


def test_research_report_rows_totals_and_payback():
    now = 10_000
    jobs = [
        {"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
         "blueprint_name": "Rifter Blueprint", "character_name": "Tester", "runs": 3,
         "status": "delivered", "start": 1000, "end": 1000 + 5 * H, "cost": 7000.0},
        {"job_id": 2, "activity_id": R.ACT_TE, "blueprint_id": 77, "blueprint_type_id": 900,
         "blueprint_name": "Rifter Blueprint", "character_name": "Tester", "runs": 2,
         "status": "active", "start": now - H, "end": now + H, "cost": 500.0, "from_level": 6},
        {"job_id": 3, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
         "runs": 1, "status": "cancelled", "start": 1, "end": 2, "cost": 99.0},
    ]
    levels = _levels(me=3, te=6)
    info = {900: {"name": "Rifter Blueprint", "product_name": "Rifter",
                  "isk_per_me_level": 70.0, "me0_cost": 7000.0, "base_time": 6000,
                  "priced": True}}
    builds = [{"runs": 10, "snapshot": {"blueprint_id": 900, "me_used": 3,
               "required_items": [{"base_qty": 1000, "unit_price": 5.0}]}}]
    rep = R.research_report(jobs, levels, info, builds, now)
    t = rep["totals"]
    assert t["in_progress"] == 1 and t["completed"] == 1
    assert t["me_levels"] == 3 and t["te_levels"] == 0
    assert t["fees"] == 7500.0                           # cancelled job excluded
    assert t["lab_seconds"] == 5 * H
    done = rep["history"][0]
    assert (done["from"], done["to"]) == (0, 3)
    assert done["isk_per_run"] == 210.0
    assert abs(done["payback_runs"] - 7000.0 / 210.0) < 1e-9
    lab = rep["active"][0]
    assert (lab["from"], lab["to"]) == (6, 10) and lab["activity"] == "TE"
    assert lab["seconds_per_run"] == 6000 * 4 / 100.0
    assert abs(lab["progress"] - 0.5) < 1e-9
    bp = rep["blueprints"][0]
    assert bp["me"] == 3 and bp["te"] == 6 and bp["in_lab"]
    assert bp["isk_per_run_left"] == 70.0 * 7
    assert bp["realized_savings"] == (10000 - 9700) * 5.0
    assert t["realized_savings"] == bp["realized_savings"]


def test_job_past_end_reads_ready():
    jobs = [{"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 1, "status": "active", "start": 0, "end": 50, "from_level": 0}]
    row = R.research_report(jobs, {}, {}, [], now=100)["active"][0]
    assert row["status"] == "ready" and row["progress"] == 1.0


# ── server wiring ────────────────────────────────────────────────────────────

def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Tester"}
    a.active_char_id = 1
    return a


def test_sweep_hooks_persist_and_endpoint_reports(monkeypatch, tmp_path):
    acct = _acct()
    monkeypatch.setattr(lp_web, "IND_RESEARCH_JOBS_PATH", tmp_path / "rj.json")
    monkeypatch.setattr(lp_web, "IND_BP_LEVELS_PATH", tmp_path / "lv.json")
    monkeypatch.setattr(lp_web, "IND_BUILDS_PATH", tmp_path / "b.json")
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)
    monkeypatch.setattr(lp_web, "_research_bp_info", lambda tids: {
        900: {"name": "Rifter Blueprint", "product_name": "Rifter",
              "isk_per_me_level": 10.0, "me0_cost": 1000.0, "base_time": 100, "priced": True}})
    lp_web._record_bp_levels(acct, 1, [_bp(77, 900, 2, 4)])
    lp_web._record_research_jobs(acct, 1, "Tester", [
        {"job_id": 9, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
         "runs": 2, "status": "active", "start_date": "2026-10-01T00:00:00Z",
         "end_date": "2099-01-01T00:00:00Z", "cost": 40.0, "facility_id": 5}],
        {900: "Rifter Blueprint"}, {5: "My Lab"})
    rep = lp_web.do_ind_research({})
    assert rep["totals"]["in_progress"] == 1
    job = rep["active"][0]
    assert (job["from"], job["to"]) == (2, 4) and job["location"] == "My Lab"
    assert job["isk_per_run"] == 20.0 and job["payback_runs"] == 2.0
    assert rep["levels_known"] is True


def test_research_requires_account(monkeypatch):
    monkeypatch.setattr(lp_web, "current_account", lambda: None)
    assert "error" in lp_web.do_ind_research({})


def test_research_mode_wired():
    src = lp_web.FRONTEND_SOURCE
    assert "/api/ind/research" in lp_web._GET_ROUTES
    assert 'class="ind-mode-btn" data-mode="research"' in src
    assert 'id="ind-research-view"' in src and 'id="research-body"' in src
    assert "/static/js/research.js" in src
    assert "function loadResearch" in src and "function renderResearch" in src
    assert 's.ind_mode==="research"' in src


def test_in_lab_blueprint_keeps_last_known_other_level():
    # The blueprint is in an ME job (gone from /blueprints/); its TE comes from
    # the last-known snapshot instead of reading "?".
    levels = {"1": {"refreshed_at": 20, "items": {
        "77": {"type_id": 900, "me": 2, "te": 14, "bpo": True, "seen": 10}}}}
    jobs = [{"job_id": 1, "activity_id": R.ACT_ME, "blueprint_id": 77, "blueprint_type_id": 900,
             "runs": 2, "status": "active", "start": 0, "end": 10**12, "from_level": 2}]
    bp = R.research_report(jobs, levels, {}, [], now=100)["blueprints"][0]
    assert bp["me"] == 2 and bp["te"] == 14 and bp["in_lab"]
