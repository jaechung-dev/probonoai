"""
Ingestion v2: writes to the case model in migrations/001_case_model_v2.sql.

Import-light on purpose: stdlib only. The DB connection (psycopg2-style), the
LLM, the embedder and the tokenizer are all injected, so every function here
is unit-testable without network, DB or heavy dependencies.

Never writes issues / document_issues / event_issues (interpretation belongs to
the case owner). Never writes events.legacy_category.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50
META_HEAD_CHARS = 6000
META_TAIL_CHARS = 1500
EVENT_WINDOW_CHARS = 12000
EXCERPT_MAX_CHARS = 1500

# Enums mirror the CHECK constraints in 001_case_model_v2.sql
DOC_TYPES = {"email", "letter", "decision", "submission", "memo", "form", "attachment", "record", "other"}
SOURCE_ORIGINS = {"received", "sent", "gipa", "own_record", "public", "other"}
REDACTION_STATUSES = {"none", "partial", "full", "unknown"}
PARTY_ROLES = {"sender", "recipient", "cc", "bcc", "author", "subject", "mentioned"}
PRECISIONS = {"day", "month", "year", "approx"}


class OwnershipError(Exception):
    """The case does not exist or does not belong to the user (fail closed)."""


# ── Pure helpers ───────────────────────────────────────────────────────────────

_PAGE_NO_LINE = re.compile(
    r"^\s*[-–—\[(]?\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*[-–—\])]?\s*$", re.I
)


def normalise_page_text(text: str) -> str:
    """Lowercase, drop bare page-number lines, collapse whitespace."""
    lines = [ln for ln in (text or "").splitlines() if not _PAGE_NO_LINE.match(ln)]
    return re.sub(r"\s+", " ", " ".join(lines).lower()).strip()


def page_hash(text: str) -> Optional[str]:
    """sha256 of normalised text; None for blank pages (never deduplicated)."""
    norm = normalise_page_text(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest() if norm else None


def value_norm(value: str) -> str:
    """Lower-case, letters and digits only (matches reference_numbers.value_norm)."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _enum(value, allowed: set, default):
    v = str(value).strip().lower() if value is not None else ""
    return v if v in allowed else default


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _parse_ts(value) -> Optional[str]:
    """Return an ISO string Postgres can cast to timestamptz, or None."""
    if not value:
        return None
    s = str(value).strip()
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        d = _parse_date(s)
        return d.isoformat() if d else None


def _clean(value, maxlen=500) -> Optional[str]:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s[:maxlen] or None


def match_institution(name, institutions: list[dict]) -> Optional[str]:
    """Match a name against institution names/aliases (case-insensitive, exact). Never creates."""
    key = _clean(name)
    if not key:
        return None
    key = key.lower()
    for inst in institutions:
        names = [inst.get("name", "")] + list(inst.get("aliases") or [])
        if any((n or "").strip().lower() == key for n in names):
            return str(inst["id"])
    return None


def validate_metadata(raw, institutions: list[dict]) -> dict:
    """Coerce LLM JSON to values that satisfy the schema CHECKs. Bad values fall back."""
    raw = raw if isinstance(raw, dict) else {}
    parties, seen = [], set()
    for p in raw.get("parties") or []:
        if not isinstance(p, dict):
            continue
        name = _clean(p.get("name"), 200)
        if not name:
            continue
        role = _enum(p.get("role"), PARTY_ROLES, "mentioned")
        title = _clean(p.get("role_title"), 200)
        key = (name.lower(), role, (title or "").lower())
        if key in seen:
            continue
        seen.add(key)
        email_addr = _clean(p.get("email"), 200)
        parties.append({
            "name": name, "role": role, "role_title": title,
            "email": email_addr if email_addr and "@" in email_addr else None,
            "institution_id": match_institution(p.get("institution"), institutions),
        })
    refs, seen_refs = [], set()
    for r in raw.get("reference_numbers") or []:
        if isinstance(r, str):
            r = {"value": r}
        if not isinstance(r, dict):
            continue
        val = _clean(r.get("value"), 200)
        norm = value_norm(val or "")
        if not norm or norm in seen_refs:
            continue
        seen_refs.add(norm)
        refs.append({"kind": _clean(r.get("kind"), 60) or "other", "value": val, "value_norm": norm})
    return {
        "doc_type": _enum(raw.get("doc_type"), DOC_TYPES, "other"),
        "title": _clean(raw.get("title"), 300),
        "institution_id": match_institution(raw.get("institution"), institutions),
        "institution_name": next((i["name"] for i in institutions
                                  if str(i["id"]) == match_institution(raw.get("institution"), institutions)), None),
        "occurred_on": _parse_date(raw.get("occurred_on")),
        "sent_at": _parse_ts(raw.get("sent_at")),
        "authored_on": _parse_date(raw.get("authored_on")),
        "parties": parties,
        "reference_numbers": refs,
        "thread_subject": _clean(raw.get("thread_subject"), 300),
    }


