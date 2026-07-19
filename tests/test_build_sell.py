"""Tests for tracked-build sell tracking (realized profit): the cost-basis /
stage helpers, the sell/start|link|unlink|cancel routes, the fill-accrual
reconcile, and the portfolio summary roll-up."""
import datetime
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
    monkeypatch.setattr(lp_web, "ORDER_EVENTS_PATH", tmp_path / "ev.json")
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


def _save_build(runs=10, **snap_over):
    return lp_web.do_ind_builds_save(
        {"runs": [str(runs)], "snapshot": [json.dumps(_snapshot(**snap_over))]})["build"]


class TestCostHelpers:
    def test_units_produced(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        assert lp_web._build_units_produced(b) == 10  # 1/run × 10 runs

    def test_units_produced_multi_output(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=5, product={"type_id": 1, "name": "X", "quantity": 3})
        assert lp_web._build_units_produced(b) == 15

    def test_batch_cost_from_materials(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        # 100 Trit/run × 10 runs × 0.9 = 900 material; + job 10 × 10 runs = 100.
        assert lp_web._build_batch_cost(b) == 1000.0

    def test_batch_cost_fallback_total_cost(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        # No base_qty on materials → falls back to total_cost × runs.
        b = _save_build(runs=4, required_items=[
            {"name": "Trit", "eff_qty": 100, "unit_price": 0.9}])
        assert lp_web._build_batch_cost(b) == 400.0

    def test_cost_per_unit(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        assert lp_web._build_cost_per_unit(b) == 100.0  # 1000 / 10 units


class TestStage:
    def test_planned(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert lp_web._build_stage(_save_build()) == "planned"

    def test_building(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["job_id"] = "123"
        assert lp_web._build_stage(b) == "building"

    def test_built(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["job_id"] = "123"
        b["done_at"] = time.time()
        assert lp_web._build_stage(b) == "built"

    def test_listed_and_sold(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        b["done_at"] = time.time()
        b["sell"] = {"started_at": time.time()}
        assert lp_web._build_stage(b) == "listed"
        b["sell"]["closed_at"] = time.time()
        assert lp_web._build_stage(b) == "sold"


class TestSellRoutes:
    def test_start_freezes_cost_basis(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        res = lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        assert res["ok"] is True
        sell = res["build"]["sell"]
        assert sell["cost_per_unit"] == 100.0
        assert sell["qty_target"] == 10  # defaults to full production
        assert sell["order_ids"] == []
        assert sell["realized"] == []

    def test_start_partial_target(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        res = lp_web.do_ind_builds_sell_start({"id": [b["id"]], "qty_target": ["4"]})
        assert res["build"]["sell"]["qty_target"] == 4

    def test_start_unknown_build(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert "error" in lp_web.do_ind_builds_sell_start({"id": ["nope"]})

    def test_manual_link_and_unlink(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        lp_web.do_ind_builds_sell_link({"id": [b["id"]], "order_id": ["555"]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["sell"]["order_ids"] == ["555"]
        assert stored["sell"]["needs_pick"] is False
        lp_web.do_ind_builds_sell_unlink({"id": [b["id"]], "order_id": ["555"]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored["sell"]["order_ids"] == []

    def test_link_dedups(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        lp_web.do_ind_builds_sell_link({"id": [b["id"]], "order_id": ["555"]})
        lp_web.do_ind_builds_sell_link({"id": [b["id"]], "order_id": ["555"]})
        assert lp_web.do_ind_builds_list({})["builds"][0]["sell"]["order_ids"] == ["555"]

    def test_cancel_drops_sell_state(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build()
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        lp_web.do_ind_builds_sell_cancel({"id": [b["id"]]})
        assert "sell" not in lp_web.do_ind_builds_list({})["builds"][0]


def _now_iso(delta_s=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=delta_s)).isoformat()


def _sell_order(order_id, type_id, remain, total, price, issued=None):
    return {"order_id": order_id, "type_id": type_id, "type_name": "Rifter",
            "volume_remain": remain, "volume_total": total, "price": price,
            "is_buy_order": False, "issued": issued or _now_iso(), "duration": 90}


class TestReconcileAutoMatch:
    def test_single_order_auto_links(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders = [_sell_order(700, 587, 10, 10, 160.0)]
        # Seed the order-diff baseline, then reconcile.
        lp_web._track_order_changes(acct, 1, orders, {})
        lp_web._reconcile_sell_builds(acct, orders)
        sell = lp_web.do_ind_builds_list({})["builds"][0]["sell"]
        assert sell["order_ids"] == ["700"]
        assert sell["needs_pick"] is False

    def test_multiple_orders_need_pick(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders = [_sell_order(700, 587, 5, 5, 160.0),
                  _sell_order(701, 587, 5, 5, 162.0)]
        lp_web._reconcile_sell_builds(acct, orders)
        sell = lp_web.do_ind_builds_list({})["builds"][0]["sell"]
        assert sell["order_ids"] == []
        assert sell["needs_pick"] is True

    def test_different_item_not_matched(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders = [_sell_order(700, 999999, 10, 10, 160.0)]  # wrong type_id
        lp_web._reconcile_sell_builds(acct, orders)
        sell = lp_web.do_ind_builds_list({})["builds"][0]["sell"]
        assert sell["order_ids"] == []
        assert sell["needs_pick"] is False

    def test_stale_order_before_start_not_matched(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        # Issued two hours before Sell was clicked → outside the match margin.
        orders = [_sell_order(700, 587, 10, 10, 160.0, issued=_now_iso(-7200))]
        lp_web._reconcile_sell_builds(acct, orders)
        assert lp_web.do_ind_builds_list({})["builds"][0]["sell"]["order_ids"] == []

    def test_manual_link_respects_unlink_tombstone(self, monkeypatch, tmp_path):
        # After an explicit unlink, the auto-linker must not re-grab that order.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._reconcile_sell_builds(acct, orders)
        assert lp_web.do_ind_builds_list({})["builds"][0]["sell"]["order_ids"] == ["700"]
        lp_web.do_ind_builds_sell_unlink({"id": [b["id"]], "order_id": ["700"]})
        # Same order still present in-game — but it was rejected, so stays unlinked.
        lp_web._reconcile_sell_builds(acct, orders)
        assert lp_web.do_ind_builds_list({})["builds"][0]["sell"]["order_ids"] == []


class TestAutoStart:
    """A finished build auto-starts sell-tracking when its product appears as a
    single fresh sell order — no 'Start tracking' click required."""

    def _built(self, runs=10, **snap):
        b = _save_build(runs=runs, **snap)
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        stored["job_id"] = "123"
        stored["done_at"] = time.time()
        lp_web._save_tracked_builds(lp_web.current_account(), [stored])
        return stored

    def test_built_single_order_auto_starts(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        self._built(runs=10)
        orders = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders, {})
        lp_web._reconcile_sell_builds(acct, orders)
        sell = lp_web.do_ind_builds_list({})["builds"][0]["sell"]
        assert sell["started_at"] is not None
        assert sell["auto"] is True
        assert sell["order_ids"] == ["700"]
        assert sell["qty_target"] == 10
        assert sell["cost_per_unit"] == 100.0

    def test_built_multiple_orders_do_not_auto_start(self, monkeypatch, tmp_path):
        # Ambiguous → leave it for the user; don't guess which order is the batch.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        self._built(runs=10)
        orders = [_sell_order(700, 587, 5, 5, 160.0),
                  _sell_order(701, 587, 5, 5, 162.0)]
        lp_web._reconcile_sell_builds(acct, orders)
        assert (lp_web.do_ind_builds_list({})["builds"][0].get("sell") or {}) \
            .get("started_at") is None

    def test_not_yet_built_does_not_auto_start(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)          # planned, no done_at
        orders = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._reconcile_sell_builds(acct, orders)
        assert (lp_web.do_ind_builds_list({})["builds"][0].get("sell") or {}) \
            .get("started_at") is None

    def test_auto_start_accrues_real_fill_price(self, monkeypatch, tmp_path):
        # Profit is computed from the order's actual fill price, not our proposal.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        self._built(runs=10)
        orders0 = [_sell_order(700, 587, 10, 10, 137.0)]  # listed at 137, not our ask
        lp_web._track_order_changes(acct, 1, orders0, {})
        lp_web._reconcile_sell_builds(acct, orders0)
        orders1 = [_sell_order(700, 587, 6, 10, 137.0)]   # 4 sold @ 137
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        rz = lp_web._build_realized(lp_web.do_ind_builds_list({})["builds"][0])
        assert rz["units"] == 4
        assert rz["net"] == 4 * 137.0
        assert rz["profit"] == 4 * (137.0 - 100.0)

    def test_cancel_tombstone_blocks_reauto(self, monkeypatch, tmp_path):
        # Cancelling an auto-started sale must not silently re-start it next sweep.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        self._built(runs=10)
        orders = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders, {})
        lp_web._reconcile_sell_builds(acct, orders)
        assert lp_web.do_ind_builds_list({})["builds"][0]["sell"]["auto"] is True
        b = lp_web.do_ind_builds_list({})["builds"][0]
        lp_web.do_ind_builds_sell_cancel({"id": [b["id"]]})
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert "sell" not in stored
        assert stored["no_auto_sell"] is True
        # Order still live → but the user opted out, so no re-auto-start.
        lp_web._reconcile_sell_builds(acct, orders)
        assert "sell" not in lp_web.do_ind_builds_list({})["builds"][0]

    def test_manual_start_clears_cancel_tombstone(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = self._built(runs=10)
        b["no_auto_sell"] = True
        lp_web._save_tracked_builds(acct, [b])
        res = lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        assert res["ok"] is True
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        assert stored.get("no_auto_sell") in (None, False) \
            and "no_auto_sell" not in stored
        assert stored["sell"]["started_at"] is not None


class TestReconcileAccrual:
    def _setup(self, monkeypatch, tmp_path, sales_tax=0.0):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10, sales_tax=sales_tax)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        # Baseline order (10 units listed) + auto-link it.
        orders0 = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders0, {})
        lp_web._reconcile_sell_builds(acct, orders0)
        return acct, b

    def test_partial_fill_accrues(self, monkeypatch, tmp_path):
        acct, b = self._setup(monkeypatch, tmp_path)
        # 4 units sell (remain 10 → 6).
        orders1 = [_sell_order(700, 587, 6, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        rz = lp_web._build_realized(lp_web.do_ind_builds_list({})["builds"][0])
        assert rz["units"] == 4
        assert rz["net"] == 4 * 160.0            # 0% tax
        assert rz["cost_of_sold"] == 4 * 100.0   # cost_per_unit 100
        assert rz["profit"] == 4 * 60.0

    def test_accrual_applies_sales_tax(self, monkeypatch, tmp_path):
        acct, b = self._setup(monkeypatch, tmp_path, sales_tax=0.05)
        orders1 = [_sell_order(700, 587, 6, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        rz = lp_web._build_realized(lp_web.do_ind_builds_list({})["builds"][0])
        assert rz["net"] == 4 * 160.0 * 0.95

    def test_no_double_count_on_rerun(self, monkeypatch, tmp_path):
        acct, b = self._setup(monkeypatch, tmp_path)
        orders1 = [_sell_order(700, 587, 6, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        # Reconcile again with the SAME order state — no new fill event, so no
        # change; and even a redundant reconcile must not re-accrue.
        lp_web._reconcile_sell_builds(acct, orders1)
        rz = lp_web._build_realized(lp_web.do_ind_builds_list({})["builds"][0])
        assert rz["units"] == 4

    def test_fills_accumulate_and_close(self, monkeypatch, tmp_path):
        acct, b = self._setup(monkeypatch, tmp_path)
        # Advance the clock between syncs so each fill event gets a distinct id
        # ({order}_{int(now)}); real sweeps are minutes apart, but the test runs
        # sub-second, which would otherwise collide the two events' ids.
        clock = [time.time() + 100]
        monkeypatch.setattr(lp_web.time, "time", lambda: clock[0])
        # Sell 6 (→4 remain), then the last 4 (order vanishes).
        orders1 = [_sell_order(700, 587, 4, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        clock[0] += 300
        lp_web._track_order_changes(acct, 1, [], {})     # fully filled
        lp_web._reconcile_sell_builds(acct, [])
        stored = lp_web.do_ind_builds_list({})["builds"][0]
        rz = lp_web._build_realized(stored)
        assert rz["units"] == 10
        assert stored["sell"]["closed_at"] is not None
        assert lp_web._build_stage(stored) == "sold"

    def test_expired_fill_not_accrued(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        # Order issued 91 days ago w/ 90-day duration → vanishing = expired.
        issued = _now_iso(-91 * 86400)
        orders0 = [_sell_order(700, 587, 10, 10, 160.0, issued=issued)]
        lp_web._track_order_changes(acct, 1, orders0, {})
        lp_web._reconcile_sell_builds(acct, orders0)
        lp_web._track_order_changes(acct, 1, [], {})     # vanishes → expired
        lp_web._reconcile_sell_builds(acct, [])
        rz = lp_web._build_realized(lp_web.do_ind_builds_list({})["builds"][0])
        assert rz["units"] == 0


class TestSummary:
    def test_empty(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        assert lp_web.do_ind_summary({})["builds"] == []

    def test_capital_in_flight_counts_unsold(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        _save_build(runs=10)  # batch cost 1000, nothing sold
        s = lp_web.do_ind_summary({})
        assert s["totals"]["capital_in_flight"] == 1000.0
        assert s["totals"]["realized_profit"] == 0.0

    def test_realized_profit_rolls_up(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders0 = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders0, {})
        lp_web._reconcile_sell_builds(acct, orders0)
        orders1 = [_sell_order(700, 587, 6, 10, 160.0)]  # 4 sold
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        s = lp_web.do_ind_summary({})
        assert s["totals"]["realized_profit"] == 4 * 60.0
        # 4 of 10 units sold → 600 of the 1000 batch cost still in flight.
        assert s["totals"]["capital_in_flight"] == 600.0
        assert s["by_product"][0]["units_sold"] == 4
        assert s["by_product"][0]["realized_profit"] == 240.0

    def test_summary_exposes_raw_fills_for_time_filter(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        b = _save_build(runs=10)
        lp_web.do_ind_builds_sell_start({"id": [b["id"]]})
        orders0 = [_sell_order(700, 587, 10, 10, 160.0)]
        lp_web._track_order_changes(acct, 1, orders0, {})
        lp_web._reconcile_sell_builds(acct, orders0)
        orders1 = [_sell_order(700, 587, 6, 10, 160.0)]  # 4 sold
        lp_web._track_order_changes(acct, 1, orders1, {})
        lp_web._reconcile_sell_builds(acct, orders1)
        s = lp_web.do_ind_summary({})
        sell = s["builds"][0]["sell"]
        assert sell["cost_per_unit"] == 100.0
        assert len(sell["fills"]) == 1
        assert sell["fills"][0]["units"] == 4
        assert sell["fills"][0]["net"] == 4 * 160.0
        assert sell["fills"][0]["ts"] is not None

    def test_sold_build_no_capital(self, monkeypatch, tmp_path):
        _bind(monkeypatch, tmp_path, _acct())
        b = _save_build(runs=10)
        b_stored = lp_web.do_ind_builds_list({})["builds"][0]
        b_stored["sell"] = {"started_at": 1.0, "closed_at": 2.0,
                            "qty_target": 10, "cost_per_unit": 100.0,
                            "realized": [{"event_id": "x", "units": 10,
                                          "price": 160.0, "net": 1600.0}]}
        lp_web._save_tracked_builds(lp_web.current_account(), [b_stored])
        s = lp_web.do_ind_summary({})
        assert s["totals"]["capital_in_flight"] == 0.0
        assert s["totals"]["realized_profit"] == 600.0
