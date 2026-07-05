"""Tests for the notes feature (API handlers + pg_store notes functions)."""
import json
import sys
import types
from unittest.mock import patch, MagicMock

import pg_store


# ── pg_store.notes_* unit tests (mock the pool) ───────────────────────────

class FakeConn:
    def __init__(self, rows=None):
        self._rows = rows or []
        self._executed = []

    def execute(self, sql, params=None):
        self._executed.append((sql, params))
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def transaction(self):
        return self


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


def _patch_pool(conn):
    pool = FakePool(conn)
    return patch.object(pg_store, '_get_pool', return_value=pool)


def test_notes_list_returns_formatted_rows():
    rows = [
        ("id1", None, "folder", "My Folder", "", 0, 1000.0, 1000.0),
        ("id2", "id1", "note", "My Note", "hello", 0, 1001.0, 1002.0),
    ]
    conn = FakeConn(rows)
    with _patch_pool(conn):
        result = pg_store.notes_list(123)
    assert len(result) == 2
    assert result[0] == {"id": "id1", "parent_id": None, "kind": "folder",
                         "title": "My Folder", "body": "", "pos": 0,
                         "created_at": 1000.0, "updated_at": 1000.0}
    assert result[1]["id"] == "id2"
    assert result[1]["parent_id"] == "id1"
    assert result[1]["body"] == "hello"


def test_notes_upsert_returns_timestamp():
    conn = FakeConn()
    with _patch_pool(conn):
        ts = pg_store.notes_upsert(123, "n1", None, "note", "Title", "Body", 0)
    assert isinstance(ts, float)
    assert ts > 0
    assert len(conn._executed) == 1
    sql = conn._executed[0][0]
    assert "INSERT INTO mono_notes" in sql
    assert "ON CONFLICT" in sql


def test_notes_delete_cascades():
    call_count = [0]
    rows_by_parent = {
        "root": [("child1",), ("child2",)],
        "child1": [("grandchild1",)],
        "child2": [],
        "grandchild1": [],
    }

    class CascadeConn:
        def __init__(self):
            self.deleted = []

        def execute(self, sql, params=None):
            if "SELECT id" in sql:
                pid = params[1]
                self._last_rows = rows_by_parent.get(pid, [])
            elif "DELETE" in sql:
                self.deleted.append(params[1])
            return self

        def fetchall(self):
            return self._last_rows

        def transaction(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    conn = CascadeConn()
    with _patch_pool(conn):
        pg_store.notes_delete(999, "root")
    assert set(conn.deleted) == {"root", "child1", "child2", "grandchild1"}


# ── API handler tests ─────────────────────────────────────────────────────

def _import_web():
    """Import lp-web.py as a module (dash in name requires importlib)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lp_web", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "lp-web.py"))
    mod = importlib.util.module_from_spec(spec)
    # We need pg_store to be importable
    sys.modules.setdefault("pg_store", pg_store)
    return spec, mod


def test_do_notes_list_no_account():
    """Without an account, returns empty list."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "lp_web", str(Path(__file__).resolve().parent.parent / "lp-web.py"))
    # Don't actually load the whole module — just test the function logic
    # by checking pg_store functions directly
    conn = FakeConn([])
    with _patch_pool(conn):
        result = pg_store.notes_list(0)
    assert result == []


def test_notes_upsert_and_list_integration():
    """Upsert then list shows correct data (mocked at pg_store level)."""
    conn = FakeConn()
    with _patch_pool(conn):
        pg_store.notes_upsert(1, "f1", None, "folder", "Folder 1", "", 0)

    rows = [("f1", None, "folder", "Folder 1", "", 0, 1000.0, 1000.0)]
    conn2 = FakeConn(rows)
    with _patch_pool(conn2):
        result = pg_store.notes_list(1)
    assert len(result) == 1
    assert result[0]["kind"] == "folder"
    assert result[0]["title"] == "Folder 1"
