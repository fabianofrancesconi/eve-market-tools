"""
Full blueprint detail breakdown for the detail panel: material shopping list,
EIV/job-cost components, blueprint buy-in / invention cost, output, and
revenue/profit in both sell modes.
"""
import math

from .costs import manufacturing_cost, build_time
from .invention import invention_cost_per_run
from ..lp.evaluate import _best


def build_industry_detail(bp, prices, names, volumes, params):
    """Full per-item breakdown for the detail panel: the material shopping list
    (qty, unit price, line cost and line m3 at batch N), the EIV/job-cost
    components, the blueprint buy-in / invention cost, output, and revenue/profit
    in both sell modes. Mirrors lp_core.build_detail()."""
    me = params.get("me", 0)
    te = params.get("te", 0)
    n = max(1, int(params.get("runs", 1)))
    job_rate = params.get("job_rate", 0.0)
    sales_tax = params.get("sales_tax", 0.0)
    broker = params.get("broker_fee", 0.0)
    volumes = volumes or {}
    names = names or {}

    cost = manufacturing_cost(bp, prices, adjusted=params.get("adjusted", {}),
                              job_rate=job_rate, me=me)
    pid = bp["product_id"]
    out_qty = bp.get("out_qty") or 1

    required = []
    input_volume = 0.0
    for line in cost["lines"]:
        tid = line["type_id"]
        vol_each = volumes.get(tid)
        line_vol = (line["eff_qty"] * vol_each) if vol_each is not None else None
        if line_vol is not None:
            input_volume += line_vol
        required.append({
            "type_id": tid,
            "name": names.get(tid, str(tid)),
            "base_qty": line["base_qty"],
            "eff_qty": line["eff_qty"],
            "unit_price": line["unit_price"],
            "line_cost": line["line_cost"],
            "line_cost_batch": None if line["line_cost"] is None else line["line_cost"] * n,
            "volume_each": vol_each,
            "line_volume_batch": None if line_vol is None else line_vol * n,
        })

    inv = bp.get("invention")
    bpo_price = (params.get("bpo_prices") or {}).get(bp["blueprint_id"])
    invention_cost = invention_cost_per_run(inv, prices, params) if inv else 0.0
    bp_buyin = None if inv else bpo_price
    bp_source = "invention" if inv else ("market" if bpo_price else "none")
    operating_cost = cost["material_cost"] + cost["job_cost"] + invention_cost
    p = prices.get(pid, {})
    ask, bid = p.get("sell_min"), p.get("buy_max")
    rev_patient = (out_qty * ask * (1 - sales_tax - broker)) if ask else None
    rev_instant = (out_qty * bid * (1 - sales_tax)) if bid else None
    profit_patient = None if rev_patient is None else rev_patient - operating_cost
    profit_instant = None if rev_instant is None else rev_instant - operating_cost
    profit_best = _best(profit_patient, profit_instant)
    payback_runs = (math.ceil(bp_buyin / profit_best)
                    if (bp_buyin and profit_best and profit_best > 0) else None)
    payback_runs_patient = (math.ceil(bp_buyin / profit_patient)
                            if (bp_buyin and profit_patient and profit_patient > 0) else None)
    payback_runs_instant = (math.ceil(bp_buyin / profit_instant)
                            if (bp_buyin and profit_instant and profit_instant > 0) else None)
    out_vol_each = volumes.get(pid)
    out_vol = (out_qty * out_vol_each) if out_vol_each is not None else None

    return {
        "blueprint_id": bp["blueprint_id"],
        "product": {
            "type_id": pid,
            "name": names.get(pid, bp.get("product_name") or str(pid)),
            "quantity": out_qty,
            "volume_each": out_vol_each,
        },
        "required_items": required,
        "material_cost": cost["material_cost"],
        "eiv": cost["eiv"],
        "job_rate": job_rate,
        "job_cost": cost["job_cost"],
        "invention_cost": invention_cost,
        "bp_price": bp_buyin,
        "bp_source": bp_source,
        "bp_available": bool(inv or bpo_price),
        "payback_runs": payback_runs,
        "payback_runs_patient": payback_runs_patient,
        "payback_runs_instant": payback_runs_instant,
        "total_cost": operating_cost,
        "missing_price": cost["missing_price"],
        "ask": ask,
        "bid": bid,
        "sales_tax": sales_tax,
        "broker_fee": broker,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": profit_patient,
        "profit_instant": profit_instant,
        "build_time": build_time(bp.get("base_time"), te,
                                 params.get("skill_profile"),
                                 params.get("skills_level", 0)),
        "me_used": me,
        "te_used": te,
        "runs": n,
        # cargo for the whole batch
        "input_volume_batch": input_volume * n,
        "output_volume_batch": None if out_vol is None else out_vol * n,
        "invention": _invention_detail(bp, prices, names, params),
    }


def _invention_detail(bp, prices, names, params):
    """The invention breakdown for the detail panel (None for T1 items): the
    datacore shopping list, the skill-adjusted success probability, runs per
    invented BPC and the resulting per-run blueprint cost."""
    inv = bp.get("invention")
    if not inv:
        return None
    lvl = params.get("skills_level", 0)
    p = min(1.0, (inv.get("probability") or 0.0) * (1 + lvl / 40.0 + 2 * lvl / 30.0))
    datacores = []
    for dcid, qty in inv.get("datacores", []):
        unit = (prices.get(dcid) or {}).get("sell_min")
        datacores.append({
            "type_id": dcid,
            "name": (names or {}).get(dcid, str(dcid)),
            "quantity": qty,
            "unit_price": unit,
            "line_cost": (qty * unit) if unit else None,
        })
    return {
        "datacores": datacores,
        "base_probability": inv.get("probability"),
        "probability": p,
        "runs_per_bpc": inv.get("runs_per_bpc"),
        "cost_per_run": invention_cost_per_run(inv, prices, params),
    }
