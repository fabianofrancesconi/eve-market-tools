"""Pure blueprint-research bookkeeping for the Industry → Research view.

WHAT THIS MODULE ANSWERS
========================
"Which blueprints have I researched, what is still in the lab, and what did
each level actually buy me?" — for Material Efficiency (ME) and Time Efficiency
(TE) research, the two jobs that permanently improve a BPO.

DATA MODEL
----------
ESI only ever shows the last ~90 days of industry jobs, and a research job never
says which level it started from — only how many levels it adds (``runs``). So
two small append-only stores are kept by ``lp-web``, both built here:

* **the research-job ledger** (:func:`merge_jobs`) — every ME/TE job ever seen,
  keyed by ``job_id`` and updated in place as it moves active → ready →
  delivered. Persisting it is what keeps history past ESI's 90-day window. The
  first time a job is seen *running*, the blueprint's last-known level is
  stamped onto it as ``from_level`` — the one moment that's knowable for sure.
* **per-item blueprint levels** (:func:`merge_bp_levels`) — each blueprint
  item's last-seen ME/TE. ESI drops a blueprint from ``/blueprints/`` while it
  sits in a job, so the last-known level is kept rather than forgotten, which is
  what lets an in-lab job know where it started.

:func:`infer_levels` turns those into a from → to for every job (stamped
``from_level`` wins; otherwise chained backwards from the blueprint's current
level), and :func:`research_report` prices the benefit of each level:

* **ME** — each level cuts every material by 1% of its base quantity, so one
  level saves ``Σ base_qty × price / 100`` ISK per run (before job-level
  rounding, which only bites on tiny quantities). Payback = job fee ÷ that.
* **TE** — each TE level (2% in-game, ``time_efficiency`` steps of 2) cuts the
  blueprint's base build time by 2% per run, before skills/structure bonuses.

Everything is pure — dicts in, dicts out. ``lp-web`` owns ESI, the SDE, prices
and persistence.
"""

import ind_core

ACT_TE = 3
ACT_ME = 4
RESEARCH_ACTIVITIES = (ACT_TE, ACT_ME)
ME_MAX = 10
TE_MAX = 20

# Job statuses. ``ready`` = finished, awaiting delivery (levels apply on
# delivery); ``cancelled`` / ``reverted`` never changed the blueprint.
_DONE = ("delivered",)
_LIVE = ("active", "paused", "ready")
_VOID = ("cancelled", "reverted")


def level_gain(activity_id, runs):
    """Levels a research job adds, in the blueprint's own units: ME +1 per run,
    TE +2 per run (ESI's ``time_efficiency`` counts the in-game 2% steps)."""
    runs = int(runs or 0)
    return runs * 2 if activity_id == ACT_TE else runs


def level_max(activity_id):
    return TE_MAX if activity_id == ACT_TE else ME_MAX


# ── Ledgers ──────────────────────────────────────────────────────────────────

def merge_bp_levels(store, char_id, blueprints, now):
    """Fold one character's ``/blueprints/`` response into the per-item level
    store ``{str(char_id): {refreshed_at, items: {str(item_id): {type_id, me,
    te, bpo, seen}}}}``. Items missing from this response are KEPT with their
    old ``seen`` (they may just be in a job); :func:`owned_items` tells current
    from stale. Returns the (mutated) store."""
    entry = store.setdefault(str(char_id), {"refreshed_at": None, "items": {}})
    items = entry.setdefault("items", {})
    for b in blueprints or []:
        iid = b.get("item_id")
        if iid is None or b.get("type_id") is None:
            continue
        items[str(iid)] = {
            "type_id": b["type_id"],
            "me": int(b.get("material_efficiency") or 0),
            "te": int(b.get("time_efficiency") or 0),
            "bpo": b.get("quantity", -1) == -1,
            "seen": now,
        }
    entry["refreshed_at"] = now
    return store


def _item_levels(levels):
    """Newest known level snapshot per blueprint item across characters:
    ``{str(item_id): {type_id, me, te, bpo, seen}}`` (stale entries included)."""
    out = {}
    for entry in (levels or {}).values():
        for iid, it in (entry.get("items") or {}).items():
            prev = out.get(iid)
            if prev is None or (it.get("seen") or 0) > (prev.get("seen") or 0):
                out[iid] = it
    return out


