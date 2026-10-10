"""Industry → Statistics: sales over time + margin by blueprint.

The stats view must read the SAME money the Tracker books — live builds via
reconcile's wallet FIFO, dead (archived/stopped) builds pinned to their frozen
snapshot — so its all-time totals always equal the Tracker's realized profit.
These tests pin that equality, the frozen-scaling rule, the time bucketing and
the per-blueprint margin maths, plus the frontend wiring."""
import datetime
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")
import ind_track

DAY = 86400
T0 = int(datetime.datetime(2026, 8, 3, tzinfo=datetime.timezone.utc).timestamp())  # a Monday


def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Tester"}
    a.active_char_id = 1
    return a


def _bind(monkeypatch, tmp_path, acct):
    monkeypatch.setattr(lp_web, "IND_BUILDS_PATH", tmp_path / "builds.json")
    monkeypatch.setattr(lp_web, "IND_SELL_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(lp_web, "IND_OBSERVED_LEDGER_PATH", tmp_path / "observed.json")
    monkeypatch.setattr(lp_web, "IND_LISTED_UNITS_PATH", tmp_path / "listed.json")
    monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", tmp_path / "ev.json")
    monkeypatch.setattr(lp_web, "IND_TRACK_MIGRATED_PATH", tmp_path / "migrated.json")
    monkeypatch.setattr(lp_web, "IND_ARCHIVED_FROZEN_PATH", tmp_path / "frozen.json")
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)


def _build(bid, pid, name, runs, done_at, cost=100.0, tax=0.0, **extra):
    b = {"id": bid, "product_type_id": pid, "product_name": name, "runs": runs,
         "created_at": done_at - DAY, "done_at": done_at,
         "snapshot": {"product": {"type_id": pid, "name": name, "quantity": 1},
                      "total_cost": cost, "sales_tax": tax, "required_items": []}}
    b.update(extra)
    return b


def _fill(tid, ts, units, price):
    return {"transaction_id": tid, "ts": ts, "units": units, "price": price}


def _store(acct, builds, ledger):
    lp_web._save_tracked_builds(acct, builds)
    lp_web._save_sell_ledger(acct, ledger)


# ── ind_track primitives ─────────────────────────────────────────────────────

def test_allocate_fifo_events_sum_to_per_lot_totals():
    lots = [{"id": "a", "units": 5, "cost_per_unit": 10.0, "sales_tax": 0.1, "done_at": 0},
            {"id": "b", "units": 5, "cost_per_unit": 20.0, "sales_tax": 0.1, "done_at": 0}]
    fills = [_fill(1, 10, 3, 50.0), _fill(2, 20, 4, 60.0)]
    ev = []
    per_lot, _ = ind_track.allocate_fifo(lots, fills, events=ev)
    # The second fill spills across both lots → three slices.
    assert [(e["lot_id"], e["ts"], e["units"]) for e in ev] == \
        [("a", 10, 3), ("a", 20, 2), ("b", 20, 2)]
    for lid in ("a", "b"):
        mine = [e for e in ev if e["lot_id"] == lid]
        assert abs(sum(e["net"] for e in mine) - per_lot[lid]["net"]) < 1e-6
        assert abs(sum(e["cost"] for e in mine) - per_lot[lid]["cost"]) < 1e-6
    # Recording events never changes the result.
    assert ind_track.allocate_fifo(lots, fills) == (per_lot, _)


def test_scale_to_frozen_pins_amounts_and_keeps_timing():
    ev = [{"ts": 100, "units": 1, "net": 10.0, "cost": 5.0},
          {"ts": 200, "units": 3, "net": 30.0, "cost": 15.0}]
    out = ind_track.scale_to_frozen(ev, {"units": 8, "net": 80.0, "cost_of_sold": 20.0}, 999)
    assert [e["ts"] for e in out] == [100, 200]
    assert sum(e["units"] for e in out) == 8
    assert abs(sum(e["net"] for e in out) - 80.0) < 1e-9
    assert abs(sum(e["cost"] for e in out) - 20.0) < 1e-9


