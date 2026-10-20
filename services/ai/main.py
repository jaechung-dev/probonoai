"""
AI Lambda — search, ask, chat.
Full LangChain + OpenAI stack. Cold start ~3s.
Routes: /search  /ask  /chat
"""
import os
import json
import asyncio
import logging
import time
from typing import Any

import psycopg2
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from jose import jwt
from mangum import Mangum
from pydantic import BaseModel, field_validator

from services.core.settings import settings
from services.core.middleware import AccessLogMiddleware
from services.core import ratelimit

JWT_SECRET = settings.JWT_SECRET
JWT_ALG    = settings.JWT_ALG
from services.rag.retrievers import (
    LegislationRetriever,
    CaselawRetriever,
    CaseEventRetriever,
    CaseChunkRetriever,
    DSN,
    EMBED_MODEL,
    embed_query_cached,
)
from services.rag.chains import (
    format_docs,
    stream_ask as _stream_ask,
    stream_chat as _stream_chat,
    CHAT_MODEL,
)
from services.rag.prompts import PLAIN_ENGLISH_SYSTEM
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, trim_messages

logger = logging.getLogger(__name__)

# ── Pydantic models ────────────────────────────────────────────────────────────


VALID_SOURCES = {"legislation", "caselaw", "case_events", "both"}
VALID_ROLES   = {"user", "assistant"}


class SearchRequest(BaseModel):
    query: str
    source: str = "legislation"
    jurisdiction: str = "NSW"
    case_id: str = ""
    k: int = 5

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}")
        return v


class AskRequest(BaseModel):
    question: str
    source: str = "legislation"
    jurisdiction: str = "NSW"
    case_id: str = ""
    k: int = 4

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}")
        return v


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")
        return v


class ChatRequest(BaseModel):
    question: str
    messages: list[ChatMessage] = []
    case_id: str | None = None
    k: int = 5


# ── Auth helpers ───────────────────────────────────────────────────────────────


def _get_user_from_header(authorization: str = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anon"
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=[JWT_ALG], audience="probonoai-api")
        return claims.get("sub", "anon")
    except Exception:
        return "anon"


# ── Anonymous quotas ───────────────────────────────────────────────────────────
# The frontend's guest limit (frontend/hooks/useGuestQuota.ts) lives in
# localStorage and is bypassed by any direct API call, so it is enforced here
# per client IP. request.client is API Gateway's requestContext sourceIp (via
# Mangum), which the caller cannot spoof, unlike X-Forwarded-For.
# 401 (not 429) on the LLM endpoints so the frontend shows its register/log-in
# gate. Logged-in users are not counted; API Gateway throttles everyone.

ANON_LLM_LIMIT     = int(os.getenv("ANON_LLM_LIMIT", "2"))        # /chat + /ask combined
ANON_LLM_WINDOW    = int(os.getenv("ANON_LLM_WINDOW", "86400"))   # 24h
ANON_SEARCH_LIMIT  = int(os.getenv("ANON_SEARCH_LIMIT", "30"))
ANON_SEARCH_WINDOW = int(os.getenv("ANON_SEARCH_WINDOW", "3600"))  # 1h


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _enforce_anon_llm_quota(request: Request) -> None:
    if not await ratelimit.hit(f"anon-llm:{_client_ip(request)}", ANON_LLM_LIMIT, ANON_LLM_WINDOW):
        raise HTTPException(401, "Free question limit reached. Please register or log in to continue.")


async def _enforce_anon_search_quota(request: Request) -> None:
    if not await ratelimit.hit(f"anon-search:{_client_ip(request)}", ANON_SEARCH_LIMIT, ANON_SEARCH_WINDOW):
        raise HTTPException(429, "Too many searches. Please wait a while or log in.")


