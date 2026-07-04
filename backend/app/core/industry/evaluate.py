"""
Profit evaluation: rank assembled blueprints by ISK/hour.
"""
import math

from .costs import manufacturing_cost, build_time, _buildable, TRADEABILITY_FULL
from .invention import invention_cost_per_run
from ..lp.evaluate import _best


def evaluate_industry(candidates, prices, adjusted, params):
    """Rank assembled blueprints by ISK/hour. Mirrors lp_core.evaluate()'s
    dual-mode shape: every row carries patient (list at the ask, pay sales tax +
    broker) and instant (dump to the bid, pay sales tax only) figures plus a
    *_best convenience field, then batch totals for params['runs'] units.

    Assumes you own no blueprints: a T1 item's BPO is a one-time buy-in (reported
    as bp_price, with payback_runs) kept OUT of the per-craft margin; a T2 item's
    invention datacores are a recurring cost folded INTO it.

    params keys: me, te, job_rate, sales_tax, broker_fee, runs (N), bpo_prices
    (blueprint_id -> region BPO sell price), skill_profile, skills_level,
    daily_vols (product_id -> median daily volume, for days-to-sell), volumes
    (type_id -> packaged m3, for cargo), owned_me_te (blueprint_id -> (me, te)
    of a blueprint you actually own, overriding the uniform me/te for that row
    only).

    Rows are sorted by isk_per_hour_patient (None last)."""
    me = params.get("me", 0)
    te = params.get("te", 0)
    job_rate = params.get("job_rate", 0.0)
    sales_tax = params.get("sales_tax", 0.0)
    broker = params.get("broker_fee", 0.0)
    n = max(1, int(params.get("runs", 1)))
    skill_profile = params.get("skill_profile") or {}
    default_level = params.get("skills_level", 0)
    daily_vols = params.get("daily_vols") or {}
    volumes = params.get("volumes") or {}
    owned_me_te = params.get("owned_me_te") or {}
    patient_factor = 1 - sales_tax - broker
    instant_factor = 1 - sales_tax

    rows = []
    for bp in candidates:
        pid = bp["product_id"]
        out_qty = bp.get("out_qty") or 1
        owned_entry = owned_me_te.get(bp["blueprint_id"])
        if owned_entry:
            bp_me, bp_te = owned_entry[0], owned_entry[1]
            bp_is_bpo = owned_entry[2] if len(owned_entry) > 2 else True
            bp_max_runs = owned_entry[3] if len(owned_entry) > 3 else -1
        else:
            bp_me, bp_te = me, te
            bp_is_bpo, bp_max_runs = False, 0
        cost = manufacturing_cost(bp, prices, adjusted, job_rate, bp_me)

        # Blueprint economics differ by tech tier (assuming you own nothing):
        #   T2 — you can't buy the blueprint (BPCs are contract-only); you invent
        #        it, and the datacores are a RECURRING per-run cost that belongs
        #        in the per-craft margin.
        #   T1 — you buy a reusable BPO: a one-time CAPITAL buy-in, kept out of
        #        the per-craft margin and reported separately with a payback.
        inv = bp.get("invention")
        bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
        invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
        bp_buyin = None if inv else bpo_price          # T1 BPO purchase price
        bp_available = bool(inv or bpo_price)          # obtainable: inventable or BPO on sale

        # Operating (per-craft) cost = materials + job (+ invention for T2). The
        # BPO buy-in is NOT here — that's capital, recovered via payback.
        operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
        total_cost = operating_cost

        p = prices.get(pid, {})
        ask, bid = p.get("sell_min"), p.get("buy_max")
        rev_patient = (out_qty * ask * patient_factor) if ask else None
        rev_instant = (out_qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - operating_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - operating_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
        margin = lambda pr: (pr / operating_cost) if (pr is not None and operating_cost > 0) else None
        # Runs of profit needed to recoup the BPO purchase (T1 only).
        payback_runs = (math.ceil(bp_buyin / profit_best)
                        if (bp_buyin and profit_best and profit_best > 0) else None)
        payback_runs_patient = (math.ceil(bp_buyin / profit_patient)
                                if (bp_buyin and profit_patient and profit_patient > 0) else None)
        payback_runs_instant = (math.ceil(bp_buyin / profit_instant)
                                if (bp_buyin and profit_instant and profit_instant > 0) else None)

        secs = build_time(bp.get("base_time"), bp_te, skill_profile, default_level)
        hours = (secs / 3600.0) if secs else None
        iph = lambda pr: (pr / hours) if (pr is not None and hours) else None

        in_vol = sum(line["eff_qty"] * volumes[line["type_id"]]
                     for line in cost["lines"] if volumes.get(line["type_id"]) is not None)
        out_vol_each = volumes.get(pid)
        out_vol = (out_qty * out_vol_each) if out_vol_each is not None else None
        dv = daily_vols.get(pid)
        days_to_sell = ((out_qty * n) / dv) if dv else None

        rows.append({
            "blueprint_id": bp["blueprint_id"],
            "product_id": pid,
            "product_name": bp.get("product_name"),
            "market_group_id": bp.get("market_group_id"),
            "tech_level": bp.get("tech_level"),
            "out_qty": out_qty,
            "material_cost": cost["material_cost"],
            "eiv": cost["eiv"],
            "job_cost": cost["job_cost"],
            "invention_cost": invention_cost,   # recurring per-run cost (T2 only)
            "bp_price": bp_buyin,               # one-time BPO buy-in (T1; None for T2)
            "bp_source": "invention" if inv else ("market" if bpo_price else "none"),
            "bp_available": bp_available,
            "payback_runs": payback_runs,
            "payback_runs_patient": payback_runs_patient,
            "payback_runs_instant": payback_runs_instant,
            "requires_invention": bool(inv),
            "total_cost": operating_cost,
            "missing_price": cost["missing_price"],
            "ask": ask,
            "bid": bid,
            "profit_patient": profit_patient,
            "profit_instant": profit_instant,
            "profit_best": profit_best,
            "margin_patient": margin(profit_patient),
            "margin_instant": margin(profit_instant),
            "margin_best": margin(profit_best),
            "build_time": secs,
            "isk_per_hour_patient": iph(profit_patient),
            "isk_per_hour_instant": iph(profit_instant),
            "isk_per_hour_best": iph(profit_best),
            "runs": n,
            "total_profit_patient": None if profit_patient is None else profit_patient * n,
            "total_profit_instant": None if profit_instant is None else profit_instant * n,
            "input_volume": in_vol * n,
            "output_volume": None if out_vol is None else out_vol * n,
            # Per-run building blocks so the UI can rescale batch columns live
            # (profit×N, cargo, days-to-sell) when the run count changes.
            "in_vol_run": in_vol,
            "out_vol_run": out_vol,
            "daily_vol": dv,
            "days_to_sell": days_to_sell,
            "tradeability": None,   # patched for the top rows by the web layer
            "buildable": _buildable(bp, skill_profile, default_level),
            "me_used": bp_me,
            "te_used": bp_te,
            "owned_bp_me_te": bp["blueprint_id"] in owned_me_te,
            "owned_is_bpo": bp_is_bpo,
            "owned_max_runs": bp_max_runs,
        })

    rows.sort(key=lambda r: (r["isk_per_hour_patient"] if r["isk_per_hour_patient"] is not None
                             else float("-inf")), reverse=True)
    return rows
