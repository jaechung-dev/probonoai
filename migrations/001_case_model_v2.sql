-- ============================================================================
-- 001_case_model_v2.sql  —  DRAFT, NOT YET APPLIED
--
-- User-centred case model: a case is a set of DOCUMENTS (emails, letters,
-- decisions, submissions, attachments) from INSTITUTIONS, sent by/to PEOPLE,
-- linked into THREADS and relationships, with reference NUMBERS. Only objective
-- facts are extracted automatically. ISSUES are interpretation: the case owner
-- defines them; ingestion may only SUGGEST a link, a person confirms it.
-- Pages are tracked individually so duplicate pages (e.g. GIPA releases that
-- repeat earlier documents) are referenced, not re-chunked. Retrieval has two paths over the same data:
--   structured browse : filter by institution / doc type / date / party /
--                       reference number / thread / disclosure status
--   search            : semantic (embedding) + exact string (full-text, trigram)
-- Every row carries user_id (UUID) so ownership is checked uniformly.
--
-- Purely additive: old tables (case_events, case_chunks, case_intakes,
-- demo_case_events) are untouched until data is migrated and verified.
-- cases.id reuses case_intakes.id so existing case IDs keep working.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Cases ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- = case_intakes.id when migrated
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    matter      JSONB NOT NULL DEFAULT '{}',    -- intake "matter" answers (no personal details)
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, user_id)          -- target of the composite FKs below: child user_id must equal case owner
);
CREATE INDEX IF NOT EXISTS cases_user_idx ON cases (user_id, created_at DESC);