def merge_jobs(store, jobs, char_id, char_name, names, locations, levels,
               parse_ts):
    """Fold one character's ESI industry jobs into the research-job ledger
    ``{str(job_id): record}`` (ME/TE jobs only; other activities ignored).

    A record is refreshed in place on every sweep (status, completion), but its
    ``from_level`` is only ever set ONCE: the first time the job is seen still
    running, from the blueprint's last-known level in ``levels`` (the per-item
    store from :func:`merge_bp_levels`). That's the only moment the starting
    level is certain — after delivery the blueprint already shows the result.

    ``names`` — type_id → name; ``locations`` — facility_id → name; ``parse_ts``
    — ESI ISO date → epoch. Returns ``(store, changed)``."""
    item_levels = _item_levels(levels)
    changed = False
    for j in jobs or []:
        act = j.get("activity_id")
        jid = j.get("job_id")
        if act not in RESEARCH_ACTIVITIES or jid is None:
            continue
        key = str(jid)
        rec = store.get(key) or {}
        new = dict(rec)
        bp_tid = j.get("blueprint_type_id")
        new.update({
            "job_id": jid,
            "character_id": char_id,
            "character_name": char_name,
            "activity_id": act,
            "blueprint_id": j.get("blueprint_id"),
            "blueprint_type_id": bp_tid,
            "blueprint_name": (names or {}).get(bp_tid) or rec.get("blueprint_name") or "?",
            "runs": int(j.get("runs") or 0),
            "cost": j.get("cost"),
            "status": j.get("status"),
            "start": parse_ts(j.get("start_date")),
            "end": parse_ts(j.get("end_date")),
            "completed": parse_ts(j.get("completed_date")),
            "location": ((locations or {}).get(j.get("facility_id"))
                         or rec.get("location")),
        })
        if "from_level" not in rec and j.get("status") in ("active", "paused"):
            it = item_levels.get(str(j.get("blueprint_id")))
            if it is not None and it.get("type_id") == bp_tid:
                new["from_level"] = it["te"] if act == ACT_TE else it["me"]
        if new != rec:
            store[key] = new
            changed = True
    return store, changed


def owned_items(levels):
    """Blueprint items seen in each character's LATEST ``/blueprints/`` refresh:
    ``[{item_id, character_id, type_id, me, te, bpo}]``. Stale items (gone at
    the last refresh — sold, consumed, or in a job) are excluded; a blueprint in
    a research job is surfaced through its job instead."""
    out = []
    for cid, entry in (levels or {}).items():
        ref = entry.get("refreshed_at")
        for iid, it in (entry.get("items") or {}).items():
            if ref is not None and it.get("seen") == ref:
                out.append(dict(it, item_id=int(iid), character_id=int(cid)))
    return out


# ── Level inference ──────────────────────────────────────────────────────────

def infer_levels(jobs, levels):
    """``{job_id: (from_level, to_level)}`` for every research job, either side
    None when it can't be known.

    Per blueprint item and activity, jobs are walked newest → oldest from an
    anchor: a running/ready job's stamped ``from_level`` (else the item's
    last-known level), or — with nothing in the lab — the item's current level,
    which is the result of its latest delivered job. Each older delivered job
    then ended where the next one began. A stamped ``from_level`` always wins
    over the chain. Results outside 0..max are dropped as unknowable."""
    item_levels = _item_levels(levels)
    groups = {}
    for j in jobs:
        if j.get("status") in _VOID:
            continue
        groups.setdefault((str(j.get("blueprint_id")), j.get("activity_id")), []).append(j)
    out = {}
    for (iid, act), group in groups.items():
        field = "te" if act == ACT_TE else "me"
        top = level_max(act)
        it = item_levels.get(iid)
        known = it.get(field) if it and it.get("type_id") == group[0].get("blueprint_type_id") else None
        group.sort(key=lambda j: (j.get("end") or 0), reverse=True)
        anchor = known          # level the newest job ENDED at (if delivered)
        for j in group:
            gain = level_gain(act, j.get("runs"))
            frm = j.get("from_level")
            if j.get("status") in _LIVE:
                if frm is None:
                    frm = known
                to = None if frm is None else frm + gain
                anchor = frm    # the job before this one ended where this began
            else:
                if frm is None and anchor is not None:
                    frm = anchor - gain
                to = None if frm is None else frm + gain
                anchor = frm
            if frm is None or frm < 0 or to > top:
                out[j["job_id"]] = (None, None)
            else:
                out[j["job_id"]] = (frm, to)
    return out


# ── Benefit maths ────────────────────────────────────────────────────────────

