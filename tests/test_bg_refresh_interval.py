"""
Tests for _bg_refresh_interval: the background-sync cadence is env-configurable
via BG_REFRESH_INTERVAL_SECS, defaulting to 300s with a 60s floor and a safe
fallback on garbage input.
"""
from pathlib import Path

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "lp_web", Path(__file__).resolve().parent.parent / "lp-web.py")
lp_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp_web)


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("BG_REFRESH_INTERVAL_SECS", raising=False)
    assert lp_web._bg_refresh_interval() == 300


def test_blank_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BG_REFRESH_INTERVAL_SECS", "   ")
    assert lp_web._bg_refresh_interval() == 300


def test_custom_value_honoured(monkeypatch):
    monkeypatch.setenv("BG_REFRESH_INTERVAL_SECS", "900")
    assert lp_web._bg_refresh_interval() == 900


def test_floored_at_60(monkeypatch):
    monkeypatch.setenv("BG_REFRESH_INTERVAL_SECS", "5")
    assert lp_web._bg_refresh_interval() == 60


def test_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BG_REFRESH_INTERVAL_SECS", "soon")
    assert lp_web._bg_refresh_interval() == 300