-- ── Institutions (DOE, NCAT, IPC, Ombudsman, a school, police ...) ──────────
-- Shared seed list; user_id NULL = built in, otherwise added by that user.
CREATE TABLE IF NOT EXISTS institutions (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   UUID REFERENCES users(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'other'
              CHECK (kind IN ('government','tribunal','regulator','ombudsman','school','police','court','other')),
    aliases   TEXT[] NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS institutions_name_uq
    ON institutions (COALESCE(user_id::text, ''), lower(name));

INSERT INTO institutions (name, kind, aliases) VALUES
    ('NSW Department of Education', 'government', ARRAY['DOE','Department of Education']),
    ('NSW Civil and Administrative Tribunal', 'tribunal', ARRAY['NCAT']),
    ('Information and Privacy Commission', 'regulator', ARRAY['IPC']),
    ('NSW Ombudsman', 'ombudsman', ARRAY['Ombudsman']),
    ('NSW Police', 'police', ARRAY['Police'])
ON CONFLICT DO NOTHING;

-- ── People / parties ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parties (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id        UUID NOT NULL,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    role_title     TEXT,                                   -- e.g. principal, case officer
    institution_id UUID REFERENCES institutions(id),
    email          TEXT,
    UNIQUE (case_id, name, role_title)
);
CREATE INDEX IF NOT EXISTS parties_case_idx ON parties (case_id);

-- ── Email threads ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS threads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       UUID NOT NULL,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    subject       TEXT,
    external_id   TEXT,                                    -- e.g. Gmail thread id
    first_sent_at TIMESTAMPTZ,
    last_sent_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS threads_case_idx ON threads (case_id);
CREATE UNIQUE INDEX IF NOT EXISTS threads_external_uq ON threads (case_id, external_id) WHERE external_id IS NOT NULL;

-- ── Documents: one row per file / email / letter ─────────────────────────────
-- Three different dates on purpose:
--   occurred_on : the event the document is about (an email on 31 Jul can describe 2 Mar)
--   sent_at     : when it was sent / issued
--   authored_on : when it was written (attachments often differ from the email date)
--   recorded_on : when it entered our records (upload / release date)
CREATE TABLE IF NOT EXISTS documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           UUID NOT NULL,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    s3_key            TEXT,
    external_id       TEXT,                                 -- Gmail message id (email) or '<message id>#<nn>' (attachment)
    source_label      TEXT,                                 -- export category / Gmail label the file came from
    filename          TEXT NOT NULL,
    title             TEXT,
    doc_type          TEXT NOT NULL DEFAULT 'other'
                      CHECK (doc_type IN ('email','letter','decision','submission','memo',
                                          'form','attachment','record','other')),
    institution_id    UUID REFERENCES institutions(id),
    occurred_on       DATE,
    sent_at           TIMESTAMPTZ,
    authored_on       DATE,
    recorded_on       DATE NOT NULL DEFAULT CURRENT_DATE,
    thread_id         UUID REFERENCES threads(id) ON DELETE SET NULL,
    -- how we got it and what is hidden (e.g. GIPA releases, partly redacted)
    -- NULL = unknown. No default on purpose: never assume an origin (esp. not 'gipa').
    source_origin     TEXT
                      CHECK (source_origin IS NULL OR source_origin IN ('received','sent','gipa','own_record','public','other')),
    source_detail     TEXT,                                 -- e.g. 'GIPA-26-0717'
    source_batch      TEXT,                                 -- e.g. 'release 1', 'release 2'
    redaction_status  TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (redaction_status IN ('none','partial','full','unknown')),
    page_count        INT,
    content_sha256    TEXT,
    ingest_status     TEXT NOT NULL DEFAULT 'pending'
                      CHECK (ingest_status IN ('pending','processing','ready','failed')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_case_idx        ON documents (case_id, sent_at);
CREATE INDEX IF NOT EXISTS documents_institution_idx ON documents (case_id, institution_id);
CREATE INDEX IF NOT EXISTS documents_type_idx        ON documents (case_id, doc_type);
CREATE INDEX IF NOT EXISTS documents_thread_idx      ON documents (thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS documents_external_uq ON documents (case_id, external_id)
    WHERE external_id IS NOT NULL;                          -- import is re-runnable
CREATE UNIQUE INDEX IF NOT EXISTS documents_sha_uq   ON documents (case_id, content_sha256)
    WHERE content_sha256 IS NOT NULL;                       -- re-upload safe

-- ── Issues: defined by the case owner, never invented by ingestion ──────────
CREATE TABLE IF NOT EXISTS issues (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID NOT NULL,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dropped')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, name)
);

-- ── Pages: one row per page; duplicates point at the first copy ──────────────
-- A duplicate page (same normalised text already in the case) is NOT chunked or
-- embedded; it only references the original. Original order of arrival wins:
-- own records, then GIPA release 1, then release 2.
CREATE TABLE IF NOT EXISTS document_pages (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id           UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    case_id               UUID NOT NULL,
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    page_no               INT NOT NULL,
    text_sha256           TEXT,                              -- of normalised page text
    char_count            INT,
    duplicate_of_page_id  UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    UNIQUE (document_id, page_no)
);
CREATE INDEX IF NOT EXISTS document_pages_hash_idx ON document_pages (case_id, text_sha256);
CREATE INDEX IF NOT EXISTS document_pages_dup_idx  ON document_pages (duplicate_of_page_id);

CREATE TABLE IF NOT EXISTS document_issues (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    issue_id    UUID NOT NULL REFERENCES issues(id)    ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','confirmed','rejected')),
    PRIMARY KEY (document_id, issue_id)
);
CREATE INDEX IF NOT EXISTS document_issues_issue_idx ON document_issues (issue_id);

-- ── Who was involved in a document ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_parties (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    party_id    UUID NOT NULL REFERENCES parties(id)   ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('sender','recipient','cc','bcc','author','subject','mentioned')),
    PRIMARY KEY (document_id, party_id, role)
);
CREATE INDEX IF NOT EXISTS document_parties_party_idx ON document_parties (party_id);

-- ── Relationships between documents (reply, attachment, response ...) ────────
CREATE TABLE IF NOT EXISTS document_links (
    from_document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    to_document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('reply_to','attachment_of','response_to','references','duplicate_of')),
    PRIMARY KEY (from_document_id, to_document_id, kind)
);
CREATE INDEX IF NOT EXISTS document_links_to_idx ON document_links (to_document_id);

-- ── Reference numbers (DGL26/251, FCS number, NCAT number, GIPA ...) ─────────
CREATE TABLE IF NOT EXISTS reference_numbers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID NOT NULL,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    institution_id UUID REFERENCES institutions(id),
    kind        TEXT NOT NULL DEFAULT 'other',
    value       TEXT NOT NULL,
    value_norm  TEXT NOT NULL                              -- lower-case, letters+digits only
);
CREATE INDEX IF NOT EXISTS reference_numbers_lookup ON reference_numbers (user_id, value_norm);

-- ── Chunks: the text we search, always traceable to document + page ──────────
-- Replaces case_chunks (which had no document id and no page number).
CREATE TABLE IF NOT EXISTS chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    case_id     UUID NOT NULL,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    page_id     UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    page_no     INT,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_case_idx      ON chunks (case_id, user_id);
CREATE INDEX IF NOT EXISTS chunks_doc_page_idx  ON chunks (document_id, page_no);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx       ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_trgm_idx      ON chunks USING gin (content gin_trgm_ops);  -- exact phrases

-- ── Events: dated things that happened, linked back to the source document ───
-- Replaces case_events. Extracted from the FULL document text (the old
-- pipeline only read the first 4,000 characters). Embedding is built from
-- title + summary + excerpt.
CREATE TABLE IF NOT EXISTS events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           UUID NOT NULL,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, user_id) REFERENCES cases (id, user_id) ON DELETE CASCADE,
    document_id       UUID REFERENCES documents(id) ON DELETE SET NULL,
    occurred_on       DATE NOT NULL,
    occurred_precision TEXT NOT NULL DEFAULT 'day' CHECK (occurred_precision IN ('day','month','year','approx')),
    recorded_on       DATE,
    event_type        TEXT,
    title             TEXT NOT NULL,
    summary           TEXT,
    excerpt           TEXT,
    page_no           INT,
    legacy_category   TEXT,                                -- old Court/Medical/... value, kept for tracing
    embedding         vector(1536),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_case_date_idx ON events (case_id, occurred_on);
CREATE INDEX IF NOT EXISTS events_doc_idx       ON events (document_id);
CREATE INDEX IF NOT EXISTS events_embedding_idx ON events USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS event_parties (
    event_id UUID NOT NULL REFERENCES events(id)  ON DELETE CASCADE,
    party_id UUID NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, party_id)
);

CREATE TABLE IF NOT EXISTS event_issues (
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    issue_id UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    status   TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','confirmed','rejected')),
    PRIMARY KEY (event_id, issue_id)
);