HEADER_MAX_CHARS = 300
_EMAIL_RE = re.compile(r"\S+@\S+")


def _hdr(value, maxlen=60) -> Optional[str]:
    """Header-safe text: no email addresses, single line, truncated."""
    v = re.sub(r"\s+", " ", _EMAIL_RE.sub("", str(value or ""))).strip(" <>,;")
    if not v:
        return None
    return v if len(v) <= maxlen else v[:maxlen - 1].rstrip() + "…"


def build_chunk_header(meta: dict, parties: list[dict], page_no) -> str:
    """
    One-line context for embedding only (chunks.content stays raw):
    [doc_type · institution · date · from SENDER (role) to RECIPIENTS · re: SUBJECT · p.N]
    Missing parts are omitted; never prints None or email addresses; capped at ~300 chars.
    """
    parts = []
    parts.append(_hdr(meta.get("doc_type")))
    parts.append(_hdr(meta.get("institution_name")))
    date_s = None
    for key in ("sent_at", "occurred_on", "authored_on"):
        val = meta.get(key)
        d = val if isinstance(val, date) and not isinstance(val, datetime) else (
            val.date() if isinstance(val, datetime) else _parse_date(val))
        if d:
            date_s = d.isoformat()
            break
    parts.append(date_s)
    sender = next((p for p in parties or [] if p.get("role") == "sender"), None)
    recips = [n for n in (_hdr(p.get("name")) for p in parties or [] if p.get("role") == "recipient") if n][:3]
    seg = []
    if sender and _hdr(sender.get("name")):
        s_txt = _hdr(sender["name"])
        if _hdr(sender.get("role_title")):
            s_txt += f" ({_hdr(sender['role_title'], 40)})"
        seg.append(f"from {s_txt}")
    if recips:
        seg.append("to " + ", ".join(recips))
    parts.append(" ".join(seg) or None)
    subj = _hdr(meta.get("thread_subject"), 100) or _hdr(meta.get("title"), 100)
    parts.append(f"re: {subj}" if subj else None)
    if page_no is not None:
        parts.append(f"p.{page_no}")
    out = "[" + " · ".join(x for x in parts if x) + "]"
    if len(out) > HEADER_MAX_CHARS:
        out = out[:HEADER_MAX_CHARS - 2].rstrip() + "…]"
    return out


def metadata_text(pages: list[str]) -> str:
    """First ~6000 + last ~1500 chars of the full text (headers and signatures)."""
    full = "\n\n".join(pages)
    if len(full) <= META_HEAD_CHARS + META_TAIL_CHARS:
        return full
    return full[:META_HEAD_CHARS] + "\n\n[... middle omitted ...]\n\n" + full[-META_TAIL_CHARS:]


def parse_upload_meta(meta: Optional[dict]) -> dict:
    """S3 object metadata -> source fields. Keys: source-origin, source-detail, source-batch, redaction-status."""
    m = {str(k).lower(): v for k, v in (meta or {}).items()}
    return {
        "source_origin": _enum(m.get("source-origin"), SOURCE_ORIGINS, None),  # NULL when absent/invalid; never guessed
        "source_detail": _clean(m.get("source-detail"), 200),
        "source_batch": _clean(m.get("source-batch"), 200),
        "redaction_status": _enum(m.get("redaction-status"), REDACTION_STATUSES, "unknown"),
    }


