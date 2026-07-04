"""Liquidity enrichment for evaluated LP-store offers."""


def enrich_liquidity(sellable, daily_vols):
    """Annotate evaluate()'s rows with daily_vol and days_to_clear."""
    out = {}
    for r in sellable:
        dv = daily_vols.get(r["name_id"])
        sell_vol = r.get("sell_volume") or 0
        days = (sell_vol / dv) if (dv and dv > 0) else None
        out[r["offer_id"]] = {"daily_vol": dv, "days_to_clear": days}
    return out
