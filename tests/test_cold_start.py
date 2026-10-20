"""Offline tests: embedder skips the tiktoken length check; traces are flushed after streaming."""
import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

pytest.importorskip("langchain_openai")

from services.rag import chains, retrievers  # noqa: E402


def test_embedder_skips_token_length_check(monkeypatch):
    # With the check on, the first call of every new Lambda container downloads
    # tiktoken's vocabulary file (~3 s). Queries are short, so it is switched off.
    created = {}

    def fake_embeddings(**kwargs):
        created.update(kwargs)
        return object()

    monkeypatch.setattr(retrievers, "_embedder", None)
    monkeypatch.setattr(retrievers, "OpenAIEmbeddings", fake_embeddings)
    retrievers.get_embedder()
    assert created["check_embedding_ctx_length"] is False


class FakeLLM:
    async def astream(self, messages):
        class Chunk:
            content = "hello"
        yield Chunk()


async def _noop_log(**kwargs):
    return None


def _run_chat(monkeypatch, flush):
    monkeypatch.setattr(chains, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(chains, "_flush_traces", flush)

    async def consume():
        out = []
        async for ev in chains.stream_chat([], [], "q", [], "", 5, _noop_log, "anon"):
            out.append(ev)
        return out

    return asyncio.run(consume())


def test_stream_chat_flushes_traces_after_done(monkeypatch):
    order = []

    async def flush():
        order.append("flush")

    events = _run_chat(monkeypatch, flush)
    assert events[-1] == "data: [DONE]\n\n"   # the client already has its answer
    assert order == ["flush"]                  # and the flush ran before the generator ended


def test_flush_failure_never_breaks_the_stream(monkeypatch):
    monkeypatch.setattr(chains, "wait_for_all_tracers", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("langsmith.utils.tracing_is_enabled", lambda: True)
    asyncio.run(chains._flush_traces())  # must not raise


def test_flush_skipped_when_tracing_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(chains, "wait_for_all_tracers", lambda: called.append(1))
    monkeypatch.setattr("langsmith.utils.tracing_is_enabled", lambda: False)
    asyncio.run(chains._flush_traces())
    assert called == []
