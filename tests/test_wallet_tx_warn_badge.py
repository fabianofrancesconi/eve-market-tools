"""The sales-sync (wallet-transactions) failure badge on the Character panel.

When a character's wallet/transactions pull fails, the sell ledger stops ingesting
fills and tracked builds silently under-report "sold" (a build read 0/4200 while its
order was clearly selling — a stalled ledger that ran ~17h unseen). `_walletTxWarnHTML`
(static/js/char.js) turns that invisible failure into a visible gold chip driven by
the per-character `wallet_tx_health` the server now surfaces. Behavioural checks run
the helper under node (its only dep is authEsc); the rest are static wiring guards.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHAR_JS = (_ROOT / "static" / "js" / "char.js").read_text()
_CSS = (_ROOT / "static" / "style.css").read_text()


def _extract(name):
    """Brace-match `function <name>(...) { ... }` out of char.js."""
    start = _CHAR_JS.index("function " + name + "(")
    depth, i = 0, _CHAR_JS.index("{", start)
    while i < len(_CHAR_JS):
        if _CHAR_JS[i] == "{":
            depth += 1
        elif _CHAR_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return _CHAR_JS[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match " + name)


def _run(health):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = (_extract("authEsc") + "\n" + _extract("_walletTxWarnHTML") + "\n" +
              "process.stdout.write(_walletTxWarnHTML(%s));\n" % json.dumps(health))
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout


class TestBadgeBehaviour:
    def test_no_badge_when_health_missing(self):
        # Older bundle / never fetched → nothing to warn about.
        assert _run(None) == ""

    def test_no_badge_when_last_pull_ok(self):
        # A successful pull (err_at cleared) shows no badge.
        assert _run({"ok_at": 1_700_000_000, "err_at": None,
                     "err": None, "count": 42}) == ""

    def test_badge_shown_on_failure_with_status(self):
        html = _run({"ok_at": 1_700_000_000, "err_at": 1_700_003_600,
                     "err": "403", "count": 42})
        assert "char-tx-warn" in html
        assert "sales sync failed" in html
        assert "403" in html            # the ESI status is surfaced to the user

    def test_badge_reports_never_synced(self):
        # A failure with no prior success reads "never" rather than a bogus date.
        html = _run({"ok_at": None, "err_at": 1_700_003_600,
                     "err": "Timeout", "count": None})
        assert "char-tx-warn" in html
        assert "Timeout" in html

    def test_badge_escapes_error_text(self):
        # err is interpolated into the title/label — must go through authEsc.
        html = _run({"ok_at": None, "err_at": 1_700_003_600,
                     "err": "<script>", "count": None})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestWiring:
    def test_helper_defined(self):
        assert "function _walletTxWarnHTML(" in _CHAR_JS

    def test_wired_into_both_panels(self):
        # Per-character panel and the combined "All" panel both render it.
        assert "_walletTxWarnHTML(c.wallet_tx_health)" in _CHAR_JS   # single char
        assert "txFailC.wallet_tx_health" in _CHAR_JS                # All panel
        # Guarded only-on-failure: the All panel picks the failing character.
        assert "c.wallet_tx_health&&c.wallet_tx_health.err_at" in _CHAR_JS

    def test_css_class_exists(self):
        assert ".char-tx-warn" in _CSS
