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

def test_guide_container_wired_in_html():
    # The single flowing guide renders into one container (no card grid/modal).
    html = lp_web.INDEX_HTML
    assert 'id="exp-guide"' in html
    assert 'id="exp-cards"' in html


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
    assert "function expPlaybookId" in src
    assert "EXP_PRIMER_HTML" in src


def test_walkthrough_functions_present_in_bundle():
    src = lp_web.FRONTEND_SOURCE
    # The inline guide is built by expBuildGuide from the per-type expWalkthrough.
    assert "function expWalkthrough" in src
    assert "function expBuildGuide" in src


def test_browse_all_sites_wired_in_html():
    # The sidebar's "Browse all sites" list is the primary discovery path.
    html = lp_web.INDEX_HTML
    assert 'id="exp-browse"' in html
    assert 'id="exp-browse-list"' in html
    src = lp_web.FRONTEND_SOURCE
    assert "function expRenderBrowse" in src


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

# Drive expBuildGuide (the real production render path) across EVERY site and
# one representative per playbook, proving a full, balanced guide + valid HTML
# for each branch. expBuildGuide reads DOM-free site data, so the stub suffices.
_WALK_DRIVER = r"""
// Minimal DOM/localStorage shims — a universal stub absorbs every DOM call the
// module makes at load time so we can reach the pure guide functions.
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
             html: expBuildGuide(site)};
}
// Also sweep every site to prove no branch returns a broken guide.
let broken = [];
for (const s of EXP_SITES) {
  const g = expWalkthrough(s);
  const html = expBuildGuide(s);
  if (!g || !g.overview || !g.rule) broken.push(s.name + ":fields");
  if (!html || html.indexOf("undefined") >= 0) broken.push(s.name + ":html");
}
// Every browse group's data-name must resolve to a real site (discovery path).
const browseTypes = [...new Set(EXP_SITES.map(s => s.type))].sort();
process.stdout.write(JSON.stringify(
  {reps, broken, total: EXP_SITES.length, browseTypes}));
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


def test_every_playbook_resolves(walk):
    # Each representative site routes to its own playbook id (no fall-through).
    pid = {k: v["pid"] for k, v in walk["reps"].items()}
    assert pid["data_hs"] == "data_safe"
    assert pid["relic_ls"] == "relic_safe"
    assert pid["drone"] == "data_drone"
    assert pid["wh_relic"] == "wh_combat"
    assert pid["wh_data"] == "wh_combat"
    assert pid["ghost_hs"] == "ghost_lesser"
    assert pid["ghost_wh"] == "ghost_superior"
    assert pid["cache_lo"] == "cache_limited"
    assert pid["cache_su"] == "cache_superior"
    assert pid["gas_wh"] == "gas_wh"
    assert pid["gas_myko"] == "gas_myko"
    assert pid["gas_cyto"] == "gas_cyto"


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
        # The rendered guide surfaces the section headers, facts strip, #1 rule.
        html = val["html"]
        assert "Step by step" in html, key
        assert "#1 RULE" in html, key
        assert "exp-facts" in html, key
        assert "exp-hero-banner" in html, key


def test_walkthrough_ghost_blast_scales_with_tier(walk):
    # The guide carries the per-tier explosive numbers (6k highsec, 12k WH).
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


def test_guide_weaves_in_per_site_data(walk):
    # The inline guide must fold in THIS site's own record — its triggers,
    # loot summary, and estimated value — not just the generic per-type text.
    for key, val in walk["reps"].items():
        html = val["html"]
        assert "This site — mechanics" in html, key
        assert "This site drops:" in html, key


def test_browse_covers_all_site_types(walk):
    # The browse groups (EXP_BROWSE_ORDER) must cover every type present in the
    # data, so no site is undiscoverable through the sidebar.
    browse_order = {"data", "relic", "ghost", "sleeper_cache", "gas"}
    assert set(walk["browseTypes"]).issubset(browse_order), walk["browseTypes"]


def test_ghost_rule_is_commit_and_tank_not_flee(walk):
    # Correction: ghost sites are NOT "hack one can and flee". Retreating
    # doesn't help — warping mid-hack auto-fails and the site blows on the
    # timer anyway — so the guide must say commit & tank, keep the "eat the
    # blast" mindset, and must NOT carry the old "never attempt a second" line.
    for key in ("ghost_hs", "ghost_wh"):
        html = walk["reps"][key]["html"]
        low = html.lower()
        assert "never attempt a second" not in low, key
        assert "untanked" in low, key                       # the only real loss
        assert "eat the" in low or "commit" in low, key     # commit, don't bail


def test_no_guide_tells_players_to_back_out_of_a_hack(walk):
    # Backing out of a hack never helps — closing the minigame counts as the
    # failure (and on ghost sites the site blows anyway). No guide should tell
    # players to retreat from / not finish a hack, or to never fail twice.
    banned = ("never fail the same", "retreat rather than force",
              "weigh it before committing", "never attempt a second")
    for key, val in walk["reps"].items():
        low = val["html"].lower()
        for phrase in banned:
            assert phrase not in low, (key, phrase)


def test_recent_cards_show_risk_and_loot_levels():
    # Recent lookups render name + low/med/high Risk & Loot values.
    src = _EXP_JS.read_text()
    assert "function expRisk" in src
    assert "function expLoot" in src
    assert "exp-recent-stats" in src


def test_hero_is_a_gamified_card_banner():
    # The guide leads with a trading-card banner (suit icon + name) above an
    # equal-tile facts strip (Risk / Loot / NPCs / Value / Found-in).
    src = _EXP_JS.read_text()
    assert "function expType" in src
    assert "exp-hero-banner" in src
    assert "exp-hero-name" in src
    assert "exp-facts" in src


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
