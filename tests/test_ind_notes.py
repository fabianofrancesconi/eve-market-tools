"""Frontend-wiring checks for per-blueprint notes in the Industry Planner.

Notes are a purely client-side feature riding on the existing per-key pref path
(``ind.notes`` — a ``{blueprint_id: text}`` blob, like ``ind.hidden_bps``), so
there is no new server endpoint to exercise. FRONTEND_SOURCE is index.html + all
JS bundled, so these assert the marker, the editor and the persistence are wired
without needing a browser. The dict-valued pref round-trip itself is covered by
tests/test_multiuser.py::test_object_valued_pref_roundtrips."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
lp_web = importlib.import_module("lp-web")


def test_note_state_and_helpers_present():
    src = lp_web.FRONTEND_SOURCE
    # State bucket + the read/write helpers.
    assert "notes:{}" in src
    assert "function indNote(" in src
    assert "function setIndNote(" in src
    # Writes go through the one pref funnel under ind.notes.
    assert "setPref('ind.notes'" in src


def test_note_marker_in_item_cell():
    src = lp_web.FRONTEND_SOURCE
    # A 📝 marker is appended to the Item cell when a note exists, carrying the
    # note text as a hover tooltip.
    assert "ind-note-mark" in src
    assert "indNote(r.blueprint_id)" in src


def test_note_editor_in_detail_panel():
    src = lp_web.FRONTEND_SOURCE
    # The detail panel exposes an editable textarea that autosaves on input.
    assert "ind-d-note-box" in src
    assert "setIndNote(d.blueprint_id" in src


def test_notes_restored_on_boot():
    src = lp_web.FRONTEND_SOURCE
    # loadSettings applies the stored ind.notes blob back into IND.notes.
    assert "ind.notes" in src
    assert "IND.notes=nt" in src
