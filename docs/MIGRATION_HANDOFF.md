# Case model v2 — handoff for the local session

Context: `migrations/001_case_model_v2.sql` is validated on a scratch Postgres 16 + pgvector
(applies cleanly, idempotent, dup-page/chunk/FTS/trigram/hnsw smoke test OK). NOT applied to the real DB.

## Do, in order (stop and report after each step)
1. Back up the real DB (pg_dump) before touching anything.
2. Apply `001_case_model_v2.sql` (additive only; old tables untouched). Check the real DB has
   `users` table and pgvector/pg_trgm available.
3. Backfill (dry-run first, report counts, then run):
   - `case_intakes` -> `cases` (same id).
   - Originals in S3 -> `documents` (+ `document_pages` with normalised-text sha256).
   - Page dedup: process in arrival order (own records, GIPA release 1, GIPA release 2);
     a page whose hash already exists in the case gets `duplicate_of_page_id` and is NOT
     chunked/embedded; new pages -> `chunks` (with page_id/page_no) and embeddings.
   - Set `source_origin='gipa'`, `source_detail='GIPA-26-0717'`, `source_batch='release 1|2'`.
   - Events from FULL text (not first 4000 chars) -> `events`.
   - Never fill issues; at most insert `document_issues/event_issues` as `suggested`.
4. Compare old vs new (counts, spot-check pages) before any switch-over. Do not drop old tables.
5. Do not deploy Lambda changes without showing the diff first.

## Scope (important)
- Production has ~47 local signups + 2 Google + 2 seed accounts; only 5 are email-verified.
- Backfill ONLY Sojung's case(s) (find by her user id). Do not touch other users' data.
- Before step 2, check signup pattern (read-only, counts only, no emails in output):
  `SELECT date_trunc('day',created_at) d, count(*) FROM users GROUP BY 1 ORDER BY 1;`
  `SELECT split_part(email,'@',2) dom, count(*) FROM users GROUP BY 1 ORDER BY 2 DESC LIMIT 15;`
- Do NOT send verification-reminder emails until the above shows they are real people (bounce risk).

## Constraints
- Sojung's data: get her OK before reprocessing; no case content in logs or chat output.
- Do not print secrets (AWS keys, DB URL). Delete `~/mcp-env-backup-20261002.json` when done.

## Deferred (security / hygiene) — decided to do later, not blocking the migration
- Purge test accounts (@example.com, @mailinator.com, @test.probonoai.internal; 47 rows, all tests)
  after pg_dump + dry-run counts; keep jaechung0709@gmail.com and the 2 Google accounts.
  Fix the cause: E2E/Playwright tests run against prod -> add teardown or a staging DB.
- Re-enable GPT IP restriction (GPT_ALLOWED_CIDRS), mirror OAUTH_ISSUER into Terraform.
- Web login: shorten 1-year JWT, remove/isolate demo + seed accounts.
- Consent screen: show name/email instead of UUID.
- Check/rotate the admin key in api_keys.json.example; replace AWS root creds with a limited IAM user.
- Delete ~/mcp-env-backup-20261002.json.

## Gmail export import design (decided 2026-10-02)
Source: bella-legal-data export (server `iai`, /home/gom/bella-legal-data, data/Bella/<Category>/ with
emails/, attachments/, extracted_text/, manifest.jsonl). manifest.jsonl per mail: sender, to, cc,
date_received, subject, gmail_message_id, gmail_thread_id, email_path(.eml), email_text_path, attachments[],
persons, organisations, reference_numbers, tags.
- Inbox only was exported; sent mail exists only quoted inside replies.
- email -> documents(doc_type=email, source_origin='received', external_id=gmail_message_id,
  source_label=<Category>); sent_at from the .eml Date header (fallback date_received).
- attachment -> documents(doc_type=attachment, external_id='<message id>#<nn>'), document_links
  attachment_of -> its email; inherits thread (threads.external_id=gmail_thread_id), sender/recipient/cc, sent_at.
- Page numbers: re-extract attachments from the original files in attachments/ (extracted_text .md has no pages);
  fall back to .md with page_no NULL.
- Do NOT reuse chunks.jsonl/embeddings.jsonl (different chunking, no header) — re-embed.
- persons/organisations/reference_numbers are LLM output: validate; link institutions only on exact match.
  `tags` are NOT imported as issues.
- Strip quoted reply history ("On ... wrote:") before chunking, keep raw .eml. ONLY do this once Sent mail is
  imported too, otherwise Sojung's own words (visible only as quotes) vanish from search.
- OPEN: does export_gmail_label.py support the SENT label / a label for sent mail? If yes, import Sent as
  source_origin='sent'. Check: `grep -n -i "label\|sent" scripts/export_gmail_label.py | head -40`.
- GIPA is handled LAST (after emails+attachments), as copies/duplicates.