def _require_case_owner(case_id: str, user_id: str) -> None:
    """Verify the caller owns case_id. Checks the v2 cases table first, then
    falls back to case_intakes. Raises 401 for anonymous callers, 403 if the
    case belongs to someone else, 404 if the case does not exist."""
    if user_id == "anon":
        raise HTTPException(status_code=401, detail="Authentication required for case data")
    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM cases WHERE id::text = %s LIMIT 1", (case_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT user_id FROM case_intakes WHERE id::text = %s", (case_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    if str(row[0]) != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Audit logging ──────────────────────────────────────────────────────────────


def _write_request_log(
    endpoint: str, user_id: str, input_data: dict,
    output_data: dict, elapsed_ms: int,
) -> None:
    try:
        conn = psycopg2.connect(settings.DATABASE_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO request_logs (endpoint, user_id, input, output, elapsed_ms) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s)",
                (endpoint, user_id, json.dumps(input_data), json.dumps(output_data), elapsed_ms),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("request_log failed: %s", e)


async def _log_request(
    endpoint: str, user_id: str, input_data: dict,
    output_data: dict, elapsed_ms: int,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_request_log, endpoint, user_id, input_data, output_data, elapsed_ms
    )


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Legal Intelligence — AI", version="1.0")

app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MIN_CASE_SCORE = settings.MIN_CASE_SCORE
MIN_LAW_SCORE = settings.MIN_LAW_SCORE

# Added to the system prompt when no legislation/caselaw chunk passes the score
# gate. The model may still answer from general knowledge (product decision in
# PLAIN_ENGLISH_SYSTEM) but must say the answer is not from our database.
NO_LAW_SOURCE_WARNING = (
    "\n\nIMPORTANT: Our NSW legal database returned nothing relevant to this question. "
    "You may still give general information from your own knowledge, but say clearly that it is "
    "not drawn from our legislation database and could not be checked against it. Do not state "
    "specific section numbers, penalties or time limits as certain; say they should be confirmed "
    "on legislation.nsw.gov.au or with a lawyer, and mention if the law may be Commonwealth "
    "rather than NSW."
)


def _dedupe(docs):
    """Drop exact duplicate chunks (same citation and text), keeping order."""
    seen, out = set(), []
    for d in docs:
        key = (d.metadata.get("citation"), d.page_content)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _gate_law_docs(docs):
    """Keep legislation/caselaw hits at or above MIN_LAW_SCORE, without duplicates."""
    return _dedupe([d for d in docs if d.metadata.get("score", 0) >= MIN_LAW_SCORE])


async def _retrieve_concurrently(question, retrievers, case_args=None):
    """Run the retrievers and the optional case-document lookup in parallel threads.

    The question is embedded once first so every retriever hits the embedding cache.
    Returns (law_docs, case_docs); case_args is (case_id, user, k) or None.
    """
    await asyncio.to_thread(embed_query_cached, question)
    tasks = [asyncio.to_thread(r.invoke, question) for r in retrievers]
    if case_args:
        case_id, user, k = case_args
        tasks.append(asyncio.to_thread(_fetch_case_docs, case_id, user, question, k))
    results = list(await asyncio.gather(*tasks))
    case_docs = results.pop() if case_args else []
    return [d for docs in results for d in docs], case_docs


def _fetch_case_docs(case_id: str, user: str, question: str, k: int):
    """Retrieve and score-gate case document chunks. Enforces auth; cross-user case_id yields []."""
    if user == "anon":
        raise HTTPException(status_code=401, detail="Authentication required to query case documents")
    raw = CaseChunkRetriever(case_id=case_id, user_id=user, k=k).invoke(question)
    return _dedupe([d for d in raw if d.metadata.get("score", 0) >= MIN_CASE_SCORE])


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.post("/search")
async def search(
    req: SearchRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user = _get_user_from_header(authorization)
    if user == "anon":
        await _enforce_anon_search_quota(request)
    if req.source == "caselaw":
        retriever = CaselawRetriever(k=req.k)
    elif req.source == "case_events":
        # Case events are private; verify ownership when a case is specified.
        cid = req.case_id or ""
        if cid:
            _require_case_owner(cid, user)
        retriever = CaseEventRetriever(k=req.k, case_id=cid, user_id=user)
    else:
        retriever = LegislationRetriever(k=req.k, jurisdiction=req.jurisdiction)

    t0 = time.time()
    docs = retriever.invoke(req.query)
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info("search query=%r source=%s results=%d ms=%d user=%s", req.query, req.source, len(docs), elapsed_ms, user)

    results = [{"content": d.page_content[:600], "metadata": d.metadata} for d in docs]

    asyncio.create_task(_log_request(
        endpoint="/search",
        user_id=user,
        input_data={"query": req.query, "source": req.source, "k": req.k},
        output_data={"results": results, "count": len(results)},
        elapsed_ms=elapsed_ms,
    ))

    return {"query": req.query, "source": req.source, "results": results}


@app.post("/ask")
async def ask(
    req: AskRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    user = _get_user_from_header(authorization)
    if user == "anon":
        await _enforce_anon_llm_quota(request)

    # Retrieve base docs for the requested source.
    if req.source == "both":
        retrievers = [
            LegislationRetriever(k=req.k // 2 + 1, jurisdiction=req.jurisdiction),
            CaselawRetriever(k=req.k // 2 + 1),
        ]
    elif req.source == "caselaw":
        retrievers = [CaselawRetriever(k=req.k)]
    elif req.source == "case_events":
        cid = req.case_id or ""
        if cid:
            _require_case_owner(cid, user)
        retrievers = [CaseEventRetriever(k=req.k, case_id=cid, user_id=user)]
    else:
        retrievers = [LegislationRetriever(k=req.k, jurisdiction=req.jurisdiction)]

    # Prepend user's case document chunks when case_id is provided. Retrieval runs
    # concurrently: the law retrievers and the case lookup do not depend on each other.
    want_case_docs = bool(req.case_id) and req.source != "case_events"
    all_docs, case_docs = await _retrieve_concurrently(
        req.question, retrievers, (req.case_id, user, req.k) if want_case_docs else None
    )

    gate_law = req.source != "case_events"
    if gate_law:
        all_docs = _gate_law_docs(all_docs)
    no_law_docs = gate_law and not all_docs

    all_docs = case_docs + all_docs

    sources = [
        {
            "citation":    d.metadata.get("citation") or d.metadata.get("case_name") or d.metadata.get("source", ""),
            "content":     d.page_content,
            "score":       d.metadata.get("score", 0),
            "source_type": d.metadata.get("source", ""),
        }
        for d in all_docs
    ]

    context = format_docs(all_docs)
    if len(context) > 24_000:
        context = context[:24_000] + "\n\n[Context truncated to fit token budget]"

    no_doc_warning = ""
    if req.case_id and req.source != "case_events" and not case_docs:
        no_doc_warning = (
            "\n\nIMPORTANT: No uploaded case documents were found relevant to this question. "
            "Do NOT answer as though you know the user's specific situation. "
            "If the question is about their personal case, tell them you have no relevant documents "
            "and suggest they upload the relevant documents or consult a qualified lawyer."
        )

    if no_law_docs and not case_docs:
        no_doc_warning += NO_LAW_SOURCE_WARNING

    lc_messages = [
        SystemMessage(content=PLAIN_ENGLISH_SYSTEM + no_doc_warning + "\n\nRelevant legal context:\n" + context),
        HumanMessage(content=req.question),
    ]

    return StreamingResponse(
        _stream_ask(lc_messages, sources, req.question, req.source, req.case_id or "", req.k, _log_request, user),
        media_type="text/event-stream",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    user = _get_user_from_header(authorization)
    if user == "anon":
        await _enforce_anon_llm_quota(request)

    all_docs, case_docs = await _retrieve_concurrently(
        req.question,
        [LegislationRetriever(k=req.k, jurisdiction="NSW"), CaselawRetriever(k=req.k)],
        (req.case_id, user, req.k) if req.case_id else None,
    )
    all_docs = _gate_law_docs(all_docs)
    no_law_docs = not all_docs
    all_docs = case_docs + all_docs

    sources = [
        {
            "citation":    meta.get("citation") or meta.get("case_name") or meta.get("source", ""),
            "content":     d.page_content,
            "score":       meta.get("score", 0),
            "source_type": meta.get("source", ""),
        }
        for d in all_docs
        for meta in [d.metadata]
    ]

    context = format_docs(all_docs)
    if len(context) > 24_000:  # ~6k tokens @ 4 chars/token
        context = context[:24_000] + "\n\n[Context truncated to fit token budget]"

    # Hard gate: if a case_id was provided but no documents scored above the
    # threshold, inject an explicit warning so the model does not hallucinate
    # case-specific facts from training data.
    if req.case_id and not case_docs:
        no_doc_warning = (
            "\n\nIMPORTANT: No uploaded case documents were found relevant to this question. "
            "Do NOT answer as though you know the user's specific situation. "
            "If the question is about their personal case, tell them you have no relevant documents "
            "and suggest they upload the relevant documents or consult a qualified lawyer."
        )
    else:
        no_doc_warning = ""

    if no_law_docs and not case_docs:
        no_doc_warning += NO_LAW_SOURCE_WARNING

    _history_msgs = [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in req.messages[-20:]  # 10 turns max before token trim
    ]
    history = trim_messages(
        _history_msgs,
        max_tokens=4000,
        token_counter=lambda msgs: sum(len(m.content) for m in msgs) // 4,
        strategy="last",
        start_on="human",
        include_system=False,
    )
    lc_messages = [
        SystemMessage(content=PLAIN_ENGLISH_SYSTEM + no_doc_warning + "\n\nRelevant legal context:\n" + context),
        *history,
        HumanMessage(content=req.question),
    ]

    messages_raw = [{"role": m.role, "content": m.content} for m in req.messages]

    return StreamingResponse(
        _stream_chat(
            lc_messages, sources, req.question, messages_raw,
            req.case_id, req.k, _log_request, user,
        ),
        media_type="text/event-stream",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
        },
    )


# ── Lambda handler ─────────────────────────────────────────────────────────────

handler = Mangum(app, lifespan="off")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.ai.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "20003")),
        reload=False,
    )
