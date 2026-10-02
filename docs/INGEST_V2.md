# Ingestion v2 (case model v2)

Writes uploads to the tables in `migrations/001_case_model_v2.sql` instead of `case_chunks` / `case_events`.
Code: `services/ingestion/v2.py` (stdlib only, DB/LLM/embedder injected) routed from `services/ingestion/handler.py`.

## Enable
Set `INGEST_V2=1` on the ingest Lambda. Default is off: the old path is untouched. The migration must be applied first.
Optional S3 object metadata: `case-id`, `source-origin` (received|sent|gipa|own_record|public|other),
`source-detail` (e.g. GIPA-26-0717), `source-batch` (e.g. release 1), `redaction-status` (none|partial|full|unknown).

## What it does
1. Verifies `cases.user_id = user_id` (fails closed; nothing written otherwise).
2. `documents` row (`ON CONFLICT (case_id, content_sha256) DO NOTHING`); an existing `ready` document is skipped.
   Status: pending -> processing -> ready / failed.
3. Full text per page -> `document_pages` with sha256 of normalised text (lowercase, whitespace collapsed, bare page-number lines dropped).
   Which copy is the original is decided by source priority: own_record/received/sent (0), NULL/other/public (1), gipa (2); ties by documents.created_at then id. A page that loses gets `duplicate_of_page_id` and is not chunked, embedded or sent for events. If a higher-priority document arrives after a lower-priority one holding the same page, in the same transaction the older page becomes a duplicate of the new original, its chunks are deleted and pages that pointed at it are re-pointed.
   `documents.redaction_status` is `unknown` unless `redaction-status` metadata is valid (never assumed `none`).
   `documents.source_origin` is NULL unless `source-origin` upload metadata is valid (never guessed; needs the SQL change making it nullable with no default).
4. New pages -> page-aware chunks (500 tokens / 50 overlap, never across pages) with embeddings. The embedded text is `build_chunk_header(...) + "\n" + content`, e.g. `[email · NSW Department of Education · 2026-07-31 · from Jane Roe (Principal) to Sam Poe · re: subject · p.3]` (omits missing parts, no email addresses, ~300 char cap); `chunks.content` stays the raw chunk text. Metadata is therefore extracted before chunks are embedded.
5. One LLM call (gpt-4o-mini, first 6000 + last 1500 chars) for doc_type, institution (matched to existing, never created),
   occurred_on / sent_at / authored_on, parties (+ document_parties), reference numbers (value_norm), email thread subject.
   Invalid enum values fall back to `other` / null.
6. Events from full text in ~12k-char page windows: dated only, free-text event_type, embedding of title+summary+excerpt, event_parties on name match.
7. Delete + reinsert of a document's pages/chunks/events/parties/refs happens in one transaction, so re-processing is idempotent.
Issues are never written (no issues / document_issues / event_issues); `events.legacy_category` stays null.

## Test
`python3 -m unittest services.ingestion.tests.test_v2` (stdlib only; no DB, network or psycopg2 needed).

## Known limits
- Not run against a real Postgres; SQL is only checked via a recording fake cursor.
- Duplicate detection is exact on normalised page text. Demoting a page does not remove events already extracted from it. The originals lookup happens before the write transaction, so two concurrent ingests in one case could race.
- Party matching for events is exact (case-insensitive) on names the metadata call returned.
- Parties with NULL role_title are de-duplicated by a SELECT, not by the unique constraint (NULLs are distinct in Postgres).
- Each event window is one extra LLM call; LLM failures yield no events rather than failing the document.
- Email thread subject is only stored for doc_type=email. .eml attachments are still ignored; non-PDF files are a single page.

## Gmail export importer
`python3 scripts/import_gmail_export.py --export-dir data/Bella/<Category> --case-id <uuid> --user-id <uuid> --owner-email me@x.org --owner-name me [--keep-quotes] [--apply]`
- Dry run is the default and prints counts only (emails, attachments, sent, received, threads, skipped, parse_errors). `--apply` writes; it needs `DATABASE_URL` and `OPENAI_API_KEY` in the env and `pymupdf`, `psycopg2`, `openai`, `tiktoken` installed.
- Logic lives in `services/ingestion/gmail_import.py` (pure helpers + `import_message`), reusing `v2.ingest_document`. Tests: `python3 -m unittest services.ingestion.tests.test_gmail_import` (synthetic fixtures only).
- Messages are processed by date ascending, email before its attachments, one DB transaction per message (+attachments); a failure rolls that message back, logs only its gmail_message_id and continues. Re-runs skip emails whose `external_id` already exists.
- Owner = `sent` if the sender address is in `--owner-email` or display name in `--owner-name` (case-insensitive), else `received`. `sent_at` = manifest `date_received`.
- Email -> `documents(doc_type='email', external_id=message id, source_label=Category)`, thread by `threads.external_id`, parties sender/recipient/cc deduplicated by email (else name). Attachment -> `doc_type='attachment'`, `external_id='<id>#<nn>'`, same thread/origin/sent_at/parties, `document_links(attachment_of)`.
- Attachment text: PDF originals from `attachments/` per page (PyMuPDF native text); original missing, non-PDF or image-only (under half the pages have text) -> extracted `.md` as one pseudo-page: `document_pages.page_no` 1 (column is NOT NULL) but `chunks.page_no` / `events.page_no` NULL.
- Manifest hints: dates, parties, thread and reference numbers are NOT re-asked from the LLM (`prevalidated_meta`); organisations link to an institution only on exact name/alias match; persons only become `mentioned` if they match an existing party; `tags` ignored; no issues written. Event extraction still uses the LLM.
- Quoted reply history (`On ... wrote:`, `-----Original Message-----`, Outlook header blocks, Korean `...님이 작성:`, `>` lines) is stripped for chunking/embedding/events only; the page hash and `.md` text are the raw text. `--keep-quotes` disables it. Only rely on stripping once Sent mail is imported too.
- `content_sha256` for imported documents = sha256(external_id + raw text), so identical bodies in different mails are not merged at document level (duplicate pages are still detected via page hashes).
- Not covered: OCR for image-only PDFs (uses the export's `.md`), `.eml` fallback when the `.md` is missing (that message fails), the manifest `email_path`.
