"""Stdlib-only tests for the Gmail-export importer. Synthetic fixtures only; no DB, network or psycopg2."""
import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.ingestion import gmail_import as gi
from services.ingestion import v2
from services.ingestion.tests.test_v2 import FakeConn, OWNED, run_ingest

enc = lambda t: t.split()
dec = lambda toks: " ".join(toks)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestAddresses(unittest.TestCase):
    def test_parse_address_variants(self):
        self.assertEqual(gi.parse_address("Ann Lee <Ann@Ex.org>"), ("Ann Lee", "ann@ex.org"))
        self.assertEqual(gi.parse_address('"Lee, Ann" <a@ex.org>'), ("Lee, Ann", "a@ex.org"))
        self.assertEqual(gi.parse_address("a@ex.org"), ("", "a@ex.org"))
        self.assertEqual(gi.parse_address("권소정"), ("권소정", ""))
        self.assertEqual(gi.parse_address({"name": "N", "email": "X@y.z"}), ("N", "x@y.z"))
        self.assertEqual(gi.parse_address(None), ("", ""))

    def test_parse_list_variants(self):
        self.assertEqual(gi.parse_address_list("A <a@x.org>, b@x.org; C <c@x.org>"),
                         [("A", "a@x.org"), ("", "b@x.org"), ("C", "c@x.org")])
        self.assertEqual(gi.parse_address_list(["A <a@x.org>", "b@x.org"]), [("A", "a@x.org"), ("", "b@x.org")])
        self.assertEqual(gi.parse_address_list(None), [])
        self.assertEqual(gi.parse_address_list(""), [])

    def test_owner_detection(self):
        self.assertEqual(gi.message_origin("X", "ME@ex.org", ["me@ex.org"], []), "sent")
        self.assertEqual(gi.message_origin("권소정", "", [], ["me", "권소정"]), "sent")
        self.assertEqual(gi.message_origin("ME", "other@z.org", [], ["me"]), "sent")
        self.assertEqual(gi.message_origin("Someone", "s@z.org", ["me@ex.org"], ["me"]), "received")
        self.assertEqual(gi.message_origin("", "", [], []), "received")

    def test_normalise_subject(self):
        self.assertEqual(gi.normalise_subject("RE: Fwd: Re: Hello  there"), "Hello there")
        self.assertEqual(gi.normalise_subject("답장: 회신: 안녕"), "안녕")
        self.assertIsNone(gi.normalise_subject("Re:"))
        self.assertIsNone(gi.normalise_subject(None))


class TestStripQuoted(unittest.TestCase):
    def test_english_wrote_single_and_wrapped(self):
        a = "Thanks, see below.\n\nOn Mon, 3 Mar 2026 at 10:00, Pat Doe <p@x.org> wrote:\n> old text\n> more"
        self.assertEqual(gi.strip_quoted(a), "Thanks, see below.")
        b = "Reply here.\n\nOn Mon, 3 Mar 2026 at 10:00 AM Pat Doe\n<p@x.org> wrote:\nold text"
        self.assertEqual(gi.strip_quoted(b), "Reply here.")

    def test_korean(self):
        t = "확인했습니다.\n\n2026년 3월 3일 (월) 오전 10:00, 홍길동 <h@x.org>님이 작성:\n> 이전 내용"
        self.assertEqual(gi.strip_quoted(t), "확인했습니다.")

    def test_original_message_and_outlook_block(self):
        a = "Hi\n\n-----Original Message-----\nFrom: A\nSent: x\nbody"
        self.assertEqual(gi.strip_quoted(a), "Hi")
        b = "Hello\n\nFrom: Pat Doe\nSent: Monday\nTo: Me\nSubject: S\n\nquoted"
        self.assertEqual(gi.strip_quoted(b), "Hello")
        c = "Hello\n\n**From:** Pat\n**Sent:** Monday\n**To:** Me\nquoted"
        self.assertEqual(gi.strip_quoted(c), "Hello")

    def test_gt_lines_removed_and_plain_text_untouched(self):
        self.assertEqual(gi.strip_quoted("a\n> q\nb"), "a\nb")
        plain = "On the other hand we agree.\nNothing quoted here."
        self.assertEqual(gi.strip_quoted(plain), plain)

    def test_forwarded_header_kept(self):
        t = "FYI\n\n---------- Forwarded message ---------\nFrom: A\nDate: Mon\nTo: B\nSubject: S\n\nbody"
        self.assertEqual(gi.strip_quoted(t), t)

    def test_all_quoted_returns_empty(self):
        self.assertEqual(gi.strip_quoted("On Mon, X wrote:\n> q"), "")


