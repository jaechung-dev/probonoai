#!/usr/bin/env python3
"""Read-only health check of the case model v2 data. Prints COUNTS and ids only, never content.

  DATABASE_URL=... python3 scripts/verify_v2.py --user-id <uuid> --case-id <uuid> \
      [--ref DGL26/251] [--semantic "query"]   # --semantic needs OPENAI_API_KEY; prints ids/scores only
Exit code 1 if any hard check fails.
"""
import argparse, os, sys
import psycopg2

CHECKS = [  # (label, sql, expect_zero)
 ("attachments without attachment_of link",
  "SELECT count(*) FROM documents d WHERE d.case_id=%(c)s AND d.user_id=%(u)s AND d.doc_type='attachment' "
  "AND NOT EXISTS (SELECT 1 FROM document_links l WHERE l.from_document_id=d.id AND l.kind='attachment_of')", True),
 ("attachments linked more than once",
  "SELECT count(*) FROM (SELECT from_document_id FROM document_links WHERE kind='attachment_of' "
  "GROUP BY 1 HAVING count(*)>1) x", True),
 ("documents not ready",
  "SELECT count(*) FROM documents WHERE case_id=%(c)s AND user_id=%(u)s AND ingest_status<>'ready'", True),
 ("emails without thread",
  "SELECT count(*) FROM documents WHERE case_id=%(c)s AND user_id=%(u)s AND doc_type='email' AND thread_id IS NULL", True),
 ("documents without source_origin (unknown)",
  "SELECT count(*) FROM documents WHERE case_id=%(c)s AND user_id=%(u)s AND source_origin IS NULL", False),
 ("ORIGINAL pages with text but NO chunks (search blind spot)",
  "SELECT count(*) FROM document_pages p WHERE p.case_id=%(c)s AND p.user_id=%(u)s AND p.duplicate_of_page_id IS NULL "
  "AND coalesce(p.char_count,0)>0 AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.page_id=p.id)", True),
 ("duplicate pages that still have chunks",
  "SELECT count(*) FROM document_pages p WHERE p.case_id=%(c)s AND p.user_id=%(u)s AND p.duplicate_of_page_id IS NOT NULL "
  "AND EXISTS (SELECT 1 FROM chunks c WHERE c.page_id=p.id)", True),
 ("chunks without embedding",
  "SELECT count(*) FROM chunks WHERE case_id=%(c)s AND user_id=%(u)s AND embedding IS NULL", True),
 ("events without embedding",
  "SELECT count(*) FROM events WHERE case_id=%(c)s AND user_id=%(u)s AND embedding IS NULL", True),
 ("events with no source document (orphans)",
  "SELECT count(*) FROM events WHERE case_id=%(c)s AND user_id=%(u)s AND document_id IS NULL", True),
 ("rows in issues/document_issues/event_issues (must stay empty)",
  "SELECT (SELECT count(*) FROM issues WHERE case_id=%(c)s)+(SELECT count(*) FROM document_issues di JOIN documents d ON d.id=di.document_id WHERE d.case_id=%(c)s)"
  "+(SELECT count(*) FROM event_issues ei JOIN events e ON e.id=ei.event_id WHERE e.case_id=%(c)s)", True),
 ("threads without first/last_sent_at",
  "SELECT count(*) FROM threads WHERE case_id=%(c)s AND user_id=%(u)s AND (first_sent_at IS NULL OR last_sent_at IS NULL)", True),
 ("duplicate external_id (should be impossible)",
  "SELECT count(*) FROM (SELECT external_id FROM documents WHERE case_id=%(c)s AND external_id IS NOT NULL GROUP BY 1 HAVING count(*)>1) x", True),
]
COUNTS = [
 ("documents by type/origin/label",
  "SELECT doc_type, coalesce(source_origin,'NULL'), coalesce(source_label,'NULL'), count(*) FROM documents "
  "WHERE case_id=%(c)s AND user_id=%(u)s GROUP BY 1,2,3 ORDER BY 3,1,2"),
 ("pages (total / duplicate)",
  "SELECT count(*), count(duplicate_of_page_id) FROM document_pages WHERE case_id=%(c)s AND user_id=%(u)s"),
 ("chunks / with page_no",
  "SELECT count(*), count(page_no) FROM chunks WHERE case_id=%(c)s AND user_id=%(u)s"),
 ("text-free attachments (stub page_count=0 or image-only; excludes dedup — ref: 33 before OCR)",
  "SELECT count(*) FROM documents d WHERE d.case_id=%(c)s AND d.user_id=%(u)s AND d.doc_type='attachment' "
  "AND NOT EXISTS (SELECT 1 FROM document_pages p WHERE p.document_id=d.id "
  "AND p.duplicate_of_page_id IS NULL AND coalesce(p.char_count,0)>0) "
  "AND (d.page_count=0 OR EXISTS (SELECT 1 FROM document_pages p "
  "WHERE p.document_id=d.id AND p.duplicate_of_page_id IS NULL))"),
 ("events / by precision",
  "SELECT occurred_precision, count(*) FROM events WHERE case_id=%(c)s AND user_id=%(u)s GROUP BY 1"),
 ("parties / threads / reference_numbers",
  "SELECT (SELECT count(*) FROM parties WHERE case_id=%(c)s),(SELECT count(*) FROM threads WHERE case_id=%(c)s),"
  "(SELECT count(*) FROM reference_numbers WHERE case_id=%(c)s)"),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", required=True); ap.add_argument("--case-id", required=True)
    ap.add_argument("--ref"); ap.add_argument("--semantic")
    a = ap.parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.set_session(readonly=True)
    cur = conn.cursor(); p = {"c": a.case_id, "u": a.user_id}
    cur.execute("SELECT 1 FROM cases WHERE id=%(c)s AND user_id=%(u)s", p)
    if not cur.fetchone(): sys.exit("case not found for this user")
    print("== counts")
    for label, sql in COUNTS:
        cur.execute(sql, p); print(f"- {label}:"); [print("   ", r) for r in cur.fetchall()]
    print("== checks"); bad = 0
    for label, sql, zero in CHECKS:
        cur.execute(sql, p); n = cur.fetchone()[0]
        ok = (n == 0) or not zero; bad += (not ok)
        print(f"[{'OK ' if ok else 'FAIL'}] {label}: {n}")
    if a.ref:
        norm = "".join(ch for ch in a.ref.lower() if ch.isalnum())
        cur.execute("SELECT count(DISTINCT document_id) FROM reference_numbers WHERE user_id=%s AND case_id=%s AND value_norm=%s", (a.user_id, a.case_id, norm))
        r1 = cur.fetchone()[0]
        cur.execute("SELECT count(*), count(DISTINCT document_id) FROM chunks WHERE user_id=%s AND case_id=%s AND content ILIKE %s", (a.user_id, a.case_id, f"%{a.ref}%"))
        print(f"== exact '{a.ref}': documents via reference_numbers={r1}; chunks containing it={cur.fetchone()}")
    if a.semantic:
        from openai import OpenAI
        v = OpenAI().embeddings.create(model="text-embedding-3-small", input=a.semantic).data[0].embedding
        lit = "[" + ",".join(map(str, v)) + "]"
        cur.execute("SELECT c.document_id, c.page_no, d.doc_type, round((1-(c.embedding<=>%s::vector))::numeric,3) "
                    "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.case_id=%s AND c.user_id=%s "
                    "ORDER BY c.embedding<=>%s::vector LIMIT 5", (lit, a.case_id, a.user_id, lit))
        print("== semantic top5 (document_id, page_no, doc_type, score)"); [print("   ", r) for r in cur.fetchall()]
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