def me_cost_per_level(materials, prices):
    """ISK one ME level saves per run: ``Σ base_qty × price / 100`` over the
    manufacturing bill of materials. ``materials`` = ``[(material_id,
    base_qty)]`` (ME0, from the SDE); ``prices`` = ``{material_id: unit price}``.
    Returns ``(isk_per_level, me0_cost_per_run, priced)`` — ``priced`` False when
    any material has no price (the figure is then a lower bound)."""
    total = 0.0
    priced = True
    for mid, qty in materials or []:
        p = (prices or {}).get(mid)
        if p is None:
            priced = False
            continue
        total += (qty or 0) * p
    return total / 100.0, total, priced


def build_me_savings(snapshot, runs):
    """ISK a tracked build saved on materials thanks to its blueprint's ME,
    versus building the same runs at ME0 — at the prices frozen into the build's
    snapshot, with EVE's job-level rounding. 0 when the snapshot predates
    per-material ``base_qty`` (nothing to compare against)."""
    d = snapshot or {}
    me = d.get("me_used") or 0
    n = max(1, int(runs or 1))
    saved = 0.0
    for m in d.get("required_items") or []:
        bq, up = m.get("base_qty"), m.get("unit_price")
        if bq is None or up is None:
            continue
        saved += (ind_core.effective_qty(bq, 0, n) - ind_core.effective_qty(bq, me, n)) * up
    return saved


def _job_row(j, frm, to, info, now):
    act = j.get("activity_id")
    gain = level_gain(act, j.get("runs"))
    start, end = j.get("start"), j.get("end")
    dur = (end - start) if (start is not None and end is not None) else None
    status = j.get("status")
    if status == "active" and end is not None and end <= now:
        status = "ready"
    progress = None
    if status in ("ready", "delivered"):
        progress = 1.0
    elif dur and start is not None:
        progress = max(0.0, min(1.0, (now - start) / dur))
    row = {
        "job_id": j.get("job_id"), "activity": "TE" if act == ACT_TE else "ME",
        "blueprint_id": j.get("blueprint_id"),
        "blueprint_type_id": j.get("blueprint_type_id"),
        "blueprint_name": j.get("blueprint_name") or "?",
        "product_name": info.get("product_name"),
        "character_name": j.get("character_name"), "location": j.get("location"),
        "runs": j.get("runs"), "gain": gain, "from": frm, "to": to,
        "start": start, "end": end, "completed": j.get("completed") or (end if status == "delivered" else None),
        "duration": dur, "progress": progress, "status": status,
        "cost": j.get("cost"),
        "isk_per_run": None, "pct_of_materials": None, "seconds_per_run": None,
        "payback_runs": None,
    }
    if act == ACT_ME:
        per = info.get("isk_per_me_level")
        if per is not None:
            row["isk_per_run"] = per * gain
            me0 = info.get("me0_cost") or 0
            at_from = me0 * (1 - (frm or 0) / 100.0)
            row["pct_of_materials"] = (per * gain / at_from * 100.0) if at_from else None
            if row["isk_per_run"] and j.get("cost"):
                row["payback_runs"] = j["cost"] / row["isk_per_run"]
    else:
        bt = info.get("base_time")
        if bt:
            row["seconds_per_run"] = bt * gain / 100.0
    return row


