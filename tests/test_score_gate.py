"""Offline unit tests for the retrieval score gate in services/ai/main.py.

No database, no LLM, no network: retrievers and the stream function are replaced
with fakes. (The other files in tests/ are smoke tests against the live API.)
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("JWT_SECRET", "test-secret")

pytest.importorskip("fastapi")
pytest.importorskip("langchain_core")

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from services.ai import main  # noqa: E402


def doc(citation, score, text="text", source="legislation"):
    return Document(page_content=text, metadata={"citation": citation, "score": score, "source": source})


def test_gate_drops_weak_hits_keeps_threshold_and_order():
    docs = [doc("A", 0.80), doc("B", main.MIN_LAW_SCORE), doc("C", main.MIN_LAW_SCORE - 0.01)]
    assert [d.metadata["citation"] for d in main._gate_law_docs(docs)] == ["A", "B"]


def test_gate_removes_exact_duplicates_only():
    docs = [doc("A", 0.9, "same"), doc("A", 0.9, "same"), doc("A", 0.9, "different")]
    assert len(main._gate_law_docs(docs)) == 2


@pytest.fixture
def client(monkeypatch):
    captured = {}

    class FakeLaw:
        scores = [0.30, 0.31]

        def __init__(self, **kw):
            pass

        def invoke(self, q):
            return [doc(f"Law {i}", s) for i, s in enumerate(self.scores)]

    class FakeCase(FakeLaw):
        scores = [0.25]

    async def fake_stream(lc_messages, sources, *a, **kw):
        captured["system"] = lc_messages[0].content
        captured["sources"] = sources
        yield "data: [DONE]\n\n"

    async def no_quota(request):
        return None

    monkeypatch.setattr(main, "LegislationRetriever", FakeLaw)
    monkeypatch.setattr(main, "CaselawRetriever", FakeCase)
    monkeypatch.setattr(main, "_stream_chat", fake_stream)
    monkeypatch.setattr(main, "_enforce_anon_llm_quota", no_quota)
    monkeypatch.setattr(main, "embed_query_cached", lambda q: ())
    c = TestClient(main.app)
    c.captured, c.FakeLaw, c.FakeCase = captured, FakeLaw, FakeCase
    return c


def test_chat_weak_hits_are_dropped_and_warning_added(client):
    r = client.post("/chat", json={"question": "what is the maximum sentence of cigarette smuggling?"})
    assert r.status_code == 200
    assert client.captured["sources"] == []
    assert main.NO_LAW_SOURCE_WARNING in client.captured["system"]


def test_chat_relevant_hits_pass_without_warning(client):
    client.FakeLaw.scores = [0.70, 0.30]
    r = client.post("/chat", json={"question": "How much notice for a rent increase?"})
    assert r.status_code == 200
    assert [s["citation"] for s in client.captured["sources"]] == ["Law 0"]
    assert main.NO_LAW_SOURCE_WARNING not in client.captured["system"]
