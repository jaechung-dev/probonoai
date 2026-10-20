---
name: run-evals
description: Run ProbonoAI's retrieval/answer evals and compare to the committed baseline. Use after changing services/rag, services/ai, prompts, chunking, the embedding model or the golden set, or when asked whether quality regressed.
---

# Run evals

Evals are read-only against Postgres. Needs `DATABASE_URL` (read-only role) and `OPENAI_API_KEY` in the environment.
If either is missing, run only the offline steps and say the live eval was skipped.

## Steps
1. Offline checks (always):
   ```bash
   PYTHONPATH=evals python -m pytest evals/tests -q
   PYTHONPATH=evals python evals/validate_golden.py --no-corpus
   ```
2. Live (only if secrets are present):
   ```bash
   export PYTHONPATH=.:evals
   python evals/corpus_snapshot.py > /dev/null
   python evals/validate_golden.py
   python evals/retrieval_eval.py --out evals/results/run-$(date +%Y%m%d-%H%M).json
   python evals/compare.py evals/results/baseline-retrieval.json evals/results/run-*.json
   ```
   `compare.py` exits 1 on regression.
3. Report recall@4, recall@8, MRR and the delta vs baseline. If it regressed, hand the two result files to the
   `eval-analyst` subagent and report its findings.

## Do not
- Overwrite `baseline-*.json` to make a regression disappear. Moving the baseline is only valid when corpus, model or
  prompt changed on purpose; say why in the commit message and ask first.
- Treat a difference of a few points as signal: the golden set is small (~60), so it is noisy.