def chunk_text(text: str, encode: Callable, decode: Callable,
               size: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """Same sliding-window algorithm as handler._chunk, tokenizer injected."""
    tokens = encode(text)
    out, start = [], 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        out.append(decode(tokens[start:end]))
        start = end - overlap if end < len(tokens) else end
    return out


def chunk_pages(pages: list[tuple[int, str]], encode: Callable, decode: Callable,
                size: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[dict]:
    """Page-aware chunking: a chunk never spans pages. chunk_index runs across the document."""
    chunks, idx = [], 0
    for page_no, text in pages:
        if not (text or "").strip():
            continue
        for piece in chunk_text(text, encode, decode, size, overlap):
            if piece.strip():
                chunks.append({"page_no": page_no, "chunk_index": idx, "content": piece})
                idx += 1
    return chunks


def origin_rank(origin) -> int:
    """Original-page priority: 0 own_record/received/sent, 1 NULL/other/public, 2 gipa."""
    if origin in ("own_record", "received", "sent"):
        return 0
    if origin == "gipa":
        return 2
    return 1


def plan_originals(rows: list[tuple], my_key: tuple) -> tuple[dict, dict]:
    """
    rows: (text_sha256, page_id, rank, created_at, document_id, page_no) of ORIGINAL pages
    (duplicate_of_page_id IS NULL) in other documents of the case.
    my_key: (rank, created_at, document_id) of the document being ingested.
    Lower key wins (rank, then arrival, then id). Returns
      known: hash -> page_id of the best existing original that beats this document
      worse: hash -> [page_ids] of existing originals this document outranks (to be demoted)
    """
    best: dict[str, tuple] = {}
    worse: dict[str, list] = {}
    for h, pid, rank, created, doc, pno in rows:
        key = (rank, created, str(doc), pno)
        if key[:3] < (my_key[0], my_key[1], str(my_key[2])):
            if h not in best or key < best[h][0]:
                best[h] = (key, str(pid))
        else:
            worse.setdefault(h, []).append(str(pid))
    return {h: v[1] for h, v in best.items()}, worse


def assign_duplicates(page_texts: list[str], known: dict[str, str]) -> list[dict]:
    """
    Decide per page (in order) whether it duplicates an earlier page.
    known: text_sha256 -> id of the ORIGINAL page already in the case (earlier arrival wins).
    Returns [{page_no, hash, dup_of_existing: id|None, dup_of_page_no: int|None}].
    A later page in the same document with the same hash points at the first one here.
    """
    first_here: dict[str, int] = {}
    out = []
    for i, text in enumerate(page_texts, start=1):
        h = page_hash(text)
        rec = {"page_no": i, "hash": h, "dup_of_existing": None, "dup_of_page_no": None}
        if h is not None:
            if h in known:
                rec["dup_of_existing"] = known[h]
            elif h in first_here:
                rec["dup_of_page_no"] = first_here[h]
            else:
                first_here[h] = i
        out.append(rec)
    return out


def event_windows(pages: list[tuple[int, str]], max_chars: int = EVENT_WINDOW_CHARS) -> list[list[tuple[int, str]]]:
    """Group whole pages into windows <= max_chars; an oversized page is split but keeps its page_no."""
    windows, cur, size = [], [], 0
    for page_no, text in pages:
        text = text or ""
        parts = [text[i:i + max_chars] for i in range(0, len(text), max_chars)] or [""]
        for part in parts:
            if cur and size + len(part) > max_chars:
                windows.append(cur)
                cur, size = [], 0
            cur.append((page_no, part))
            size += len(part)
    if cur:
        windows.append(cur)
    return [w for w in windows if any(t.strip() for _, t in w)]


def validate_events(raw, valid_pages: set[int]) -> list[dict]:
    """Keep only events with a valid date. Month/year-only dates get matching precision."""
    items = raw.get("events") if isinstance(raw, dict) else None
    out = []
    for e in items or []:
        if not isinstance(e, dict):
            continue
        ds = str(e.get("occurred_on") or "").strip()
        precision = _enum(e.get("occurred_precision"), PRECISIONS, "day")
        d = _parse_date(ds) if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", ds) else None
        if d is None and re.fullmatch(r"\d{4}-\d{2}", ds):
            d = _parse_date(ds + "-01")
            precision = "month"
        elif d is None and re.fullmatch(r"\d{4}", ds):
            d = _parse_date(ds + "-01-01")
            precision = "year"
        title = _clean(e.get("title"), 300)
        if d is None or not title:
            continue
        try:
            pn = int(e.get("page_no"))
        except (TypeError, ValueError):
            pn = None
        out.append({
            "occurred_on": d, "occurred_precision": precision,
            "event_type": _clean(e.get("event_type"), 100),
            "title": title,
            "summary": _clean(e.get("summary"), 1000),
            "excerpt": (str(e.get("excerpt")).strip()[:EXCERPT_MAX_CHARS] or None) if e.get("excerpt") else None,
            "page_no": pn if pn in valid_pages else None,
            "parties": [n for n in (_clean(x, 200) for x in (e.get("parties") or [])) if n],
        })
    return out


def event_embedding_text(ev: dict) -> str:
    return " ".join(x for x in (ev.get("title"), ev.get("summary"), ev.get("excerpt")) if x).strip()


def vec_literal(emb) -> str:
    return "[" + ",".join(str(x) for x in emb) + "]"


# ── Prompts ────────────────────────────────────────────────────────────────────

METADATA_SYSTEM = (
    "You extract OBJECTIVE metadata from one legal-case document. Do not interpret or judge. "
    "Return a JSON object with keys: "
    f"doc_type (one of {sorted(DOC_TYPES)}), title (short), institution (issuing organisation name or null), "
    "occurred_on (YYYY-MM-DD the document is about, or null), sent_at (ISO 8601 when sent/issued, or null), "
    "authored_on (YYYY-MM-DD written, or null), "
    f"parties (array of {{name, role (one of {sorted(PARTY_ROLES)}), role_title, email, institution}}), "
    "reference_numbers (array of {kind, value}), thread_subject (email subject without Re:/Fwd:, or null). "
    "Use null when unknown. Never guess."
)

EVENTS_SYSTEM = (
    "You extract dated events from part of a legal-case document. Pages are marked [[PAGE n]]. "
    "Return a JSON object {\"events\": [...]}; each event: {occurred_on (YYYY-MM-DD, or YYYY-MM / YYYY if only that is known), "
    f"occurred_precision (one of {sorted(PRECISIONS)}), event_type (short free-text noun phrase), title (one sentence), "
    "summary (1-2 plain sentences), excerpt (short verbatim quote), page_no (int), parties (names involved)}. "
    "State facts only. Omit events with no date."
)


# ── DB steps (conn is psycopg2-like; all SQL parameterised) ────────────────────

def check_case_owner(cur, user_id: str, case_id: str) -> None:
    if not user_id or not case_id or user_id == "anon":
        raise OwnershipError("missing user or case")
    cur.execute("SELECT 1 FROM cases WHERE id = %s AND user_id = %s", (case_id, user_id))
    if cur.fetchone() is None:
        raise OwnershipError("case not found for user")


def _upsert_party(cur, user_id, case_id, p: dict) -> str:
    if p.get("email"):  # same person = same email within a case
        cur.execute("SELECT id FROM parties WHERE case_id = %s AND user_id = %s AND lower(email) = %s",
                    (case_id, user_id, p["email"].lower()))
        row = cur.fetchone()
        if row:
            return str(row[0])
    cur.execute(
        "SELECT id FROM parties WHERE case_id = %s AND user_id = %s AND name = %s "
        "AND role_title IS NOT DISTINCT FROM %s",
        (case_id, user_id, p["name"], p["role_title"]),
    )
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute(
        "INSERT INTO parties (case_id, user_id, name, role_title, institution_id, email) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (case_id, name, role_title) DO NOTHING RETURNING id",
        (case_id, user_id, p["name"], p["role_title"], p["institution_id"], p["email"]),
    )
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute(
        "SELECT id FROM parties WHERE case_id = %s AND user_id = %s AND name = %s "
        "AND role_title IS NOT DISTINCT FROM %s",
        (case_id, user_id, p["name"], p["role_title"]),
    )
    return str(cur.fetchone()[0])


def _upsert_thread(cur, user_id, case_id, subject: Optional[str], sent_at, external_id: Optional[str] = None) -> str:
    if external_id:
        cur.execute("SELECT id FROM threads WHERE case_id = %s AND user_id = %s AND external_id = %s",
                    (case_id, user_id, external_id))
    else:
        cur.execute("SELECT id FROM threads WHERE case_id = %s AND user_id = %s AND subject = %s",
                    (case_id, user_id, subject))
    row = cur.fetchone()
    if row:
        tid = str(row[0])
        if sent_at:
            cur.execute(
                "UPDATE threads SET first_sent_at = LEAST(COALESCE(first_sent_at, %s::timestamptz), %s::timestamptz), "
                "last_sent_at = GREATEST(COALESCE(last_sent_at, %s::timestamptz), %s::timestamptz) "
                "WHERE id = %s AND user_id = %s", (sent_at, sent_at, sent_at, sent_at, tid, user_id))
        return tid
    cur.execute(
        "INSERT INTO threads (case_id, user_id, subject, first_sent_at, last_sent_at, external_id) "
        "VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz, %s) RETURNING id",
        (case_id, user_id, subject, sent_at, sent_at, external_id))
    return str(cur.fetchone()[0])


def ingest_document(
    conn,
    *,
    user_id: str,
    case_id: str,
    filename: str,
    s3_key: Optional[str],
    content_sha256: str,
    pages: list[str],
    upload_meta: Optional[dict],
    llm_json: Callable[[str, str], dict],
    embed: Callable[[list[str]], list[list[float]]],
    encode: Callable,
    decode: Callable,
    force: bool = False,
    # --- optional, used by importers (S3 path leaves all of these at defaults) ---
    prevalidated_meta: Optional[dict] = None,   # validate_metadata()-shaped; skips the metadata LLM call
    index_texts: Optional[list[str]] = None,    # text to chunk/embed/extract events from (pages are still hashed raw)
    numbered_pages: bool = True,                # False: pseudo-pages; chunks/events get page_no NULL
    external_id: Optional[str] = None,
    source_label: Optional[str] = None,
    thread_external_id: Optional[str] = None,   # email: upsert thread by external id
    thread_id: Optional[str] = None,            # attachment: reuse the parent's thread
    link_to_document_id: Optional[str] = None,  # document_links(kind='attachment_of') -> this document
    defer_commit: bool = False,                 # caller owns the transaction (no commit/rollback here)
) -> dict:
    """
    Ingest one document into the v2 model. pages[i] is the FULL text of page i+1.
    Returns {"status": "ready"|"duplicate_document", "document_id": ...}. Raises on failure
    (after marking the document 'failed'). Idempotent per (case_id, content_sha256).
    """
    src = parse_upload_meta(upload_meta)
    cur = conn.cursor()

    def _commit():
        if not defer_commit:
            conn.commit()

    def _rollback():
        if not defer_commit:
            conn.rollback()

    # 1. ownership, fail closed
    try:
        check_case_owner(cur, user_id, case_id)
    except Exception:
        _rollback()
        raise

    # 2. documents row (ON CONFLICT DO NOTHING on the unique sha index)
    cur.execute(
        "INSERT INTO documents (case_id, user_id, s3_key, filename, content_sha256, page_count, "
        "source_origin, source_detail, source_batch, redaction_status, external_id, source_label, "
        "ingest_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending') "
        "ON CONFLICT (case_id, content_sha256) WHERE content_sha256 IS NOT NULL DO NOTHING "
        "RETURNING id, created_at",
        (case_id, user_id, s3_key, filename, content_sha256, len(pages),
         src["source_origin"], src["source_detail"], src["source_batch"], src["redaction_status"],
         external_id, source_label),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "SELECT id, created_at, ingest_status FROM documents "
            "WHERE case_id = %s AND user_id = %s AND content_sha256 = %s",
            (case_id, user_id, content_sha256),
        )
        existing = cur.fetchone()
        if existing is None:
            _rollback()
            raise OwnershipError("document hash conflict outside this user's case")
        doc_id, created_at, status = str(existing[0]), existing[1], existing[2]
        if status == "ready" and not force:
            _commit()
            return {"status": "duplicate_document", "document_id": doc_id}
    else:
        doc_id, created_at = str(row[0]), row[1]

    try:
        cur.execute("UPDATE documents SET ingest_status = 'processing' WHERE id = %s AND user_id = %s",
                    (doc_id, user_id))
        _commit()

        # 3. read-only context: institutions, earlier original pages in this case
        cur.execute("SELECT id, name, aliases FROM institutions WHERE user_id IS NULL OR user_id = %s", (user_id,))
        institutions = [{"id": r[0], "name": r[1], "aliases": r[2] or []} for r in cur.fetchall()]

        hashes = [h for h in (page_hash(t) for t in pages) if h]
        known: dict[str, str] = {}
        worse: dict[str, list] = {}
        if hashes:
            cur.execute(
                "SELECT p.text_sha256, p.id, "
                "CASE WHEN d.source_origin IN ('own_record','received','sent') THEN 0 "
                "WHEN d.source_origin = 'gipa' THEN 2 ELSE 1 END AS rank, "
                "d.created_at, d.id, p.page_no "
                "FROM document_pages p JOIN documents d ON d.id = p.document_id "
                "WHERE p.case_id = %s AND p.user_id = %s AND p.text_sha256 = ANY(%s) "
                "AND p.duplicate_of_page_id IS NULL AND p.document_id <> %s",
                (case_id, user_id, hashes, doc_id),
            )
            known, worse = plan_originals(cur.fetchall(), (origin_rank(src["source_origin"]), created_at, doc_id))
        _rollback()  # end the read transaction

        dups = assign_duplicates(pages, known)
        idx_pages = index_texts if index_texts is not None else pages
        new_pages = [(d["page_no"], idx_pages[d["page_no"] - 1]) for d in dups
                     if d["dup_of_existing"] is None and d["dup_of_page_no"] is None]

        # 4. LLM + embeddings (no DB transaction held open)
        if prevalidated_meta is not None:
            meta = {"title": None, "doc_type": "other", "institution_id": None, "institution_name": None,
                    "occurred_on": None, "sent_at": None, "authored_on": None, "parties": [],
                    "reference_numbers": [], "thread_subject": None, **prevalidated_meta}
        else:
            meta = validate_metadata(llm_json(METADATA_SYSTEM, f"Document ({filename}):\n\n{metadata_text(pages)}"),
                                     institutions)
        chunks = chunk_pages(new_pages, encode, decode)
        shown_no = (lambda c: c["page_no"]) if numbered_pages else (lambda c: None)
        chunk_emb = embed([build_chunk_header(meta, meta["parties"], shown_no(c)) + "\n" + c["content"]
                           for c in chunks]) if chunks else []  # header is embedding-only

        events = []
        for win in event_windows(new_pages):
            body = "\n\n".join(f"[[PAGE {n}]]\n{t}" for n, t in win)
            events.extend(validate_events(llm_json(EVENTS_SYSTEM, f"Document ({filename}):\n\n{body}"),
                                          {n for n, _ in win} if numbered_pages else set()))
        ev_emb = embed([event_embedding_text(e) for e in events]) if events else []

        # 5. one transaction: delete + reinsert everything for this document
        cur.execute("DELETE FROM events WHERE document_id = %s AND user_id = %s", (doc_id, user_id))
        cur.execute("DELETE FROM chunks WHERE document_id = %s AND user_id = %s", (doc_id, user_id))
        cur.execute("DELETE FROM document_pages WHERE document_id = %s AND user_id = %s", (doc_id, user_id))
        cur.execute("DELETE FROM document_parties WHERE document_id = %s", (doc_id,))
        cur.execute("DELETE FROM reference_numbers WHERE document_id = %s AND user_id = %s", (doc_id, user_id))

        page_ids: dict[int, str] = {}
        for d in dups:
            n = d["page_no"]
            dup_id = d["dup_of_existing"] or (page_ids.get(d["dup_of_page_no"]) if d["dup_of_page_no"] else None)
            cur.execute(
                "INSERT INTO document_pages (document_id, case_id, user_id, page_no, text_sha256, char_count, "
                "duplicate_of_page_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (doc_id, case_id, user_id, n, d["hash"], len(pages[n - 1]), dup_id),
            )
            page_ids[n] = str(cur.fetchone()[0])
            demote = worse.get(d["hash"]) if (dup_id is None and d["hash"]) else None
            if demote:
                # lower-priority copies become duplicates of this page: drop their chunks, re-point their dependants
                cur.execute("DELETE FROM chunks WHERE page_id = ANY(%s::uuid[]) AND user_id = %s", (demote, user_id))
                cur.execute(
                    "UPDATE document_pages SET duplicate_of_page_id = %s WHERE case_id = %s AND user_id = %s "
                    "AND (id = ANY(%s::uuid[]) OR duplicate_of_page_id = ANY(%s::uuid[]))",
                    (page_ids[n], case_id, user_id, demote, demote),
                )

        for c, emb in zip(chunks, chunk_emb):
            cur.execute(
                "INSERT INTO chunks (document_id, case_id, user_id, page_id, page_no, chunk_index, content, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                (doc_id, case_id, user_id, page_ids[c["page_no"]], shown_no(c), c["chunk_index"],
                 c["content"], vec_literal(emb)),
            )

        # parties / document_parties
        party_by_name: dict[str, str] = {}
        for p in meta["parties"]:
            pid = _upsert_party(cur, user_id, case_id, p)
            party_by_name.setdefault(p["name"].lower(), pid)
            cur.execute("INSERT INTO document_parties (document_id, party_id, role) VALUES (%s, %s, %s) "
                        "ON CONFLICT DO NOTHING", (doc_id, pid, p["role"]))
        for r in meta["reference_numbers"]:
            cur.execute(
                "INSERT INTO reference_numbers (case_id, user_id, document_id, institution_id, kind, value, value_norm) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (case_id, user_id, doc_id, meta["institution_id"], r["kind"], r["value"], r["value_norm"]))
        if thread_id is None and meta["doc_type"] == "email" and (meta["thread_subject"] or thread_external_id):
            thread_id = _upsert_thread(cur, user_id, case_id, meta["thread_subject"], meta["sent_at"],
                                       thread_external_id)

        cur.execute(
            "UPDATE documents SET title = %s, doc_type = %s, institution_id = %s, occurred_on = %s, "
            "sent_at = %s::timestamptz, authored_on = %s, thread_id = %s, page_count = %s "
            "WHERE id = %s AND user_id = %s",
            (meta["title"], meta["doc_type"], meta["institution_id"], meta["occurred_on"], meta["sent_at"],
             meta["authored_on"], thread_id, len(pages), doc_id, user_id))

        if link_to_document_id:
            cur.execute("INSERT INTO document_links (from_document_id, to_document_id, kind) "
                        "VALUES (%s, %s, 'attachment_of') ON CONFLICT DO NOTHING", (doc_id, link_to_document_id))

        for ev, emb in zip(events, ev_emb):
            cur.execute(
                "INSERT INTO events (case_id, user_id, document_id, occurred_on, occurred_precision, event_type, "
                "title, summary, excerpt, page_no, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector) RETURNING id",
                (case_id, user_id, doc_id, ev["occurred_on"], ev["occurred_precision"], ev["event_type"],
                 ev["title"], ev["summary"], ev["excerpt"], ev["page_no"], vec_literal(emb)))
            eid = str(cur.fetchone()[0])
            for name in ev["parties"]:
                pid = party_by_name.get(name.lower())
                if pid:
                    cur.execute("INSERT INTO event_parties (event_id, party_id) VALUES (%s, %s) "
                                "ON CONFLICT DO NOTHING", (eid, pid))

        cur.execute("UPDATE documents SET ingest_status = 'ready' WHERE id = %s AND user_id = %s", (doc_id, user_id))
        _commit()
        logger.info("v2 ingest ready doc=%s pages=%d new_pages=%d chunks=%d events=%d",
                    doc_id, len(pages), len(new_pages), len(chunks), len(events))
        return {"status": "ready", "document_id": doc_id, "pages": len(pages),
                "duplicate_pages": len(pages) - len(new_pages), "chunks": len(chunks), "events": len(events)}
    except Exception:
        if defer_commit:
            raise  # caller rolls back the whole unit of work
        conn.rollback()
        try:
            cur.execute("UPDATE documents SET ingest_status = 'failed' WHERE id = %s AND user_id = %s",
                        (doc_id, user_id))
            conn.commit()
        except Exception:
            logger.exception("could not mark document failed doc=%s", doc_id)
        raise
