"""
BFF (Backend-for-Frontend) — thin FastAPI layer.

Imports domain logic from services/ and exposes:
  GET  /health
  POST /search
  GET  /case/{case_id}/timeline
  POST /ask
  POST /chat

Auth routes are handled by services/auth/service.py via APIRouter.
"""
import os
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

import psycopg2
from psycopg2.extras import Json
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from jose import jwt
from mangum import Mangum
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from services.auth.service import (
    router as auth_router,
    seed_db,
    JWT_SECRET,
    JWT_ALG,
)
from services.core.cache import get_redis
from services.rag.retrievers import (
    LegislationRetriever,
    CaselawRetriever,
    CaseEventRetriever,
    CaseChunkRetriever,
    DSN,
    EMBED_MODEL,
)
from services.rag.chains import (
    get_llm,
    format_docs,
    strip_think,
    stream_single,
    stream_both,
    stream_chat as _stream_chat,
    CHAT_MODEL,
)
from services.rag.prompts import PLAIN_ENGLISH_SYSTEM

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

logger = logging.getLogger(__name__)

# ── Audit logging ──────────────────────────────────────────────────────────────


def _write_request_log(
    endpoint: str, user_id: str, input_data: dict,
    output_data: dict, elapsed_ms: int,
) -> None:
    try:
        conn = psycopg2.connect(DSN)
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


# ── Pydantic models ────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    source: str = "legislation"   # legislation | caselaw | case_events
    jurisdiction: str = "NSW"     # NSW | Commonwealth | both
    case_id: str = "nguyen"
    k: int = 5


class AskRequest(BaseModel):
    question: str
    source: str = "legislation"   # legislation | caselaw | both | case_events
    jurisdiction: str = "NSW"     # NSW | Commonwealth | both
    case_id: str = "nguyen"
    k: int = 4


class ChatMessage(BaseModel):
    role: str    # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    messages: list[ChatMessage] = []
    case_id: str | None = None
    k: int = 5


class UploadUrlRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    case_id: str | None = None


class IntakeRequest(BaseModel):
    personal: dict
    matter: dict
    files: list = []


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    case_id: str | None = None


class ConversationPatch(BaseModel):
    title: str


class ConversationMessageItem(BaseModel):
    role: str   # "user" | "assistant"
    content: str
    sources: list | None = None


# ── Auth helper ────────────────────────────────────────────────────────────────


def _get_user_from_header(authorization: str = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anon"
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=[JWT_ALG], audience="probonoai-api")
        return claims.get("sub", "anon")
    except Exception:
        return "anon"


def _require_auth(authorization: str | None) -> str:
    """Like _get_user_from_header but raises 401 for anonymous/invalid tokens."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=[JWT_ALG], audience="probonoai-api")
        user_id = claims.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Anonymous chat quota ─────────────────────────────────────────────────────────
#
# Anonymous (not-logged-in) visitors may send at most ANON_CHAT_LIMIT chat
# messages before they must register or log in. The frontend also enforces this
# (see frontend/hooks/useGuestQuota.ts) for instant UX, but that count lives in
# localStorage and is trivially reset. This server-side check is the real gate:
# it counts anonymous /chat calls per client IP in Redis with a rolling daily
# window. It fails OPEN when Redis is not configured (same policy as the auth
# rate limiter) so local/dev without Redis still works.

ANON_CHAT_LIMIT = int(os.getenv("ANON_CHAT_LIMIT", "2"))
_ANON_CHAT_WINDOW = int(os.getenv("ANON_CHAT_WINDOW", str(24 * 60 * 60)))  # seconds


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "-"
    return (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip", "")
        or (request.client.host if request.client else "-")
    )


async def _enforce_anon_chat_limit(request: Request | None) -> None:
    """Raise 401 once an anonymous IP has used up its free chat messages.

    401 (rather than 429) so the frontend treats it like an auth requirement and
    surfaces the register / log-in prompt. No-op when Redis is unavailable.
    """
    redis = await get_redis()
    if redis is None:
        return
    ip = _client_ip(request)
    if ip in ("", "-"):
        return
    key = f"anon:chat:{ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _ANON_CHAT_WINDOW)
    if count > ANON_CHAT_LIMIT:
        raise HTTPException(
            status_code=401,
            detail="Free message limit reached. Please register or log in to continue.",
        )


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Legal Intelligence", version="1.0")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000)
        ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip", "")
            or (request.client.host if request.client else "-")
        )
        user = "-"
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            try:
                payload = jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALG], audience="probonoai-api")
                user = payload.get("sub", "-")
            except Exception:
                pass
        print(
            f'ACCESS {datetime.now(timezone.utc).isoformat()} '
            f'ip={ip} method={request.method} path={request.url.path} '
            f'status={response.status_code} ms={duration_ms} user={user}',
            flush=True,
        )
        return response


app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount auth router
app.include_router(auth_router)

# Seed demo users
seed_db()

# ── S3 client (boto3 is provided by the Lambda runtime; not in requirements.txt) ──

UPLOADS_BUCKET = os.getenv("UPLOADS_BUCKET", "")
_AWS_REGION = os.getenv("AWS_REGION_NAME", "ap-southeast-2")

try:
    import boto3 as _boto3
    _s3 = _boto3.client("s3", region_name=_AWS_REGION)
except ImportError:
    _s3 = None

# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    try:
        conn = psycopg2.connect(DSN)
        conn.cursor().execute("SELECT 1")
        conn.close()
        db = "ok"
    except Exception as e:
        db = str(e)
    return {"status": "ok", "db": db, "model": CHAT_MODEL}


@app.post("/intake/upload-url")
async def intake_upload_url(req: UploadUrlRequest, authorization: str = Header(default=None)):
    if not _s3 or not UPLOADS_BUCKET:
        raise HTTPException(status_code=501, detail="File uploads not configured")
    user_id = _get_user_from_header(authorization)
    safe_name = os.path.basename(req.filename)
    key = f"intakes/{user_id}/{uuid4()}/{safe_name}"
    params = {"Bucket": UPLOADS_BUCKET, "Key": key, "ContentType": req.content_type}
    if req.case_id:
        # case-id in S3 metadata lets the ingestion Lambda skip the DB lookup,
        # avoiding a race condition if the intake was just created.
        params["Metadata"] = {"case-id": req.case_id}
    url = _s3.generate_presigned_url("put_object", Params=params, ExpiresIn=300)
    return {"upload_url": url, "key": key}


@app.post("/intake")
async def submit_intake(req: IntakeRequest, authorization: str = Header(default=None)):
    user_id = _get_user_from_header(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO case_intakes (user_id, personal, matter, files)
               VALUES (%s, %s, %s, %s) RETURNING id, created_at""",
            (user_id, Json(req.personal), Json(req.matter), Json(req.files))
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": str(row[0]), "created_at": row[1].isoformat()}


