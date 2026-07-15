"""Guards the Industry detail 'Runs' field against the focus/caret regression.

Typing a run count re-renders the whole detail panel (materials, batch costs
and cargo all scale with the run count), which rebuilds box.innerHTML and so
destroys the very <input> being typed into. The fix keeps focus + caret across
that re-render — but it only works if the field is a *text* input, because
<input type=number> returns null for selectionStart and refuses
setSelectionRange, which is what made the cursor snap to the end on every digit.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_IND_JS = (_ROOT / "static" / "js" / "ind.js").read_text()


def test_runs_input_is_a_numeric_text_field_not_type_number():
    # type=number breaks caret restore (selectionStart is null) — must be a
    # text field with a numeric input mode instead.
    assert 'class="ind-d-runs" type="text"' in _IND_JS
    assert 'inputmode="numeric"' in _IND_JS
    assert 'class="ind-d-runs" type="number"' not in _IND_JS


def test_runs_input_handler_restores_focus_and_caret_after_rerender():
    # The input listener must re-grab the freshly rendered field, refocus it,
    # and restore the caret so multi-digit / mid-string typing works.
    handler = _IND_JS[_IND_JS.index('runsInput.addEventListener("input"'):]
    handler = handler[:handler.index("});") + 3]
    assert "selectionStart" in handler          # reads the caret
    assert 'box.querySelector(".ind-d-runs")' in handler  # re-grabs fresh node
    assert ".focus()" in handler                # restores focus
    assert "setSelectionRange" in handler       # restores the caret
