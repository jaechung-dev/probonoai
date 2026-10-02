"""Stdlib-only tests for services/ingestion/v2.py (no DB, no network, no psycopg2/openai)."""
import re
import unittest
from datetime import datetime, timezone

from services.ingestion import v2

enc = lambda t: t.split()
dec = lambda toks: " ".join(toks)


class FakeCursor:
    """Records SQL. responders: list of (regex, rows-or-callable); first match wins."""

    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self._n = 0

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.conn.log.append((sql, params))
        self._rows = []
        for pat, rows in self.conn.responders:
            if re.search(pat, sql):
                self._rows = list(rows(params) if callable(rows) else rows)
                return
        if "RETURNING id" in sql:
            self._n += 1
            self.conn.counter += 1
            self._rows = [(f"id-{self.conn.counter}",)]

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        r, self._rows = self._rows, []
        return r


class FakeConn:
    def __init__(self, responders=None):
        self.log, self.responders, self.counter = [], responders or [], 0
        self.commits = self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def sql(self):
        return [s for s, _ in self.log]


def run_ingest(conn, pages, llm=None, **kw):
    calls = {"embed": []}

    def embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1, 0.2] for _ in texts]

    res = v2.ingest_document(
        conn, user_id="u1", case_id="c1", filename="f.pdf", s3_key="k", content_sha256="abc",
        pages=pages, upload_meta=kw.pop("upload_meta", None),
        llm_json=llm or (lambda s, u: {}), embed=embed, encode=enc, decode=dec, **kw)
    return res, calls


EARLY = datetime(2025, 1, 1, tzinfo=timezone.utc)
OWNED = (r"FROM cases WHERE id", [(1,)])
DOC_NEW = (r"^INSERT INTO documents", [("doc-1", datetime(2026, 1, 1, tzinfo=timezone.utc))])


class TestNormalise(unittest.TestCase):
    def test_hash_ignores_case_whitespace_and_page_numbers(self):
        a = "Dear Sir,\n\n  Hello   WORLD.\n\nPage 3 of 10"
        b = "dear sir, hello world.\n7"
        self.assertEqual(v2.page_hash(a), v2.page_hash(b))
        self.assertEqual(len(v2.page_hash(a)), 64)

    def test_different_text_differs_and_blank_is_none(self):
        self.assertNotEqual(v2.page_hash("alpha"), v2.page_hash("beta"))
        self.assertIsNone(v2.page_hash("  \n 4 \n"))


class TestDuplicates(unittest.TestCase):
    def test_existing_original_wins(self):
        known = {v2.page_hash("same text"): "orig-page"}
        out = v2.assign_duplicates(["SAME  text", "new text"], known)
        self.assertEqual(out[0]["dup_of_existing"], "orig-page")
        self.assertIsNone(out[1]["dup_of_existing"])

    def test_within_document_first_copy_is_original(self):
        out = v2.assign_duplicates(["x y", "other", "X Y"], {})
        self.assertIsNone(out[0]["dup_of_page_no"])
        self.assertEqual(out[2]["dup_of_page_no"], 1)

    def test_duplicate_pages_not_chunked_or_embedded(self):
        h = v2.page_hash("repeat page")
        conn = FakeConn([OWNED, DOC_NEW, (r"SELECT p.text_sha256", [(h, "orig-page", 0, EARLY, "doc-0", 1)])])
        res, calls = run_ingest(conn, ["repeat page", "fresh words here"])
        self.assertEqual(res["status"], "ready")
        self.assertEqual(res["duplicate_pages"], 1)
        chunk_inserts = [p for s, p in conn.log if s.startswith("INSERT INTO chunks")]
        self.assertEqual(len(chunk_inserts), 1)
        self.assertEqual(chunk_inserts[0][4], 2)  # page_no
        self.assertEqual(sum(len(t) for t in calls["embed"]), 1)
        page_inserts = [p for s, p in conn.log if s.startswith("INSERT INTO document_pages")]
        self.assertEqual(page_inserts[0][6], "orig-page")
        self.assertIsNone(page_inserts[1][6])

    def test_original_lookup_is_scoped_to_case_user_and_earlier_arrival(self):
        conn = FakeConn([OWNED, DOC_NEW])
        run_ingest(conn, ["some text"])
        sql, params = next((s, p) for s, p in conn.log if s.startswith("SELECT p.text_sha256"))
        self.assertIn("p.case_id = %s AND p.user_id = %s", sql)
        self.assertIn("duplicate_of_page_id IS NULL", sql)
        self.assertEqual(params[0:2], ("c1", "u1"))


