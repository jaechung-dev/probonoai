"""
RAG retriever classes — pgvector similarity search over Supabase/Postgres.

Each retriever is a LangChain BaseRetriever that embeds the query with
OpenAI text-embedding-3-small, then performs a cosine-distance lookup
against the appropriate table.

CaseChunkRetriever and CaseEventRetriever query the v2 schema
(chunks / events joined to documents) when v2 data exists for the case,
and fall back to the legacy case_chunks / case_events tables otherwise.
"""
import datetime as _dt
import uuid as _uuid
from functools import lru_cache
import psycopg2

from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from pydantic import Field

from services.core.settings import settings

DSN         = settings.DATABASE_URL
EMBED_MODEL = settings.EMBED_MODEL

_embedder: OpenAIEmbeddings | None = None


def get_embedder() -> OpenAIEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = OpenAIEmbeddings(model=EMBED_MODEL)
    return _embedder


@lru_cache(maxsize=64)
def embed_query_cached(query: str) -> tuple[float, ...]:
    """Embed a query once per process. /chat and /ask run several retrievers on the
    same question; without this each one makes its own OpenAI embedding call."""
    return tuple(get_embedder().embed_query(query))


def _format_occurred(occurred_on, precision: str) -> str:
    """Format an occurred_on date according to its recorded precision."""
    if occurred_on is None:
        return ""
    if isinstance(occurred_on, str):
        try:
            occurred_on = _dt.date.fromisoformat(occurred_on)
        except ValueError:
            return occurred_on
    if precision == "month":
        return occurred_on.strftime("%B %Y")
    if precision in ("year", "approx"):
        return f"approx. {occurred_on.year}"
    return str(occurred_on)  # 'day' or unknown: full ISO date


class LegislationRetriever(BaseRetriever):
    """Semantic search over NSW (or Commonwealth) legislation chunks."""

    k: int = Field(default=4)
    jurisdiction: str = Field(default="NSW")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        vec = embed_query_cached(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            if self.jurisdiction == "both":
                cur.execute("""
                    SELECT citation, text, 1 - (embedding <=> %s::vector) AS score
                    FROM legislation_chunks
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, vec_str, self.k))
            else:
                cur.execute("""
                    SELECT citation, text, 1 - (embedding <=> %s::vector) AS score
                    FROM legislation_chunks
                    WHERE embedding IS NOT NULL AND jurisdiction = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, self.jurisdiction, vec_str, self.k))
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Document(
                page_content=r[1],
                metadata={"source": "legislation", "citation": r[0], "score": round(r[2], 4)},
            )
            for r in rows
        ]


class CaselawRetriever(BaseRetriever):
    """Semantic search over NSW caselaw paragraph chunks."""

    k: int = Field(default=4)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        vec = embed_query_cached(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT neutral_citation, title, text, 1 - (embedding <=> %s::vector) AS score
                FROM caselaw_chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, vec_str, self.k))
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Document(
                page_content=r[2],
                metadata={
                    "source": "caselaw",
                    "citation": f"{r[1]} ({r[0]})",
                    "score": round(r[3], 4),
                },
            )
            for r in rows
        ]


class CaseChunkRetriever(BaseRetriever):
    """Semantic search over uploaded case document chunks for a specific case.

    Queries the v2 chunks table (joined to documents and document_pages) when
    v2 data exists for the case; falls back to the legacy case_chunks table
    otherwise so old cases continue to work.

    Scoped by ``user_id`` as well as ``case_id`` — a case_id belonging to
    another user returns nothing, so an attacker-supplied case_id can never
    leak private documents.
    """

    k: int = Field(default=5)
    case_id: str = Field(default="")
    user_id: str = Field(default="")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        if not self.user_id:
            return []
        try:
            _uuid.UUID(self.case_id)
        except (ValueError, AttributeError):
            return []
        vec = embed_query_cached(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM chunks WHERE case_id = %s AND user_id = %s LIMIT 1",
                (self.case_id, self.user_id),
            )
            use_v2 = cur.fetchone() is not None

            if use_v2:
                cur.execute("""
                    SELECT c.content,
                           1 - (c.embedding <=> %s::vector) AS score,
                           c.document_id::text,
                           d.filename,
                           d.title,
                           d.doc_type,
                           d.occurred_on,
                           d.sent_at,
                           d.source_origin,
                           d.redaction_status,
                           c.page_no
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    LEFT JOIN document_pages p ON p.id = c.page_id
                    WHERE c.case_id = %s AND c.user_id = %s
                      AND d.ingest_status = 'ready'
                      AND c.embedding IS NOT NULL
                      AND (c.page_id IS NULL OR p.duplicate_of_page_id IS NULL)
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, self.case_id, self.user_id, vec_str, self.k))
                rows = cur.fetchall()
                docs = []
                for r in rows:
                    (content, score, doc_id, filename, title, doc_type,
                     occurred_on, sent_at, source_origin, redaction_status, page_no) = r
                    if filename and page_no is not None:
                        citation = f"{filename} p.{page_no}"
                    else:
                        citation = filename or ""
                    docs.append(Document(
                        page_content=content,
                        metadata={
                            "source":           "case_chunk",
                            "score":            round(float(score), 4),
                            "citation":         citation,
                            "document_id":      doc_id,
                            "filename":         filename,
                            "title":            title,
                            "doc_type":         doc_type,
                            "occurred_on":      str(occurred_on) if occurred_on else None,
                            "sent_at":          str(sent_at) if sent_at else None,
                            "source_origin":    source_origin,
                            "redaction_status": redaction_status,
                            "page_no":          page_no,
                        },
                    ))
                return docs

            # Legacy fallback: case_chunks table
            cur.execute("""
                SELECT content, metadata, 1 - (embedding <=> %s::vector) AS score
                FROM case_chunks
                WHERE case_id = %s AND user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, self.case_id, self.user_id, vec_str, self.k))
            rows = cur.fetchall()
            return [
                Document(
                    page_content=r[0],
                    metadata={**(r[1] if isinstance(r[1], dict) else {}), "source": "case_chunk", "score": round(r[2], 4)},
                )
                for r in rows
            ]
        finally:
            conn.close()


