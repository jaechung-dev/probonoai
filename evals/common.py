"""Shared helpers for the eval pipeline. All DB access here is READ-ONLY."""
from __future__ import annotations
import hashlib, json, os, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
RESULTS = EVALS / "results"
GOLDEN = EVALS / "golden.jsonl"
SNAPSHOT = EVALS / "corpus_snapshot.json"
CITATIONS_CACHE = EVALS / ".corpus_citations.json"  # gitignored


def load_env() -> None:
    """Load .env into os.environ (never prints values)."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def enforce_read_only() -> None:
    """Force every psycopg2 connection in this process to be read-only."""
    import psycopg2
    if getattr(psycopg2, "_evals_ro", False):
        return
    _orig = psycopg2.connect

    def _ro_connect(*a, **k):
        conn = _orig(*a, **k)
        conn.set_session(readonly=True, autocommit=False)
        return conn

    psycopg2.connect = _ro_connect
    psycopg2._evals_ro = True


def connect():
    load_env()
    enforce_read_only()
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "services"], cwd=ROOT, text=True).strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def load_golden(path: Path = GOLDEN) -> list[dict]:
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def embed(texts: list[str], model: str) -> list[list[float]]:
    load_env()
    from openai import OpenAI
    r = OpenAI().embeddings.create(model=model, input=texts)
    return [d.embedding for d in r.data]


def run_meta(model: str | None = None) -> dict:
    from services.rag.prompts import PLAIN_ENGLISH_SYSTEM
    snap = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
    return {
        "commit": git_commit(),
        "model": model,
        "embed_model": os.environ.get("EMBED_MODEL", "text-embedding-3-small"),
        "corpus_hash": snap.get("corpus_hash"),
        "prompt_hash": sha256_text(PLAIN_ENGLISH_SYSTEM)[:16],
        "golden_hash": sha256_file(GOLDEN)[:16] if GOLDEN.exists() else None,
    }
