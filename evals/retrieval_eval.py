"""Retrieval eval: recall@4, recall@8, hit@k, MRR on golden questions with expected citations.
READ-ONLY. Mirrors /ask legislation retrieval (NSW, dense cosine)."""
import argparse, json, datetime
from common import RESULTS, load_golden, run_meta
import metrics as M
import retrieval as R


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out"); a = ap.parse_args()
    rows = [r for r in load_golden() if r["expected_citations"]]
    vecs = R.embed_questions([r["question"] for r in rows])
    conn = R.connect_ro(); cur = conn.cursor()
    per = []
    for r, v in zip(rows, vecs):
        got = [d["citation"] for d in R.legislation(cur, v, 8)]
        e = r["expected_citations"]
        per.append({"id": r["id"], "category": r["category"], "expected": e, "retrieved_top8": got,
                    "recall@4": M.recall_at_k(got, e, 4), "recall@8": M.recall_at_k(got, e, 8),
                    "hit@4": M.hit_at_k(got, e, 4), "hit@8": M.hit_at_k(got, e, 8),
                    "rr": M.reciprocal_rank(got, e)})
    agg = {"n": len(per), "recall@4": M.mean([p["recall@4"] for p in per]),
           "recall@8": M.mean([p["recall@8"] for p in per]),
           "hit@4": M.mean([p["hit@4"] for p in per]), "hit@8": M.mean([p["hit@8"] for p in per]),
           "mrr": M.mean([p["rr"] for p in per])}
    agg = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in agg.items()}
    out = {"kind": "retrieval", "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "meta": run_meta(), "metrics": agg, "per_question": per}
    path = a.out or str(RESULTS / f"retrieval-{out['meta']['commit']}-{out['timestamp'][:19].replace(':','')}.json")
    open(path, "w").write(json.dumps(out, indent=2) + "\n")
    print(json.dumps(agg), "->", path)


if __name__ == "__main__":
    main()
