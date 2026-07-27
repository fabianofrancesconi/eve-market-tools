"""Chaos / property tests for the build reconciliation authority.

These drive the FULL server stack — save build, deliver, reconcile wallet
transactions, record open-order volume, abandon, delete, re-add — through long
randomized sequences of the exact operations that kept breaking in the field:

  * add a build, then remove it, then add another of the same item
  * cancel an order (open-order volume drops to zero)
  * two (or more) batches of the same item running in parallel
  * multiple sales, trickling in at drifting prices, some before delivery

After EVERY step the invariants below must hold. The one that matters most — the
reason this module was rewritten — is the **LINKED-badge theorem**: the market-
order 🔗 badge (which the client derives from ``is_listed_anchor``) shows for a
product **iff** some tracked build of that product reads as ``listed``. If those
two ever disagree the user sees "order LINKED but the build says Built", which is
the bug we're here to make impossible.

The randomness is seeded and deterministic (fixed seeds), so a failure always
reproduces. Each seed prints nothing on success; on failure the assertion message
carries the seed + step so it can be replayed.
"""
import importlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


# ── Harness ──────────────────────────────────────────────────────────────────
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


# A couple of distinct products so "same item, parallel batches" and "different
# items" both get exercised. quantity 1 so runs == produced units.
_PRODUCTS = [
    {"type_id": 587, "name": "Rifter", "quantity": 1},
    {"type_id": 2929, "name": "Standup Light Missile", "quantity": 1},
]


def _snapshot(product, cost=100.0, tax=0.0):
    return {
        "blueprint_id": 900 + product["type_id"],
        "product": product,
        "total_cost": cost,
        "sales_tax": tax, "broker_fee": 0.0, "ask": 150.0, "bid": 120.0,
        "me_used": 0, "required_items": [],
    }


def _client_linked_badge(summary, product_type_id):
    """Reproduce char.js `_trackedBuildForOrder`: an open sell order on a product
    shows the 🔗 badge iff some tracked build of that product is the reconcile
    anchor (`is_listed_anchor`). This is the client's ONLY rule now — no second
    "is it listed?" derivation — so it must always match the server stage."""
    for b in summary["builds"]:
        if b["product_type_id"] == product_type_id and b.get("is_listed_anchor"):
            return b["id"]
    return None


