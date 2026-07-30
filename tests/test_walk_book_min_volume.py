"""walkBook (static/js/lp.js) — the buy-order min_volume gate.

A buy order carries a min_volume it won't transact below. When dumping a produced
batch into buy orders, an order whose minimum exceeds the units still left to sell
can't be filled at all, so walkBook must skip it — otherwise a big buyer's bid (e.g.
60 000 units wanted, min 60 000) mints a phantom instant-sell price for a 4 200 batch
that could never reach it.

walkBook is plain JS with no dependencies, so we extract it from the source and run
it under node with a few scenarios. Skips cleanly if node isn't installed.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LP_JS = _ROOT / "static" / "js" / "lp.js"


def _extract_walk_book():
    """Pull the `function walkBook(...) { ... }` definition out of lp.js by
    brace-matching from its declaration, so the test tracks the real source."""
    src = _LP_JS.read_text()
    start = src.index("function walkBook(")
    depth, i = 0, src.index("{", start)
    body_start = i
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match walkBook in lp.js")


def _run_walk(book, qty):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    fn = _extract_walk_book()
    script = (fn + "\n" +
              f"const r = walkBook({json.dumps(book)}, {qty});\n"
              "process.stdout.write(JSON.stringify(r));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_source_gate_present():
    # Guard the gate itself so a refactor can't silently drop it.
    fn = _extract_walk_book()
    assert "minVol" in fn and "continue" in fn


def test_skips_buy_order_whose_min_exceeds_batch():
    # The reported bug: top bid wants 60k min, batch is 4200. That order is
    # unreachable, so nothing fills and the batch reads "no instant market".
    book = [[13000.0, 60000, 60000], [6797.0, 5417, 1]]
    r = _run_walk(book, 4200)
    assert r["filled"] == 4200          # filled entirely from the min-1 order
    assert r["avg"] == 6797.0           # at the reachable bid, NOT the 13k top bid
    assert r["shortBy"] == 0


def test_batch_large_enough_reaches_the_high_bid():
    # A 60k batch CAN meet the 60k minimum, so the top bid is used first.
    book = [[13000.0, 60000, 60000], [6797.0, 5417, 1]]
    r = _run_walk(book, 60000)
    assert r["filled"] == 60000
    assert r["avg"] == 13000.0
    assert r["lastPrice"] == 13000.0


def test_gate_is_on_remaining_not_total():
    # After a high bid consumes part of the batch, the remainder must still meet a
    # later order's minimum. 100 units: 60 taken at 10 ISK, leaving 40 — a min-50
    # order can't be met by the 40 left, so it's skipped.
    book = [[10.0, 60, 1], [9.0, 100, 50]]
    r = _run_walk(book, 100)
    assert r["filled"] == 60
    assert r["shortBy"] == 40


def test_sell_side_two_tuples_unaffected():
    # Sell levels have no third element; the gate must never trip on them.
    book = [[100.0, 5], [101.0, 10]]
    r = _run_walk(book, 12)
    assert r["filled"] == 12
    assert r["shortBy"] == 0
