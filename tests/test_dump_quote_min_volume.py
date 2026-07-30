"""_dumpQuote (static/js/ind.js) — the build-decider "Dump now" price.

The decider panel ("IT'S BUILT — LIST OR DUMP?") computes its instant-sell price
through _dumpQuote, which walks the LIVE buy book honouring each order's min_volume.
A buyer wanting more units than the whole batch (60 000 min vs a 4 200 build) must
not set the "Dump now" price — that was the reported bug: the panel showed a phantom
13 000/u dump against a 60k-min bid for a 4 200-unit batch.

_dumpQuote depends on walkBook (lp.js). Both are dependency-free JS, so we extract
them from source and run under node. Skips cleanly if node isn't installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LP_JS = _ROOT / "static" / "js" / "lp.js"
_IND_JS = _ROOT / "static" / "js" / "ind.js"


def _extract_fn(src, name):
    """Brace-match `function <name>(...) { ... }` out of `src`."""
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match %s" % name)


def _run_dump(st, frozen_bid, qty):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    walk = _extract_fn(_LP_JS.read_text(), "walkBook")
    dump = _extract_fn(_IND_JS.read_text(), "_dumpQuote")
    script = (walk + "\n" + dump + "\n" +
              "const r = _dumpQuote(%s, %s, %s);\n"
              "process.stdout.write(JSON.stringify(r));\n"
              % (json.dumps(st), json.dumps(frozen_bid), qty))
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# The reported case: a 60k-min buyer at 13000 and a min-1 buyer at 6797.
_BOOK = [[13000.0, 60000, 60000], [6797.0, 5417, 1]]


def test_batch_below_big_min_prices_off_the_reachable_bid():
    # 4 200-unit batch can't touch the 60k-min order → dumps at 6797, all 4200 fit.
    r = _run_dump({"live": {"buy_book": _BOOK}}, None, 4200)
    assert r["bid"] == 6797.0
    assert r["fillQty"] == 4200


def test_batch_above_min_reaches_the_top_bid():
    # A 60 000-unit batch clears the 60k-min order; blended avg exceeds 6797.
    r = _run_dump({"live": {"buy_book": _BOOK}}, None, 60000)
    assert r["bid"] > 6797.0
    assert r["fillQty"] == 60000


def test_only_a_big_min_order_means_no_reachable_bid():
    # Every buy order demands more than the batch → nothing fits, bid is null.
    r = _run_dump({"live": {"buy_book": [[13000.0, 60000, 60000]]}}, None, 4200)
    assert r["bid"] is None
    assert r["fillQty"] == 0


def test_empty_live_book_means_nobody_buying():
    # A shipped-but-empty book is authoritative: no fallback to a frozen bid.
    r = _run_dump({"live": {"buy_book": []}}, 9999.0, 4200)
    assert r["bid"] is None
    assert r["fillQty"] == 0


def test_no_book_falls_back_to_live_raw_bid():
    # Live state without a book (older fetch) → raw live bid, whole qty assumed.
    r = _run_dump({"live": {"bid": 500.0}}, 100.0, 4200)
    assert r["bid"] == 500.0
    assert r["fillQty"] == 4200


def test_no_live_state_falls_back_to_frozen_bid():
    # No live state at all → frozen snapshot bid, whole qty assumed.
    r = _run_dump({}, 250.0, 4200)
    assert r["bid"] == 250.0
    assert r["fillQty"] == 4200
