"""Mirror of production retrieval (dense cosine over pgvector). READ-ONLY."""
from common import connect, embed, load_env
import os

EMBED_MODEL = None


def _model():
    load_env()
    return os.environ.get("EMBED_MODEL", "text-embedding-3-small")


def _vec(v):
    return "[" + ",".join(str(x) for x in v) + "]"


def legislation(cur, vec, k, jurisdiction="NSW"):
    cur.execute("""SELECT citation, text FROM legislation_chunks
                   WHERE embedding IS NOT NULL AND jurisdiction = %s
                   ORDER BY embedding <=> %s::vector LIMIT %s""", (jurisdiction, _vec(vec), k))
    return [{"citation": c, "text": t} for c, t in cur.fetchall()]


def caselaw(cur, vec, k):
    cur.execute("""SELECT neutral_citation, title, text FROM caselaw_chunks
                   WHERE embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s""", (_vec(vec), k))
    return [{"citation": f"{ti} ({nc})", "text": t} for nc, ti, t in cur.fetchall()]


def embed_questions(qs):
    out = []
    for i in range(0, len(qs), 64):
        out += embed(qs[i:i + 64], _model())
    return out


def connect_ro():
    return connect()
