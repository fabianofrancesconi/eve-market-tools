"""Tests for tracked-build sell tracking under the pooled, wallet-driven model.

Money comes only from wallet sell transactions (deduped by transaction_id into a
per-product ledger); sold units are FIFO-allocated across the product's built
lots; stages and realized profit are recomputed on read. This exercises the
server integration (ledger reconcile → summary), the cost/stage helpers, the
one-time migration from the legacy per-build sell blobs, and the abandon route.
The pure allocation math lives in tests/test_ind_track.py.
"""
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def _acct():
    a = lp_web.Account(1)
    a.characters[1] = {"character_id": 1, "name": "Tester"}
    a.active_char_id = 1
    return a


def _bind(monkeypatch, tmp_path, acct):
    monkeypatch.setattr(lp_web, "IND_BUILDS_PATH", tmp_path / "builds.json")
    monkeypatch.setattr(lp_web, "IND_SELL_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(lp_web, "IND_LISTED_UNITS_PATH", tmp_path / "listed.json")
    monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", tmp_path / "ev.json")
    monkeypatch.setattr(lp_web, "IND_TRACK_MIGRATED_PATH", tmp_path / "migrated.json")
    monkeypatch.setattr(lp_web, "current_account", lambda: acct)


def _snapshot(**over):
    snap = {
        "blueprint_id": 999,
        "product": {"type_id": 587, "name": "Rifter", "quantity": 1},
        "total_cost": 100.0,
        "material_cost": 90.0,
        "job_cost": 10.0,
        "ask": 150.0, "bid": 120.0,
        "sales_tax": 0.0, "broker_fee": 0.0,
        "me_used": 0,
        "required_items": [
            {"name": "Tritanium", "base_qty": 100, "eff_qty": 100,
             "unit_price": 0.9, "volume_each": 0.01},
        ],
    }
    snap.update(over)
    return snap


_ID_SEQ = [0]


def _save_build(runs=10, bid=None, **snap_over):
    if bid is None:
        _ID_SEQ[0] += 1
        bid = f"test-build-{_ID_SEQ[0]}"
    return lp_web.do_ind_builds_save(
        {"id": [bid], "runs": [str(runs)],
         "snapshot": [json.dumps(_snapshot(**snap_over))]})["build"]


def _built(runs=10, done_at=None, bid=None, **snap_over):
    """Save a build and mark it delivered (persisted)."""
    b = _save_build(runs=runs, bid=bid, **snap_over)
    acct = lp_web.current_account()
    builds = lp_web._load_tracked_builds(acct)
    rec = next(x for x in builds if x["id"] == b["id"])
    # Default delivery well before the default sale date (_txn: 2026-07-20) so
    # fills are causally valid — a unit can't sell before its lot is produced.
    rec["done_at"] = done_at if done_at is not None else 1000.0
    lp_web._save_tracked_builds(acct, builds)
    return rec


def _txn(tid, pid=587, qty=1, price=150.0, date="2026-07-20T00:00:00Z", is_buy=False):
    return {"transaction_id": tid, "type_id": pid, "quantity": qty,
            "unit_price": price, "date": date, "is_buy": is_buy}


def _reconcile(acct, txns):
    lp_web._reconcile_sell_ledger(acct, txns)


def _summary_build(res, build_id):
    return next(b for b in res["builds"] if b["id"] == build_id)


# ── Cost / units helpers (unchanged from the batch-economics model) ──────────
class TestCostHelpers:
    def test_units_produced(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert lp_web._build_units_produced(_save_build(runs=10)) == 10

    def test_units_produced_multi_output(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=5, product={"type_id": 1, "name": "X", "quantity": 3})
        assert lp_web._build_units_produced(b) == 15

    def test_batch_cost_from_materials(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        # 10 runs × 100 Trit × 0.9 = 900 mat + 10×10 job = 1000
        assert lp_web._build_batch_cost(b) == 1000.0

    def test_batch_cost_fallback_total(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=4, required_items=[], total_cost=50.0)
        assert lp_web._build_batch_cost(b) == 200.0

    def test_cost_per_unit(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert lp_web._build_cost_per_unit(_save_build(runs=10)) == 100.0


# ── Stage: the manufacturing job is checked FIRST ────────────────────────────
class TestStage:
    def test_planned(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert lp_web._build_stage(_save_build()) == "planned"

    def test_building(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["job_id"] = "123"
        assert lp_web._build_stage(b) == "building"

    def test_built_when_delivered_and_unsold(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["done_at"] = time.time()
        assert lp_web._build_stage(b, alloc={"sold": 0}, listed_units=0) == "built"

    def test_building_job_never_shows_listed(self, monkeypatch, tmp_path):
        # The reported bug: a still-running job must never read as listed/sold,
        # regardless of market activity on the same item.
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["job_id"] = "123"   # active job, done_at still None
        assert lp_web._build_stage(b, alloc={"sold": 5}, listed_units=99) == "building"

    def test_listed_when_stock_on_market(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        b["done_at"] = time.time()
        assert lp_web._build_stage(b, alloc={"sold": 2}, listed_units=8) == "listed"

    def test_sold_when_all_units_sold(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        b["done_at"] = time.time()
        assert lp_web._build_stage(b, alloc={"sold": 10}, listed_units=0) == "sold"

    def test_abandoned_is_sold(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        b["done_at"] = time.time()
        b["abandoned"] = True
        assert lp_web._build_stage(b, alloc={"sold": 3}, listed_units=5) == "sold"


# ── Ledger reconcile: wallet transactions in, deduped by transaction_id ──────
class TestLedgerReconcile:
    def test_sell_txn_accrues_realized_profit(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=150.0)])
        res = lp_web.do_ind_summary({})
        sb = _summary_build(res, b["id"])
        assert sb["realized"]["units"] == 4
        assert sb["realized"]["net"] == 600.0        # 4×150, tax 0
        assert sb["realized"]["profit"] == 200.0     # 600 − 4×100

    def test_sales_tax_applied(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10, sales_tax=0.05)
        _reconcile(acct, [_txn(1, qty=10, price=200.0)])
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["net"] == 10 * 200.0 * 0.95

    def test_dedup_across_reruns(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        t = _txn(1, qty=4, price=150.0)
        _reconcile(acct, [t])
        _reconcile(acct, [t])   # same transaction next sweep
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 4

    def test_buy_transactions_ignored(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=150.0, is_buy=True)])
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 0

    def test_reprice_is_invisible(self, monkeypatch, tmp_path):
        # Two fills of the same item at different prices (a re-priced listing):
        # both accrue at their real wallet price — price is never a matching key.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=5, price=150.0)])
        _reconcile(acct, [_txn(2, qty=5, price=130.0)])   # dropped the price
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 10
        assert sb["realized"]["net"] == 5 * 150 + 5 * 130


# ── Causality: a fresh build isn't flipped by older surplus sales ────────────
class TestDeliveryGate:
    def test_fresh_build_stays_built_when_only_old_fills_exist(self, monkeypatch, tmp_path):
        # The reported bug: 38 old sales sit in the cumulative ledger (flipped /
        # untracked stock). A brand-new 38-run batch delivers and must NOT read as
        # sold — those units are still in the hangar.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        # Sale happened at 2026-07-20, delivery observed a week later.
        _reconcile(acct, [_txn(1, qty=38, price=150.0, date="2026-07-20T00:00:00Z")])
        b = _built(runs=38, done_at=lp_web._parse_iso_ts("2026-07-27T00:00:00Z"))
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 0
        assert sb["stage"] == "built"

    def test_job_end_tolerates_observation_lag(self, monkeypatch, tmp_path):
        # done_at lags real completion (client only stamps it on the next sweep).
        # A sale between real job end and observation is legitimate: job_end gates,
        # not done_at.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10, done_at=lp_web._parse_iso_ts("2026-07-27T12:00:00Z"))
        builds = lp_web._load_tracked_builds(acct)
        rec = next(x for x in builds if x["id"] == b["id"])
        rec["job_end"] = "2026-07-27T00:00:00Z"   # really finished at midnight
        lp_web._save_tracked_builds(acct, builds)
        # Sold at 06:00 — after job_end, before done_at. Must allocate.
        _reconcile(acct, [_txn(1, qty=10, price=150.0, date="2026-07-27T06:00:00Z")])
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 10
        assert sb["stage"] == "sold"


# ── Listed stage: one open order doesn't flag every build of the product ─────
class TestListedAllocation:
    def test_one_order_flags_only_oldest_held_build(self, monkeypatch, tmp_path):
        # The reported bug: two delivered builds of "Capital Command Processor I",
        # a single open sell order. Only the oldest still-held build may read as
        # listed; the other stays built (its stock is in the hangar, not listed).
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        old = _built(runs=10, done_at=1000.0)
        new = _built(runs=10, done_at=2000.0)
        # 5 units on the market (fits within the oldest build's 10 unsold).
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": 587, "volume_remain": 5}])
        res = lp_web.do_ind_summary({})
        assert _summary_build(res, old["id"])["stage"] == "listed"
        assert _summary_build(res, new["id"])["stage"] == "built"

    def test_order_spills_to_second_build(self, monkeypatch, tmp_path):
        # An order bigger than the oldest build's unsold stock lists both.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        old = _built(runs=10, done_at=1000.0)
        new = _built(runs=10, done_at=2000.0)
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": 587, "volume_remain": 14}])
        res = lp_web.do_ind_summary({})
        assert _summary_build(res, old["id"])["stage"] == "listed"
        assert _summary_build(res, new["id"])["stage"] == "listed"


# ── Parallel batches of the same item: FIFO, no order linking ────────────────
class TestParallelBatches:
    def test_two_batches_fifo_by_done_at(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        old = _built(runs=40, done_at=1000.0)
        new = _built(runs=40, done_at=2000.0)
        # 50 units sold across (re-priced) orders — no order id anywhere.
        _reconcile(acct, [_txn(1, qty=50, price=150.0, date="2026-07-20T00:00:00Z")])
        res = lp_web.do_ind_summary({})
        assert _summary_build(res, old["id"])["realized"]["units"] == 40
        assert _summary_build(res, new["id"])["realized"]["units"] == 10

    def test_user_40_40_30_scenario(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b1 = _built(runs=40, done_at=1000.0)
        b2 = _built(runs=40, done_at=2000.0)
        b3 = _built(runs=30, done_at=3000.0, total_cost=90.0, required_items=[])
        _reconcile(acct, [
            _txn(1, qty=25, price=150.0, date="2026-07-01T00:00:00Z"),
            _txn(2, qty=30, price=140.0, date="2026-07-02T00:00:00Z"),
            _txn(3, qty=20, price=160.0, date="2026-07-03T00:00:00Z"),
        ])
        res = lp_web.do_ind_summary({})
        assert _summary_build(res, b1["id"])["realized"]["units"] == 40
        assert _summary_build(res, b2["id"])["realized"]["units"] == 35
        assert _summary_build(res, b3["id"])["realized"]["units"] == 0
        # Product roll-up: 75 sold, net independent of order attribution.
        prod = next(p for p in res["by_product"] if p["type_id"] == 587)
        assert prod["units_sold"] == 75

    def test_overflow_not_over_attributed(self, monkeypatch, tmp_path):
        # One 10-unit batch, 15 units sold (5 flipped from untracked stock).
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=15, price=150.0)])
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        assert sb["realized"]["units"] == 10   # capped at production


# ── Summary: totals, capital in flight, pipeline flow ────────────────────────
class TestSummary:
    def test_empty(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        res = lp_web.do_ind_summary({})
        assert res["builds"] == []
        assert res["totals"]["realized_profit"] == 0.0

    def test_capital_in_flight_planned(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        _save_build(runs=10)   # planned, nothing produced
        res = lp_web.do_ind_summary({})
        assert res["totals"]["capital_in_flight"] == 1000.0

    def test_capital_in_flight_drops_as_units_sell(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=150.0)])
        res = lp_web.do_ind_summary({})
        # 6 unsold × 100 cost basis
        assert res["totals"]["capital_in_flight"] == 600.0

    def test_sold_build_no_capital(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        _built(runs=10)
        _reconcile(acct, [_txn(1, qty=10, price=150.0)])
        res = lp_web.do_ind_summary({})
        assert res["totals"]["capital_in_flight"] == 0.0
        assert res["totals"]["realized_profit"] == 500.0

    def test_pipeline_flow_reported(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=150.0)])
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": 587, "volume_remain": 6}])
        res = lp_web.do_ind_summary({})
        prod = next(p for p in res["by_product"] if p["type_id"] == 587)
        flow = prod["flow"]
        assert flow["produced"] == 10
        assert flow["sold"] == 4
        assert flow["in_stock"] == 6
        assert flow["listed"] == 6


# ── Abandon: write off the unsold remainder ──────────────────────────────────
class TestAbandon:
    def test_abandon_writes_off_remainder(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=60.0)])   # sold cheap
        res = lp_web.do_ind_builds_sell_abandon({"id": [b["id"]]})
        assert res["ok"] is True
        assert res["build"]["writeoff_units"] == 6
        sb = _summary_build(lp_web.do_ind_summary({}), b["id"])
        # profit = 4×60 net − 4×100 cost − 6×100 writeoff
        assert sb["realized"]["profit"] == 4 * 60 - 4 * 100 - 6 * 100
        assert sb["stage"] == "sold"

    def test_abandon_clears_capital_in_flight(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=4, price=150.0)])
        lp_web.do_ind_builds_sell_abandon({"id": [b["id"]]})
        res = lp_web.do_ind_summary({})
        assert res["totals"]["capital_in_flight"] == 0.0

    def test_abandoned_lot_stops_absorbing_fills(self, monkeypatch, tmp_path):
        # After abandoning batch A's remainder, later sales flow to batch B.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        a = _built(runs=10, done_at=1000.0)
        b = _built(runs=10, done_at=2000.0)
        _reconcile(acct, [_txn(1, qty=4, price=150.0, date="2026-07-01T00:00:00Z")])
        lp_web.do_ind_builds_sell_abandon({"id": [a["id"]]})   # a: 4 sold, 6 written off
        _reconcile(acct, [_txn(2, qty=5, price=150.0, date="2026-07-05T00:00:00Z")])
        res = lp_web.do_ind_summary({})
        # a stays at 4 (cap now 4); the 5 new units go to b.
        assert _summary_build(res, a["id"])["realized"]["units"] == 4
        assert _summary_build(res, b["id"])["realized"]["units"] == 5

    def test_unabandon_restores(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        lp_web.do_ind_builds_sell_abandon({"id": [b["id"]]})
        lp_web.do_ind_builds_sell_abandon({"id": [b["id"]], "abandoned": ["0"]})
        res = lp_web.do_ind_summary({})
        assert _summary_build(res, b["id"])["abandoned"] is False
        assert res["totals"]["capital_in_flight"] == 1000.0

    def test_abandon_requires_built(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)   # planned, not built
        res = lp_web.do_ind_builds_sell_abandon({"id": [b["id"]]})
        assert "error" in res


# ── Archive still counts in stats (declutter, not delete) ────────────────────
class TestArchive:
    def test_archived_build_still_counts(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        _reconcile(acct, [_txn(1, qty=10, price=150.0)])
        lp_web.do_ind_builds_archive({"id": [b["id"]]})
        res = lp_web.do_ind_summary({})
        assert res["totals"]["realized_profit"] == 500.0


# ── Migration from the legacy per-build sell blobs ───────────────────────────
class TestMigration:
    def test_migrates_realized_fills_into_ledger(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        # Hand-craft a legacy build with a sell blob (as the old model stored it).
        builds = lp_web._load_tracked_builds(acct)
        rec = next(x for x in builds if x["id"] == b["id"])
        rec["sell"] = {
            "started_at": 1.0, "cost_per_unit": 100.0, "qty_target": 10,
            "realized": [
                {"event_id": "e1", "ts": 5000.0, "units": 3, "price": 150.0,
                 "net": 450.0, "transaction_ids": [111]},
                {"event_id": "e2", "ts": 6000.0, "units": 2, "price": 150.0,
                 "net": 300.0},   # order-diff fill with no txn id
            ],
        }
        lp_web._save_tracked_builds(acct, builds)
        res = lp_web.do_ind_summary({})   # triggers migration
        sb = _summary_build(res, b["id"])
        assert sb["realized"]["units"] == 5
        assert sb["realized"]["net"] == 750.0
        # Legacy sell scaffolding is gone; ledger now holds the fills.
        migrated = next(x for x in lp_web._load_tracked_builds(acct)
                        if x["id"] == b["id"])
        assert "sell" not in migrated
        assert len(lp_web._load_sell_ledger(acct)["587"]) == 2

    def test_migration_is_idempotent(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        builds = lp_web._load_tracked_builds(acct)
        rec = next(x for x in builds if x["id"] == b["id"])
        rec["sell"] = {"started_at": 1.0, "cost_per_unit": 100.0, "qty_target": 10,
                       "realized": [{"event_id": "e1", "ts": 5.0, "units": 3,
                                     "price": 150.0, "net": 450.0}]}
        lp_web._save_tracked_builds(acct, builds)
        lp_web.do_ind_summary({})
        lp_web.do_ind_summary({})   # second call must not re-seed
        assert len(lp_web._load_sell_ledger(acct)["587"]) == 1

    def test_migrates_closed_early_to_abandoned(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _built(runs=10)
        builds = lp_web._load_tracked_builds(acct)
        rec = next(x for x in builds if x["id"] == b["id"])
        rec["sell"] = {
            "started_at": 1.0, "closed_at": 9.0, "closed_early": True,
            "cost_per_unit": 100.0, "qty_target": 10, "writeoff_units": 6,
            "realized": [{"event_id": "e1", "ts": 5000.0, "units": 4, "price": 60.0,
                          "net": 240.0}],
        }
        lp_web._save_tracked_builds(acct, builds)
        res = lp_web.do_ind_summary({})
        sb = _summary_build(res, b["id"])
        assert sb["abandoned"] is True
        assert sb["stage"] == "sold"
        assert sb["realized"]["profit"] == 4 * 60 - 4 * 100 - 6 * 100
