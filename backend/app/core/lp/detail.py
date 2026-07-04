"""Detailed per-offer breakdown for the LP-store detail view."""
import math

from .evaluate import _spread_pct, _best


def build_detail(offer, prices, names, volumes, lp_budget, sales_tax, broker_fee):
    """Full per-offer breakdown for the detail view."""
    out_tid = offer["type_id"]
    qty = offer.get("quantity", 1)
    lp_cost = offer.get("lp_cost") or 0
    isk_fee = offer.get("isk_cost") or 0
    p = prices.get(out_tid, {})
    ask = p.get("sell_min")
    bid = p.get("buy_max")
    out_vol_each = volumes.get(out_tid)

    required = []
    req_cost = 0.0
    req_vol_each_total = 0.0
    req_missing_price = False
    for req in offer.get("required_items", []):
        tid, q = req["type_id"], req["quantity"]
        price = prices.get(tid, {}).get("sell_min")
        vol_each = volumes.get(tid)
        line_cost = (q * price) if price else None
        if line_cost is None:
            req_missing_price = True
        else:
            req_cost += line_cost
        line_vol = (q * vol_each) if vol_each is not None else None
        if line_vol is not None:
            req_vol_each_total += line_vol
        required.append({
            "type_id": tid,
            "name": names.get(tid, str(tid)),
            "quantity": q,
            "unit_price": price,
            "line_cost": line_cost,
            "volume_each": vol_each,
            "line_volume": line_vol,
        })

    total_cost = isk_fee + req_cost
    rev_patient = (qty * ask * (1 - sales_tax - broker_fee)) if ask else None
    rev_instant = (qty * bid * (1 - sales_tax)) if bid else None
    profit_patient = (rev_patient - total_cost) if rev_patient is not None else None
    profit_instant = (rev_instant - total_cost) if rev_instant is not None else None
    ipl_patient = (profit_patient / lp_cost) if (profit_patient is not None and lp_cost) else None
    ipl_instant = (profit_instant / lp_cost) if (profit_instant is not None and lp_cost) else None
    max_units = math.floor(lp_budget / lp_cost) if (lp_budget and lp_cost) else 0
    out_vol_per_redemption = (out_vol_each * qty) if out_vol_each is not None else None

    return {
        "offer_id": offer.get("offer_id"),
        "output": {
            "type_id": out_tid,
            "name": names.get(out_tid, str(out_tid)),
            "quantity": qty,
            "volume_each": out_vol_each,
            "volume_per_redemption": out_vol_per_redemption,
        },
        "required_items": required,
        "ask": ask,
        "bid": bid,
        "spread_pct": _spread_pct(ask, bid),
        "buy_volume": p.get("buy_volume", 0),
        "sell_volume": p.get("sell_volume", 0),
        "lp_cost": lp_cost,
        "isk_fee": isk_fee,
        "req_cost": req_cost,
        "req_missing_price": req_missing_price,
        "total_cost": total_cost,
        "revenue_patient": rev_patient,
        "revenue_instant": rev_instant,
        "profit_patient": profit_patient,
        "profit_instant": profit_instant,
        "profit_best": _best(profit_patient, profit_instant),
        "isk_per_lp_patient": ipl_patient,
        "isk_per_lp_instant": ipl_instant,
        "isk_per_lp_best": _best(ipl_patient, ipl_instant),
        "input_volume_per_redemption": req_vol_each_total,
        "output_volume_per_redemption": out_vol_per_redemption,
        "max_units": max_units,
    }
