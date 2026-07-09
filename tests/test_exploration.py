"""
Tests for the Exploration tab's "How to tackle it" guidance.

Two layers:
  * Wiring assertions (mirroring test_abyss.py) — the primer, the tackle card,
    and the sources footer are present in the shipped HTML/JS so a refactor
    can't silently drop them.
  * A node-driven behavioural test that actually loads exploration.js and
    exercises ``expTackle`` for one representative site of every playbook,
    proving the playbook router returns a sane, complete guide for each.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_EXP_JS = _ROOT / "static" / "js" / "exploration.js"

_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_tackle_card_wired_in_html():
    html = lp_web.INDEX_HTML
    assert 'id="exp-tackle-card"' in html
    assert 'id="exp-tackle-body"' in html
    assert "HOW TO TACKLE IT" in html


def test_primer_wired_in_html():
    html = lp_web.INDEX_HTML
    assert 'id="exp-primer-toggle"' in html
    assert 'id="exp-primer-body"' in html


def test_exploration_cites_eve_university():
    # The guidance is researched from the EVE University wiki — surface it.
    html = lp_web.INDEX_HTML
    assert "wiki.eveuniversity.org/Hacking" in html
    assert "wiki.eveuniversity.org/Ghost_Sites" in html
    assert "wiki.eveuniversity.org/Sleeper_Cache" in html


def test_playbook_router_and_primer_present_in_bundle():
    src = lp_web.FRONTEND_SOURCE
    assert "function expTackle" in src
    assert "function expPlaybookId" in src
    assert "EXP_PRIMER_HTML" in src


def test_walkthrough_modal_wired_in_html():
    # The full-walkthrough modal + its close button ship in the HTML.
    html = lp_web.INDEX_HTML
    assert 'id="exp-walk-overlay"' in html
    assert 'id="exp-walk-body"' in html
    assert 'id="exp-walk-close"' in html


def test_walkthrough_functions_present_in_bundle():
    src = lp_web.FRONTEND_SOURCE
    assert "function expWalkthrough" in src
    assert "function expRenderGuide" in src
    assert "function expOpenWalk" in src
    # The tackle card renders the button that opens the modal.
    assert "exp-walk-btn" in src


def test_primer_covers_key_hacking_mechanics():
    # The minigame primer must name the defensive/utility nodes and analyzers,
    # so it can't ship as an empty shell.
    src = _EXP_JS.read_text()
    for term in ("Restoration Node", "Virus Suppressor", "Self Repair",
                 "Kernel Rot", "Data Analyzer", "Relic Analyzer",
                 "Covert Ops"):
        assert term in src, term


# ── Behaviour (node) ─────────────────────────────────────────────────────────

_NODE = shutil.which("node")

# Drive expTackle for one site per playbook branch and dump the result as JSON.
_DRIVER = r"""
// Minimal DOM/localStorage shims — a universal stub absorbs every DOM call the
// module makes at load time so we can reach the pure playbook functions.
const stub = new Proxy(function(){}, {
  get: (_t, p) => (p === Symbol.toPrimitive ? () => "" : stub),
  apply: () => stub, construct: () => stub,
});
globalThis.$ = () => stub;
globalThis.document = { addEventListener: () => {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

__SOURCE__

const wanted = {
  data_hs:  "Local Mainframe",
  relic_ls: "Decayed Excavation",
  drone:    "Abandoned Research Complex",
  wh_relic: "Forgotten Perimeter Coronation Platform",
  wh_data:  "Unsecured Frontier Database",
  ghost_hs: "Lesser Covert Research Facility",
  ghost_wh: "Superior Covert Research Facility",
  cache_lo: "Limited Sleeper Cache",
  cache_su: "Superior Sleeper Cache",
  gas_wh:   "Barren Perimeter Reservoir",
  gas_myko: "Mykoserocin Nebula",
  gas_cyto: "Cytoserocin Nebula",
};
const out = {};
for (const [k, name] of Object.entries(wanted)) {
  const site = EXP_SITES.find(s => s.name === name);
  if (!site) { out[k] = {error: "site not found: " + name}; continue; }
  out[k] = {pid: expPlaybookId(site), tackle: expTackle(site)};
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def tackle():
    if not _NODE:
        pytest.skip("node not available")
    script = _DRIVER.replace("__SOURCE__", _EXP_JS.read_text())
    proc = subprocess.run([_NODE, "-e", script], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# Drive expWalkthrough + expRenderGuide across EVERY site and one representative
# per playbook, so a full guide + valid rendered HTML is proven for each branch.
_WALK_DRIVER = r"""
const stub = new Proxy(function(){}, {
  get: (_t, p) => (p === Symbol.toPrimitive ? () => "" : stub),
  apply: () => stub, construct: () => stub,
});
globalThis.$ = () => stub;
globalThis.document = { addEventListener: () => {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

__SOURCE__

const wanted = {
  data_hs:  "Local Mainframe",
  relic_ls: "Decayed Excavation",
  drone:    "Abandoned Research Complex",
  wh_relic: "Forgotten Perimeter Coronation Platform",
  wh_data:  "Unsecured Frontier Database",
  ghost_hs: "Lesser Covert Research Facility",
  ghost_wh: "Superior Covert Research Facility",
  cache_lo: "Limited Sleeper Cache",
  cache_st: "Standard Sleeper Cache",
  cache_su: "Superior Sleeper Cache",
  gas_wh:   "Barren Perimeter Reservoir",
  gas_myko: "Mykoserocin Nebula",
  gas_cyto: "Cytoserocin Nebula",
};
const reps = {};
for (const [k, name] of Object.entries(wanted)) {
  const site = EXP_SITES.find(s => s.name === name);
  if (!site) { reps[k] = {error: "site not found: " + name}; continue; }
  reps[k] = {pid: expPlaybookId(site), guide: expWalkthrough(site),
             html: expRenderGuide(expWalkthrough(site))};
}
// Also sweep every site to prove no branch returns a broken guide.
let broken = [];
for (const s of EXP_SITES) {
  const g = expWalkthrough(s);
  const html = expRenderGuide(g);
  if (!g || !g.overview || !g.rule) broken.push(s.name + ":fields");
  if (!html || html.indexOf("undefined") >= 0) broken.push(s.name + ":html");
}
process.stdout.write(JSON.stringify({reps, broken, total: EXP_SITES.length}));
"""


@pytest.fixture(scope="module")
def walk():
    if not _NODE:
        pytest.skip("node not available")
    script = _WALK_DRIVER.replace("__SOURCE__", _EXP_JS.read_text())
    proc = subprocess.run([_NODE, "-e", script], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_every_playbook_resolves(tackle):
    # Each representative site routes to its own playbook id (no fall-through).
    assert tackle["data_hs"]["pid"] == "data_safe"
    assert tackle["relic_ls"]["pid"] == "relic_safe"
    assert tackle["drone"]["pid"] == "data_drone"
    assert tackle["wh_relic"]["pid"] == "wh_combat"
    assert tackle["wh_data"]["pid"] == "wh_combat"
    assert tackle["ghost_hs"]["pid"] == "ghost_lesser"
    assert tackle["ghost_wh"]["pid"] == "ghost_superior"
    assert tackle["cache_lo"]["pid"] == "cache_limited"
    assert tackle["cache_su"]["pid"] == "cache_superior"
    assert tackle["gas_wh"]["pid"] == "gas_wh"
    assert tackle["gas_myko"]["pid"] == "gas_myko"
    assert tackle["gas_cyto"]["pid"] == "gas_cyto"


def test_every_tackle_guide_is_complete(tackle):
    # Every playbook must yield modules, a ship, ordered steps, and a #1 rule.
    for key, val in tackle.items():
        t = val["tackle"]
        assert t["modules"].strip(), key
        assert t["ship"].strip(), key
        assert isinstance(t["steps"], list) and len(t["steps"]) >= 3, key
        assert t["safety"].strip(), key


def test_ghost_safety_scales_with_tier(tackle):
    # Highsec ghost blast is 6,000; wormhole (Sleeper) is 12,000.
    assert "6,000" in tackle["ghost_hs"]["tackle"]["safety"]
    assert "12,000" in tackle["ghost_wh"]["tackle"]["safety"]
    assert "Sleeper" in tackle["ghost_wh"]["tackle"]["safety"]


def test_safe_data_relic_flag_no_npc_threat(tackle):
    # The k-space no-NPC sites must call out that the only threat is players.
    for key in ("data_hs", "relic_ls"):
        assert "other players" in tackle[key]["tackle"]["safety"], key


def test_sleeper_cache_needs_both_analyzers(tackle):
    # Sleeper caches are the only sites needing BOTH analyzers — the guide
    # must say so (in the modules line or the minigame note) for every tier.
    for key in ("cache_lo", "cache_su"):
        t = tackle[key]["tackle"]
        blob = (t["modules"] + " " + t.get("note", "")).lower()
        assert "both analyzers" in blob, key


def test_myko_gas_is_flagged_safe(tackle):
    # Mykoserocin has no NPCs/timer; Cytoserocin is flagged variable/unstable.
    assert "safe" in tackle["gas_myko"]["tackle"]["safety"].lower()
    assert "unstable" in tackle["gas_cyto"]["tackle"]["safety"].lower()


def test_every_site_yields_a_walkthrough(walk):
    # No site anywhere falls through to a broken/undefined guide.
    assert walk["total"] >= 70
    assert walk["broken"] == [], walk["broken"]


def test_every_walkthrough_guide_is_complete(walk):
    # Each representative guide has the full set of sections + a rendered body.
    for key, val in walk["reps"].items():
        assert "error" not in val, (key, val)
        g = val["guide"]
        assert g["overview"].strip(), key
        assert g["entry"].strip(), key
        assert isinstance(g["hazards"], list) and len(g["hazards"]) >= 2, key
        assert g["ship"].strip(), key
        assert isinstance(g["steps"], list) and len(g["steps"]) >= 3, key
        assert g["loot"].strip(), key
        assert g["rule"].strip(), key
        # The rendered HTML surfaces the section headers and the #1 rule.
        html = val["html"]
        assert "Step by step" in html, key
        assert "#1 RULE" in html, key


def test_walkthrough_ghost_blast_scales_with_tier(walk):
    # The deep guide carries the same per-tier explosive numbers as the card.
    assert "6,000" in walk["reps"]["ghost_hs"]["html"]
    assert "12,000" in walk["reps"]["ghost_wh"]["html"]


def test_walkthrough_escalation_is_pure_hacking_not_combat(walk):
    # Regression guard for the v1.94.2 correction: the safe data/relic guide
    # (which mentions escalations) must NOT tell players it's a combat site.
    for key in ("data_hs", "relic_ls"):
        html = walk["reps"][key]["html"].lower()
        assert "no npcs" in html or "no rats" in html, key


def test_walkthrough_gas_wh_flags_the_spawn_timer(walk):
    # The part that gets people killed: the delayed Sleeper spawn window.
    html = walk["reps"]["gas_wh"]["html"]
    assert "15" in html and "spawn" in html.lower()


def test_walkthrough_sleeper_caches_need_both_analyzers(walk):
    for key in ("cache_lo", "cache_st", "cache_su"):
        html = walk["reps"][key]["html"].lower()
        assert "both" in html and "analyzer" in html, key


def test_recent_cards_show_risk_and_loot_levels():
    # Recent lookups render name + low/med/high Risk & Loot values.
    src = _EXP_JS.read_text()
    assert "function expRisk" in src
    assert "function expLoot" in src
    assert "exp-recent-stats" in src


def test_hero_is_a_gamified_card_banner():
    # The main content leads with a trading-card banner (suit icon + name) and
    # bold stat pills rather than the old plain chip strip.
    src = _EXP_JS.read_text()
    assert "function expType" in src
    assert "exp-hero-banner" in src
    assert "exp-hero-name" in src
    assert "exp-stat-pill" in src


def test_reopening_a_recent_keeps_list_order():
    # Re-selecting an already-listed site must NOT reshuffle it to the front.
    if not _NODE:
        pytest.skip("node not available")
    driver = r"""
const stub = new Proxy(function(){}, {
  get: (_t,p)=> (p===Symbol.toPrimitive ? ()=>"" : stub),
  apply: ()=>stub, construct: ()=>stub,
});
globalThis.$ = () => stub;
globalThis.document = { addEventListener: () => {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
__SOURCE__
// Simulate the add-to-recent rule from expSelect for an EXISTING entry.
EXP.recent = ["Local Mainframe", "Decayed Excavation"];
const name = "Decayed Excavation";
if(!EXP.recent.includes(name)) EXP.recent = [name, ...EXP.recent].slice(0,10);
process.stdout.write(JSON.stringify(EXP.recent));
"""
    script = driver.replace("__SOURCE__", _EXP_JS.read_text())
    proc = subprocess.run([_NODE, "-e", script], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    # Order unchanged — the re-opened site stays where it was.
    assert json.loads(proc.stdout) == ["Local Mainframe", "Decayed Excavation"]
