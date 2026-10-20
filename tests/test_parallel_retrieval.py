"""Offline tests: retrieval runs concurrently and the question is embedded once."""
import asyncio
import os
import threading

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("JWT_SECRET", "test-secret")

pytest.importorskip("fastapi")
pytest.importorskip("langchain_core")

from langchain_core.documents import Document  # noqa: E402

from services.ai import main  # noqa: E402
from services.rag import retrievers  # noqa: E402


class Waiter:
    """Fake retriever that only returns once every other waiter has started.

    With sequential execution the barrier times out (BrokenBarrierError), so the
    test fails deterministically instead of depending on wall-clock timing.
    """

    def __init__(self, barrier, name):
        self.barrier, self.name = barrier, name

    def invoke(self, q):
        self.barrier.wait(timeout=5)
        return [Document(page_content=self.name, metadata={"citation": self.name, "score": 0.9})]


def test_retrievers_and_case_lookup_run_concurrently(monkeypatch):
    barrier = threading.Barrier(3)  # legislation + caselaw + case lookup
    monkeypatch.setattr(main, "embed_query_cached", lambda q: ())

    def fake_case(case_id, user, q, k):
        barrier.wait(timeout=5)
        return [Document(page_content="case", metadata={"citation": "case", "score": 0.9})]

    monkeypatch.setattr(main, "_fetch_case_docs", fake_case)
    law, case = asyncio.run(
        main._retrieve_concurrently("q", [Waiter(barrier, "law"), Waiter(barrier, "cases")], ("cid", "u", 5))
    )
    assert [d.metadata["citation"] for d in law] == ["law", "cases"]  # order preserved
    assert [d.metadata["citation"] for d in case] == ["case"]


def test_no_case_args_means_no_case_docs(monkeypatch):
    monkeypatch.setattr(main, "embed_query_cached", lambda q: ())
    barrier = threading.Barrier(1)
    law, case = asyncio.run(main._retrieve_concurrently("q", [Waiter(barrier, "law")], None))
    assert len(law) == 1 and case == []


def test_question_is_embedded_once(monkeypatch):
    calls = []

    class FakeEmbedder:
        def embed_query(self, q):
            calls.append(q)
            return [0.1, 0.2]

    retrievers.embed_query_cached.cache_clear()
    monkeypatch.setattr(retrievers, "get_embedder", lambda: FakeEmbedder())
    a = retrievers.embed_query_cached("same question")
    b = retrievers.embed_query_cached("same question")
    assert a == b == (0.1, 0.2)
    assert calls == ["same question"]
    retrievers.embed_query_cached.cache_clear()
