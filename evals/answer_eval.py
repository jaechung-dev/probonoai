"""Answer eval: runs the production prompt over golden questions and checks
citation validity, disclaimer, forbidden patterns, must_mention. READ-ONLY DB.
Needs OPENAI_API_KEY (or ANTHROPIC_API_KEY with --model claude-*)."""
import argparse, json, os, datetime
from common import RESULTS, CITATIONS_CACHE, load_golden, load_env, run_meta, sha256_text
import metrics as M
import retrieval as R

from services.rag.prompts import PLAIN_ENGLISH_SYSTEM


VARIANTS = {
    "baseline": "",
    "cite": (
        "\n\nCitation rules:\n"
        "- When you rely on the provided legal context, name the provision in the exact form shown in the "
        "square-bracket header of that context block (for example: Residential Tenancies Act 2010 (NSW) s 41).\n"
        "- Only cite provisions whose headers appear in the context. Never cite a section number from memory.\n"
        "- If the context does not cover the question, say so briefly and do not invent a citation."
    ),
    "cite_strict": (
        "\n\nCitation rules:\n"
        "- After your answer and before the disclaimer, add one line starting with 'Sources:' that lists, exactly as "
        "written in the square-bracket headers, each context block you actually relied on "
        "(for example: Sources: Residential Tenancies Act 2010 (NSW) s 41).\n"
        "- Only list headers that appear in the context and that are relevant to the question. Never cite a section "
        "number from memory.\n"
        "- If no context block is relevant, write 'Sources: none in the provided context' and answer from general knowledge."
    ),
}


def format_docs(docs):
    return "\n\n---\n\n".join(f"[{d['citation']}]\n{d['text']}" for d in docs)


def build_docs(cur, vec, ep):
    if ep == "chat":
        docs = R.legislation(cur, vec, 5) + R.caselaw(cur, vec, 5)
    else:
        docs = R.legislation(cur, vec, 4)
    ctx = format_docs(docs)
    if len(ctx) > 24_000:
        ctx = ctx[:24_000] + "\n\n[Context truncated to fit token budget]"
    return ctx, [d["citation"] for d in docs]


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
    ap.add_argument("--variant", default="baseline", choices=sorted(VARIANTS))
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
        ctx, ctx_cites = build_docs(cur, v, r["endpoint"])
        system = PLAIN_ENGLISH_SYSTEM + VARIANTS[a.variant] + "\n\nRelevant legal context:\n" + ctx
        ans = generate(model, system, r["question"])
        cv = M.citation_validity(ans, corpus)
        fb = M.forbidden_hits(ans, r["must_not_match"])
        in_ctx = [c for c in M.extract_citations(ans) if M.citation_valid(c, set(ctx_cites))]
        per.append({"id": r["id"], "category": r["category"], "answer": ans,
                    "cites": cv["n"], "invalid_cites": cv["invalid"], "cites_in_context": len(in_ctx),
                    "expected_hit": (bool(set(M._norm(c) for c in M.extract_citations(ans)) & set(M._norm(e) for e in r["expected_citations"])) if r["expected_citations"] else None),
                    "context_citations": ctx_cites,
                    "disclaimer": M.has_disclaimer(ans) if r["requires_disclaimer"] else None,
                    "forbidden": fb,
                    "must_mention_ok": M.contains_all(ans, r["must_mention"]) if r["must_mention"] else None})
    tot = sum(p["cites"] for p in per); bad = sum(len(p["invalid_cites"]) for p in per)
    in_ctx_total = sum(p["cites_in_context"] for p in per)
    eh = [p["expected_hit"] for p in per if p["expected_hit"] is not None]
    dis = [p["disclaimer"] for p in per if p["disclaimer"] is not None]
    mm = [p["must_mention_ok"] for p in per if p["must_mention_ok"] is not None]
    agg = {"n": len(per), "citations_total": tot, "citation_validity": round(1 - bad / tot, 4) if tot else None,
           "expected_cite_hit_rate": round(sum(eh) / len(eh), 4) if eh else None,
           "cites_in_context_rate": round(in_ctx_total / tot, 4) if tot else None,
           "answers_with_citation_rate": round(sum(1 for p in per if p["cites"]) / len(per), 4),
           "disclaimer_rate": round(sum(dis) / len(dis), 4) if dis else None,
           "forbidden_rate": round(sum(1 for p in per if p["forbidden"]) / len(per), 4),
           "must_mention_rate": round(sum(mm) / len(mm), 4) if mm else None}
    out = {"kind": "answer", "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "meta": {**run_meta(model), "prompt_variant": a.variant,
                    "prompt_hash": sha256_text(PLAIN_ENGLISH_SYSTEM + VARIANTS[a.variant])[:16]}, "metrics": agg, "per_question": per}
    path = a.out or str(RESULTS / f"answer-{out['meta']['commit']}-{out['timestamp'][:19].replace(':','')}.json")
    open(path, "w").write(json.dumps(out, indent=2) + "\n")
    print(json.dumps(agg), "->", path)


if __name__ == "__main__":
    main()
