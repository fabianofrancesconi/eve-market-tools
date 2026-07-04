"""
T2 invention cost calculation: effective blueprint cost per manufacturing run
sourced from invention.
"""


def invention_cost_per_run(inv, prices, params):
    """Effective blueprint cost for one T2 manufacturing run, sourced from
    invention rather than a bought BPO.

      inv     {datacores:[(id,qty)], probability (base), runs_per_bpc, ...}
      prices  live prices (datacores priced at sell_min)
      params  carries skills_level (the science-skill assumption) and an optional
              decryptor_price.

    Each invention attempt costs the datacores (+ optional decryptor) and yields,
    on success, a BPC good for `runs_per_bpc` manufacturing runs. So the cost
    charged to ONE T2 run is attempt_cost / (success_prob * runs_per_bpc). The
    base success probability is lifted by the three invention skills, here
    approximated with the flat skills_level (encryption /40, two datacore /30).
    The consumed T1 BPC (the invention input) is assumed owned/free -- modelling
    a copied T1 original you already run."""
    runs = inv.get("runs_per_bpc") or 1
    p_base = inv.get("probability") or 0.0
    if p_base <= 0 or runs <= 0:
        return 0.0
    lvl = params.get("skills_level", 0)
    p = min(1.0, p_base * (1 + lvl / 40.0 + 2 * lvl / 30.0))
    attempt = 0.0
    for dcid, qty in inv.get("datacores", []):
        unit = (prices.get(dcid) or {}).get("sell_min")
        if unit:
            attempt += qty * unit
    if params.get("decryptor_price"):
        attempt += params["decryptor_price"]
    return attempt / (p * runs)
