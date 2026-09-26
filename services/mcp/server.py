"""
MCP server — FastMCP tools + streamable HTTP transport.

Auth: opaque MCP token issued by probonoai.com.au/connect.
      Every request must carry:  Authorization: Bearer mcp-<token>
      Token is validated against the mcp_tokens table (DB lookup, not JWT).

Run standalone:
    python -m services.mcp.server
or via uvicorn:
    uvicorn services.mcp.server:app --port 20002
"""
import os
import json
import logging
import hashlib
import requests
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)
log = logging.getLogger("mcp.server")

load_dotenv()

from mcp.server.fastmcp import FastMCP, Context
try:
    # Not present on every resolved `mcp` version (CI's pinned Python 3.12
    # build has previously resolved one without this submodule --
    # ModuleNotFoundError). Optional: when unavailable we just skip passing
    # transport_security= below and rely solely on the outer
    # AllowedHostsMiddleware for host-header enforcement in Lambda.
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:
    TransportSecuritySettings = None
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from services.core.settings import settings  # loads Secrets Manager at cold start

# ── Config ─────────────────────────────────────────────────────────────────────

DSN = settings.DATABASE_URL or os.getenv("DATABASE_URL", "")
_default_rag = (
    "https://api.probonoai.com.au"
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    else "http://127.0.0.1:20000"
)
RAG_URL  = os.getenv("RAG_URL", _default_rag)
_PUBLIC  = {"/"}

# ── DB helpers ─────────────────────────────────────────────────────────────────


@contextmanager
def _db():
    conn = psycopg2.connect(DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ── Allowed-hosts middleware ────────────────────────────────────────────────────
# FastMCP's built-in DNS-rebinding protection rejects requests whose Host header
# doesn't match localhost. Starlette-level allowlist lets prod requests through.

_ALLOWED_HOSTS = {
    "api.probonoai.com.au",
    "127.0.0.1",
    "localhost",
}


class AllowedHostsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            host = request.headers.get("host", "").split(":")[0]
            if host and host not in _ALLOWED_HOSTS:
                return JSONResponse({"error": "Invalid host"}, status_code=421)
            # GET /mcp opens a keep-alive SSE stream for server-initiated events.
            # Lambda can't hold long-lived connections, and stateless mode has no
            # server-initiated events anyway — reject immediately instead of hanging
            # for the full function timeout.
            if request.method == "GET" and request.url.path.rstrip("/") == "/mcp":
                log.info("GET /mcp rejected (SSE not supported in Lambda stateless mode)")
                return JSONResponse(
                    {"error": "SSE stream not supported in stateless mode"},
                    status_code=405,
                    headers={"Allow": "POST"},
                )
        return await call_next(request)


# ── Auth middleware ────────────────────────────────────────────────────────────


class MCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {
                    "error": (
                        "Missing Authorization header. "
                        "Get an MCP token at probonoai.com.au/connect"
                    )
                },
                status_code=401,
            )

        raw = auth[len("Bearer "):]
        try:
            with _db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE mcp_tokens SET last_used_at=NOW() "
                    "WHERE token_hash=%s AND expires_at>NOW() "
                    "RETURNING id, user_id",
                    (_h(raw),),
                )
                row = cur.fetchone()
        except Exception:
            return JSONResponse({"error": "Token validation failed"}, status_code=500)

        if not row:
            return JSONResponse(
                {
                    "error": (
                        "Invalid or expired MCP token. "
                        "Get a new one at probonoai.com.au/connect"
                    )
                },
                status_code=401,
            )

        request.state.mcp_token_id = str(row[0])
        request.state.user_id      = str(row[1])
        return await call_next(request)


# ── FastMCP server ─────────────────────────────────────────────────────────────

