"""
Regression tests: v2 `events.case_id` is UUID, legacy `case_events.case_id` is TEXT.

Passing a non-UUID case_id (e.g. 'test-case-001') to a UUID column raised
"invalid input syntax for type uuid" in production, and the UNION in
collections() failed on uuid/text, which then crashed formatting "unknown".
"""
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from services.mcp.tests.test_server import _import_server

V2_CASE = "6f1c2a3e-0000-4000-8000-000000000001"


class _FakeCursor:
    """Records SQL; answers the v2 probe and returns canned rows."""

    def __init__(self, has_v2: bool, rows):
        self.has_v2 = has_v2
        self.rows = rows
        self.sql = []
        self._last = ""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchone(self):
        if "FROM events" in self._last and "LIMIT 1" in self._last:
            return (1,) if self.has_v2 else None
        return (0,)

    def fetchall(self):
        return self.rows


def _fake_db(cursor):
    @contextmanager
    def _db():
        class _Conn:
            def cursor(self_inner):
                return cursor
        yield _Conn()
    return _db


class TestFetchCaseIdTypes(unittest.TestCase):
    def setUp(self):
        self.srv = _import_server()

    def test_non_uuid_case_id_skips_v2_events_table(self):
        cur = _FakeCursor(has_v2=False, rows=[("2024-01-01", "Email", "Subject", "Summary")])
        with patch.object(self.srv, "_db", _fake_db(cur)), \
             patch.object(self.srv, "_require_scope", return_value="user-1"):
            out = self.srv.fetch("test-case-001", ctx=None)
        self.assertFalse(any("FROM events" in s for s in cur.sql),
                         "non-UUID case_id must never be compared to events.case_id (UUID)")
        self.assertTrue(any("FROM case_events" in s for s in cur.sql))
        self.assertIn("1 events", out)

    def test_uuid_case_id_with_v2_data_reads_events(self):
        cur = _FakeCursor(has_v2=True, rows=[("2024-01-01", "hearing", "Title", "Summary")])
        with patch.object(self.srv, "_db", _fake_db(cur)), \
             patch.object(self.srv, "_require_scope", return_value="user-1"):
            out = self.srv.fetch(V2_CASE, ctx=None)
        self.assertFalse(any("FROM case_events" in s for s in cur.sql))
        self.assertIn("hearing — Title", out)

    def test_uuid_case_id_without_v2_data_falls_back(self):
        cur = _FakeCursor(has_v2=False, rows=[("2024-01-01", "Email", "Subject", "Summary")])
        with patch.object(self.srv, "_db", _fake_db(cur)), \
             patch.object(self.srv, "_require_scope", return_value="user-1"):
            self.srv.fetch(V2_CASE, ctx=None)
        self.assertTrue(any("FROM case_events" in s for s in cur.sql))


class TestCollectionsCaseIdTypes(unittest.TestCase):
    def setUp(self):
        self.srv = _import_server()
        health = MagicMock(status_code=200, json=lambda: {"model": "test"})
        p = patch.object(self.srv.requests, "get", return_value=health)
        p.start()
        self.addCleanup(p.stop)

    def test_union_casts_uuid_case_id_to_text(self):
        cur = _FakeCursor(has_v2=False, rows=[])
        with patch.object(self.srv, "_db", _fake_db(cur)), \
             patch.object(self.srv, "_require_scope", return_value="user-1"):
            out = self.srv.collections(ctx=None)
        union = next(s for s in cur.sql if "UNION" in s)
        self.assertIn("case_id::text FROM events", union)
        self.assertIn("Available collections", out)

    def test_count_failure_does_not_crash_formatting(self):
        @contextmanager
        def _broken_db():
            raise RuntimeError("db down")
            yield  # pragma: no cover

        with patch.object(self.srv, "_db", _broken_db), \
             patch.object(self.srv, "_require_scope", return_value="user-1"):
            out = self.srv.collections(ctx=None)
        self.assertIn("legislation  : unknown", out)


if __name__ == "__main__":
    unittest.main()
