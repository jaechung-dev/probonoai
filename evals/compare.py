"""Regression detection: compare.py BASELINE.json NEW.json  (exit 1 on regression)."""
import json, sys
import metrics as M

TOL = {
    "retrieval": {"recall@4": .03, "recall@8": .03, "hit@4": .03, "hit@8": .03, "mrr": .03},
    "answer": {"citation_validity": .03, "answers_with_citation_rate": .05, "cites_in_context_rate": .03, "expected_cite_hit_rate": .05, "disclaimer_rate": .05, "forbidden_rate": .0, "must_mention_rate": .05},
}
LOWER_IS_BETTER = {"forbidden_rate"}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: compare.py BASELINE.json NEW.json")
    b, n = (json.load(open(p)) for p in sys.argv[1:3])
    if b["kind"] != n["kind"]:
        sys.exit("kind mismatch")
    tol = TOL[b["kind"]]
    rows = M.compare_metrics(b["metrics"], n["metrics"], tol, set(tol) - LOWER_IS_BETTER)
    for k in ("corpus_hash", "golden_hash", "prompt_hash", "model", "embed_model", "commit", "prompt_variant"):
        if b["meta"].get(k) != n["meta"].get(k):
            print(f"note: {k} changed {b['meta'].get(k)} -> {n['meta'].get(k)}")
    for r in rows:
        print(f"{'REGRESSION' if r['regressed'] else 'ok':10} {r['metric']:20} {r['base']} -> {r['new']} ({r['delta']:+})")
    sys.exit(1 if any(r["regressed"] for r in rows) else 0)


if __name__ == "__main__":
    main()
