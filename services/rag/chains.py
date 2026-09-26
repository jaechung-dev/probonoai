"""
LangChain LCEL chains and streaming generators for the legal RAG pipeline.

Provider selection: set CHAT_MODEL in .env.
  gpt-4o-mini          → OpenAI  (default)
  claude-haiku-4-5     → Anthropic
"""
import re
import json
import time
import asyncio
import logging
from typing import AsyncIterator

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

from services.core.settings import settings
from services.rag.prompts import PROMPT, PLAIN_ENGLISH_SYSTEM
from services.rag.retrievers import (
    LegislationRetriever,
    CaselawRetriever,
    CaseEventRetriever,
)

logger = logging.getLogger(__name__)

CHAT_MODEL = settings.CHAT_MODEL

# ── Lazy LLM — initialised on first use, not at import time ───────────────────

_llm = None


def get_llm():
    global _llm
    if _llm is not None:
        return _llm
    if CHAT_MODEL.startswith("claude-"):
        from langchain_anthropic import ChatAnthropic
        _llm = ChatAnthropic(model=CHAT_MODEL, temperature=0.1, max_tokens=2048)
    else:
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(model=CHAT_MODEL, temperature=0.1, streaming=True)
    return _llm

# ── Shared helpers ─────────────────────────────────────────────────────────────


def format_docs(docs: list[Document]) -> str:
    """Concatenate retrieved docs into a single context string with citation headers."""
    parts = []
    for d in docs:
        src = (
            d.metadata.get("citation")
            or d.metadata.get("case_name")
            or d.metadata.get("category", "")
        )
        parts.append(f"[{src}]\n{d.page_content}")
    return "\n\n---\n\n".join(parts)


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL)
_THINK_OPEN = "<think>"


def strip_think(text: str) -> str:
    """Remove <think>…</think> blocks emitted by reasoning models."""
    return _THINK_BLOCK_RE.sub("", text).strip()


def _strip_think_blocks(text: str) -> str:
    """Remove only *complete* <think>…</think> blocks, without trimming
    whitespace — keeps the result prefix-stable for incremental streaming."""
    return _THINK_BLOCK_RE.sub("", text)


def _safe_visible(buffer: str) -> str:
    """The portion of a partial stream buffer that is safe to reveal to an
    append-only client. Complete <think> blocks are removed; an unclosed
    <think> block (and any partially-written opening tag at the very end) is
    held back so reasoning text and half-written tags are never streamed and
    then need retracting."""
    text = _strip_think_blocks(buffer)
    open_idx = text.rfind(_THINK_OPEN)
    if open_idx != -1:                      # unclosed <think> — hold from here
        return text[:open_idx]
    for i in range(len(_THINK_OPEN) - 1, 0, -1):   # hold a partial "<think>" tag
        if text.endswith(_THINK_OPEN[:i]):
            return text[:-i]
    return text


# ── /ask streaming generators ─────────────────────────────────────────────────


async def stream_single(
    retriever,
    question: str,
    log_request_fn,
    user: str,
    source: str,
    k: int,
) -> AsyncIterator[str]:
    """
    Stream SSE tokens for a single-retriever /ask request.
    Yields ``data: {...}`` lines terminated with ``data: [DONE]``.
    """
    chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | PROMPT
        | get_llm()
        | StrOutputParser()
    )
    buffer = ""
    emitted = 0
    chunks = 0
    t0 = time.time()
    async for chunk in chain.astream(question):
        if not chunk:
            continue
        buffer += chunk
        chunks += 1
        safe = _safe_visible(buffer)
        if len(safe) > emitted:
            yield f"data: {json.dumps({'text': safe[emitted:]})}\n\n"
            emitted = len(safe)
    final = _strip_think_blocks(buffer)          # flush any held-back trailing text
    if len(final) > emitted:
        yield f"data: {json.dumps({'text': final[emitted:]})}\n\n"
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "ask question=%r source=%s k=%d user=%s elapsed_ms=%d chunks=%d",
        question[:120], source, k, user, elapsed_ms, chunks,
    )
    yield "data: [DONE]\n\n"
    asyncio.create_task(log_request_fn(
        endpoint="/ask",
        user_id=user,
        input_data={"question": question, "source": source, "k": k},
        output_data={"answer": strip_think(buffer)},
        elapsed_ms=elapsed_ms,
    ))


