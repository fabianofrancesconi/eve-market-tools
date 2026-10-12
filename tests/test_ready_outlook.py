"""The "Ready for delivery" building card previews the sale (static/js/ind.js).

Once a job's timer has run out the stock is real; it just hasn't been delivered.
The card shows the call the Built stage will make (the price to list at, profit,
week odds, time to clear) so the user can line up the order before delivery.
These tests check how it's wired (it fetches the order book only once ready) and
run the outlook helpers under node (skipped when node isn't installed).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()
_CSS = (_ROOT / "static" / "style.css").read_text()


def _extract_fn(src, name):
    """Brace-match `function <name>(...) { ... }` out of `src`."""
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("(", start)
    while True:
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        if depth == 0:
            break
        i += 1
    depth, i = 0, src.index("{", i)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match %s" % name)


class TestWiring:
    def test_ready_building_insight_uses_outlook(self):
        fn = _extract_fn(_IND_JS, "_insightInner")
        build = fn[fn.index('stage==="building"'):fn.index('stage==="built"')]
        assert "_readyOutlook(b)" in build
        assert "outlook:o.text" in build

    def test_order_book_fetched_only_once_ready(self):
        # The insight wiring and the background sweep both ask for the market
        # (order book + history) for a building card only when its job is done.
        assert _IND_JS.count('_deciderEnsure(b, stage!=="building" || _buildJobReady(b))') == 2
        # The countdown hitting zero flips the card and kicks that fetch.
        assert "_deciderEnsure(bb, true)" in _IND_JS

    def test_shell_renders_outlook_line(self):
        fn = _extract_fn(_IND_JS, "_insightShell")
        assert "ind-ins-outlook" in fn
        assert ".ind-ins-outlook" in _CSS

    def test_built_read_exposes_eta(self):
        fn = _extract_fn(_IND_JS, "_builtRead")
        assert "eta=sp.eta" in fn
        assert "weekAll, eta," in fn


_NODE = shutil.which("node")


def _run(body):
    src = "\n".join([
        _extract_fn(_IND_JS, "_fmtEtaDays"),
        _extract_fn(_IND_JS, "_readyOutlook"),
        _extract_fn(_IND_JS, "_buildJobReady"),
        "function fmtISKFull(v){return v.toFixed(2)+' ISK';}",
        "function _signIsk(v){return v==null?'—':(v>=0?'+':'')+v;}",
        "function _beWarn(ctx,p,lead){return lead;}",
        "let READ=null; function _builtRead(b){return READ;}",
        body,
    ])
    out = subprocess.run([_NODE, "-e", src], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(not _NODE, reason="node not installed")
class TestOutlook:
    def test_eta_formatting(self):
        r = _run("console.log(JSON.stringify([_fmtEtaDays(null), _fmtEtaDays(0.25),"
                 " _fmtEtaDays(1), _fmtEtaDays(3.24), _fmtEtaDays(42.4), _fmtEtaDays(200)]))")
        assert r == ["—", "~6h", "~1.0 day", "~3.2 days", "~42 days", "90+ days"]

    def test_job_ready(self):
        r = _run("console.log(JSON.stringify([_buildJobReady({job_end:new Date(Date.now()-1000).toISOString()}),"
                 " _buildJobReady({job_end:new Date(Date.now()+60000).toISOString()}), _buildJobReady({})]))")
        assert r == [True, False, False]

    def test_loading(self):
        r = _run("READ={loading:true}; console.log(JSON.stringify(_readyOutlook({})))")
        assert "Reading the market" in r["text"]
        assert r["stats"] == []

    def test_list_outlook(self):
        r = _run("""READ={price:1000, bestAsk:1000.1, bid:800, listProfit:5000, instProfit:-10,
            weekAll:0.8, eta:2.5, dump:false, be:{list:500}, ctx:{}, marketLoading:false};
            console.log(JSON.stringify(_readyOutlook({})))""")
        assert "list at <b>1000.00 ISK</b>" in r["text"]
        assert "80% odds" in r["text"] and "~2.5 days to clear" in r["text"]
        assert [s["k"] for s in r["stats"]] == ["Profit if listed", "Sells within a week", "Time to clear"]
        assert r["stats"][1]["cls"] == "pos"
        assert 'data-price="1000"' in r["actions"]
        assert r["warn"] == ""

    def test_under_break_even_warns(self):
        r = _run("""READ={price:400, bestAsk:400.1, bid:300, listProfit:-5000, instProfit:-9000,
            weekAll:0.2, eta:20, dump:false, be:{list:500}, ctx:{}, marketLoading:false};
            console.log(JSON.stringify(_readyOutlook({})))""")
        assert r["warn"] == "The market is under your break-even"
        assert r["stats"][1]["cls"] == "neg"

    def test_dump_outlook(self):
        r = _run("""READ={price:1000, bestAsk:1000.1, bid:990, listProfit:5000, instProfit:6000,
            weekAll:0.5, eta:5, dump:true, be:{list:500}, ctx:{}, marketLoading:false};
            console.log(JSON.stringify(_readyOutlook({})))""")
        assert "dumping is the call" in r["text"]
        assert 'data-price="990"' in r["actions"]
        assert r["stats"][0]["k"] == "Profit if dumped"