class TestManifest(unittest.TestCase):
    def line(self, **kw):
        d = {"sender": "A <a@x.org>", "to": "B <b@x.org>", "cc": None, "date_received": "2026-03-02T10:00:00+11:00",
             "subject": "Subj", "gmail_message_id": "m1", "gmail_thread_id": "t1", "attachments": ["f.pdf"]}
        d.update(kw)
        return json.dumps(d)

    def test_defensive_parsing(self):
        m, e = gi.parse_manifest_line(self.line())
        self.assertIsNone(e)
        self.assertEqual((m["message_id"], m["thread_id"], m["sender"], m["to"], m["cc"]),
                         ("m1", "t1", ("A", "a@x.org"), [("B", "b@x.org")], []))
        self.assertEqual(gi.parse_manifest_line("not json")[1], "parse_error")
        self.assertEqual(gi.parse_manifest_line("[1]")[1], "parse_error")
        self.assertEqual(gi.parse_manifest_line(json.dumps({"subject": "x"}))[1], "skipped")
        m, _ = gi.parse_manifest_line(json.dumps({"gmail_message_id": "z", "attachments": "one.pdf", "to": ["a@b.c"]}))
        self.assertEqual((m["attachments"], m["sent_at"], m["thread_id"]), (["one.pdf"], None, None))

    def test_date_formats(self):
        self.assertTrue(gi.parse_date_received("Mon, 02 Mar 2026 10:00:00 +1100").startswith("2026-03-02T10:00:00"))
        self.assertIsNone(gi.parse_date_received("garbage"))

    def test_sorted_ascending_dedup_and_counts(self):
        lines = [self.line(gmail_message_id="late", date_received="2026-05-01T00:00:00Z"),
                 "oops", "", self.line(gmail_message_id="early", date_received="2026-01-01T00:00:00Z",
                                       sender="me <me@x.org>", gmail_thread_id="t2"),
                 self.line(gmail_message_id="early"), self.line(gmail_message_id="undated", date_received=None)]
        msgs, base = gi.read_manifest(lines)
        self.assertEqual([m["message_id"] for m in msgs], ["early", "late", "undated"])
        self.assertEqual(base, {"parse_errors": 1, "skipped": 2})
        c = gi.plan_counts(msgs, base, ["me@x.org"], [])
        self.assertEqual((c["emails"], c["attachments"], c["sent"], c["received"], c["threads"]), (3, 3, 1, 2, 2))


class TestPartiesAndPages(unittest.TestCase):
    def msg(self, **kw):
        base = {"sender": ("Ann", "a@x.org"), "to": [("Bob", "b@x.org"), ("Bob again", "b@x.org")],
                "cc": [("", "c@x.org")], "persons": ["Zed Unknown", "Pat Known"], "organisations": [],
                "reference_numbers": [], "subject": "S", "sent_at": None}
        base.update(kw)
        return base

    def test_parties_dedupe_and_persons_only_if_existing(self):
        ps = gi.build_parties(self.msg(), [("pat known", "Principal")])
        roles = [(p["role"], p["email"]) for p in ps]
        self.assertEqual(roles, [("sender", "a@x.org"), ("recipient", "b@x.org"), ("cc", "c@x.org"), ("mentioned", None)])
        self.assertEqual(ps[2]["name"], "c@x.org")
        self.assertNotIn("Zed Unknown", [p["name"] for p in ps])
        self.assertEqual(ps[3]["role_title"], "Principal")

    def test_choose_pages(self):
        good = "x" * 80
        self.assertEqual(gi.choose_pages([good, good], "md"), ([good, good], True))
        self.assertEqual(gi.choose_pages(["", "", good], "md text"), (["md text"], False))   # image-only
        self.assertEqual(gi.choose_pages(None, " md "), (["md"], False))                    # original missing
        self.assertEqual(gi.choose_pages(None, None), ([], False))

    def test_find_attachment_file(self):
        names = ["1_m1_01_report.pdf", "2_m2_01_report.pdf"]
        self.assertEqual(gi.find_attachment_file(names, "report.pdf", "m2"), "2_m2_01_report.pdf")
        self.assertEqual(gi.find_attachment_file(names, "1_m1_01_report.pdf", "m1"), "1_m1_01_report.pdf")
        self.assertIsNone(gi.find_attachment_file(names, "nope.pdf", "m1"))

    def test_content_hash_unique_per_external_id(self):
        self.assertNotEqual(gi.content_hash("a", "same"), gi.content_hash("b", "same"))