async def stream_both(
    leg_docs: list[Document],
    cas_docs: list[Document],
    question: str,
    log_request_fn,
    user: str,
    source: str,
    k: int,
) -> AsyncIterator[str]:
    """
    Stream SSE tokens for a dual-retriever (legislation + caselaw) /ask request.
    Accepts already-fetched doc lists so the caller controls retrieval.
    """
    docs = leg_docs + cas_docs
    context = format_docs(docs)
    chain = PROMPT | get_llm() | StrOutputParser()

    buffer = ""
    emitted = 0
    chunks = 0
    t0 = time.time()
    async for chunk in chain.astream({"context": context, "question": question}):
        if not chunk:
            continue
        buffer += chunk
        chunks += 1
        safe = _safe_visible(buffer)
        if len(safe) > emitted:
            yield f"data: {json.dumps({'text': safe[emitted:]})}\n\n"
            emitted = len(safe)
    final = _strip_think_blocks(buffer)          # flush any held-back trailing text
    if len(final) > emitted:
        yield f"data: {json.dumps({'text': final[emitted:]})}\n\n"
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "ask question=%r source=%s k=%d user=%s elapsed_ms=%d chunks=%d",
        question[:120], source, k, user, elapsed_ms, chunks,
    )
    yield "data: [DONE]\n\n"
    asyncio.create_task(log_request_fn(
        endpoint="/ask",
        user_id=user,
        input_data={"question": question, "source": source, "k": k},
        output_data={"answer": strip_think(buffer)},
        elapsed_ms=elapsed_ms,
    ))


async def stream_ask(
    lc_messages: list,
    sources: list[dict],
    question: str,
    source: str,
    case_id: str,
    k: int,
    log_request_fn,
    user: str,
) -> AsyncIterator[str]:
    """
    Stream SSE events for the /ask endpoint.
    Same wire format as stream_chat (sources event first, then typed token events)
    so MCP and frontend consumers share one parsing path.
    """
    yield f"data: {json.dumps({'type': 'sources', 'docs': sources})}\n\n"
    buffer = ""
    emitted = 0
    chunks = 0
    t0 = time.time()
    async for chunk in get_llm().astream(lc_messages):
        token = chunk.content if hasattr(chunk, "content") else str(chunk)
        if not token:
            continue
        buffer += token
        chunks += 1
        safe = _safe_visible(buffer)
        if len(safe) > emitted:
            yield f"data: {json.dumps({'type': 'token', 'text': safe[emitted:]})}\n\n"
            emitted = len(safe)
    final = _strip_think_blocks(buffer)
    if len(final) > emitted:
        yield f"data: {json.dumps({'type': 'token', 'text': final[emitted:]})}\n\n"
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "ask question=%r source=%s k=%d case_id=%s user=%s elapsed_ms=%d chunks=%d",
        question[:120], source, k, case_id or "", user, elapsed_ms, chunks,
    )
    yield "data: [DONE]\n\n"
    asyncio.create_task(log_request_fn(
        endpoint="/ask",
        user_id=user,
        input_data={"question": question, "source": source, "k": k, "case_id": case_id or ""},
        output_data={"answer": strip_think(buffer)},
        elapsed_ms=elapsed_ms,
    ))


async def stream_chat(
    lc_messages: list,
    sources: list[dict],
    question: str,
    messages_raw: list,
    case_id: str,
    k: int,
    log_request_fn,
    user: str,
) -> AsyncIterator[str]:
    """
    Stream SSE events for the /chat endpoint.
    Yields a ``sources`` event first, then ``token`` events, then [DONE].
    """
    yield f"data: {json.dumps({'type': 'sources', 'docs': sources})}\n\n"
    buffer = ""
    emitted = 0
    chunks = 0
    t0 = time.time()
    async for chunk in get_llm().astream(lc_messages):
        token = chunk.content if hasattr(chunk, "content") else str(chunk)
        if not token:
            continue
        buffer += token
        chunks += 1
        safe = _safe_visible(buffer)
        if len(safe) > emitted:
            yield f"data: {json.dumps({'type': 'token', 'text': safe[emitted:]})}\n\n"
            emitted = len(safe)
    final = _strip_think_blocks(buffer)          # flush any held-back trailing text
    if len(final) > emitted:
        yield f"data: {json.dumps({'type': 'token', 'text': final[emitted:]})}\n\n"
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "chat question=%r messages=%d case_id=%s user=%s elapsed_ms=%d chunks=%d",
        question[:120], len(messages_raw), case_id, user, elapsed_ms, chunks,
    )
    yield "data: [DONE]\n\n"
    asyncio.create_task(log_request_fn(
        endpoint="/chat",
        user_id=user,
        input_data={
            "question": question,
            "messages": messages_raw,
            "case_id": case_id,
            "k": k,
        },
        output_data={"answer": strip_think(buffer)},
        elapsed_ms=elapsed_ms,
    ))