@app.get("/user/case")
async def get_user_case(authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, matter, created_at FROM case_intakes
               WHERE user_id = %s ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"case": None}
    return {"case": {"id": str(row[0]), "matter": row[1], "created_at": row[2].isoformat()}}


@app.post("/search")
async def search(req: SearchRequest, authorization: str = Header(default=None)):
    user = _get_user_from_header(authorization)
    if req.source == "caselaw":
        retriever = CaselawRetriever(k=req.k)
    elif req.source == "case_events":
        retriever = CaseEventRetriever(k=req.k, case_id=req.case_id)
    else:
        retriever = LegislationRetriever(k=req.k, jurisdiction=req.jurisdiction)

    logger.info("search query=%r source=%s k=%d user=%s", req.query, req.source, req.k, user)
    t0 = time.time()
    docs = retriever.invoke(req.query)
    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "search query=%r source=%s k=%d user=%s results=%d elapsed_ms=%d",
        req.query, req.source, req.k, user, len(docs), elapsed_ms,
    )

    results = [{"content": d.page_content[:600], "metadata": d.metadata} for d in docs]

    asyncio.create_task(_log_request(
        endpoint="/search",
        user_id=user,
        input_data={"query": req.query, "source": req.source, "k": req.k},
        output_data={"results": results, "count": len(results)},
        elapsed_ms=elapsed_ms,
    ))

    return {"query": req.query, "source": req.source, "results": results}