def docs_responder():
    n = {"i": 0}

    def rows(params):
        n["i"] += 1
        return [(f"doc-{n['i']}", T0)]
    return rows


class TestImportMessage(unittest.TestCase):
    MSG = {"message_id": "m1", "thread_id": "t1", "subject": "RE: Synthetic subject",
           "sender": ("Me", "me@x.org"), "to": [("Bob", "b@x.org")], "cc": [], "sent_at": "2026-03-02T10:00:00+11:00",
           "email_text_path": "extracted_text/ts_m1/email.md", "attachments": ["a.pdf", "b.docx", "c.pdf"],
           "persons": [], "organisations": ["NCAT"], "reference_numbers": ["REF 12/34"]}
    RAW = "My new words here.\n\nOn Mon, 2 Mar 2026, Bob <b@x.org> wrote:\n> quoted history words"

    def conn(self):
        return FakeConn([OWNED, (r"^SELECT 1 FROM documents", []),
                         (r"^SELECT id, name, aliases FROM institutions", [("i1", "NSW Civil and Administrative Tribunal", ["NCAT"])]),
                         (r"^SELECT name, role_title FROM parties", []),
                         (r"^INSERT INTO documents", docs_responder()),
                         (r"^SELECT thread_id FROM documents", [("th-1",)])])

    def run_msg(self, conn, **kw):
        llm_calls = []

        def llm(system, user):
            llm_calls.append(system)
            return {}
        pdf = {"1_m1_01_a.pdf": ["page one words " * 6, "page two words " * 6]}
        texts = {"extracted_text/ts_m1/email.md": self.RAW, "extracted_text/ts_m1/02_b.md": "docx extracted words",
                 "extracted_text/ts_m1/03_c.md": "image only md words"}
        embeds = []

        def embed(ts):
            embeds.extend(ts)
            return [[0.0] for _ in ts]
        args = dict(msg=self.MSG, user_id="u1", case_id="c1", category="Cat", owner_emails=["me@x.org"],
                    owner_names=[], keep_quotes=False, read_text=texts.get,
                    read_pdf_pages=lambda n: pdf.get(n) if n != "3_m1_03_c.pdf" else ["", ""],
                    list_attachment_files=lambda: ["1_m1_01_a.pdf", "2_m1_02_b.docx", "3_m1_03_c.pdf"],
                    attachment_text_files=lambda m: {1: "extracted_text/ts_m1/01_a.md",
                                                     2: "extracted_text/ts_m1/02_b.md", 3: "extracted_text/ts_m1/03_c.md"},
                    llm_json=llm, embed=embed, encode=enc, decode=dec)
        args.update(kw)
        return gi.import_message(conn, **args), llm_calls, embeds

    def test_full_flow(self):
        conn = self.conn()
        res, llm_calls, embeds = self.run_msg(conn)
        self.assertEqual(res, {"status": "imported", "attachments": 3, "origin": "sent"})
        # metadata LLM never asked; only event extraction
        self.assertNotIn(v2.METADATA_SYSTEM, llm_calls)
        self.assertTrue(llm_calls and all(c == v2.EVENTS_SYSTEM for c in llm_calls))
        # caller owns the transaction
        self.assertEqual((conn.commits, conn.rollbacks), (0, 0))
        docs = [p for s, p in conn.log if s.startswith("INSERT INTO documents")]
        self.assertEqual([d[3] for d in docs][:1], ["email.md"])
        self.assertEqual([d[9 + 1] for d in docs], ["m1", "m1#01", "m1#02", "m1#03"])  # external_id
        self.assertTrue(all(d[11] == "Cat" and d[6] == "sent" for d in docs))        # label, origin
        links = [p for s, p in conn.log if s.startswith("INSERT INTO document_links")]
        self.assertEqual(links, [("doc-2", "doc-1"), ("doc-3", "doc-1"), ("doc-4", "doc-1")])
        # thread by external id, attachments reuse the email's thread
        thr = next((p for s, p in conn.log if s.startswith("INSERT INTO threads")), None)
        self.assertEqual((thr[2], thr[5]), ("Synthetic subject", "t1"))
        upd = [p for s, p in conn.log if s.startswith("UPDATE documents SET title")]
        self.assertEqual([u[6] for u in upd][1:], ["th-1"] * 3)
        # parties/refs written, institution matched by alias only
        self.assertTrue(any(s.startswith("INSERT INTO reference_numbers") for s in conn.sql()))
        refs = next(p for s, p in conn.log if s.startswith("INSERT INTO reference_numbers"))
        self.assertEqual((refs[3], refs[-1]), ("i1", "ref1234"))
        self.assertEqual(sum(1 for s in conn.sql() if s.startswith("INSERT INTO document_parties")), 2 * 4)
        # no issues, no legacy category
        for s in conn.sql():
            self.assertFalse(re.search(r"\b(document_issues|event_issues|issues)\b", s))
            self.assertNotIn("legacy_category", s)

    def test_quotes_stripped_for_embedding_but_hash_and_page_are_raw(self):
        conn = self.conn()
        _, _, embeds = self.run_msg(conn)
        email_embed = [e for e in embeds if "My new words" in e]
        self.assertEqual(len(email_embed), 1)
        self.assertNotIn("quoted history", email_embed[0])
        chunk = next(p for s, p in conn.log if s.startswith("INSERT INTO chunks"))
        self.assertNotIn("quoted history", chunk[6])
        page = next(p for s, p in conn.log if s.startswith("INSERT INTO document_pages"))
        self.assertEqual(page[3], 1)
        self.assertEqual(page[4], v2.page_hash(self.RAW))            # raw text hash
        self.assertEqual(page[5], len(self.RAW.strip()))

    def test_keep_quotes(self):
        _, _, embeds = self.run_msg(self.conn(), keep_quotes=True)
        self.assertTrue(any("quoted history" in e for e in embeds))

    def test_pseudo_pages_have_null_page_no_and_numbered_pdf_keeps_numbers(self):
        conn = self.conn()
        _, _, embeds = self.run_msg(conn)
        chunks = [p for s, p in conn.log if s.startswith("INSERT INTO chunks")]
        by_doc = {}
        for p in chunks:
            by_doc.setdefault(p[0], []).append(p[4])
        self.assertEqual(set(by_doc["doc-1"]), {None})              # email: pseudo page
        self.assertEqual(sorted(set(by_doc["doc-2"])), [1, 2])      # original PDF: page-aware
        self.assertEqual(set(by_doc["doc-3"]), {None})              # docx: md fallback
        self.assertEqual(set(by_doc["doc-4"]), {None})              # image-only PDF: md fallback
        self.assertTrue(any(e.startswith("[attachment") and "p." in e.split("\n")[0] for e in embeds))
        self.assertFalse(any(e.startswith("[email") and "p." in e.split("\n")[0] for e in embeds))

    def test_existing_external_id_is_skipped(self):
        conn = FakeConn([OWNED, (r"^SELECT 1 FROM documents", [(1,)])])
        res, calls, embeds = self.run_msg(conn)
        self.assertEqual(res["status"], "exists")
        self.assertFalse([s for s in conn.sql() if s.startswith(("INSERT", "UPDATE", "DELETE"))])

    def test_ownership_fail_closed(self):
        conn = FakeConn([(r"FROM cases WHERE id", [])])
        with self.assertRaises(v2.OwnershipError):
            self.run_msg(conn)
        self.assertFalse([s for s in conn.sql() if s.startswith(("INSERT", "UPDATE", "DELETE"))])

    def test_failure_propagates_without_commit_or_rollback_inside(self):
        conn = self.conn()

        def boom(ts):
            raise RuntimeError("embed down")
        with self.assertRaises(RuntimeError):
            self.run_msg(conn, embed=boom)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 0))  # the script rolls back the message