class CaseEventRetriever(BaseRetriever):
    """Semantic search over case timeline events for a specific case.

    Queries the v2 events table (joined to documents) when v2 data exists
    for the case; falls back to the legacy case_events table otherwise.

    Scoped by ``user_id`` as well as ``case_id`` (same rule as
    CaseChunkRetriever): a case_id belonging to another user returns nothing.
    """

    k: int = Field(default=5)
    case_id: str = Field(default="")
    user_id: str = Field(default="")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        if not self.user_id:
            return []
        try:
            _uuid.UUID(self.case_id)
        except (ValueError, AttributeError):
            return []
        vec = embed_query_cached(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM events WHERE case_id = %s AND user_id = %s LIMIT 1",
                (self.case_id, self.user_id),
            )
            use_v2 = cur.fetchone() is not None

            if use_v2:
                cur.execute("""
                    SELECT e.title,
                           e.summary,
                           e.excerpt,
                           e.event_type,
                           e.occurred_on,
                           e.occurred_precision,
                           1 - (e.embedding <=> %s::vector) AS score,
                           e.document_id::text,
                           d.filename,
                           e.page_no
                    FROM events e
                    LEFT JOIN documents d ON d.id = e.document_id
                    WHERE e.case_id = %s AND e.user_id = %s
                      AND e.embedding IS NOT NULL
                    ORDER BY e.embedding <=> %s::vector
                    LIMIT %s
                """, (vec_str, self.case_id, self.user_id, vec_str, self.k))
                rows = cur.fetchall()
                docs = []
                for r in rows:
                    (title, summary, excerpt, event_type, occurred_on, precision,
                     score, doc_id, filename, page_no) = r
                    date_str = _format_occurred(occurred_on, precision or "day")
                    parts = []
                    if event_type:
                        parts.append(f"[{event_type}] {title}")
                    else:
                        parts.append(title or "")
                    if summary:
                        parts.append(summary)
                    if excerpt:
                        parts.append(excerpt)
                    if date_str:
                        parts.append(f"Date: {date_str}")
                    citation = (f"{filename} p.{page_no}"
                                if (filename and page_no is not None) else (filename or ""))
                    docs.append(Document(
                        page_content="\n".join(p for p in parts if p),
                        metadata={
                            "source":      "case_event",
                            "score":       round(float(score), 4),
                            "citation":    citation,
                            "document_id": doc_id,
                            "filename":    filename,
                            "page_no":     page_no,
                        },
                    ))
                return docs

            # Legacy fallback: case_events table
            cur.execute("""
                SELECT date, category, event_type, subject, content,
                       1 - (embedding <=> %s::vector) AS score
                FROM case_events
                WHERE case_id = %s AND user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, self.case_id, self.user_id, vec_str, self.k))
            rows = cur.fetchall()
            return [
                Document(
                    page_content=f"[{r[1]} — {r[2]}] {r[3]}\n{r[4]}",
                    metadata={
                        "source":   "case_event",
                        "date":     str(r[0]),
                        "category": r[1],
                        "score":    round(r[5], 4),
                    },
                )
                for r in rows
            ]
        finally:
            conn.close()