class TestSourcePriority(unittest.TestCase):
    NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def test_rank(self):
        self.assertEqual([v2.origin_rank(o) for o in ("own_record", "received", "sent", None, "other", "public", "gipa")],
                         [0, 0, 0, 1, 1, 1, 2])

    def test_gipa_never_original_over_email_regardless_of_order(self):
        # existing email page (rank 0, uploaded LATER than me) vs me = gipa uploaded earlier
        known, worse = v2.plan_originals([("h", "email-pg", 0, self.NOW, "d-email", 1)],
                                         (2, EARLY, "d-gipa"))
        self.assertEqual(known, {"h": "email-pg"})
        self.assertEqual(worse, {})

    def test_email_ingested_after_gipa_outranks_it(self):
        known, worse = v2.plan_originals([("h", "gipa-pg", 2, EARLY, "d-gipa", 3)], (0, self.NOW, "d-email"))
        self.assertEqual(known, {})
        self.assertEqual(worse, {"h": ["gipa-pg"]})

    def test_equal_rank_uses_arrival_then_id(self):
        known, _ = v2.plan_originals([("h", "p1", 2, EARLY, "a", 1)], (2, self.NOW, "b"))
        self.assertEqual(known, {"h": "p1"})
        _, worse = v2.plan_originals([("h", "p1", 2, self.NOW, "a", 1)], (2, EARLY, "b"))
        self.assertEqual(worse, {"h": ["p1"]})

    def test_normal_order_gipa_after_email(self):
        h = v2.page_hash("shared page")
        conn = FakeConn([OWNED, DOC_NEW, (r"SELECT p.text_sha256", [(h, "email-pg", 0, EARLY, "d-email", 1)])])
        res, calls = run_ingest(conn, ["shared page", "other words"], upload_meta={"source-origin": "gipa"})
        self.assertEqual(res["duplicate_pages"], 1)
        pg = [p for s, p in conn.log if s.startswith("INSERT INTO document_pages")]
        self.assertEqual(pg[0][6], "email-pg")
        self.assertFalse([s for s in conn.sql() if s.startswith("DELETE FROM chunks WHERE page_id")])

    def test_email_after_gipa_demotes_gipa_page_and_removes_its_chunks(self):
        h = v2.page_hash("shared page")
        conn = FakeConn([OWNED, DOC_NEW, (r"SELECT p.text_sha256", [(h, "gipa-pg", 2, EARLY, "d-gipa", 5)])])
        res, calls = run_ingest(conn, ["shared page"], upload_meta={"source-origin": "received"})
        self.assertEqual(res["duplicate_pages"], 0)
        self.assertEqual(res["chunks"], 1)  # email page is chunked as the original
        sqls = conn.sql()
        i_ins = next(i for i, s in enumerate(sqls) if s.startswith("INSERT INTO document_pages"))
        i_del = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM chunks WHERE page_id"))
        i_upd = next(i for i, s in enumerate(sqls) if s.startswith("UPDATE document_pages SET duplicate_of_page_id"))
        i_ready = next(i for i, s in enumerate(sqls) if "ingest_status = 'ready'" in s)
        self.assertTrue(i_ins < i_del < i_upd < i_ready)  # same transaction, before the single commit
        self.assertEqual(conn.log[i_del][1], (["gipa-pg"], "u1"))
        self.assertEqual(conn.log[i_upd][1][0], "id-1")  # the new original page id
        self.assertEqual(conn.log[i_upd][1][3], ["gipa-pg"])


