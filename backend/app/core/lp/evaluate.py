"""Offer evaluation — profit and ISK/LP calculations."""
import math


def _spread_pct(sell_min, buy_max):
    if sell_min and buy_max:
        return (sell_min - buy_max) / sell_min * 100.0
    if sell_min and not buy_max:
        return 100.0
    return None


def _best(*vals):
    """Highest of the supplied values, ignoring None."""
    present = [v for v in vals if v is not None]
    return max(present) if present else None


def evaluate(offers, prices, lp_budget, sales_tax, broker_fee):
    """Annotate offers with profit / ISK-per-LP for BOTH sell modes.
    Returns (sellable_sorted_by_isk_per_lp_best, unsellable)."""
    rows = []
    patient_factor = 1 - sales_tax - broker_fee
    instant_factor = 1 - sales_tax
    for o in offers:
        lp_cost = o.get("lp_cost") or 0
        if lp_cost <= 0:
            continue
        qty = o.get("quantity", 1)
        out_tid = o["type_id"]
        p = prices.get(out_tid, {})
        ask = p.get("sell_min")
        bid = p.get("buy_max")
        if not ask and not bid:
            rows.append({"name_id": out_tid, "lp_cost": lp_cost, "qty": qty,
                         "offer_id": o.get("offer_id"), "unsellable": True})
            continue

        req_cost, req_missing = 0.0, False
        for req in o.get("required_items", []):
            rp = prices.get(req["type_id"], {}).get("sell_min")
            if not rp:
                req_missing = True
                continue
            req_cost += req["quantity"] * rp
        isk_cost = o.get("isk_cost") or 0
        base_cost = isk_cost + req_cost

        rev_patient = (qty * ask * patient_factor) if ask else None
        rev_instant = (qty * bid * instant_factor) if bid else None
        profit_patient = (rev_patient - base_cost) if rev_patient is not None else None
        profit_instant = (rev_instant - base_cost) if rev_instant is not None else None
        profit_best = _best(profit_patient, profit_instant)
        ipl_patient = (profit_patient / lp_cost) if profit_patient is not None else None
        ipl_instant = (profit_instant / lp_cost) if profit_instant is not None else None
        ipl_best = _best(ipl_patient, ipl_instant)
        max_units = math.floor(lp_budget / lp_cost) if lp_budget else 0
        rows.append({
            "offer_id": o.get("offer_id"),
            "name_id": out_tid,
            "qty": qty,
            "lp_cost": lp_cost,
            "isk_cost": isk_cost,
            "req_cost": req_cost,
            "req_missing": req_missing,
            "ak_cost": o.get("ak_cost") or 0,
            "required_items": o.get("required_items", []),
            "ask": ask,
            "bid": bid,
            "spread_pct": _spread_pct(ask, bid),
            "buy_volume": p.get("buy_volume", 0),
            "sell_volume": p.get("sell_volume", 0),
            "profit_patient": profit_patient,
            "profit_instant": profit_instant,
            "profit_best": profit_best,
            "isk_per_lp_patient": ipl_patient,
            "isk_per_lp_instant": ipl_instant,
            "isk_per_lp_best": ipl_best,
            "max_units": max_units,
            "total_profit_patient": None if profit_patient is None else profit_patient * max_units,
            "total_profit_instant": None if profit_instant is None else profit_instant * max_units,
            "total_profit_best": None if profit_best is None else profit_best * max_units,
            "unsellable": False,
        })
    sellable = [r for r in rows if not r["unsellable"]]
    sellable.sort(key=lambda r: (r["isk_per_lp_best"] if r["isk_per_lp_best"] is not None
                                 else float("-inf")), reverse=True)
    return sellable, [r for r in rows if r["unsellable"]]