def _assert_invariants(summary, open_orders, seed, step):
    """The contract the whole app leans on, re-checked after every chaos step."""
    ctx = f"[seed={seed} step={step}]"
    builds = summary["builds"]
    by_pid = {}
    for b in builds:
        by_pid.setdefault(b["product_type_id"], []).append(b)

    for pid, group in by_pid.items():
        listed = [b for b in group if b["stage"] == "listed"]
        anchors = [b for b in group if b.get("is_listed_anchor")]
        order_vol = sum(o["volume_remain"] for o in open_orders
                        if not o["is_buy_order"] and o["type_id"] == pid)

        # 1. At most one anchor per product, and it is a listed build.
        assert len(anchors) <= 1, f"{ctx} product {pid}: {len(anchors)} anchors"
        if anchors:
            assert anchors[0]["stage"] == "listed", \
                f"{ctx} anchor {anchors[0]['id']} not listed"

        # 2. THE THEOREM: badge shown  ⟺  some build listed  ⟺  one anchor exists.
        badge = _client_linked_badge(summary, pid)
        assert (badge is not None) == bool(listed), (
            f"{ctx} product {pid}: badge={badge} but listed builds={[b['id'] for b in listed]}"
            " — the LINKED-vs-built contradiction")
        assert (badge is not None) == bool(anchors), f"{ctx} badge/anchor mismatch"
        if badge is not None:
            assert badge == anchors[0]["id"], f"{ctx} badge points off-anchor"

        # 3. Anything listed requires real open-order volume on that product;
        #    a product with no open order can have nothing listed.
        if order_vol <= 0:
            assert not listed, f"{ctx} product {pid} listed with no open order"
        # 4. A build with a live job (undelivered) is never listed/sold.
        for b in group:
            if b["done_at"] is None:
                assert b["stage"] in ("planned", "building"), \
                    f"{ctx} undelivered build {b['id']} stage={b['stage']}"

        # 4b. THE FIELD BUG: an archived build (hidden from the board) must never
        #     be listed or carry the 🔗 anchor — else the badge points at a lot
        #     the user can't see while the visible build reads Built.
        for b in group:
            if b.get("archived"):
                assert b["stage"] != "listed", \
                    f"{ctx} archived build {b['id']} is listed"
                assert not b.get("is_listed_anchor"), \
                    f"{ctx} archived build {b['id']} is the anchor (hidden badge)"
                assert b["listed_units"] == 0, \
                    f"{ctx} archived build {b['id']} has listed_units"

        # 5. Per-build sanity: sold never exceeds produced; listed ≤ held.
        for b in group:
            rz = b["realized"]
            prod = b["units_produced"] or 0
            assert rz["units"] <= prod, f"{ctx} build {b['id']} oversold"
            assert b["listed_units"] <= b["held_units"] or b["held_units"] == 0, \
                f"{ctx} build {b['id']} listed>{b['held_units']} held"

        # 6. Listed units across the product == min(order volume, in-stock).
        prod_row = next((p for p in summary["by_product"] if p["type_id"] == pid), None)
        if prod_row and "flow" in prod_row:
            flow = prod_row["flow"]
            assert flow["listed"] == min(order_vol, flow["in_stock"]), \
                f"{ctx} product {pid} flow.listed drift"
            assert sum(b["listed_units"] for b in group) == flow["listed"], \
                f"{ctx} per-build listed != flow.listed"


