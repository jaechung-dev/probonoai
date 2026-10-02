"""
Gmail-export importer helpers (stdlib only; DB, LLM, embedder and file readers are injected).

Pure functions (address parsing, owner detection, quote stripping, manifest parsing, metadata
building) plus import_message(), which feeds one email and its attachments into
services.ingestion.v2.ingest_document inside ONE caller-owned transaction.
Never writes issues; manifest `tags` are ignored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime
from typing import Callable, Optional

from services.ingestion import v2

logger = logging.getLogger(__name__)

MIN_NATIVE_PAGE_CHARS = 50


# ── Addresses and owner ────────────────────────────────────────────────────────

def parse_address(value) -> tuple[str, str]:
    """'Name <a@b>' | '"Name" <a@b>' | 'a@b' | 'Name' | {'name','email'} -> (name, email_lower)."""
    if isinstance(value, dict):
        return (str(value.get("name") or "").strip(), str(value.get("email") or value.get("address") or "").strip().lower())
    pairs = getaddresses([str(value or "")])
    name, addr = pairs[0] if pairs else ("", "")
    if "@" not in addr:
        if addr and not name:
            name, addr = addr, ""
        else:
            addr = ""
    return name.strip().strip("\"'"), addr.strip().lower()


def parse_address_list(value) -> list[tuple[str, str]]:
    """to/cc may be a string (comma/semicolon separated), a list of strings/dicts, or missing."""
    if not value:
        return []
    if isinstance(value, str):
        return [parse_address(f"{n} <{a}>" if a else n) for n, a in getaddresses([value.replace(";", ",")]) if (n or a)]
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            if isinstance(v, str) and ("," in v or ";" in v):
                out.extend(parse_address_list(v))
            elif v:
                out.append(parse_address(v))
        return [a for a in out if a[0] or a[1]]
    return []


def is_owner(name: str, email_addr: str, owner_emails, owner_names) -> bool:
    emails = {e.strip().lower() for e in owner_emails or [] if e}
    names = {n.strip().lower() for n in owner_names or [] if n}
    return bool((email_addr and email_addr.lower() in emails) or (name and name.strip().lower() in names))


def message_origin(sender_name: str, sender_email: str, owner_emails, owner_names) -> str:
    return "sent" if is_owner(sender_name, sender_email, owner_emails, owner_names) else "received"


_SUBJ_PREFIX = re.compile(r"^\s*((re|fwd?|fw|답장|회신|전달|转发|回复)\s*(\[\d+\])?\s*[:：]\s*)+", re.I)


def normalise_subject(subject) -> Optional[str]:
    s = _SUBJ_PREFIX.sub("", str(subject or "")).strip()
    return re.sub(r"\s+", " ", s)[:300] or None


# ── Quoted reply history ───────────────────────────────────────────────────────

_ORIG_MSG = re.compile(r"^\s*[-_=*\s]*(original message|원본 메시지|원본메시지)[-_=*\s]*$", re.I)
_WROTE_END = re.compile(r"(wrote|writes)\s*:\s*$|님이 작성\s*:\s*$|작성함\s*:\s*$", re.I)
_ON_START = re.compile(r"^\s*(on|at)\s+.{3,}", re.I)
_KO_WROTE = re.compile(r".*님이 작성\s*:\s*$")
_HDR_FROM = re.compile(r"^\W*(from|de|발신|보낸 사람)\W*\s*:", re.I)
_HDR_SENT = re.compile(r"^\W*(sent|보낸 날짜|보낸 시간)\W*\s*:", re.I)
_HDR_TO = re.compile(r"^\W*(to|subject|받는 사람|제목)\W*\s*:", re.I)
_FORWARDED = re.compile(r"forwarded message", re.I)


def strip_quoted(text: str) -> str:
    """
    Remove quoted reply history: 'On <date>, X wrote:' (also wrapped over 2 lines), Korean '...님이 작성:',
    '-----Original Message-----', Outlook From/Sent/To header blocks, and '>' lines. Forwarded-message
    headers are kept. Everything from the first reply marker onwards is dropped.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    cut = len(lines)
    for i, ln in enumerate(lines):
        if _ORIG_MSG.match(ln):
            cut = i
            break
        if _KO_WROTE.match(ln):
            cut = i
            break
        if _ON_START.match(ln):
            joined = " ".join(x.strip() for x in lines[i:i + 3])
            end = next((k for k in range(i, min(i + 3, len(lines))) if _WROTE_END.search(lines[k].strip())), None)
            if end is not None and len(joined) < 400:
                cut = i
                break
        if _HDR_FROM.match(ln) and not any(_FORWARDED.search(x) for x in lines[max(0, i - 3):i]):
            window = lines[i + 1:i + 6]
            if any(_HDR_SENT.match(x) for x in window) and any(_HDR_TO.match(x) for x in window):
                cut = i
                break
    kept = [ln for ln in lines[:cut] if not ln.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ── Manifest ───────────────────────────────────────────────────────────────────

def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def parse_date_received(value) -> Optional[str]:
    """ISO string (kept as given) or RFC 2822 -> ISO; None if unparseable. No .eml Date parsing."""
    if not value:
        return None
    s = str(value).strip()
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def parse_manifest_line(line: str) -> tuple[Optional[dict], Optional[str]]:
    """-> (normalised message, None) | (None, 'parse_error'|'skipped'). Never raises, never echoes content."""
    line = (line or "").strip()
    if not line:
        return None, "skipped"
    try:
        raw = json.loads(line)
    except ValueError:
        return None, "parse_error"
    if not isinstance(raw, dict):
        return None, "parse_error"
    mid = str(raw.get("gmail_message_id") or raw.get("message_id") or "").strip()
    if not mid:
        return None, "skipped"
    s_name, s_email = parse_address(raw.get("sender") or raw.get("from"))
    return {
        "message_id": mid,
        "thread_id": str(raw.get("gmail_thread_id") or "").strip() or None,
        "subject": str(raw.get("subject") or "").strip(),
        "sender": (s_name, s_email),
        "to": parse_address_list(raw.get("to")),
        "cc": parse_address_list(raw.get("cc")),
        "sent_at": parse_date_received(raw.get("date_received") or raw.get("date")),
        "email_path": raw.get("email_path"),
        "email_text_path": raw.get("email_text_path"),
        "attachments": [str(a.get("name") or a.get("filename") or "") if isinstance(a, dict) else str(a)
                        for a in _as_list(raw.get("attachments")) if a],
        "persons": [str(x) for x in _as_list(raw.get("persons")) if x],
        "organisations": [str(x) for x in _as_list(raw.get("organisations")) if x],
        "reference_numbers": [x for x in _as_list(raw.get("reference_numbers")) if x],
    }, None


def read_manifest(lines) -> tuple[list[dict], dict]:
    """Parse all manifest lines, sort by date ascending (undated last, stable). Returns (messages, counts)."""
    msgs, counts = [], {"parse_errors": 0, "skipped": 0}
    seen = set()
    for ln in lines:
        m, err = parse_manifest_line(ln)
        if err == "parse_error":
            counts["parse_errors"] += 1
        elif err:
            counts["skipped"] += 1
        elif m["message_id"] in seen:
            counts["skipped"] += 1
        else:
            seen.add(m["message_id"])
            msgs.append(m)
    msgs.sort(key=lambda m: (m["sent_at"] is None, _sort_key(m["sent_at"])))
    return msgs, counts


def _sort_key(ts) -> float:
    if not ts:
        return 0.0
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.timestamp() if d.tzinfo else d.replace(tzinfo=None).timestamp()
    except ValueError:
        return 0.0


def plan_counts(msgs: list[dict], base: dict, owner_emails, owner_names) -> dict:
    """Counts only (safe to print)."""
    sent = sum(1 for m in msgs if message_origin(*m["sender"], owner_emails, owner_names) == "sent")
    return {
        "emails": len(msgs), "attachments": sum(len(m["attachments"]) for m in msgs),
        "sent": sent, "received": len(msgs) - sent,
        "threads": len({m["thread_id"] for m in msgs if m["thread_id"]}),
        "skipped": base.get("skipped", 0), "parse_errors": base.get("parse_errors", 0),
    }


# ── Metadata and parties ───────────────────────────────────────────────────────

def build_parties(msg: dict, existing_parties: list[tuple[str, Optional[str]]] = ()) -> list[dict]:
    """sender/to/cc -> party dicts (v2 shape), deduplicated by email else name per role.
    Manifest persons only become 'mentioned' parties when they match an EXISTING party (name)."""
    out, seen = [], set()

    def add(name, addr, role, role_title=None):
        name = (name or "").strip() or addr
        if not name:
            return
        key = (addr or name.lower(), role)
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name, "role": role, "role_title": role_title, "email": addr or None,
                    "institution_id": None})

    add(*msg["sender"], "sender")
    for n, a in msg["to"]:
        add(n, a, "recipient")
    for n, a in msg["cc"]:
        add(n, a, "cc")
    have = {(x["name"].lower()) for x in out}
    by_name = {n.lower(): t for n, t in existing_parties}
    for person in msg["persons"]:
        k = person.strip().lower()
        if k in by_name and k not in have:
            out.append({"name": person.strip(), "role": "mentioned", "role_title": by_name[k],
                        "email": None, "institution_id": None})
    return out