def test_scale_to_frozen_falls_back_to_one_slice():
    out = ind_track.scale_to_frozen([], {"units": 4, "net": 40.0, "cost_of_sold": 10.0}, 555)
    assert out == [{"ts": 555, "units": 4, "net": 40.0, "cost": 10.0}]
    assert ind_track.scale_to_frozen([], {"units": 0, "net": 0.0}, 1) == []


def test_sales_stats_margin_buckets_and_distinct_blueprints():
    ev = [
        {"build_id": "a", "type_id": 1, "name": "A", "ts": T0 + 1 * DAY, "done_at": T0,
         "units": 2, "net": 300.0, "cost": 200.0, "writeoff": 0.0},
        {"build_id": "b", "type_id": 2, "name": "B", "ts": T0 + 2 * DAY, "done_at": T0,
         "units": 1, "net": 50.0, "cost": 100.0, "writeoff": 0.0},
        # Two weeks later only A sells again; the week between is empty.
        {"build_id": "a", "type_id": 1, "name": "A", "ts": T0 + 15 * DAY, "done_at": T0,
         "units": 1, "net": 150.0, "cost": 100.0, "writeoff": 0.0},
    ]
    st = ind_track.sales_stats(ev)
    t = st["totals"]
    assert t["blueprints"] == 2 and t["units"] == 4
    assert t["profit"] == 100.0 and t["margin"] == 25.0       # 100 / 400
    a = next(p for p in st["products"] if p["type_id"] == 1)
    assert a["profit"] == 150.0 and a["margin"] == 50.0
    assert a["batches"] == 1 and a["profit_per_unit"] == 50.0
    assert abs(a["avg_days_to_sell"] - (2 * 1 + 1 * 15) / 3) < 1e-9
    assert st["products"][0]["type_id"] == 1                  # sorted by profit
    weeks = st["timeline"]["week"]
    assert [w["blueprints"] for w in weeks] == [2, 0, 1]      # gap-filled
    assert weeks[0]["start"] == T0
    assert [d["start"] for d in st["timeline"]["day"]][:2] == [T0 + DAY, T0 + 2 * DAY]
    assert len(st["timeline"]["month"]) == 1
    # The range filter drops older slices.
    late = ind_track.sales_stats(ev, since=T0 + 10 * DAY)
    assert late["totals"]["blueprints"] == 1 and late["totals"]["units"] == 1


def test_writeoff_counts_against_profit_and_margin():
    ev = [{"build_id": "a", "type_id": 1, "name": "A", "ts": T0, "done_at": T0,
           "units": 1, "net": 200.0, "cost": 100.0, "writeoff": 0.0},
          {"build_id": "a", "type_id": 1, "name": "A", "ts": T0, "done_at": T0,
           "units": 0, "net": None, "cost": None, "writeoff": 100.0}]
    t = ind_track.sales_stats(ev)["totals"]
    assert t["profit"] == 0.0 and t["margin"] == 0.0 and t["units"] == 1


def test_month_bucket_rolls_over_year():
    dec = int(datetime.datetime(2026, 12, 20, tzinfo=datetime.timezone.utc).timestamp())
    ev = [{"build_id": "a", "type_id": 1, "name": "A", "ts": dec, "units": 1,
           "net": 1.0, "cost": 1.0},
          {"build_id": "a", "type_id": 1, "name": "A", "ts": dec + 40 * DAY,
           "units": 1, "net": 1.0, "cost": 1.0}]
    months = ind_track.sales_stats(ev)["timeline"]["month"]
    starts = [datetime.datetime.fromtimestamp(m["start"], datetime.timezone.utc).strftime("%Y-%m")
              for m in months]
    assert starts == ["2026-12", "2027-01"]


# ── Endpoint: equals the Tracker ─────────────────────────────────────────────

