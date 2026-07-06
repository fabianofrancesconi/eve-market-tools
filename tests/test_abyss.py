"""
Tests for the Abyss tab — a pure client-side static reference (Abyssal Deadspace
tiers / weather / enemy roster), wired in like the Exploration tab.

The parametrized ``test_tab_url_serves_app_shell`` in test_regression.py already
covers that ``/abyss`` and ``/aby`` serve the app shell (they're in TAB_ROUTES).
These tests guard the routing wiring and that the front-end module is present
and internally consistent, so a refactor can't silently drop the tab.
"""
from pathlib import Path

import importlib.util

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lp_web", _ROOT / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def test_abyss_routes_registered():
    # Deep-link / refresh on the Abyss tab must serve the SPA shell, not 404.
    assert "/abyss" in lp_web.TAB_ROUTES
    assert "/aby" in lp_web.TAB_ROUTES


def test_abyss_routes_public():
    # TAB_ROUTES are folded into _PUBLIC_PATHS so the shell renders pre-login on
    # a multi-user deploy (boot.js resolves auth client-side).
    assert "/abyss" in lp_web._PUBLIC_PATHS
    assert "/aby" in lp_web._PUBLIC_PATHS


def test_index_html_wires_abyss_tab():
    html = lp_web.INDEX_HTML
    assert 'data-tab="aby"' in html          # nav button
    assert 'id="aby-tablewrap"' in html      # tab layout container
    assert "/static/js/abyss.js" in html     # module script tag


def test_frontend_routing_maps_abyss():
    src = lp_web.FRONTEND_SOURCE
    # Clean-URL maps (shared.js) both directions.
    assert 'aby:"/abyss"' in src
    assert '"/abyss":"aby"' in src
    # switchTab shows the Abyss pane and calls the module initializer.
    assert '$("#aby-tablewrap").classList.toggle("hidden", tab!=="aby");' in src
    assert "abyInit" in src


def test_abyss_module_data_present():
    # abyss.js is bundled into FRONTEND_SOURCE; check the core data structures
    # exist so the tab can't ship empty.
    src = lp_web.FRONTEND_SOURCE
    assert "const ABY_TIERS" in src
    assert "const ABY_WEATHER" in src
    assert "const ABY_FACTIONS" in src


def test_abyss_faction_and_weather_counts():
    # Guard the researched roster: 7 factions, 5 weathers, 7 tiers (T0–T6).
    src = (_ROOT / "static" / "js" / "abyss.js").read_text()
    for faction in ("triglavian", "roguedrones", "drifters", "sleepers",
                    "sansha", "angel", "edencom"):
        assert f'key:"{faction}"' in src, faction
    for weather in ("dark", "electrical", "exotic", "firestorm", "gamma"):
        assert f'key:"{weather}"' in src, weather
    # Tiers T0..T6.
    for n in range(7):
        assert f"n:{n}," in src, n


def test_abyss_every_faction_has_prob_and_spot():
    # Each faction needs a 7-element per-tier probability array and a
    # "spot the room" identification cue.
    import re
    src = (_ROOT / "static" / "js" / "abyss.js").read_text()
    # 7 prob arrays, each with 7 comma-separated numbers.
    prob_arrays = re.findall(r"prob:\[([0-9,\s]+)\]", src)
    assert len(prob_arrays) == 7, prob_arrays
    for arr in prob_arrays:
        assert len([x for x in arr.split(",") if x.strip() != ""]) == 7, arr
    assert src.count("spot:") == 7


def test_abyss_cites_spawn_sources():
    # Spawn-likelihood provenance and the corrected fit link must be present.
    html = lp_web.INDEX_HTML
    assert "qsna.eu" in html
    assert "abyssal.space" in html
    assert "caldarijoans.streamlit.app" in html