@app.get("/case/{case_id}/timeline")
def timeline(case_id: str):
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, category, event_type, subject, summary, content, attachments
            FROM case_events
            WHERE case_id = %s
            ORDER BY date ASC
        """, (case_id,))
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    return {
        "case_id": case_id,
        "total":   len(rows),
        "events": [
            {
                "date":        str(r[0]),
                "category":    r[1],
                "event_type":  r[2],
                "subject":     r[3],
                "summary":     r[4],
                "content":     r[5],
                "attachments": r[6],
            }
            for r in rows
        ],
    }


@app.post("/ask")
async def ask(req: AskRequest, authorization: str = Header(default=None)):
    user = _get_user_from_header(authorization)
    logger.info("ask question=%r source=%s k=%d user=%s", req.question[:120], req.source, req.k, user)

    if req.source == "both":
        leg = LegislationRetriever(k=req.k // 2 + 1, jurisdiction=req.jurisdiction)
        cas = CaselawRetriever(k=req.k // 2 + 1)
        leg_docs = leg.invoke(req.question)
        cas_docs = cas.invoke(req.question)
        return StreamingResponse(
            stream_both(
                leg_docs, cas_docs, req.question, _log_request,
                user, req.source, req.k,
            ),
            media_type="text/event-stream",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )

    if req.source == "caselaw":
        retriever = CaselawRetriever(k=req.k)
    elif req.source == "case_events":
        retriever = CaseEventRetriever(k=req.k, case_id=req.case_id)
    else:
        retriever = LegislationRetriever(k=req.k, jurisdiction=req.jurisdiction)

    return StreamingResponse(
        stream_single(retriever, req.question, _log_request, user, req.source, req.k),
        media_type="text/event-stream",
    )


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, authorization: str = Header(default=None)):
    user = _get_user_from_header(authorization)
    # Gate anonymous users to the free-message quota (logged-in users unlimited).
    if user == "anon":
        await _enforce_anon_chat_limit(request)
    logger.info(
        "chat question=%r messages=%d case_id=%s user=%s",
        req.question[:120], len(req.messages), req.case_id, user,
    )

    # Minimum similarity score for case chunks to be included in context/sources.
    # Below this threshold the question is considered unrelated to the user's case.
    MIN_CASE_SCORE = 0.35

    leg = LegislationRetriever(k=req.k, jurisdiction="NSW")
    cas = CaselawRetriever(k=req.k)
    all_docs = leg.invoke(req.question) + cas.invoke(req.question)
    if req.case_id:
        raw_case_docs = CaseChunkRetriever(case_id=req.case_id, k=req.k).invoke(req.question)
        case_docs = [d for d in raw_case_docs if d.metadata.get("score", 0) >= MIN_CASE_SCORE]
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

    context     = format_docs(all_docs)
    lc_messages = [
        SystemMessage(content=PLAIN_ENGLISH_SYSTEM + "\n\nRelevant legal context:\n" + context)
    ]
    for m in req.messages[-8:]:
        lc_messages.append(
            HumanMessage(content=m.content) if m.role == "user"
            else AIMessage(content=m.content)
        )
    lc_messages.append(HumanMessage(content=req.question))

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


# ── Conversation history ───────────────────────────────────────────────────────


@app.get("/conversations")
async def list_conversations(authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, title, updated_at, case_id
               FROM conversations
               WHERE user_id = %s
               ORDER BY updated_at DESC
               LIMIT 50""",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "updated_at": r[2].isoformat(),
            "case_id": r[3],
        }
        for r in rows
    ]


@app.post("/conversations", status_code=201)
async def create_conversation(req: ConversationCreate, authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO conversations (user_id, title, case_id)
               VALUES (%s, %s, %s)
               RETURNING id, title, updated_at, case_id""",
            (user_id, req.title[:200], req.case_id),
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "id": str(row[0]),
        "title": row[1],
        "updated_at": row[2].isoformat(),
        "case_id": row[3],
    }


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, updated_at, case_id, user_id FROM conversations WHERE id = %s",
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[4] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute(
            """SELECT id, role, content, sources, created_at
               FROM conversation_messages
               WHERE conversation_id = %s
               ORDER BY created_at ASC""",
            (conv_id,),
        )
        msgs = cur.fetchall()
    finally:
        conn.close()
    return {
        "id": str(row[0]),
        "title": row[1],
        "updated_at": row[2].isoformat(),
        "case_id": row[3],
        "messages": [
            {
                "id": str(m[0]),
                "role": m[1],
                "content": m[2],
                "sources": m[3],
                "created_at": m[4].isoformat(),
            }
            for m in msgs
        ],
    }


@app.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str, authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM conversations WHERE id = %s",
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))
        conn.commit()
    finally:
        conn.close()


@app.patch("/conversations/{conv_id}")
async def patch_conversation(conv_id: str, req: ConversationPatch, authorization: str = Header(default=None)):
    user_id = _require_auth(authorization)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM conversations WHERE id = %s",
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute(
            """UPDATE conversations SET title = %s, updated_at = NOW()
               WHERE id = %s
               RETURNING id, title, updated_at, case_id""",
            (req.title[:200], conv_id),
        )
        updated = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "id": str(updated[0]),
        "title": updated[1],
        "updated_at": updated[2].isoformat(),
        "case_id": updated[3],
    }


@app.post("/conversations/{conv_id}/messages", status_code=201)
async def append_messages(
    conv_id: str,
    messages: list[ConversationMessageItem],
    authorization: str = Header(default=None),
):
    user_id = _require_auth(authorization)
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM conversations WHERE id = %s",
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        for msg in messages:
            if msg.role not in ("user", "assistant"):
                raise HTTPException(status_code=400, detail=f"Invalid role: {msg.role}")
            cur.execute(
                """INSERT INTO conversation_messages (conversation_id, role, content, sources)
                   VALUES (%s, %s, %s, %s)""",
                (conv_id, msg.role, msg.content, Json(msg.sources) if msg.sources else None),
            )
        cur.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
            (conv_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "count": len(messages)}


# ── Lambda handler ─────────────────────────────────────────────────────────────

handler = Mangum(app, lifespan="off")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.bff.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "20000")),
        reload=False,
    )