class TestV2Extensions(unittest.TestCase):
    def test_prevalidated_meta_skips_metadata_llm_and_numbered_false(self):
        conn = FakeConn([OWNED, (r"^INSERT INTO documents", [("doc-1", T0)])])
        seen = []

        def llm(system, user):
            seen.append(system)
            return {"events": [{"occurred_on": "2026-01-02", "title": "T", "page_no": 1}]}
        res, calls = run_ingest(conn, ["raw text one"], llm=llm, prevalidated_meta={"doc_type": "letter", "title": "X"},
                                index_texts=["indexed text"], numbered_pages=False)
        self.assertEqual(seen, [v2.EVENTS_SYSTEM])
        self.assertEqual(calls["embed"][0], ["[letter · re: X]\nindexed text"])
        chunk = next(p for s, p in conn.log if s.startswith("INSERT INTO chunks"))
        self.assertEqual((chunk[4], chunk[6]), (None, "indexed text"))
        ev = next(p for s, p in conn.log if s.startswith("INSERT INTO events"))
        self.assertIsNone(ev[9])                                   # events.page_no NULL
        page = next(p for s, p in conn.log if s.startswith("INSERT INTO document_pages"))
        self.assertEqual(page[4], v2.page_hash("raw text one"))    # hash of raw, not indexed
        self.assertEqual((conn.commits, conn.rollbacks), (2, 1))  # unchanged S3 transaction handling

    def test_s3_path_unchanged_still_asks_metadata_llm(self):
        conn = FakeConn([OWNED, (r"^INSERT INTO documents", [("doc-1", T0)])])
        seen = []
        run_ingest(conn, ["some text"], llm=lambda s, u: seen.append(s) or {})
        self.assertIn(v2.METADATA_SYSTEM, seen)

    def test_party_reused_by_email(self):
        conn = FakeConn([OWNED, (r"^INSERT INTO documents", [("doc-1", T0)]),
                         (r"lower\(email\) = %s", [("party-9",)])])
        run_ingest(conn, ["t"], prevalidated_meta={"parties": [{"name": "N", "role": "sender", "role_title": None,
                                                                "email": "A@X.org", "institution_id": None}]})
        self.assertFalse([s for s in conn.sql() if s.startswith("INSERT INTO parties")])
        dp = next(p for s, p in conn.log if s.startswith("INSERT INTO document_parties"))
        self.assertEqual(dp[1], "party-9")