# FastMCP ALSO runs its own internal transport_security check (independent of
# the AllowedHostsMiddleware above), defaulting to localhost-only when no host
# is given. That inner check was still rejecting api.probonoai.com.au with its
# own 421 "Invalid Host header" even after the outer middleware passed the
# request through -- confirmed via curl (Content-Length: 19 == len("Invalid Host header")).
# Must override it here too.
_mcp_kwargs = dict(
    instructions=(
        "Legal intelligence platform — search NSW legislation and caselaw, "
        "ask questions in plain English."
    ),
)
if TransportSecuritySettings is not None:
    _mcp_kwargs["transport_security"] = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["api.probonoai.com.au", "127.0.0.1:*", "localhost:*"],
    )

# Lambda can route two requests with the same mcp-session-id to different
# containers (or recycle the one that held the session), making in-memory
# session state useless. stateless_http=True tells FastMCP to handle each
# request independently with no server-side session — the right model for
# Lambda. The cold-start session_manager.run() below is still required even
# in stateless mode because the task group must be initialized before any
# request is dispatched (see streamable_http_manager.py:160).
_mcp_kwargs["stateless_http"] = True

mcp = FastMCP("Legal RAG", **_mcp_kwargs)


@mcp.tool()
def search(query: str, source: str = "legislation", k: int = 5) -> str:
    """
    Search NSW legislation and caselaw semantically.
    source: 'legislation' | 'caselaw' | 'both' | 'case_events'
    Returns top-k relevant chunks with citations.
    """
    log.info("search request: query=%r source=%s k=%d", query, source, k)
    r = requests.post(
        f"{RAG_URL}/search",
        json={"query": query, "source": source, "jurisdiction": "NSW", "k": k},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    output  = f"Search: '{query}' ({source}, {len(results)} results)\n\n"
    for i, res in enumerate(results, 1):
        citation      = res["metadata"].get("citation") or res["metadata"].get("case_name", "")
        score         = res["metadata"].get("score", 0)
        display_score = min(round(score * 200), 99)
        output  += f"[{i}] {citation} (relevance: {display_score}%)\n{res['content']}\n\n"
    citations = [r["metadata"].get("citation") or r["metadata"].get("case_name", "") for r in results]
    log.info("search response: %d results citations=%s", len(results), citations)
    return output


@mcp.tool()
def ask(question: str, source: str = "both", k: int = 5) -> str:
    """
    Ask a legal question in plain English. Returns an answer backed by NSW legislation and caselaw.
    source: 'legislation' | 'caselaw' | 'both'
    """
    log.info("ask request: question=%r source=%s k=%d", question, source, k)
    r = requests.post(
        f"{RAG_URL}/chat",
        json={"question": question, "messages": [], "k": k},
        timeout=120,
        stream=True,
    )
    r.raise_for_status()

    answer, sources = "", []
    for line in r.iter_lines():
        if not line:
            continue
        line = line.decode() if isinstance(line, bytes) else line
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            evt = json.loads(line[6:])
            if evt["type"] == "sources":
                sources = evt["docs"]
            elif evt["type"] == "token":
                answer += evt["text"]
        except Exception:
            pass

    output = f"Answer:\n{answer}\n\nSources used:\n"
    for s in sources[:5]:
        display_score = min(round(s['score'] * 200), 99)
        output += f"- {s['citation']} ({s['source_type']}, relevance: {display_score}%)\n"
    log.info("ask response: answer_chars=%d sources=%s", len(answer), [s["citation"] for s in sources[:5]])
    return output


@mcp.tool()
def fetch(case_id: str, ctx: Context) -> str:
    """
    Fetch all timeline events for the authenticated user's own case.
    case_id: e.g. 'test-case-001'
    Only returns events owned by the user whose MCP token was used — never
    another user's data, even if the case_id is guessed correctly.
    """
    user_id = ctx.request_context.request.state.user_id
    log.info("fetch request: case_id=%r user_id=%s", case_id, user_id)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT date, category, subject, summary FROM case_events "
            "WHERE user_id = %s AND case_id = %s ORDER BY date",
            (user_id, case_id),
        )
        rows = cur.fetchall()
    if not rows:
        log.info("fetch response: case_id=%r user_id=%s — no events found", case_id, user_id)
        return f"No events found for case '{case_id}' (or you don't have access to it)."
    output = f"Case: {case_id} — {len(rows)} events\n\n"
    for date, category, subject, summary in rows:
        output += f"[{date}] {category} — {subject}\n{summary}\n\n"
    log.info("fetch response: case_id=%r user_id=%s events=%d", case_id, user_id, len(rows))
    return output