def build_email_meta(msg: dict, institutions: list[dict], existing_parties=()) -> dict:
    org = next((o for o in msg["organisations"] if v2.match_institution(o, institutions)), None)
    refs = []
    for r in msg["reference_numbers"]:
        refs.append(r if isinstance(r, dict) else {"value": str(r)})
    meta = v2.validate_metadata({
        "doc_type": "email", "title": msg["subject"], "institution": org, "sent_at": msg["sent_at"],
        "reference_numbers": refs, "thread_subject": normalise_subject(msg["subject"]),
    }, institutions)
    meta["parties"] = build_parties(msg, existing_parties)  # keep emails; validate_metadata has no email-less dedupe need
    return meta


def build_attachment_meta(msg: dict, email_meta: dict, name: str) -> dict:
    return {
        "doc_type": "attachment", "title": (name or "")[:300] or None, "institution_id": None, "institution_name": None,
        "occurred_on": None, "sent_at": email_meta["sent_at"], "authored_on": None,
        "parties": email_meta["parties"], "reference_numbers": [],
        "thread_subject": email_meta["thread_subject"],  # context for the embedding header only
    }


def content_hash(external_id: str, raw: str) -> str:
    """Unique per message/attachment so identical bodies in different mails are never merged at
    document level (duplicate PAGES are still detected by the page hash)."""
    return hashlib.sha256((external_id + "\0" + (raw or "")).encode("utf-8")).hexdigest()