class _World:
    """A tiny model of the character's real state the chaos loop mutates: which
    builds exist, whether each is delivered, its produced units, and the current
    open sell orders. Each op is applied to BOTH this model and the server, then
    the summary is reconciled and checked."""

    def __init__(self, acct, rng):
        self.acct = acct
        self.rng = rng
        self.builds = {}      # id -> {"pid","runs","delivered","done_at","abandoned"}
        self.clock = 1_000.0
        self.txn_id = 0
        self.done_seq = 0

    # -- operations --------------------------------------------------------
    def add_build(self):
        product = self.rng.choice(_PRODUCTS)
        runs = self.rng.choice([5, 10, 30, 40])
        res = lp_web.do_ind_builds_save(
            {"runs": [str(runs)],
             "snapshot": [__import__("json").dumps(_snapshot(product))]})
        bid = res["build"]["id"]
        self.builds[bid] = {"pid": product["type_id"], "runs": runs,
                            "delivered": False, "done_at": None, "abandoned": False}
        return bid

    def deliver_build(self):
        pending = [i for i, b in self.builds.items() if not b["delivered"]]
        if not pending:
            return
        bid = self.rng.choice(pending)
        self.done_seq += 1
        done_at = self.clock + self.done_seq          # strictly increasing
        srv = lp_web._load_tracked_builds(self.acct)
        rec = next((x for x in srv if x["id"] == bid), None)
        if not rec:
            return
        rec["done_at"] = done_at
        lp_web._save_tracked_builds(self.acct, srv)
        self.builds[bid]["delivered"] = True
        self.builds[bid]["done_at"] = done_at

    def delete_build(self):
        if not self.builds:
            return
        bid = self.rng.choice(list(self.builds))
        lp_web.do_ind_builds_delete({"id": [bid]})
        del self.builds[bid]

    def abandon_build(self):
        delivered = [i for i, b in self.builds.items()
                     if b["delivered"] and not b["abandoned"]]
        if not delivered:
            return
        bid = self.rng.choice(delivered)
        res = lp_web.do_ind_builds_sell_abandon({"id": [bid]})
        if res.get("ok"):
            self.builds[bid]["abandoned"] = True

    def toggle_archive(self):
        """Archive or un-archive a random build. Archiving hides it from the
        tracker board's lanes; the field bug was that a hidden archived build
        still grabbed a live order's 🔗 while the visible build read Built."""
        if not self.builds:
            return
        bid = self.rng.choice(list(self.builds))
        cur = self.builds[bid].get("archived", False)
        lp_web.do_ind_builds_archive({"id": [bid], "archived": ["0" if cur else "1"]})
        self.builds[bid]["archived"] = not cur

    def sell_units(self):
        """A wallet sale of a random product at a drifting price, dated 'now'.
        Units may exceed held stock (flipped/overflow) — reconcile must cap it."""
        product = self.rng.choice(_PRODUCTS)
        qty = self.rng.randint(1, 20)
        price = self.rng.choice([120.0, 140.0, 150.0, 160.0, 175.0])
        self.txn_id += 1
        self.clock += 10
        # ESI date derived from the monotonic clock so fills sort after deliveries.
        date = f"2026-07-27T{int(self.clock) % 24:02d}:00:00Z"
        # Anchor the sale time strictly after existing deliveries by using a big
        # base year offset; _parse_iso_ts is monotonic in the clock we feed.
        txn = {"transaction_id": self.txn_id, "type_id": product["type_id"],
               "quantity": qty, "unit_price": price, "date": date,
               "is_buy": False}
        lp_web._reconcile_sell_ledger(self.acct, [txn])

    def set_orders(self):
        """Rewrite the open sell orders for a product — models placing, resizing,
        or CANCELLING (volume 0) a listing. Persisted via _record_listed_units,
        exactly as the sweep does."""
        product = self.rng.choice(_PRODUCTS)
        vol = self.rng.choice([0, 0, 1, 5, 15, 40, 200])   # 0 == cancelled
        orders = []
        if vol > 0:
            orders.append({"is_buy_order": False, "type_id": product["type_id"],
                           "volume_remain": vol})
        # Keep the OTHER product's orders untouched by re-recording them too.
        other = [p for p in _PRODUCTS if p is not product][0]
        cur = self._open_orders_for(other["type_id"])
        for o in cur:
            orders.append(o)
        lp_web._record_listed_units(self.acct, 1, orders)

    def _open_orders_for(self, pid):
        store = lp_web._acct_kv_load(self.acct, "ind_listed_units",
                                     lp_web.IND_LISTED_UNITS_PATH, None) or {}
        per = store.get("1", {})
        vol = per.get(str(pid), 0)
        return [{"is_buy_order": False, "type_id": pid, "volume_remain": vol}] if vol else []

    def open_orders(self):
        out = []
        for p in _PRODUCTS:
            out.extend(self._open_orders_for(p["type_id"]))
        return out


def _run_chaos(monkeypatch, tmp_path, seed, steps=120):
    rng = random.Random(seed)
    acct = _acct()
    _bind(monkeypatch, tmp_path, acct)
    world = _World(acct, rng)
    ops = [world.add_build, world.add_build, world.deliver_build,
           world.deliver_build, world.sell_units, world.sell_units,
           world.set_orders, world.set_orders, world.delete_build,
           world.abandon_build, world.toggle_archive, world.toggle_archive]
    for step in range(steps):
        rng.choice(ops)()
        summary = lp_web.do_ind_summary({})
        _assert_invariants(summary, world.open_orders(), seed, step)