@mcp.tool()
def collections() -> str:
    """List available data collections and their sizes."""
    log.info("collections request")
    r = requests.get(f"{RAG_URL}/health", timeout=10)
    r.raise_for_status()
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM legislation_chunks")
            leg_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM caselaw_chunks")
            case_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT case_id) FROM case_events")
            event_cases = cur.fetchone()[0]
    except Exception:
        leg_count = case_count = event_cases = "unknown"
    log.info("collections response: legislation=%s caselaw=%s case_events=%s", leg_count, case_count, event_cases)
    return (
        "Available collections:\n"
        f"- legislation  : {leg_count:,} NSW legislation chunks (OALC corpus, text-embedding-3-small)\n"
        f"- caselaw      : {case_count:,} NSW caselaw paragraph chunks\n"
        f"- case_events  : Case event timelines ({event_cases} case(s))\n"
        f"Model: {r.json().get('model', 'unknown')}"
    )


# ── ASGI app ───────────────────────────────────────────────────────────────────

_mcp_app = mcp.streamable_http_app()

app = CORSMiddleware(
    AllowedHostsMiddleware(MCPAuthMiddleware(_mcp_app)),
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "Authorization"],
    expose_headers=["mcp-session-id"],
)

from mangum import Mangum  # noqa: E402

# ── Lambda lifespan handling for FastMCP's streamable-http session manager ──
#
# DO NOT use lifespan="off": FastMCP's StreamableHTTPSessionManager task group
# is only created inside the ASGI *lifespan* startup event, so skipping the
# lifespan protocol entirely leaves it uninitialized and every request 500s
# with RuntimeError("Task group is not initialized. Make sure to use run().")
# -- see mcp/server/streamable_http_manager.py:201 in the installed package.
#
# DO NOT use lifespan="auto"/"on" either: read Mangum's own adapter.py --
# Mangum.__call__() wraps EVERY SINGLE invocation in its own LifespanCycle
# (ExitStack), i.e. it runs a full ASGI lifespan *startup* AND *shutdown* on
# every request, not once per cold start. But session_manager.run() is a
# single-use async context manager (StreamableHTTPSessionManager raises
# "run() can only be called once per instance" if entered twice). So the
# first request on a warm container succeeds (starts, handles it, shuts
# down), and the very next request on that SAME warm container tries to
# re-enter session_manager.run() and crashes with exactly that RuntimeError
# -- confirmed via CloudWatch: `initialize` returned 200, then
# `notifications/initialized` on the same container 500'd with
# "StreamableHTTPSessionManager .run() can only be called once per
# instance."
#
# Fix: keep Mangum out of lifespan management altogether (lifespan="off")
# and instead enter session_manager.run() ourselves EXACTLY ONCE per Lambda
# execution environment, here at module import / cold-start time, on the
# same event loop Mangum reuses for every subsequent invocation (Mangum
# pins one via asyncio.set_event_loop() in its constructor, and
# HTTPCycle.__call__ later drives requests with
# asyncio.get_event_loop().run_until_complete(...) against that same loop).
# We never call __aexit__, so the task group stays alive for the life of
# the warm container, and each request is handled against the already-
# running session manager instead of trying to start/stop it per request.
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    # Only enter the session manager once at Lambda cold start.
    # Skipped outside Lambda (uvicorn dev, unit tests) because uvicorn drives
    # the ASGI lifespan itself, and the test stub has no real session manager.
    import asyncio as _asyncio

    try:
        _loop = _asyncio.get_event_loop()
    except RuntimeError:
        _loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(_loop)

    _session_manager_cm = mcp.session_manager.run()
    _loop.run_until_complete(_session_manager_cm.__aenter__())

handler = Mangum(app, lifespan="off")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.mcp.server:app",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "20002")),
    )
