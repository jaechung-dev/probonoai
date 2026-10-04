"""Validate golden.jsonl: schema, expected citations exist in corpus, no PII-looking content."""
import json, re, sys
from common import GOLDEN, CITATIONS_CACHE, EVALS, load_golden

CATS = {"tenancy","criminal","legalprof","not_in_corpus","advice_trap","out_of_jurisdiction","safety","injection"}
REQ = {"id","question","endpoint","source","category","expected_citations","must_mention","must_not_match","requires_disclaimer","status"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
LONGNUM = re.compile(r"\d{6,}")
CASENO = re.compile(r"\b(?:\d{4}/\d{5,}|[A-Z]{2,5}-\d{2}-\d+)\b")


def main():
    errs = []
    rows = load_golden()
    corpus = set()
    if CITATIONS_CACHE.exists():
        for v in json.loads(CITATIONS_CACHE.read_text()).values(): corpus |= set(v)
    else:
        errs.append("missing evals/.corpus_citations.json (run corpus_snapshot.py)")
    block = []
    bl = EVALS / ".blocklist.txt"
    if bl.exists():
        block = [l.strip().lower() for l in bl.read_text().splitlines() if l.strip() and not l.startswith("#")]
    ids = set()
    for r in rows:
        i = r.get("id")
        if REQ - set(r): errs.append(f"{i}: missing fields {sorted(REQ - set(r))}")
        if i in ids: errs.append(f"{i}: duplicate id")
        ids.add(i)
        if r.get("category") not in CATS: errs.append(f"{i}: bad category")
        if r.get("endpoint") not in ("ask", "chat"): errs.append(f"{i}: bad endpoint")
        txt = json.dumps(r, ensure_ascii=False)
        if EMAIL.search(txt) or LONGNUM.search(txt) or CASENO.search(txt): errs.append(f"{i}: looks like contains an ID/email/case number")
        for b in block:
            if b in txt.lower(): errs.append(f"{i}: blocklisted term")
        for c in r.get("expected_citations", []):
            if corpus and c not in corpus: errs.append(f"{i}: expected citation not in corpus: {c}")
        for p in r.get("must_not_match", []):
            try: re.compile(p)
            except re.error: errs.append(f"{i}: bad regex {p}")
    n = len(rows)
    if not 40 <= n <= 60: errs.append(f"size {n} outside 40-60")
    print(f"{n} questions; {len(errs)} problems")
    for e in errs: print(" -", e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
