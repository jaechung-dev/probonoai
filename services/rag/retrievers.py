"""
RAG retriever classes — pgvector similarity search over Supabase/Postgres.

Each retriever is a LangChain BaseRetriever that embeds the query with
OpenAI text-embedding-3-small, then performs a cosine-distance lookup
against the appropriate table.
"""
import uuid as _uuid
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


class LegislationRetriever(BaseRetriever):
    """Semantic search over NSW (or Commonwealth) legislation chunks."""

    k: int = Field(default=4)
    jurisdiction: str = Field(default="NSW")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        vec = get_embedder().embed_query(query)
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
        vec = get_embedder().embed_query(query)
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

    Scoped by ``user_id`` as well as ``case_id`` — a case_id belonging to another
    user returns nothing, so an attacker-supplied case_id can never leak private
    documents.
    """

    k: int = Field(default=5)
    case_id: str = Field(default="")
    user_id: str = Field(default="")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        # Owner scoping is mandatory: never query case chunks without a user_id.
        if not self.user_id:
            return []
        # case_id is a UUID column — reject non-UUID strings before hitting the DB.
        try:
            _uuid.UUID(self.case_id)
        except (ValueError, AttributeError):
            return []
        vec = get_embedder().embed_query(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT content, metadata, 1 - (embedding <=> %s::vector) AS score
                FROM case_chunks
                WHERE case_id = %s AND user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, self.case_id, self.user_id, vec_str, self.k))
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Document(
                page_content=r[0],
                metadata={**(r[1] if isinstance(r[1], dict) else {}), "source": "case_chunk", "score": round(r[2], 4)},
            )
            for r in rows
        ]


class CaseEventRetriever(BaseRetriever):
    """Semantic search over case timeline events for a specific case."""

    k: int = Field(default=5)
    case_id: str = Field(default="")

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        # case_id is a UUID column — reject non-UUID strings before hitting the DB.
        try:
            _uuid.UUID(self.case_id)
        except (ValueError, AttributeError):
            return []
        vec = get_embedder().embed_query(query)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn = psycopg2.connect(DSN)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, category, event_type, subject, content,
                       1 - (embedding <=> %s::vector) AS score
                FROM case_events
                WHERE case_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec_str, self.case_id, vec_str, self.k))
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Document(
                page_content=f"[{r[1]} — {r[2]}] {r[3]}\n{r[4]}",
                metadata={
                    "source": "case_event",
                    "date": str(r[0]),
                    "category": r[1],
                    "score": round(r[5], 4),
                },
            )
            for r in rows
        ]