class TestReconcileChaos:
    def test_seeded_chaos_runs(self, monkeypatch, tmp_path):
        # A spread of seeds; each is an independent long random lifecycle. If any
        # step ever produces the LINKED-vs-built contradiction (or any other
        # invariant break) the assertion fires with its seed+step.
        for seed in range(25):
            _run_chaos(monkeypatch, tmp_path / f"s{seed}", seed)

    # ── The specific field scenarios the user named, pinned as explicit cases ──
    def test_add_remove_add_same_item(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        w = _World(acct, random.Random(0))
        a = w.add_build()
        w.delete_build_by_id = None
        lp_web.do_ind_builds_delete({"id": [a]})
        del w.builds[a]
        b = w.add_build()
        w.deliver_build()   # deliver whatever's pending (b)
        # Put an order on the product b belongs to.
        pid = w.builds[b]["pid"]
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}])
        summary = lp_web.do_ind_summary({})
        _assert_invariants(summary, w.open_orders(), "add_remove_add", 0)
        # The re-added, delivered build with an order on it must read listed and
        # be the anchor — and the badge must agree.
        sb = next(x for x in summary["builds"] if x["id"] == b)
        assert sb["stage"] == "listed"
        assert sb["is_listed_anchor"] is True
        assert _client_linked_badge(summary, pid) == b

    def test_cancel_order_unlists_build(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        w = _World(acct, random.Random(0))
        b = w.add_build()
        w.deliver_build()
        pid = w.builds[b]["pid"]
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}])
        s1 = lp_web.do_ind_summary({})
        assert next(x for x in s1["builds"] if x["id"] == b)["stage"] == "listed"
        assert _client_linked_badge(s1, pid) == b
        # Cancel the order → no open volume. Build falls back to built, badge gone.
        lp_web._record_listed_units(acct, 1, [])
        s2 = lp_web.do_ind_summary({})
        sb = next(x for x in s2["builds"] if x["id"] == b)
        assert sb["stage"] == "built"
        assert sb["is_listed_anchor"] is False
        assert _client_linked_badge(s2, pid) is None
        _assert_invariants(s2, [], "cancel", 0)

    def test_two_parallel_batches_one_order(self, monkeypatch, tmp_path):
        # The reported shape: two delivered batches of the SAME item, one order.
        # Exactly one (the oldest held) reads listed + is the anchor.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        w = _World(acct, random.Random(0))
        product = _PRODUCTS[1]           # Standup Light Missile
        import json
        r1 = lp_web.do_ind_builds_save({"runs": ["10"],
             "snapshot": [json.dumps(_snapshot(product))]})["build"]
        r2 = lp_web.do_ind_builds_save({"runs": ["10"],
             "snapshot": [json.dumps(_snapshot(product))]})["build"]
        srv = lp_web._load_tracked_builds(acct)
        for rec, t in ((r1, 1000.0), (r2, 2000.0)):
            next(x for x in srv if x["id"] == rec["id"])["done_at"] = t
        lp_web._save_tracked_builds(acct, srv)
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": product["type_id"], "volume_remain": 5}])
        summary = lp_web.do_ind_summary({})
        older = next(x for x in summary["builds"] if x["id"] == r1["id"])
        newer = next(x for x in summary["builds"] if x["id"] == r2["id"])
        assert older["stage"] == "listed" and older["is_listed_anchor"] is True
        assert newer["stage"] == "built" and newer["is_listed_anchor"] is False
        # Only the older is the badge target — no double-flagging.
        assert _client_linked_badge(summary, product["type_id"]) == r1["id"]
        _assert_invariants(summary, w.open_orders(), "parallel", 0)

    def test_archived_listed_build_does_not_flag_visible_build(self, monkeypatch, tmp_path):
        # THE EXACT REPORTED SHAPE: an older build carrying the live order gets
        # ARCHIVED (hidden from the board). Before the fix the badge kept pointing
        # at that hidden build (LINKED) while the visible newer build read Built —
        # the contradiction. Now the order must move to the visible held build.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        import json
        product = _PRODUCTS[1]           # Standup Light Missile
        older = lp_web.do_ind_builds_save({"runs": ["10"],
             "snapshot": [json.dumps(_snapshot(product))]})["build"]
        newer = lp_web.do_ind_builds_save({"runs": ["10"],
             "snapshot": [json.dumps(_snapshot(product))]})["build"]
        srv = lp_web._load_tracked_builds(acct)
        for rec, t in ((older, 1000.0), (newer, 2000.0)):
            next(x for x in srv if x["id"] == rec["id"])["done_at"] = t
        lp_web._save_tracked_builds(acct, srv)
        pid = product["type_id"]
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}])
        # Sanity: before archiving, the older build is the listed anchor.
        s0 = lp_web.do_ind_summary({})
        assert _client_linked_badge(s0, pid) == older["id"]

        # Archive the older (listed) build — it vanishes from the board's lanes.
        lp_web.do_ind_builds_archive({"id": [older["id"]], "archived": ["1"]})
        s1 = lp_web.do_ind_summary({})
        o = next(x for x in s1["builds"] if x["id"] == older["id"])
        n = next(x for x in s1["builds"] if x["id"] == newer["id"])
        # The hidden archived build is NOT listed and NOT the anchor...
        assert o["stage"] != "listed"
        assert o["is_listed_anchor"] is False
        # ...the order now surfaces on the VISIBLE build, which reads listed, and
        # the badge follows it — no more "LINKED but Built".
        assert n["stage"] == "listed"
        assert n["is_listed_anchor"] is True
        assert _client_linked_badge(s1, pid) == newer["id"]
        _assert_invariants(s1, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}],
            "archived_reported", 0)

    def test_archive_last_holder_drops_badge(self, monkeypatch, tmp_path):
        # If the ONLY held build gets archived, there's nothing visible to list —
        # the badge must disappear, not cling to the hidden build.
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        w = _World(acct, random.Random(0))
        b = w.add_build()
        w.deliver_build()
        pid = w.builds[b]["pid"]
        lp_web._record_listed_units(acct, 1, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}])
        assert _client_linked_badge(lp_web.do_ind_summary({}), pid) == b
        lp_web.do_ind_builds_archive({"id": [b], "archived": ["1"]})
        s = lp_web.do_ind_summary({})
        sb = next(x for x in s["builds"] if x["id"] == b)
        assert sb["stage"] != "listed" and sb["is_listed_anchor"] is False
        assert _client_linked_badge(s, pid) is None
        _assert_invariants(s, [
            {"is_buy_order": False, "type_id": pid, "volume_remain": 5}],
            "archive_last", 0)

    def test_multiple_sales_across_batches(self, monkeypatch, tmp_path):
        acct = _acct()
        _bind(monkeypatch, tmp_path, acct)
        import json
        product = _PRODUCTS[0]
        ids = []
        for _ in range(3):
            ids.append(lp_web.do_ind_builds_save({"runs": ["10"],
                "snapshot": [json.dumps(_snapshot(product))]})["build"]["id"])
        srv = lp_web._load_tracked_builds(acct)
        for i, t in zip(ids, (1000.0, 2000.0, 3000.0)):
            next(x for x in srv if x["id"] == i)["done_at"] = t
        lp_web._save_tracked_builds(acct, srv)
        # Multiple sales trickling in at different prices, all after delivery.
        lp_web._reconcile_sell_ledger(acct, [
            {"transaction_id": 1, "type_id": product["type_id"], "quantity": 12,
             "unit_price": 150.0, "date": "2026-08-01T00:00:00Z", "is_buy": False},
            {"transaction_id": 2, "type_id": product["type_id"], "quantity": 10,
             "unit_price": 140.0, "date": "2026-08-02T00:00:00Z", "is_buy": False},
        ])
        summary = lp_web.do_ind_summary({})
        rows = {i: next(x for x in summary["builds"] if x["id"] == i) for i in ids}
        # FIFO: 22 sold → first batch 10 (sold), second 10 (sold), third 2.
        assert rows[ids[0]]["stage"] == "sold"
        assert rows[ids[1]]["stage"] == "sold"
        assert rows[ids[2]]["realized"]["units"] == 2
        assert rows[ids[2]]["stage"] == "built"    # no order yet
        _assert_invariants(summary, [], "multi_sale", 0)