def research_report(jobs, levels, bp_info, builds, now):
    """The Research view's payload.

    ``jobs`` — research-job ledger records (:func:`merge_jobs`).
    ``levels`` — per-item level store (:func:`merge_bp_levels`).
    ``bp_info`` — ``{blueprint_type_id: {name, product_name, isk_per_me_level,
    me0_cost, base_time, priced}}`` (SDE + prices, built by ``lp-web``).
    ``builds`` — tracked builds (for the realized-savings figure).

    Returns ``{totals, active, history, blueprints}``:
      * ``active``  — jobs in the lab (running / paused / ready to deliver).
      * ``history`` — delivered jobs, newest first.
      * ``blueprints`` — one row per owned BPO type (plus any researched type):
        best current ME/TE, research spent, ISK/run gained by your research,
        what's left on the table up to ME10/TE20, and what its ME saved across
        your tracked builds.
    """
    live = [j for j in jobs if j.get("status") not in _VOID]
    lv = infer_levels(live, levels)
    rows = [_job_row(j, *lv.get(j["job_id"], (None, None)),
                     bp_info.get(j.get("blueprint_type_id")) or {}, now) for j in live]
    active = sorted((r for r in rows if r["status"] in _LIVE),
                    key=lambda r: (r["end"] or 0))
    history = sorted((r for r in rows if r["status"] in _DONE),
                     key=lambda r: (r["completed"] or r["end"] or 0), reverse=True)

    # Per blueprint type: owned BPOs (best levels) + every researched type.
    bps = {}

    def bp(tid):
        info = bp_info.get(tid) or {}
        return bps.setdefault(tid, {
            "type_id": tid, "name": info.get("name") or "?",
            "product_name": info.get("product_name"),
            "me": None, "te": None, "copies": 0, "owners": set(),
            "in_lab": False, "jobs": 0, "fees": 0.0, "me_gained": 0, "te_gained": 0,
            "isk_per_run_gained": 0.0, "seconds_per_run_gained": 0.0,
            "isk_per_run_left": None, "seconds_per_run_left": None,
            "realized_savings": 0.0, "runs_built": 0, "priced": info.get("priced", True)})

    for it in owned_items(levels):
        if not it.get("bpo"):
            continue
        r = bp(it["type_id"])
        r["copies"] += 1
        r["me"] = it["me"] if r["me"] is None else max(r["me"], it["me"])
        r["te"] = it["te"] if r["te"] is None else max(r["te"], it["te"])
    last_known = _item_levels(levels)
    for j in active:
        r = bp(j["blueprint_type_id"])
        r["in_lab"] = True
        # A blueprint in the lab drops out of /blueprints/ — count it as owned
        # at the level it went in with, and read the level the job ISN'T
        # changing from its last-known snapshot.
        k = "te" if j["activity"] == "TE" else "me"
        other = "me" if k == "te" else "te"
        if j["from"] is not None:
            r[k] = j["from"] if r[k] is None else max(r[k], j["from"])
        it = last_known.get(str(j.get("blueprint_id")))
        if it is not None and it.get("type_id") == j["blueprint_type_id"]:
            r[other] = it[other] if r[other] is None else max(r[other], it[other])
    names_by_job = {j.get("job_id"): j.get("character_name") for j in live}
    for j in active + history:
        r = bp(j["blueprint_type_id"])
        r["owners"].add(names_by_job.get(j["job_id"]))
        r["jobs"] += 1
        r["fees"] += j.get("cost") or 0.0
        if j["status"] in _DONE:
            if j["activity"] == "ME":
                r["me_gained"] += j["gain"]
                r["isk_per_run_gained"] += j["isk_per_run"] or 0.0
            else:
                r["te_gained"] += j["gain"]
                r["seconds_per_run_gained"] += j["seconds_per_run"] or 0.0
    for b in builds or []:
        d = b.get("snapshot") or {}
        tid = d.get("blueprint_id") or b.get("blueprint_id")
        if tid in bps:
            bps[tid]["realized_savings"] += build_me_savings(d, b.get("runs"))
            bps[tid]["runs_built"] += max(1, int(b.get("runs") or 1))
    for tid, r in bps.items():
        info = bp_info.get(tid) or {}
        r["owners"] = sorted(o for o in r["owners"] if o)
        if r["me"] is not None and info.get("isk_per_me_level") is not None:
            r["isk_per_run_left"] = info["isk_per_me_level"] * max(0, ME_MAX - r["me"])
        if r["te"] is not None and info.get("base_time"):
            r["seconds_per_run_left"] = info["base_time"] * max(0, TE_MAX - r["te"]) / 100.0
        r["maxed"] = (r["me"] == ME_MAX and r["te"] == TE_MAX)
    blueprints = sorted(bps.values(),
                        key=lambda r: (-(r["jobs"]), -(r["isk_per_run_left"] or 0), r["name"]))

    done = history
    totals = {
        "in_progress": len(active),
        "completed": len(done),
        "me_levels": sum(j["gain"] for j in done if j["activity"] == "ME"),
        "te_levels": sum(j["gain"] for j in done if j["activity"] == "TE"),
        "lab_seconds": sum(j["duration"] or 0 for j in done),
        "fees": sum(j.get("cost") or 0.0 for j in active + done),
        "isk_per_run_gained": sum(j["isk_per_run"] or 0.0 for j in done),
        "realized_savings": sum(r["realized_savings"] for r in bps.values()),
        "bpos_owned": sum(1 for r in bps.values() if r["copies"] or r["in_lab"]),
        "bpos_maxed": sum(1 for r in bps.values() if r["maxed"]),
        "researched_blueprints": sum(1 for r in bps.values() if r["jobs"]),
    }
    return {"totals": totals, "active": active, "history": history,
            "blueprints": blueprints}