class TestChunkHeader(unittest.TestCase):
    META = {"doc_type": "email", "institution_name": "NSW Department of Education", "sent_at": "2026-07-31T09:15:00+10:00",
            "occurred_on": None, "authored_on": None, "thread_subject": "Complaint follow-up", "title": "T"}
    PARTIES = [{"name": "Jane Roe", "role": "sender", "role_title": "Principal", "email": "jane@x.gov.au"},
               {"name": "Sam Poe", "role": "recipient", "role_title": None},
               {"name": "Cc Person", "role": "cc"}]

    def test_full(self):
        h = v2.build_chunk_header(self.META, self.PARTIES, 3)
        self.assertEqual(h, "[email · NSW Department of Education · 2026-07-31 · from Jane Roe (Principal) to Sam Poe "
                            "· re: Complaint follow-up · p.3]")

    def test_sparse_never_prints_none(self):
        h = v2.build_chunk_header({"doc_type": "other"}, [], 1)
        self.assertEqual(h, "[other · p.1]")
        self.assertNotIn("None", v2.build_chunk_header({}, [{"role": "sender"}], None))
        self.assertEqual(v2.build_chunk_header({}, [], None), "[]")

    def test_date_fallback_and_title_fallback(self):
        h = v2.build_chunk_header({"doc_type": "letter", "occurred_on": v2._parse_date("2026-03-02"),
                                   "title": "Decision"}, [], 2)
        self.assertEqual(h, "[letter · 2026-03-02 · re: Decision · p.2]")

    def test_long_names_truncated_and_capped(self):
        parties = [{"name": "N" * 500, "role": "sender", "role_title": "R" * 500}] + \
                  [{"name": "Recipient%d " % i + "x" * 200, "role": "recipient"} for i in range(10)]
        h = v2.build_chunk_header({**self.META, "thread_subject": "S" * 900}, parties, 12)
        self.assertLessEqual(len(h), v2.HEADER_MAX_CHARS)
        self.assertTrue(h.startswith("[") and h.endswith("]"))
        self.assertNotIn("\n", h)

    def test_no_email_addresses_leak(self):
        parties = [{"name": "Jane <jane@x.gov.au>", "role": "sender", "role_title": "a@b.com"},
                   {"name": "bob@x.com", "role": "recipient"}, {"name": "Real Name bob@x.com", "role": "recipient"}]
        h = v2.build_chunk_header({"doc_type": "email", "thread_subject": "Contact me@home.org now"}, parties, 1)
        self.assertNotIn("@", h)
        self.assertIn("Jane", h)
        self.assertIn("Real Name", h)

    def test_embedding_input_has_header_but_stored_content_is_raw(self):
        conn = FakeConn([OWNED, DOC_NEW])
        llm = lambda s, u: {"doc_type": "letter", "institution": "x", "title": "Hello"} \
            if s == v2.METADATA_SYSTEM else {}
        res, calls = run_ingest(conn, ["alpha beta gamma"], llm=llm)
        self.assertEqual(calls["embed"][0], ["[letter · re: Hello · p.1]\nalpha beta gamma"])
        stored = next(p for s, p in conn.log if s.startswith("INSERT INTO chunks"))
        self.assertEqual(stored[6], "alpha beta gamma")

    def test_duplicate_pages_still_get_no_chunks(self):
        h = v2.page_hash("dup page")
        conn = FakeConn([OWNED, DOC_NEW, (r"SELECT p.text_sha256", [(h, "orig", 0, EARLY, "d0", 1)])])
        res, calls = run_ingest(conn, ["dup page"])
        self.assertEqual((res["chunks"], calls["embed"]), (0, []))


class TestChunking(unittest.TestCase):
    def test_never_spans_pages_and_indexes_run_on(self):
        pages = [(1, "a1 a2 a3 a4 a5"), (2, "b1 b2 b3"), (3, "   "), (4, "c1 c2 c3 c4 c5 c6 c7")]
        chunks = v2.chunk_pages(pages, enc, dec, size=3, overlap=1)
        src = {1: set("a1 a2 a3 a4 a5".split()), 2: {"b1", "b2", "b3"}, 4: set("c1 c2 c3 c4 c5 c6 c7".split())}
        for c in chunks:
            self.assertTrue(set(c["content"].split()) <= src[c["page_no"]])
        self.assertEqual([c["chunk_index"] for c in chunks], list(range(len(chunks))))
        self.assertNotIn(3, {c["page_no"] for c in chunks})

    def test_overlap_matches_legacy_algorithm(self):
        out = v2.chunk_text("1 2 3 4 5 6 7", enc, dec, size=3, overlap=1)
        self.assertEqual(out, ["1 2 3", "3 4 5", "5 6 7"])

    def test_event_windows_group_pages(self):
        w = v2.event_windows([(1, "a" * 6), (2, "b" * 6), (3, "c" * 6)], max_chars=13)
        self.assertEqual([[n for n, _ in x] for x in w], [[1, 2], [3]])


