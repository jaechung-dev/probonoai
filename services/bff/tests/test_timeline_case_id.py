"""
Regression tests: BFF /case/{id}/timeline must not compare a non-UUID case_id
against the v2 `events.case_id` UUID column (Postgres raises
"invalid input syntax for type uuid").
"""
import unittest
from unittest.mock import MagicMock, patch

from services.bff.tests.test_bff import _import_app

V2_CASE = "6f1c2a3e-0000-4000-8000-000000000001"
LEGACY_ROW = ("2024-01-01", "Email", "email", "Subject", "Summary", "Content", [])


def _conn(has_v2: bool, rows):
    cur = MagicMock()
    cur.fetchone.return_value = (1,) if has_v2 else None
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql(cur):
    return [c.args[0] for c in cur.execute.call_args_list]


class TestTimelineCaseIdTypes(unittest.TestCase):
    def setUp(self):
        self.bff = _import_app()

    def test_non_uuid_case_id_skips_v2_events_table(self):
        conn, cur = _conn(has_v2=False, rows=[LEGACY_ROW])
        with patch.object(self.bff.psycopg2, "connect", return_value=conn):
            body = self.bff.timeline("test-case-001")
        self.assertFalse(any("FROM events" in s for s in _sql(cur)))
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["events"][0]["category"], "Email")

    def test_uuid_case_id_with_v2_data_reads_events(self):
        conn, cur = _conn(has_v2=True, rows=[("2024-01-01", "hearing", "Title", "Summary", "Excerpt")])
        with patch.object(self.bff.psycopg2, "connect", return_value=conn):
            body = self.bff.timeline(V2_CASE)
        self.assertFalse(any("FROM case_events" in s for s in _sql(cur)))
        self.assertEqual(body["events"][0]["event_type"], "hearing")

    def test_uuid_case_id_without_v2_data_falls_back(self):
        conn, cur = _conn(has_v2=False, rows=[LEGACY_ROW])
        with patch.object(self.bff.psycopg2, "connect", return_value=conn):
            self.bff.timeline(V2_CASE)
        self.assertTrue(any("FROM case_events" in s for s in _sql(cur)))


if __name__ == "__main__":
    unittest.main()
