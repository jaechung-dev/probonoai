# Evals (Phase 1)

Automated, repeatable checks for the retrieval + answer pipeline. Everything here is **read-only** against the database
(enforced in `common.enforce_read_only`, which forces every psycopg2 connection to `readonly=True`).

## Design rules
- `golden.jsonl` is **hand-written, synthetic**. It is never generated from corpus chunks, and contains no real persons,
  case numbers or document titles. `validate_golden.py` lints for emails, long numeric IDs, case-number patterns and an
  optional local `.blocklist.txt` (gitignored). Questions are `status: draft` until reviewed by a legal professional.
- Expected citations are exact corpus citation strings and are verified to exist in the corpus.
- Every result records: `commit`, `model`, `embed_model`, `corpus_hash`, `prompt_hash`, `golden_hash`.
- `corpus_snapshot.json` holds chunk/citation counts and a corpus hash (sha256 over sorted `table|citation|md5(text)`
  lines). No corpus text is stored in the repo.

## Run
```bash
export PYTHONPATH=.:evals
python evals/corpus_snapshot.py          # counts + corpus hash (+ local citation cache)
python evals/validate_golden.py          # schema / corpus / PII lint
python evals/retrieval_eval.py           # recall@4, recall@8, hit@k, MRR
python evals/answer_eval.py              # needs OPENAI_API_KEY (or --model claude-* with ANTHROPIC_API_KEY)
python evals/compare.py evals/results/baseline-retrieval.json evals/results/<new>.json   # exit 1 on regression
python -m pytest evals/tests
```

## Metrics
| Eval | Metric | Notes |
|---|---|---|
| retrieval | recall@4 / recall@8 | fraction of expected citations in top-k legislation chunks (mirrors `/ask`, NSW) |
| retrieval | hit@k, MRR | any-hit rate and mean reciprocal rank |
| answer | citation_validity | share of `<Act> <year> s N` citations in the answer that exist in the corpus. Real law not ingested counts as invalid, so this is a conservative "grounded in corpus" signal |
| answer | disclaimer_rate | on questions needing one: answer contains >=2 of the standard disclaimer signals |
| answer | forbidden_rate | answers matching strategy-recommendation / outcome-prediction patterns (lower is better) |
| answer | must_mention_rate | optional keyword checks |

Answers are generated with the production `PLAIN_ENGLISH_SYSTEM` prompt, temperature 0. Regex checks are a cheap first
line of defence, not a substitute for human or LLM-judge review.

## Known limits
- Legislation only; caselaw and uploaded case documents are not evaluated yet.
- Questions for topics absent from the corpus measure unsupported-citation behaviour, not abstention, because the
  production prompt tells the model to always answer.
- Golden set is small (~60); differences of a few points are noise.

## CI
`.github/workflows/evals.yml`:
- **evals-offline** (every PR, no secrets): metric unit tests + golden schema/PII lint.
- **evals-live** (non-fork PRs, pushes, weekly, manual): corpus snapshot, golden validation against the corpus,
  retrieval eval, and `compare.py` against `results/baseline-retrieval.json` (fails the job on regression).
  Needs repo secrets `EVAL_DATABASE_URL` (use a **read-only** Postgres role) and `OPENAI_API_KEY`; skipped when absent.

To intentionally move the baseline (corpus, model or prompt changed), re-run the eval with
`--out evals/results/baseline-retrieval.json` and commit it with the reason in the commit message.