# ── Attachment text selection ──────────────────────────────────────────────────

def find_attachment_file(names: list[str], entry: str, message_id: str) -> Optional[str]:
    """Locate the original in attachments/: exact name, else '<ts>_<msgid>_<nn>_<origname>' ending."""
    if entry in names:
        return entry
    cands = [n for n in names if n.endswith("_" + entry)]
    pref = [n for n in cands if message_id in n]
    return (pref or cands or [None])[0]


def choose_pages(native_pages: Optional[list[str]], fallback_md: Optional[str]) -> tuple[list[str], bool]:
    """
    Prefer page-aware native text from the original PDF. If the original is missing, or image-only
    (under half of the pages have native text), use the extracted .md as ONE pseudo-page (numbered=False).
    Returns (pages, numbered). ([], False) when nothing usable.
    """
    if native_pages:
        good = sum(1 for p in native_pages if len((p or "").strip()) >= MIN_NATIVE_PAGE_CHARS)
        if good * 2 >= len(native_pages):
            return [(p or "").strip() for p in native_pages], True
    if fallback_md and fallback_md.strip():
        return [fallback_md.strip()], False
    return [], False


# ── One message (+ attachments) in one transaction ─────────────────────────────

def import_message(conn, *, msg: dict, user_id: str, case_id: str, category: str,
                   owner_emails, owner_names, keep_quotes: bool,
                   read_text: Callable[[str], Optional[str]],
                   read_pdf_pages: Callable[[str], Optional[list[str]]],
                   list_attachment_files: Callable[[], list[str]],
                   attachment_text_files: Callable[[dict], dict],
                   llm_json, embed, encode, decode) -> dict:
    """
    Import one email and its attachments. The caller owns the transaction: commit after success,
    rollback on exception. Returns counts; {'status': 'exists'} if the email external_id already exists.
    """
    cur = conn.cursor()
    v2.check_case_owner(cur, user_id, case_id)
    cur.execute("SELECT 1 FROM documents WHERE case_id = %s AND user_id = %s AND external_id = %s",
                (case_id, user_id, msg["message_id"]))
    if cur.fetchone():
        return {"status": "exists", "attachments": 0}

    cur.execute("SELECT id, name, aliases FROM institutions WHERE user_id IS NULL OR user_id = %s", (user_id,))
    institutions = [{"id": r[0], "name": r[1], "aliases": r[2] or []} for r in cur.fetchall()]
    cur.execute("SELECT name, role_title FROM parties WHERE case_id = %s AND user_id = %s", (case_id, user_id))
    existing = [(r[0], r[1]) for r in cur.fetchall()]

    origin = message_origin(*msg["sender"], owner_emails, owner_names)
    upload_meta = {"source-origin": origin}
    meta = build_email_meta(msg, institutions, existing)

    raw = read_text(msg["email_text_path"]) if msg.get("email_text_path") else None
    if raw is None:
        raise ValueError("email text missing")
    raw = raw.strip()
    indexed = raw if keep_quotes else (strip_quoted(raw) or raw)
    common = dict(user_id=user_id, case_id=case_id, upload_meta=upload_meta, llm_json=llm_json, embed=embed,
                  encode=encode, decode=decode, source_label=category, defer_commit=True)

    res = v2.ingest_document(
        conn, filename="email.md", s3_key=None, external_id=msg["message_id"],
        content_sha256=content_hash(msg["message_id"], raw), pages=[raw], index_texts=[indexed],
        numbered_pages=False, prevalidated_meta=meta, thread_external_id=msg["thread_id"], **common)
    email_id = res["document_id"]
    cur.execute("SELECT thread_id FROM documents WHERE id = %s AND user_id = %s", (email_id, user_id))
    row = cur.fetchone()
    thread_id = str(row[0]) if row and row[0] else None

    files = list_attachment_files()
    n_att = 0
    for idx, entry in enumerate(msg["attachments"], start=1):
        ext_id = f"{msg['message_id']}#{idx:02d}"
        orig = find_attachment_file(files, entry, msg["message_id"])
        native = read_pdf_pages(orig) if orig and orig.lower().endswith(".pdf") else None
        md = attachment_text_files(msg).get(idx)
        pages, numbered = choose_pages(native, read_text(md) if md else None)
        if not pages:
            continue
        v2.ingest_document(
            conn, filename=entry or ext_id, s3_key=None, external_id=ext_id,
            content_sha256=content_hash(ext_id, "\n".join(pages)), pages=pages, numbered_pages=numbered,
            prevalidated_meta=build_attachment_meta(msg, meta, entry), thread_id=thread_id,
            link_to_document_id=email_id, **common)
        n_att += 1
    return {"status": "imported", "attachments": n_att, "origin": origin}