def test_stats_totals_equal_tracker_realized(monkeypatch, tmp_path):
    acct = _acct()
    _bind(monkeypatch, tmp_path, acct)
    builds = [
        # Archived, fully sold — frozen snapshot is the authority for its money.
        _build("old", 587, "Rifter", 4, T0, cost=100.0, archived=True,
               frozen={"units": 4, "net": 600.0, "cost_of_sold": 400.0,
                       "writeoff": 0.0, "profit": 200.0}),
        # Live, partially sold.
        _build("new", 587, "Rifter", 5, T0 + 3 * DAY, cost=120.0, tax=0.05),
        # Live, abandoned remainder (write-off).
        _build("ab", 2929, "Missile", 10, T0, cost=10.0, abandoned=True, writeoff_units=6),
    ]
    ledger = {
        "587": [_fill(1, T0 + DAY, 4, 150.0),            # sold the archived batch
                _fill(2, T0 + 4 * DAY, 2, 200.0)],       # live batch
        "2929": [_fill(3, T0 + DAY, 4, 25.0)],
        "34": [_fill(4, T0, 1000, 5.0)],                 # untracked sale — ignored
    }
    _store(acct, builds, ledger)
    summary = lp_web.do_ind_summary({})
    stats = lp_web.do_ind_stats({})
    assert abs(stats["totals"]["profit"] - summary["totals"]["realized_profit"]) < 1e-6
    assert abs(stats["totals"]["revenue"] - summary["totals"]["realized_net"]) < 1e-6
    assert stats["totals"]["units"] == sum(b["realized"]["units"] for b in summary["builds"])
    assert stats["totals"]["blueprints"] == 2
    rifter = next(p for p in stats["products"] if p["type_id"] == 587)
    assert rifter["batches"] == 2
    # Archived batch keeps its frozen money: 200 profit + live 2×(190−120)=140.
    assert abs(rifter["profit"] - 340.0) < 1e-6
    missile = next(p for p in stats["products"] if p["type_id"] == 2929)
    assert abs(missile["profit"] - (4 * 25.0 - 4 * 10.0 - 6 * 10.0)) < 1e-6
    assert stats["range"] == "all"


def test_stats_range_filter(monkeypatch, tmp_path):
    acct = _acct()
    _bind(monkeypatch, tmp_path, acct)
    import time
    now = time.time()
    _store(acct, [_build("x", 587, "Rifter", 4, now - 100 * DAY)],
           {"587": [_fill(1, now - 90 * DAY, 2, 150.0), _fill(2, now - 2 * DAY, 1, 150.0)]})
    assert lp_web.do_ind_stats({"range": ["30"]})["totals"]["units"] == 1
    assert lp_web.do_ind_stats({"range": ["all"]})["totals"]["units"] == 3
    assert lp_web.do_ind_stats({"range": ["bogus"]})["range"] == "all"


def test_stats_requires_account(monkeypatch, tmp_path):
    _bind(monkeypatch, tmp_path, None)
    assert "error" in lp_web.do_ind_stats({})


# ── Frontend wiring ──────────────────────────────────────────────────────────

def test_stats_mode_wired():
    src = lp_web.FRONTEND_SOURCE
    assert "/api/ind/stats" in lp_web._GET_ROUTES
    assert 'class="ind-mode-btn" data-mode="stats"' in src
    assert 'id="ind-stats-view"' in src and 'id="stats-body"' in src
    assert "/static/js/stats.js" in src
    assert "function loadStats" in src and "function renderStats" in src
    # The mode survives a reload (server pref) and loads its data on entry.
    assert 's.ind_mode==="stats"' in src
    assert 'mode==="stats"' in src


def test_stats_js_is_in_docker_image():
    # static/ is copied wholesale, so a new JS file needs no Dockerfile edit.
    df = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "Dockerfile")).read()
    assert "COPY static/ ./static/" in df


def test_stats_and_research_view_settings_persist():
    src = lp_web.FRONTEND_SOURCE
    # Saved on change…
    assert 'setPref("ind_stats_view"' in src
    assert 'setPref("ind_research_view"' in src
    # …and restored from server prefs on load.
    assert "statsApplyStored(s.ind_stats_view)" in src
    assert "researchApplyStored(s.ind_research_view)" in src
    for k in ('"range"', '"period"', '"metric"', '"marginView"'):
        assert k in src
