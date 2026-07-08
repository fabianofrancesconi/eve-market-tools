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
    # The k-space no-NPC sites must call out that the only threat is PvP.
    for key in ("data_hs", "relic_ls"):
        assert "PvP" in tackle[key]["tackle"]["safety"] \
            or "capsuleer" in tackle[key]["tackle"]["safety"], key


def test_sleeper_cache_needs_both_analyzers(tackle):
    # Sleeper caches are the only sites needing BOTH analyzers — the guide
    # must say so for every cache tier.
    for key in ("cache_lo", "cache_su"):
        mods = tackle[key]["tackle"]["modules"]
        assert "Data" in mods and "Relic" in mods, key


def test_myko_gas_is_flagged_safe(tackle):
    # Mykoserocin has no NPCs/timer; Cytoserocin is flagged variable/unstable.
    assert "safe" in tackle["gas_myko"]["tackle"]["safety"].lower()
    assert "unstable" in tackle["gas_cyto"]["tackle"]["safety"].lower()
