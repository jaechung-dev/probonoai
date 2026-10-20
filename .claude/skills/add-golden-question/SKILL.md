---
name: add-golden-question
description: Add or edit a question in evals/golden.jsonl safely (synthetic, no PII, valid citations). Use when growing the eval set or covering a retrieval failure.
---

# Add a golden question

1. Read the schema from existing lines in `evals/golden.jsonl` and `evals/validate_golden.py`; match the fields exactly.
2. Write the question by hand. It must be synthetic: no real names, case numbers, emails, long numeric IDs or document titles.
3. Expected citations must be exact corpus citation strings (`<Act> <year> s N`). Verify them with
   `PYTHONPATH=.:evals python evals/validate_golden.py` when the corpus is reachable.
4. Set `status: draft`. Only a legal professional promotes a question to reviewed.
5. Run `PYTHONPATH=evals python evals/validate_golden.py --no-corpus`; it must print `0 problems`.
6. Re-running the retrieval eval changes `golden_hash`, so the baseline comparison is no longer like for like. Tell the
   user and do not move the baseline silently.
