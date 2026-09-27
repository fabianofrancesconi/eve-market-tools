"""EVE order-price tick rules (static/js/shared.js).

EVE's market only accepts order prices with at most four significant digits and
never finer than 0.01 ISK. The tracker used to undercut with `ask * 0.9999`, which
suggested (and copied) prices like 34,286,571.00 that the game rejects — the
valid undercut of 34,300,000 is 34,290,000. These run the real helpers under
node (skips cleanly if node isn't installed).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SHARED_JS = (_ROOT / "static" / "js" / "shared.js").read_text()
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()
_CHAR_JS = (_ROOT / "static" / "js" / "char.js").read_text()

_FNS = ["_eveTickCents", "_eveCents", "eveTick", "eveSnapDown", "eveSnapUp",
        "eveSnap", "eveUndercut", "fmtISKFull"]


def _extract_fn(src, name):
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


def _call(fn, *args):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    lib = "\n".join(_extract_fn(_SHARED_JS, f) for f in _FNS)
    body = "process.stdout.write(JSON.stringify(%s(%s)));" % (
        fn, ", ".join(json.dumps(a) for a in args))
    out = subprocess.run([node, "-e", lib + "\n" + body], capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("price,tick", [
    (34_300_000, 10_000), (1_234_567, 1_000), (9_999, 1), (1_000, 1),
    (999.9, 0.1), (123.4, 0.1), (99.99, 0.01), (5.5, 0.01), (1_234_000_000, 1_000_000),
])
def test_tick_is_four_significant_digits(price, tick):
    assert _call("eveTick", price) == pytest.approx(tick)


@pytest.mark.parametrize("ask,undercut", [
    (34_300_000, 34_290_000),      # the screenshot's case
    (1_490_000, 1_489_000),
    (10_000_000, 9_999_000),       # crossing a magnitude uses the finer tick below
    (1_000, 999.9),
    (100, 99.99),
    (12.34, 12.33),
    (34_286_571, 34_280_000),      # an off-grid input still lands on the grid
])
def test_undercut_is_one_valid_tick_below(ask, undercut):
    assert _call("eveUndercut", ask) == pytest.approx(undercut)


def test_undercut_of_minimum_price_is_none():
    assert _call("eveUndercut", 0.01) is None


@pytest.mark.parametrize("p,down,up,near", [
    (17_908_607.65, 17_900_000, 17_910_000, 17_910_000),
    (18_565_416.16, 18_560_000, 18_570_000, 18_570_000),
    (9_999.5, 9_999, 10_000, 9_999),
    (34_290_000, 34_290_000, 34_290_000, 34_290_000),   # already valid: untouched
    (12.34, 12.34, 12.34, 12.34),                       # float noise doesn't shift it
])
def test_snaps(p, down, up, near):
    assert _call("eveSnapDown", p) == pytest.approx(down)
    assert _call("eveSnapUp", p) == pytest.approx(up)
    assert _call("eveSnap", p) == pytest.approx(near)


def test_full_formatter_drops_cents_on_whole_isk_prices():
    assert "." not in _call("fmtISKFull", 34_290_000)
    assert _call("fmtISKFull", 12.3).endswith("30")


def test_no_fractional_undercut_left_in_the_ui():
    # Every undercut goes through eveUndercut; ×0.9999 yields off-grid prices.
    for src in (_IND_JS, _CHAR_JS):
        assert "0.9999" not in src
    assert "eveUndercut(compAsk)" in _extract_fn(_IND_JS, "_repricePaysOff")
    assert "eveUndercut(bestAsk)" in _extract_fn(_IND_JS, "_builtRead")


def test_copied_and_dialled_prices_are_snapped():
    assert "eveSnap(price)" in _extract_fn(_IND_JS, "_deciderCopyValue")
    assert "eveSnap(price)" in _extract_fn(_IND_JS, "_updateBuildDecider")
    assert "eveSnap(price)" in _extract_fn(_CHAR_JS, "_updateBuildPeekSim")
    assert "eveSnap(price)" in _extract_fn(_CHAR_JS, "_updateBuildPeekProb")