class TestEnumValidation(unittest.TestCase):
    INST = [{"id": "i1", "name": "NSW Civil and Administrative Tribunal", "aliases": ["NCAT"]}]

    def test_bad_values_fall_back(self):
        m = v2.validate_metadata({
            "doc_type": "Banana", "institution": "Unknown Org", "occurred_on": "not a date",
            "sent_at": "garbage", "authored_on": "2026-03-02",
            "parties": [{"name": "A B", "role": "boss"}, {"name": "", "role": "sender"}, "x"],
        }, self.INST)
        self.assertEqual(m["doc_type"], "other")
        self.assertIsNone(m["institution_id"])
        self.assertIsNone(m["occurred_on"])
        self.assertIsNone(m["sent_at"])
        self.assertEqual(m["authored_on"].isoformat(), "2026-03-02")
        self.assertEqual([(p["name"], p["role"]) for p in m["parties"]], [("A B", "mentioned")])

    def test_good_values_and_alias_match(self):
        m = v2.validate_metadata({"doc_type": "EMAIL", "institution": "ncat",
                                  "parties": [{"name": "C", "role": "cc"}]}, self.INST)
        self.assertEqual((m["doc_type"], m["institution_id"], m["parties"][0]["role"]), ("email", "i1", "cc"))

    def test_garbage_input(self):
        self.assertEqual(v2.validate_metadata(None, [])["doc_type"], "other")
        self.assertEqual(v2.validate_metadata("x", [])["parties"], [])

    def test_upload_meta_enums(self):
        s = v2.parse_upload_meta({"source-origin": "GIPA", "source-batch": "release 2",
                                  "redaction-status": "weird", "source-detail": "GIPA-26-0717"})
        self.assertEqual((s["source_origin"], s["redaction_status"], s["source_batch"]), ("gipa", "unknown", "release 2"))
        d = v2.parse_upload_meta(None)
        self.assertEqual((d["source_origin"], d["redaction_status"]), (None, "unknown"))
        self.assertIsNone(v2.parse_upload_meta({"source-origin": "nonsense"})["source_origin"])
        self.assertIsNone(v2.parse_upload_meta({"source-origin": ""})["source_origin"])

    def test_events_require_date_and_title(self):
        ev = v2.validate_events({"events": [
            {"occurred_on": "2026-03-02", "title": "A", "page_no": 2, "occurred_precision": "bogus"},
            {"occurred_on": None, "title": "no date"},
            {"occurred_on": "2026-04", "title": "month only", "page_no": 99},
            {"occurred_on": "2026-02-30", "title": "bad date"},
        ]}, {1, 2})
        self.assertEqual([e["title"] for e in ev], ["A", "month only"])
        self.assertEqual(ev[0]["occurred_precision"], "day")
        self.assertEqual((ev[1]["occurred_precision"], ev[1]["page_no"]), ("month", None))


class TestReferences(unittest.TestCase):
    def test_value_norm(self):
        self.assertEqual(v2.value_norm("DGL26/251"), "dgl26251")
        self.assertEqual(v2.value_norm(" NCAT 2026/00264579 "), "ncat202600264579")

    def test_reference_rows_written_with_norm(self):
        conn = FakeConn([OWNED, DOC_NEW])
        llm = lambda s, u: {"reference_numbers": [{"kind": "doe", "value": "DGL26/251"}, "dgl26 251"]} \
            if s == v2.METADATA_SYSTEM else {}
        run_ingest(conn, ["text"], llm=llm)
        refs = [p for s, p in conn.log if s.startswith("INSERT INTO reference_numbers")]
        self.assertEqual(len(refs), 1)  # second normalises to the same value
        self.assertEqual(refs[0][-1], "dgl26251")


