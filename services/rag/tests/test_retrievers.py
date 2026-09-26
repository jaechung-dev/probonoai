"""
Smoke tests for services/rag/retrievers.py

No real DB or network calls — psycopg2.connect and OpenAIEmbeddings are mocked.
"""
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


# ---------------------------------------------------------------------------
# Helpers shared across the retrieve() tests
# ---------------------------------------------------------------------------

def _make_mock_conn(rows):
    """Return a mock psycopg2 connection whose cursor yields *rows*."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


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
# Test: CaseEventRetriever.retrieve()
# ---------------------------------------------------------------------------

class TestCaseEventRetriever(unittest.TestCase):
    def _rows(self):
        # (date, category, event_type, subject, content, score)
        return [
            ("2023-04-01", "Hearing", "Mention", "Bail application", "Judge granted bail", 0.88),
        ]

    def test_retrieve_returns_list(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaseEventRetriever
            r = CaseEventRetriever(k=1, case_id="00000000-0000-0000-0000-000000000001")
            docs = r.invoke("bail hearing")
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 1)

    def test_retrieve_result_format(self):
        with (
            patch("langchain_openai.OpenAIEmbeddings", side_effect=_stub_openai_embeddings),
            patch("psycopg2.connect", return_value=_make_mock_conn(self._rows())),
        ):
            from services.rag.retrievers import CaseEventRetriever
            r = CaseEventRetriever(k=1, case_id="00000000-0000-0000-0000-000000000001")
            docs = r.invoke("bail hearing")
        self.assertIn("Hearing", docs[0].page_content)
        self.assertEqual(docs[0].metadata["source"], "case_event")
        self.assertIn("date", docs[0].metadata)


if __name__ == "__main__":
    unittest.main()
