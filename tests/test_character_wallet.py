"""Wallet displays follow Settings without borrowing another character's ISK."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


_CHAR_JS = (Path(__file__).resolve().parent.parent / "static/js/char.js").read_text()


def _extract(name):
    start = _CHAR_JS.index("function " + name + "(")
    depth = 0
    for i in range(_CHAR_JS.index("{", start), len(_CHAR_JS)):
        if _CHAR_JS[i] == "{":
            depth += 1
        elif _CHAR_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return _CHAR_JS[start:i + 1]
    raise AssertionError("could not extract " + name)


def _run(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = """
const assert = require("node:assert/strict");
const AUTH = {
  loggedIn:true, activeCharId:1,
  characters:[{character_id:1,name:"Main"},{character_id:2,name:"Alt"}],
  data:{character_id:1,wallet:100,characters:[
    {character_id:1,wallet:100},{character_id:2,wallet:250}
  ]}
};
const PAGE_CHAR_PAGES = ["ind", "exp", "lp"];
let ACTIVE_TAB = "ind";
const prefs = {page_char:{ind:2,exp:2}};
function getPref(key, fallback){ return prefs[key] ?? fallback; }
function setPref(key, value){ prefs[key]=value; }
const elements = {};
function $(id){
  return elements[id] ??= {
    textContent:"", title:"", attrs:{}, hidden:false,
    classList:{
      toggle(cls, hidden){ elements[id].hidden=hidden; },
      add(cls){ elements[id].hidden=true; }
    },
    setAttribute(key, value){ this.attrs[key]=value; }
  };
}
function fmtISK(n){ return String(n); }
const IND = {rows:[]};
function renderPageCharBadges(){}
function updateMyLpBadge(){}
function renderSettingsPanel(){}
function accountLabel(){ return "Account"; }
function updateIndGate(){}
function expApplyAuth(){}
"""
    names = ("charById", "charName", "assignedCharId", "charWallet",
             "updateGlobalWallet", "setPageChar", "applyPageChar", "renderAuthChip")
    script += "\n".join(_extract(name) for name in names)
    result = subprocess.run([node, "-e", script + "\n" + body],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tab,balance,name", [
    ("ind", 250, "Alt"), ("exp", 250, "Alt"),
    ("lp", 100, "Main"), ("arb", 100, "Main"), ("char", 100, "Main"),
])
def test_wallet_follows_visible_page(tab, balance, name):
    _run(f"""
ACTIVE_TAB={json.dumps(tab)};
updateGlobalWallet();
assert.equal($("#chip-wallet").textContent, "{balance} ISK");
assert.equal($("#g-wallet").textContent, "{balance}");
assert.ok($("#chip-wallet").title.includes("{name}'s wallet"));
assert.ok($("#g-wallet-field").attrs["data-tip"].includes("{name}'s wallet"));
""")


def test_changing_assignment_and_default_updates_both_wallets():
    _run("""
updateGlobalWallet();
setPageChar("ind", 1);
assert.equal($("#chip-wallet").textContent, "100 ISK");
assert.equal($("#g-wallet").textContent, "100");
setPageChar("ind", null);
AUTH.activeCharId=2;
renderAuthChip();
assert.equal($("#chip-wallet").textContent, "250 ISK");
assert.equal($("#g-wallet").textContent, "250");
""")


@pytest.mark.parametrize("missing", ["null", "undefined"])
def test_unavailable_assigned_wallet_never_shows_default_balance(missing):
    _run(f"""
AUTH.data.characters[1].wallet={missing};
updateGlobalWallet();
assert.equal($("#chip-wallet").textContent, "Wallet unavailable");
assert.equal($("#g-wallet").textContent, "—");
assert.equal($("#g-wallet-field").hidden, false);
assert.ok($("#chip-wallet").title.includes("Alt's wallet"));
""")


def test_removed_assignment_falls_back_to_default_and_zero_is_valid():
    _run("""
prefs.page_char.ind=999;
AUTH.data.characters[0].wallet=0;
updateGlobalWallet();
assert.equal($("#chip-wallet").textContent, "0 ISK");
assert.equal($("#g-wallet").textContent, "0");
""")


def test_legacy_balance_requires_matching_character():
    _run("""
delete AUTH.data.characters;
assert.equal(charWallet(1), 100);
assert.equal(charWallet(2), null);
""")


def test_missing_character_bundle_does_not_borrow_top_level_balance():
    _run("""
AUTH.data.characters.pop();
updateGlobalWallet();
assert.equal($("#chip-wallet").textContent, "Wallet unavailable");
assert.equal($("#g-wallet").textContent, "—");
""")


def test_logout_clears_wallet_displays_even_if_data_remains_cached():
    _run("""
updateGlobalWallet();
AUTH.loggedIn=false;
renderAuthChip();
assert.equal($("#chip-wallet").textContent, "");
assert.equal($("#chip-wallet").title, "");
assert.equal($("#g-wallet-field").hidden, true);
assert.equal($("#g-wallet").textContent, "—");
""")