class TestOwnership(unittest.TestCase):
    def test_fails_closed_and_writes_nothing(self):
        conn = FakeConn([(r"FROM cases WHERE id", [])])
        with self.assertRaises(v2.OwnershipError):
            run_ingest(conn, ["text"])
        self.assertFalse([s for s in conn.sql() if s.startswith(("INSERT", "UPDATE", "DELETE"))])

    def test_missing_ids_rejected(self):
        for u, c in (("", "c1"), ("anon", "c1"), ("u1", None)):
            with self.assertRaises(v2.OwnershipError):
                v2.check_case_owner(FakeConn().cursor(), u, c)

    def test_every_insert_carries_user_and_case(self):
        conn = FakeConn([OWNED, DOC_NEW])
        llm = lambda s, u: ({"parties": [{"name": "P", "role": "sender"}], "doc_type": "email",
                             "thread_subject": "Hi", "reference_numbers": ["R1"]}
                            if s == v2.METADATA_SYSTEM else
                            {"events": [{"occurred_on": "2026-01-02", "title": "T", "parties": ["p"]}]})
        run_ingest(conn, ["some text"], llm=llm)
        tables = ("documents", "document_pages", "chunks", "parties", "threads", "reference_numbers", "events")
        for s, p in conn.log:
            m = re.match(r"INSERT INTO (\w+)", s)
            if m and m.group(1) in tables:
                self.assertIn("user_id", s, s)
                self.assertIn("case_id", s, s)
                self.assertIn("u1", p)
        self.assertTrue(any(s.startswith("INSERT INTO event_parties") for s in conn.sql()))


class TestNoIssues(unittest.TestCase):
    def test_issues_and_legacy_category_never_written(self):
        conn = FakeConn([OWNED, DOC_NEW])
        llm = lambda s, u: ({"issues": ["x"], "doc_type": "letter"} if s == v2.METADATA_SYSTEM
                            else {"events": [{"occurred_on": "2026-01-02", "title": "T", "issues": ["y"],
                                              "category": "Court"}]})
        run_ingest(conn, ["some text"], llm=llm)
        for s in conn.sql():
            self.assertFalse(re.search(r"\b(document_issues|event_issues|issues)\b", s), s)
            self.assertNotIn("legacy_category", s)


class TestLifecycle(unittest.TestCase):
    def test_status_transitions_and_reprocess_deletes(self):
        conn = FakeConn([OWNED, DOC_NEW])
        run_ingest(conn, ["text"])
        sqls = conn.sql()
        i_proc = next(i for i, s in enumerate(sqls) if "ingest_status = 'processing'" in s)
        i_del = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM events"))
        i_ready = next(i for i, s in enumerate(sqls) if "ingest_status = 'ready'" in s)
        self.assertLess(i_proc, i_del)
        self.assertLess(i_del, i_ready)
        self.assertTrue(any("ON CONFLICT (case_id, content_sha256)" in x for x in sqls))
        ins = next(p for s, p in conn.log if s.startswith("INSERT INTO documents"))
        self.assertIsNone(ins[6])  # no source metadata -> source_origin NULL

    def test_existing_ready_document_is_skipped(self):
        conn = FakeConn([OWNED, (r"^INSERT INTO documents", []),
                         (r"^SELECT id, created_at, ingest_status", [("doc-9", datetime(2026, 1, 1), "ready")])])
        res, calls = run_ingest(conn, ["text"])
        self.assertEqual(res["status"], "duplicate_document")
        self.assertEqual(calls["embed"], [])
        self.assertFalse([s for s in conn.sql() if s.startswith("DELETE")])

    def test_failure_marks_failed_and_reraises(self):
        conn = FakeConn([OWNED, DOC_NEW])

        def boom(texts):
            raise RuntimeError("embed down")
        with self.assertRaises(RuntimeError):
            v2.ingest_document(conn, user_id="u1", case_id="c1", filename="f", s3_key="k", content_sha256="h",
                               pages=["text"], upload_meta=None, llm_json=lambda s, u: {}, embed=boom,
                               encode=enc, decode=dec)
        self.assertTrue(any("ingest_status = 'failed'" in s for s in conn.sql()))

    def test_metadata_text_head_and_tail(self):
        t = v2.metadata_text(["H" * 7000, "M" * 5000, "T" * 2000])
        self.assertTrue(t.startswith("H" * 6000))
        self.assertTrue(t.endswith("T" * 1500))
        self.assertLess(len(t), 7700)


if __name__ == "__main__":
    unittest.main()
