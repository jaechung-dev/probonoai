"""
Smoke tests for services/rag/retrievers.py

No real DB or network calls — psycopg2.connect and OpenAIEmbeddings are mocked.
"""
import datetime
import re
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without real credentials
# ---------------------------------------------------------------------------

def _stub_openai_embeddings(*args, **kwargs):
    m = MagicMock()
    m.embed_query.return_value = [0.1] * 1536
    return m


# ---------------------------------------------------------------------------
# Connection mock helpers
# ---------------------------------------------------------------------------

def _make_mock_conn(rows, *, fetchone_rv=None):
    """Return a mock psycopg2 connection.

    fetchone_rv: what cursor.fetchone() returns for the v2 existence check
                 (None → legacy fallback; a tuple → v2 path taken).
    rows:        what cursor.fetchall() returns for the main data query.
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_rv
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ---------------------------------------------------------------------------
# Test: importability
# ---------------------------------------------------------------------------

class TestImport(unittest.TestCase):
    def test_legislation_retriever_importable(self):
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            from services.rag.retrievers import LegislationRetriever
        self.assertTrue(callable(LegislationRetriever))

    def test_caselaw_retriever_importable(self):
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            from services.rag.retrievers import CaselawRetriever
        self.assertTrue(callable(CaselawRetriever))

    def test_case_event_retriever_importable(self):
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            from services.rag.retrievers import CaseEventRetriever
        self.assertTrue(callable(CaseEventRetriever))

    def test_case_chunk_retriever_importable(self):
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            from services.rag.retrievers import CaseChunkRetriever
        self.assertTrue(callable(CaseChunkRetriever))


# ---------------------------------------------------------------------------
# Test: LegislationRetriever.retrieve() returns a list with correct shape
# ---------------------------------------------------------------------------

class TestLegislationRetriever(unittest.TestCase):
    def _rows(self):
        # (citation, text, score)
        return [
            ("Bail Act 2013 s 7", "The accused may be granted bail if …", 0.92),
            ("Bail Act 2013 s 14", "Conditions of bail include …", 0.87),
        ]

    def test_retrieve_returns_list(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import LegislationRetriever
            r = LegislationRetriever(k=2)
            docs = r.invoke("bail conditions")
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 2)

    def test_retrieve_result_has_page_content(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import LegislationRetriever
            r = LegislationRetriever(k=2)
            docs = r.invoke("bail conditions")
        self.assertTrue(docs[0].page_content)

    def test_retrieve_result_has_metadata(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import LegislationRetriever
            r = LegislationRetriever(k=2)
            docs = r.invoke("bail conditions")
        meta = docs[0].metadata
        self.assertIn("source", meta)
        self.assertIn("citation", meta)
        self.assertIn("score", meta)


# ---------------------------------------------------------------------------
# Test: CaselawRetriever.retrieve()
# ---------------------------------------------------------------------------

class TestCaselawRetriever(unittest.TestCase):
    def _rows(self):
        # (neutral_citation, title, text, score)
        return [
            ("[2019] NSWSC 42", "R v Smith", "The court held that …", 0.95),
        ]

    def test_retrieve_returns_list(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaselawRetriever
            r = CaselawRetriever(k=1)
            docs = r.invoke("domestic violence")
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 1)

    def test_retrieve_result_has_page_content_and_metadata(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaselawRetriever
            r = CaselawRetriever(k=1)
            docs = r.invoke("domestic violence")
        self.assertTrue(docs[0].page_content)
        self.assertEqual(docs[0].metadata["source"], "caselaw")


# ---------------------------------------------------------------------------
# Test: CaseChunkRetriever — v2 path
# ---------------------------------------------------------------------------

_CASE_UUID = "00000000-0000-0000-0000-000000000001"
_USER_UUID = "00000000-0000-0000-0000-0000000000aa"
_DOC_UUID  = "11111111-1111-1111-1111-111111111111"


def _v2_chunk_rows():
    # (content, score, doc_id, filename, title, doc_type,
    #  occurred_on, sent_at, source_origin, redaction_status, page_no)
    return [
        (
            "The applicant sought bail on 15 March.",
            0.85,
            _DOC_UUID,
            "bail-decision.pdf",
            "Bail Decision",
            "decision",
            datetime.date(2024, 3, 15),
            None,
            "received",
            "none",
            3,
        ),
    ]


class TestCaseChunkRetriever(unittest.TestCase):

    def test_v2_path_returns_documents(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_chunk_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseChunkRetriever
            r = CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID)
            docs = r.invoke("bail")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].page_content, "The applicant sought bail on 15 March.")

    def test_v2_metadata_shape(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_chunk_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        meta = docs[0].metadata
        self.assertEqual(meta["source"], "case_chunk")
        self.assertIn("score", meta)
        self.assertEqual(meta["document_id"], _DOC_UUID)
        self.assertEqual(meta["filename"], "bail-decision.pdf")
        self.assertEqual(meta["doc_type"], "decision")
        self.assertEqual(meta["occurred_on"], "2024-03-15")
        self.assertEqual(meta["page_no"], 3)
        self.assertEqual(meta["redaction_status"], "none")

    def test_v2_citation_includes_filename_and_page(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_chunk_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertEqual(docs[0].metadata["citation"], "bail-decision.pdf p.3")

    def test_v2_citation_filename_only_when_no_page_no(self):
        rows = [list(_v2_chunk_rows()[0])]
        rows[0] = tuple(rows[0][:-1]) + (None,)  # page_no = None
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(rows, fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertEqual(docs[0].metadata["citation"], "bail-decision.pdf")

    def test_v2_sql_filters_non_ready_and_dup_pages(self):
        """Verify the v2 query requires ingest_status=ready and excludes duplicate pages."""
        mock_conn = _make_mock_conn(_v2_chunk_rows(), fetchone_rv=(1,))
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            with patch("psycopg2.connect", return_value=mock_conn):
                from services.rag.retrievers import CaseChunkRetriever
                CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        cursor = mock_conn.cursor.return_value
        # Second execute call is the main SELECT (first is the v2 existence check)
        main_sql = cursor.execute.call_args_list[1][0][0]
        self.assertIn("ingest_status = 'ready'", main_sql)
        self.assertIn("duplicate_of_page_id IS NULL", main_sql)

    def test_v1_fallback_when_no_v2_data(self):
        # Legacy rows: (content, metadata_dict, score)
        legacy_rows = [("Old chunk text", {"source_key": "uploads/x.pdf"}, 0.77)]
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(legacy_rows, fetchone_rv=None)),
        ):
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("old")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].page_content, "Old chunk text")
        self.assertEqual(docs[0].metadata["source"], "case_chunk")
        self.assertAlmostEqual(docs[0].metadata["score"], 0.77)

    def test_empty_user_id_returns_empty_without_db(self):
        with patch("psycopg2.connect") as mock_connect:
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id=_CASE_UUID).invoke("anything")
        self.assertEqual(docs, [])
        mock_connect.assert_not_called()

    def test_non_uuid_case_id_returns_empty_without_db(self):
        with patch("psycopg2.connect") as mock_connect:
            from services.rag.retrievers import CaseChunkRetriever
            docs = CaseChunkRetriever(k=1, case_id="not-a-uuid", user_id=_USER_UUID).invoke("anything")
        self.assertEqual(docs, [])
        mock_connect.assert_not_called()

    def test_scoping_user_id_included_in_query(self):
        """The user_id must appear in every query so cross-user data can never leak."""
        mock_conn = _make_mock_conn([], fetchone_rv=None)
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            with patch("psycopg2.connect", return_value=mock_conn):
                from services.rag.retrievers import CaseChunkRetriever
                CaseChunkRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("q")
        cursor = mock_conn.cursor.return_value
        all_params = [str(c) for c in cursor.execute.call_args_list]
        self.assertTrue(any(_USER_UUID in p for p in all_params),
                        "user_id must be bound in at least one query")


# ---------------------------------------------------------------------------
# Test: CaseEventRetriever — legacy path (case_events; backward compat)
# ---------------------------------------------------------------------------

class TestCaseEventRetriever(unittest.TestCase):
    def _rows(self):
        # Legacy shape: (date, category, event_type, subject, content, score)
        return [
            ("2023-04-01", "Hearing", "Mention", "Bail application", "Judge granted bail", 0.88),
        ]

    def test_retrieve_returns_list(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaseEventRetriever
            r = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID)
            docs = r.invoke("bail hearing")
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 1)

    def test_retrieve_result_format(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaseEventRetriever
            r = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID)
            docs = r.invoke("bail hearing")
        # Legacy path includes category and subject in page_content
        self.assertIn("Hearing", docs[0].page_content)
        self.assertEqual(docs[0].metadata["source"], "case_event")
        self.assertIn("date", docs[0].metadata)

    def test_requires_user_id(self):
        with patch("psycopg2.connect") as conn:
            from services.rag.retrievers import CaseEventRetriever
            r = CaseEventRetriever(k=1, case_id=_CASE_UUID)
            self.assertEqual(r.invoke("anything"), [])
            conn.assert_not_called()


# ---------------------------------------------------------------------------
# Test: CaseEventRetriever — v2 path (events table)
# ---------------------------------------------------------------------------

def _v2_event_rows():
    # (title, summary, excerpt, event_type,
    #  occurred_on, occurred_precision, score, doc_id, filename, page_no)
    return [
        (
            "Bail application hearing",
            "Judge granted bail subject to conditions including reporting requirements.",
            "The applicant appeared before Magistrate Jones.",
            "Mention",
            datetime.date(2023, 4, 1),
            "day",
            0.88,
            _DOC_UUID,
            "hearing-transcript.pdf",
            5,
        ),
    ]


class TestCaseEventRetrieverV2(unittest.TestCase):

    def test_v2_path_returns_documents(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_event_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertEqual(len(docs), 1)

    def test_v2_page_content_contains_title_and_type(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_event_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        content = docs[0].page_content
        self.assertIn("Mention", content)
        self.assertIn("Bail application hearing", content)
        self.assertIn("2023-04-01", content)

    def test_v2_page_content_includes_summary_and_excerpt(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_event_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertIn("conditions", docs[0].page_content)   # from summary
        self.assertIn("Magistrate", docs[0].page_content)   # from excerpt

    def test_v2_metadata_shape(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(_v2_event_rows(), fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        meta = docs[0].metadata
        self.assertEqual(meta["source"], "case_event")
        self.assertIn("score", meta)
        self.assertEqual(meta["document_id"], _DOC_UUID)
        self.assertEqual(meta["filename"], "hearing-transcript.pdf")
        self.assertEqual(meta["page_no"], 5)
        self.assertEqual(meta["citation"], "hearing-transcript.pdf p.5")

    def test_v2_date_precision_month(self):
        rows = list(_v2_event_rows())
        # Change precision to 'month'
        row = rows[0]
        rows[0] = row[:4] + (datetime.date(2023, 4, 1), "month") + row[6:]
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(rows, fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertIn("April 2023", docs[0].page_content)
        self.assertNotIn("2023-04-01", docs[0].page_content)

    def test_v2_date_precision_approx(self):
        rows = list(_v2_event_rows())
        row = rows[0]
        rows[0] = row[:4] + (datetime.date(2023, 4, 1), "approx") + row[6:]
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(rows, fetchone_rv=(1,))),
        ):
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("bail")
        self.assertIn("approx. 2023", docs[0].page_content)

    def test_v2_requires_user_id(self):
        with patch("psycopg2.connect") as mock_connect:
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id=_CASE_UUID).invoke("bail")
        self.assertEqual(docs, [])
        mock_connect.assert_not_called()

    def test_v2_non_uuid_case_id_returns_empty(self):
        with patch("psycopg2.connect") as mock_connect:
            from services.rag.retrievers import CaseEventRetriever
            docs = CaseEventRetriever(k=1, case_id="INVALID", user_id=_USER_UUID).invoke("bail")
        self.assertEqual(docs, [])
        mock_connect.assert_not_called()

    def test_v2_scoping_user_id_in_query(self):
        mock_conn = _make_mock_conn([], fetchone_rv=None)
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            with patch("psycopg2.connect", return_value=mock_conn):
                from services.rag.retrievers import CaseEventRetriever
                CaseEventRetriever(k=1, case_id=_CASE_UUID, user_id=_USER_UUID).invoke("q")
        cursor = mock_conn.cursor.return_value
        all_params = [str(c) for c in cursor.execute.call_args_list]
        self.assertTrue(any(_USER_UUID in p for p in all_params))


# ---------------------------------------------------------------------------
# Test: _format_occurred helper
# ---------------------------------------------------------------------------

class TestFormatOccurred(unittest.TestCase):
    def setUp(self):
        with patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings):
            from services.rag import retrievers
            self._fmt = retrievers._format_occurred

    def test_day_precision(self):
        d = datetime.date(2024, 3, 15)
        self.assertEqual(self._fmt(d, "day"), "2024-03-15")

    def test_month_precision(self):
        d = datetime.date(2024, 3, 15)
        self.assertEqual(self._fmt(d, "month"), "March 2024")

    def test_approx_precision(self):
        d = datetime.date(2024, 3, 15)
        self.assertIn("2024", self._fmt(d, "approx"))
        self.assertIn("approx", self._fmt(d, "approx"))

    def test_year_precision(self):
        d = datetime.date(2024, 3, 15)
        self.assertIn("2024", self._fmt(d, "year"))

    def test_none_returns_empty(self):
        self.assertEqual(self._fmt(None, "day"), "")

    def test_string_date_parsed(self):
        self.assertEqual(self._fmt("2024-03-15", "day"), "2024-03-15")


if __name__ == "__main__":
    unittest.main()
