"""
Ingestion Lambda — triggered by SQS (S3 PutObject events on the uploads bucket).

Pipeline per file:
  S3 key → download → parse → chunk (~500 tokens) → embed → case_chunks → update intake status

Parsing strategy:
  .pdf              → PyMuPDF (native text); sparse pages → Amazon Textract (OCR)
  .docx             → python-docx
  .txt / .text      → raw UTF-8 read
  .eml              → email headers + plain-text body (attachments ignored)
  images (.jpg .jpeg .png .tiff) → Amazon Textract DetectDocumentText
"""
import email
import io
import json
import logging
import os
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
import fitz  # PyMuPDF
import docx  # python-docx
import openai
import psycopg2
import tiktoken
from psycopg2.extras import execute_values

# Load secrets from AWS Secrets Manager before reading env vars.
# services.core.settings._load_secrets() populates os.environ at import time.
from services.core.settings import settings as _settings  # noqa: F401

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DATABASE_URL   = os.environ.get("DATABASE_URL", "")
AWS_REGION     = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")

s3       = boto3.client("s3",       region_name=AWS_REGION)
textract = boto3.client("textract", region_name=AWS_REGION)
oai      = openai.OpenAI(api_key=OPENAI_API_KEY)

EMBED_MODEL        = "text-embedding-3-small"
CHUNK_TOKENS       = 500
OVERLAP_TOKENS     = 50
MIN_PDF_CHARS_PAGE = 50   # native text below this → OCR the page

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".text", ".eml", ".jpg", ".jpeg", ".png", ".tiff"}
MAX_BYTES = 25 * 1024 * 1024  # 25 MB

enc = tiktoken.encoding_for_model("text-embedding-3-small")


# ── Entry point ────────────────────────────────────────────────────────────────


def handler(event, context):
    for sqs_record in event["Records"]:
        body = json.loads(sqs_record["body"])
        for s3_record in body.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            key    = unquote_plus(s3_record["s3"]["object"]["key"])
            logger.info("processing bucket=%s key=%s", bucket, key)
            try:
                process_file(bucket, key)
                logger.info("done key=%s", key)
            except Exception:
                logger.exception("failed key=%s", key)
                raise  # let SQS retry / DLQ catch it


# ── Core pipeline ──────────────────────────────────────────────────────────────


def process_file(bucket: str, key: str) -> None:
    ext = Path(key).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        logger.warning("skipping unsupported ext=%s key=%s", ext, key)
        return

    obj  = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()
    meta = obj.get("Metadata", {})

    if len(data) > MAX_BYTES:
        logger.warning("skipping oversized file size=%d key=%s", len(data), key)
        return

    if ext == ".pdf":
        text = _parse_pdf(data, bucket, key)
    elif ext == ".docx":
        text = _parse_docx(data)
    elif ext in (".txt", ".text"):
        text = data.decode("utf-8", errors="replace")
    elif ext == ".eml":
        text = _parse_eml(data)
    else:
        text = _ocr_image(bucket, key)

    text = text.strip()
    if not text:
        logger.warning("no text extracted key=%s", key)
        return

    # Extract user_id from key path: intakes/{user_id}/{uuid}/{filename}
    parts = key.split("/")
    user_id = parts[1] if len(parts) >= 3 else "anon"

    # Prefer case_id from S3 metadata (set by API when uploading to an existing case)
    case_id = meta.get("case-id") or _get_latest_intake_id(user_id)
    chunks  = _chunk(text, key)

    if not chunks:
        return

    embeddings = _embed([c["content"] for c in chunks])
    _store_chunks(user_id, case_id, chunks, embeddings)
    _mark_file_ready(user_id, case_id, key)

    if case_id:
        filename = Path(key).name
        events = _extract_timeline_events(text, filename)
        if events:
            _store_timeline_events(user_id, case_id, key, events)


# ── Parsing ────────────────────────────────────────────────────────────────────


def _parse_pdf(data: bytes, bucket: str, key: str) -> str:
    doc   = fitz.open(stream=data, filetype="pdf")
    pages = []
    ocr_indices = []

    for i, page in enumerate(doc):
        native = page.get_text()
        if len(native.strip()) >= MIN_PDF_CHARS_PAGE:
            pages.append((i, native))
        else:
            ocr_indices.append(i)

    native_text = "\n\n".join(text for _, text in pages)

    if ocr_indices:
        logger.info("OCR needed for %d/%d pages in %s", len(ocr_indices), len(doc), key)
        ocr_text = _ocr_pdf_pages(doc, ocr_indices)
        native_text = (native_text + "\n\n" + ocr_text).strip()

    return native_text


def _ocr_pdf_pages(doc: fitz.Document, page_indices: list[int]) -> str:
    texts = []
    for i in page_indices:
        page = doc[i]
        mat  = fitz.Matrix(2.0, 2.0)  # 2× zoom improves OCR accuracy
        pix  = page.get_pixmap(matrix=mat)
        resp = textract.detect_document_text(Document={"Bytes": pix.tobytes("png")})
        line = " ".join(
            b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"
        )
        texts.append(line)
    return "\n\n".join(texts)


def _parse_docx(data: bytes) -> str:
    d = docx.Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in d.paragraphs if p.text.strip())


def _parse_eml(data: bytes) -> str:
    msg = email.message_from_bytes(data)
    headers = "\n".join(
        f"{h}: {msg[h]}"
        for h in ("From", "To", "Cc", "Date", "Subject")
        if msg[h]
    )
    body_parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            charset = part.get_content_charset() or "utf-8"
            body_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
    body = "\n\n".join(body_parts)
    return f"{headers}\n\n{body}".strip()


