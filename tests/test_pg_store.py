"""Unit tests for the Postgres persistence gate (pg_store).

These cover the pure logic that runs without a database — DSN normalisation and
the DATABASE_URL feature gate. The actual read/write paths need a live Postgres
and are exercised end-to-end against the deployed instance.
"""
import pg_store


def test_normalize_dsn_strips_sqlalchemy_driver():
    assert pg_store._normalize_dsn(
        "postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert pg_store._normalize_dsn(
        "postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"


def test_normalize_dsn_upgrades_legacy_postgres_scheme():
    assert pg_store._normalize_dsn("postgres://u:p@h/db") == "postgresql://u:p@h/db"


def test_normalize_dsn_passes_plain_url_through():
    assert pg_store._normalize_dsn(
        "postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_enabled_reflects_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert pg_store.enabled() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    assert pg_store.enabled() is True
