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
import hashlib
import requests
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
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

# FastMCP defaults to host="127.0.0.1", which auto-scopes allowed_hosts to
# localhost only (DNS-rebinding protection) -- that rejects every real request
# once deployed behind api.probonoai.com.au (421 Invalid Host header). Auth is
# already enforced by MCPAuthMiddleware below, so explicitly allow the prod host.
mcp = FastMCP(
    "Legal RAG",
    instructions=(
        "Legal intelligence platform — search NSW legislation and caselaw, "
        "ask questions in plain English."
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["api.probonoai.com.au", "127.0.0.1:*", "localhost:*"],
    ),
)


@mcp.tool()
def search(query: str, source: str = "legislation", k: int = 5) -> str:
    """
    Search NSW legislation and caselaw semantically.
    source: 'legislation' | 'caselaw' | 'both' | 'case_events'
    Returns top-k relevant chunks with citations.
    """
    r = requests.post(
        f"{RAG_URL}/search",
        json={"query": query, "source": source, "jurisdiction": "NSW", "k": k},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    output  = f"Search: '{query}' ({source}, {len(results)} results)\n\n"
    for i, res in enumerate(results, 1):
        citation = res["metadata"].get("citation") or res["metadata"].get("case_name", "")
        score    = res["metadata"].get("score", 0)
        output  += f"[{i}] {citation} (relevance: {score})\n{res['content']}\n\n"
    return output


@mcp.tool()
def ask(question: str, source: str = "both", k: int = 5) -> str:
    """
    Ask a legal question in plain English. Returns an answer backed by NSW legislation and caselaw.
    source: 'legislation' | 'caselaw' | 'both'
    """
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
        output += f"- {s['citation']} ({s['source_type']}, relevance: {s['score']})\n"
    return output


@mcp.tool()
def fetch(case_id: str) -> str:
    """
    Fetch all timeline events for a case.
    case_id: e.g. 'nguyen'
    """
    r = requests.get(f"{RAG_URL}/case/{case_id}/timeline", timeout=15)
    if r.status_code == 404:
        return f"Case '{case_id}' not found."
    r.raise_for_status()
    data   = r.json()
    output = f"Case: {case_id} — {data['total']} events\n\n"
    for e in data["events"]:
        output += f"[{e['date']}] {e['category']} — {e['subject']}\n{e['summary']}\n\n"
    return output


@mcp.tool()
def collections() -> str:
    """List available data collections and their sizes."""
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
    MCPAuthMiddleware(_mcp_app),
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "Authorization"],
    expose_headers=["mcp-session-id"],
)

from mangum import Mangum  # noqa: E402
handler = Mangum(app, lifespan="auto")  # "off" skips ASGI lifespan -> FastMCP session_manager task group never inits

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.mcp.server:app",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "20002")),
    )