def _ocr_image(bucket: str, key: str) -> str:
    resp = textract.detect_document_text(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    return " ".join(
        b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"
    )


# ── Chunking ───────────────────────────────────────────────────────────────────


def _chunk(text: str, source_key: str) -> list[dict]:
    tokens = enc.encode(text)
    chunks = []
    idx    = 0
    start  = 0
    while start < len(tokens):
        end         = min(start + CHUNK_TOKENS, len(tokens))
        chunk_text  = enc.decode(tokens[start:end])
        chunks.append({
            "content":  chunk_text,
            "metadata": {
                "source_key":  source_key,
                "filename":    Path(source_key).name,
                "chunk_index": idx,
            },
        })
        idx  += 1
        start = end - OVERLAP_TOKENS if end < len(tokens) else end
    return chunks


# ── Embedding ──────────────────────────────────────────────────────────────────


def _embed(texts: list[str]) -> list[list[float]]:
    result     = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        resp = oai.embeddings.create(model=EMBED_MODEL, input=texts[i:i + batch_size])
        result.extend(e.embedding for e in resp.data)
    return result


# ── Storage ────────────────────────────────────────────────────────────────────


def _get_latest_intake_id(user_id: str) -> str | None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM case_intakes WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def _extract_timeline_events(text: str, filename: str) -> list[dict]:
    """Ask GPT to pull dated events out of a document. Returns [] on failure."""
    system = (
        "You are a legal document analyser. Extract all events that have a specific date "
        "from the document. Return a JSON object with key 'events' containing an array. "
        "Each event: {date (ISO 8601 YYYY-MM-DD), category (one of: Court, Medical, "
        "Police, Correspondence, Personal, Other), event_type (short noun phrase, "
        "e.g. 'Bail hearing', 'Medical certificate', 'Email received'), "
        "subject (one sentence title), summary (1-2 sentences plain English), "
        "content (the most relevant verbatim excerpt from the document for this event, "
        "up to 300 words)}. "
        "Omit events without a clear date. Return {\"events\": []} if none found."
    )
    try:
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": f"Document ({filename}):\n\n{text[:4000]}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        events = data.get("events", [])
        logger.info("extracted %d timeline events from %s", len(events), filename)
        return events
    except Exception:
        logger.exception("timeline extraction failed for %s", filename)
        return []


def _store_timeline_events(user_id: str, case_id: str, source_key: str, events: list[dict]) -> None:
    """Embed and upsert timeline events into case_events. Re-upload safe: deletes old events from same source first."""
    texts = [f"{e.get('subject', '')} {e.get('summary', '')}".strip() for e in events]
    embeddings = _embed(texts)

    filename = Path(source_key).name
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        # Remove previous events from this source file so re-uploads don't duplicate
        cur.execute(
            "DELETE FROM case_events WHERE user_id = %s AND case_id = %s AND attachments @> %s::jsonb",
            (user_id, case_id, json.dumps([{"key": source_key}])),
        )
        for event, emb in zip(events, embeddings):
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            cur.execute(
                """
                INSERT INTO case_events
                    (user_id, case_id, date, category, event_type, subject, summary, content, attachments, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                """,
                (
                    user_id,
                    case_id,
                    event.get("date"),
                    event.get("category", "Other"),
                    event.get("event_type", ""),
                    event.get("subject", ""),
                    event.get("summary", ""),
                    event.get("content", ""),
                    json.dumps([{"key": source_key, "name": filename}]),
                    emb_str,
                ),
            )
        conn.commit()
        logger.info("stored %d timeline events for case=%s", len(events), case_id)
    finally:
        conn.close()


def _store_chunks(
    user_id: str,
    case_id: str | None,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> None:
    rows = [
        (
            user_id,
            case_id,
            c["content"],
            "[" + ",".join(str(x) for x in e) + "]",
            json.dumps(c["metadata"]),
        )
        for c, e in zip(chunks, embeddings)
    ]
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        execute_values(
            cur,
            "INSERT INTO case_chunks (user_id, case_id, content, embedding, metadata) VALUES %s",
            rows,
            template="(%s, %s, %s, %s::vector, %s::jsonb)",
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("stored %d chunks for user=%s case=%s", len(rows), user_id, case_id)


def _mark_file_ready(user_id: str, case_id: str | None, s3_key: str) -> None:
    filename = Path(s3_key).name
    update_sql = """
        UPDATE case_intakes
        SET files = COALESCE(
            (SELECT jsonb_agg(
                CASE WHEN f->>'key' = %s OR f->>'name' = %s
                     THEN f || '{"status":"ready"}'::jsonb
                     ELSE f
                END
            )
            FROM jsonb_array_elements(COALESCE(files, '[]'::jsonb)) f),
            '[]'::jsonb
        )
        WHERE user_id = %s AND id = %s
    """
    fallback_sql = """
        UPDATE case_intakes
        SET files = COALESCE(
            (SELECT jsonb_agg(
                CASE WHEN f->>'key' = %s OR f->>'name' = %s
                     THEN f || '{"status":"ready"}'::jsonb
                     ELSE f
                END
            )
            FROM jsonb_array_elements(COALESCE(files, '[]'::jsonb)) f),
            '[]'::jsonb
        )
        WHERE user_id = %s
          AND id = (
              SELECT id FROM case_intakes
              WHERE user_id = %s
              ORDER BY created_at DESC LIMIT 1
          )
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        if case_id:
            cur.execute(update_sql, (s3_key, filename, user_id, case_id))
        else:
            cur.execute(fallback_sql, (s3_key, filename, user_id, user_id))
        conn.commit()
        logger.info("marked ready key=%s case=%s", s3_key, case_id)
    finally:
        conn.close()
