"""Answer eval: runs the production prompt over golden questions and checks
citation validity, disclaimer, forbidden patterns, must_mention. READ-ONLY DB.
Needs OPENAI_API_KEY (or ANTHROPIC_API_KEY with --model claude-*)."""
import argparse, json, os, datetime
from common import RESULTS, CITATIONS_CACHE, load_golden, load_env, run_meta
import metrics as M
import retrieval as R

from services.rag.prompts import PLAIN_ENGLISH_SYSTEM


def format_docs(docs):
    return "\n\n---\n\n".join(f"[{d['citation']}]\n{d['text']}" for d in docs)


def build_context(cur, vec, ep):
    if ep == "chat":
        docs = R.legislation(cur, vec, 5) + R.caselaw(cur, vec, 5)
    else:
        docs = R.legislation(cur, vec, 4)
    ctx = format_docs(docs)
    if len(ctx) > 24_000:
        ctx = ctx[:24_000] + "\n\n[Context truncated to fit token budget]"
    return ctx


def generate(model, system, question):
    if model.startswith("claude-"):
        import anthropic
        r = anthropic.Anthropic().messages.create(model=model, max_tokens=1024, temperature=0, system=system,
                                                  messages=[{"role": "user", "content": question}])
        return r.content[0].text
    from openai import OpenAI
    r = OpenAI().chat.completions.create(model=model, temperature=0, messages=[
        {"role": "system", "content": system}, {"role": "user", "content": question}])
    return r.choices[0].message.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model"); ap.add_argument("--out"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    load_env()
    model = a.model or os.environ.get("CHAT_MODEL", "gpt-4o-mini")
    rows = load_golden()[: a.limit] if a.limit else load_golden()
    corpus = set()
    for v in json.loads(CITATIONS_CACHE.read_text()).values(): corpus |= set(v)
    vecs = R.embed_questions([r["question"] for r in rows])
    cur = R.connect_ro().cursor()
    per = []
    for r, v in zip(rows, vecs):
        system = PLAIN_ENGLISH_SYSTEM + "\n\nRelevant legal context:\n" + build_context(cur, v, r["endpoint"])
        ans = generate(model, system, r["question"])
        cv = M.citation_validity(ans, corpus)
        fb = M.forbidden_hits(ans, r["must_not_match"])
        per.append({"id": r["id"], "category": r["category"], "answer": ans,
                    "cites": cv["n"], "invalid_cites": cv["invalid"],
                    "disclaimer": M.has_disclaimer(ans) if r["requires_disclaimer"] else None,
                    "forbidden": fb,
                    "must_mention_ok": M.contains_all(ans, r["must_mention"]) if r["must_mention"] else None})
    tot = sum(p["cites"] for p in per); bad = sum(len(p["invalid_cites"]) for p in per)
    dis = [p["disclaimer"] for p in per if p["disclaimer"] is not None]
    mm = [p["must_mention_ok"] for p in per if p["must_mention_ok"] is not None]
    agg = {"n": len(per), "citations_total": tot, "citation_validity": round(1 - bad / tot, 4) if tot else None,
           "answers_with_citation_rate": round(sum(1 for p in per if p["cites"]) / len(per), 4),
           "disclaimer_rate": round(sum(dis) / len(dis), 4) if dis else None,
           "forbidden_rate": round(sum(1 for p in per if p["forbidden"]) / len(per), 4),
           "must_mention_rate": round(sum(mm) / len(mm), 4) if mm else None}
    out = {"kind": "answer", "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "meta": run_meta(model), "metrics": agg, "per_question": per}
    path = a.out or str(RESULTS / f"answer-{out['meta']['commit']}-{out['timestamp'][:19].replace(':','')}.json")
    open(path, "w").write(json.dumps(out, indent=2) + "\n")
    print(json.dumps(agg), "->", path)


if __name__ == "__main__":
    main()
