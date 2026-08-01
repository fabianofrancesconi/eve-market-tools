"""Regression: per-blueprint notes silently stop saving after the first edit.

Root cause was an object-aliasing bug in the shared pref funnel (static/js/shared.js).
``setPref`` stored the caller's object *by reference* in its in-memory mirror
(``SETTINGS.prefs[key]=value``), and on boot ``IND.notes`` was aliased to that very
object. So ``setIndNote`` mutated the mirror in place; the next ``setPref`` then saw
``JSON.stringify(cur)===JSON.stringify(value)`` (it was comparing the object to
itself) and skipped the write. The note appeared to save (in memory) but never hit
the server — a reload lost every edit after the first.

The fix: ``setPref`` clones object/array values before mirroring, and boot clones the
restored blob, so a caller-held handle can never alias the mirror. These tests drive
the real ``setPref``/``getPref`` out of shared.js under node and assert a mutate-then-
save sequence actually emits the server write.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SHARED_JS = (_ROOT / "static" / "js" / "shared.js").read_text()


def _extract(name):
    """Brace-match `function <name>(...) { ... }` out of shared.js."""
    start = _SHARED_JS.index("function " + name + "(")
    depth, i = 0, _SHARED_JS.index("{", start)
    while i < len(_SHARED_JS):
        if _SHARED_JS[i] == "{":
            depth += 1
        elif _SHARED_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return _SHARED_JS[start:i + 1]
        i += 1
    raise AssertionError("could not brace-match " + name)


# Harness: stub the module globals setPref touches (SETTINGS, timers, fetch,
# _settingsReady), capture every _sendPref, then replay a realistic note edit.
_PRELUDE = """
const SETTINGS = { prefs: {} };
const _prefTimers = {};
let _settingsReady = true;
const SENT = [];
// setPref debounces via setTimeout; make it synchronous so the test is
// deterministic and record what would go to the server.
const _origSetTimeout = setTimeout;
function _sendPref(key, value){ SENT.push([key, JSON.parse(JSON.stringify(value))]); }
"""


def _run(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    # Run pending debounced writes: setPref schedules via setTimeout(…, 400); we
    # flush by advancing fake time. Simplest: monkeypatch setTimeout to run now.
    script = (_PRELUDE
              + "setTimeout = (fn)=>{ fn(); return 0; };\n"
              + "clearTimeout = ()=>{};\n"
              + _extract("setPref") + "\n"
              + _extract("getPref") + "\n"
              + body + "\n"
              + "process.stdout.write(JSON.stringify({sent: SENT, prefs: SETTINGS.prefs}));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_first_edit_persists():
    # Baseline: a single note write reaches the server.
    res = _run("""
      const IND_notes = {};
      IND_notes['123'] = 'first';
      setPref('ind.notes', IND_notes);
    """)
    assert res["sent"] == [["ind.notes", {"123": "first"}]]


def test_second_edit_of_same_object_still_persists():
    # The bug: the same object handle is mutated and re-saved. Before the fix the
    # second setPref saw cur===value and dropped the write, so SENT had one entry.
    res = _run("""
      const IND_notes = {};
      IND_notes['123'] = 'first';
      setPref('ind.notes', IND_notes);        // save #1
      IND_notes['123'] = 'second';            // mutate the SAME object
      setPref('ind.notes', IND_notes);        // save #2 — must NOT be skipped
    """)
    assert len(res["sent"]) == 2
    assert res["sent"][1] == ["ind.notes", {"123": "second"}]
    # And the final persisted mirror reflects the latest edit.
    assert res["prefs"]["ind.notes"] == {"123": "second"}


def test_mirror_is_not_aliased_to_caller_object():
    # After setPref, mutating the caller's object must not silently change the
    # mirror (it's a clone). A subsequent no-op save of an equal object is skipped,
    # but the stored value stays correct.
    res = _run("""
      const IND_notes = { '9': 'keep' };
      setPref('ind.notes', IND_notes);
      IND_notes['9'] = 'MUTATED-WITHOUT-SAVE';   // caller edits but does NOT call setPref
    """)
    # The mirror kept the value as it was at save time — not the later mutation.
    assert res["prefs"]["ind.notes"] == {"9": "keep"}


def test_unchanged_value_is_not_resent():
    # Idempotency preserved: saving an equal object twice sends once.
    res = _run("""
      setPref('ind.notes', {'1': 'a'});
      setPref('ind.notes', {'1': 'a'});
    """)
    assert len(res["sent"]) == 1
