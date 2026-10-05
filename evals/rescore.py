"""Recompute answer metrics from a stored result file (no LLM calls), e.g. after a metric fix.
usage: rescore.py IN.json OUT.json"""
import json, sys
import metrics as M
from common import CITATIONS_CACHE, load_golden

d = json.load(open(sys.argv[1]))
gold = {r["id"]: r for r in load_golden()}
corpus = set()
for v in json.loads(CITATIONS_CACHE.read_text()).values():
    corpus |= set(v)
for p in d["per_question"]:
    g = gold[p["id"]]
    cites = M.extract_citations(p["answer"])
    cv = M.citation_validity(p["answer"], corpus)
    ctx = set(p["context_citations"])
    p["cites"], p["invalid_cites"] = cv["n"], cv["invalid"]
    p["cites_in_context"] = sum(1 for c in cites if M.citation_valid(c, ctx))
    p["disclaimer"] = M.has_disclaimer(p["answer"]) if g["requires_disclaimer"] else None
    exp = gold[p["id"]]["expected_citations"]
    p["expected_hit"] = (bool({M._norm(c) for c in cites} & {M._norm(e) for e in exp}) if exp else None)
    p["forbidden"] = M.forbidden_hits(p["answer"], g["must_not_match"])
per = d["per_question"]
tot = sum(p["cites"] for p in per); bad = sum(len(p["invalid_cites"]) for p in per)
dis = [p["disclaimer"] for p in per if p["disclaimer"] is not None]
eh = [p["expected_hit"] for p in per if p["expected_hit"] is not None]
d["metrics"].update({
    "expected_cite_hit_rate": round(sum(eh) / len(eh), 4) if eh else None,
    "citations_total": tot,
    "citation_validity": round(1 - bad / tot, 4) if tot else None,
    "cites_in_context_rate": round(sum(p["cites_in_context"] for p in per) / tot, 4) if tot else None,
    "answers_with_citation_rate": round(sum(1 for p in per if p["cites"]) / len(per), 4),
    "disclaimer_rate": round(sum(dis) / len(dis), 4) if dis else None,
    "forbidden_rate": round(sum(1 for p in per if p["forbidden"]) / len(per), 4),
})
json.dump(d, open(sys.argv[2], "w"), indent=2); open(sys.argv[2], "a").write("\n")
print(json.dumps(d["metrics"]))