class TestScriptDryRun(unittest.TestCase):
    def test_dry_run_prints_counts_only(self):
        spec = importlib.util.spec_from_file_location("imp", Path(__file__).resolve().parents[3] / "scripts/import_gmail_export.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            lines = [json.dumps({"gmail_message_id": "m1", "gmail_thread_id": "t1", "subject": "SECRETSUBJECT",
                                 "sender": "SecretName <me@x.org>", "attachments": ["x.pdf"],
                                 "date_received": "2026-01-01T00:00:00Z"}),
                     json.dumps({"gmail_message_id": "m2", "sender": "o@y.org", "date_received": "2026-01-02T00:00:00Z"}),
                     "garbage"]
            Path(d, "manifest.jsonl").write_text("\n".join(lines), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = mod.main(["--export-dir", d, "--case-id", "c", "--user-id", "u", "--owner-email", "me@x.org"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("[DRY RUN] emails=2 attachments=1 sent=1 received=1 threads=1 skipped=0 parse_errors=1", out)
        self.assertNotIn("SECRET", out)

    def test_apply_flag_parsing(self):
        spec = importlib.util.spec_from_file_location("imp2", Path(__file__).resolve().parents[3] / "scripts/import_gmail_export.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        base = ["--export-dir", "x", "--case-id", "c", "--user-id", "u"]
        self.assertFalse(mod.parse_args(base).apply)
        self.assertTrue(mod.parse_args(base + ["--apply"]).apply)
        self.assertFalse(mod.parse_args(base + ["--apply", "--dry-run"]).apply)


if __name__ == "__main__":
    unittest.main()
