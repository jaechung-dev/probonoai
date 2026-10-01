# Architecture

ProBono AI is a legal RAG platform: a static React SPA on CloudFront/S3, an
HTTP API Gateway fronting Lambda services, Supabase (Postgres + pgvector) for
data and embeddings, and OpenAI/Anthropic for generation.

---

## 1. Overall System Architecture

![Overall System Architecture](./architecture/01-overview.png)

- **Users → CloudFront → S3** serve the static React SPA (the landing page is
  pre-rendered to avoid a client-side-render flash).
- The SPA calls **API Gateway (HTTP API)**, which fronts three Lambdas:
  - **API Lambda** — authentication (custom JWT: bcrypt, Google OAuth, OTP, OAuth 2.0 + PKCE provider for the ChatGPT Action,
    `sub`/`aud`/`iss` claims), case/document APIs, search, user management.
  - **AI / RAG Lambda** — RAG orchestration, retrieval, LLM integration.
  - **MCP Lambda** — MCP endpoints, tool execution, external integrations.
- **S3** stores raw document uploads.
- **Supabase (Postgres + pgvector)** holds cases, documents, embeddings, and users.
- **External LLMs:** OpenAI (`gpt-4o-mini`, `text-embedding-3-small`) and,
  optionally, Anthropic (Claude).

## 2. RAG Retrieval Flow

![RAG Retrieval Flow](./architecture/02-rag.png)

User question → **embed** (`text-embedding-3-small`) → **vector search** over
Supabase pgvector → **retrieve top-K** legislation, caselaw, and case-document
chunks → **context assembly** with citations → **LLM generation** → structured
response with references.

Conversation history (the last 8 messages) is included in the prompt.
Query rewrite, hybrid search, cross-encoder re-ranking, and query
classification are planned enhancements.

## 3. Document Ingestion Pipeline

![Document Ingestion Pipeline](./architecture/03-ingestion.png)

**Upload → S3 → SQS → Ingest Lambda → Textract/OCR → chunking → embedding
generation (`text-embedding-3-small`) → store in pgvector (`case_chunks`).**
Failed messages route to a dead-letter queue. Implemented in
`services/ingestion/handler.py`.

## 4. MCP + AI Architecture

![MCP + AI Architecture](./architecture/04-mcp.png)

MCP clients (Claude, Cursor, custom clients; ChatGPT planned pending OAuth 2.x)
connect over the **MCP protocol (JSON-RPC over HTTPS)** to the **MCP Lambda**
(tool registry, request routing, auth, response formatting), which calls the
**AI / RAG Lambda** and **Supabase**. Authentication uses custom JWT bearer
tokens. External integrations: Amazon SES (email) is implemented; SNS/SQS
notifications and Slack/Teams webhooks are aspirational.

## 5. Content Guardrails

Every `/chat` request passes through two guardrail layers **before** any LLM
call is made, ensuring zero token cost and zero liability for harmful responses.

### Layer 1 — Crisis Detection (instant, no API call)

A regex pattern matches crisis signals in the user message:

- Suicide and self-harm intent (`suicide`, `kill myself`, `self-harm`, `want to die`, `overdose`, etc.)

If triggered, the user receives an immediate response with Australian crisis
resources — no LLM is invoked:

| Service | Contact |
|---------|---------|
| Lifeline | 13 11 14 (24/7) |
| Beyond Blue | 1300 22 4636 |
| Crisis Text | 0477 13 11 14 |

### Layer 2 — OpenAI Moderation API (free)

Any message that passes the crisis regex is run through the
[OpenAI Moderation API](https://platform.openai.com/docs/guides/moderation)
(`POST /v1/moderations`). This endpoint is **free** and flags:

- `self_harm`, `self_harm_intent`, `self_harm_instructions`
- `harassment_threatening`
- `violence`

Flagged messages return the same crisis response. Moderation API errors are
logged and silently bypassed to avoid blocking legitimate users.

### Layer 3 — Off-topic Deflection

Greetings and small talk (`hi`, `how are you`, `thanks`, etc.) are matched by
a second regex and returned a polite redirect — no retrieval, no LLM call,
no token cost.
