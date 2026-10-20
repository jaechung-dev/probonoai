# ProbonoAI — agent guide

Free plain-English NSW legal help (probonoai.com.au). Solo project. Python (FastAPI) backend on AWS Lambda,
pgvector retrieval, React/Vite frontend, Terraform. Read `docs/ARCHITECTURE.md` before structural changes.

## Layout
- `services/` — `api`, `auth`, `rag`, `ai`, `mcp` (FastMCP server), `ingestion`, `core`
- `evals/` — retrieval + answer evals, `golden.jsonl`, baselines in `evals/results/`
- `frontend/` — Vite/React (WCAG 2.1 AA; vitest + Playwright)
- `terraform/` — infrastructure; `.github/workflows/` — CI (evals, security, deploy)

## Commands
- Python tests: `python -m pytest` (testpaths: `tests`, `services`)
- Eval unit tests: `PYTHONPATH=evals python -m pytest evals/tests -q`
- Golden lint (offline): `PYTHONPATH=evals python evals/validate_golden.py --no-corpus`
- Live retrieval eval + regression check: use the `run-evals` skill
- Frontend: `npm --prefix frontend test`, `npm --prefix frontend run lint`
- Lint Python: `ruff check .`

## Rules that must always hold
- **Never read, print or edit** `.env`, `api_keys.json`, Terraform state or any secret. Hooks enforce this.
- Evals are **read-only** against the database. Never write to Postgres from eval code.
- `evals/golden.jsonl` is hand-written and synthetic: no real people, case numbers or document titles.
- Not legal advice: answers keep the standard disclaimer; never recommend strategy or predict outcomes.
- Every API route and MCP tool names its required scope (deny by default). New tools are read-only unless the
  task says otherwise. Check object ownership on every `case_id` / `conversation_id` access (IDOR).
- Do not move `evals/results/baseline-*.json` unless corpus, model or prompt changed on purpose; put the reason in
  the commit message.

## Working style
- For non-trivial changes write a short spec first (goal, constraints, tasks, how it is verified), then implement.
- Keep specs and this file in sync with the code. Run the relevant tests before saying a task is done.
- Use the `security-reviewer` subagent on any diff touching auth, scopes, tokens, SQL or the MCP server.
- Use the `eval-analyst` subagent to interpret eval result diffs instead of reading raw JSON in the main thread.
