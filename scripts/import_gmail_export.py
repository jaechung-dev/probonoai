#!/usr/bin/env python3
"""
Import a Gmail export (data/Bella/<Category>/) into the case model v2.

  python3 scripts/import_gmail_export.py --export-dir data/Bella/<Category> --case-id <uuid> --user-id <uuid> \
      --owner-email me@example.com --owner-name me [--keep-quotes] [--apply]

Default is a DRY RUN: counts only (never subjects, names or body text). --apply writes.
Needs DATABASE_URL and OPENAI_API_KEY in the environment (only with --apply). Run from the repo root.
Order: messages by date ascending, each email before its attachments, one transaction per message.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ingestion import gmail_import as gi  # noqa: E402
from services.ingestion import v2  # noqa: E402

MIN_PDF_CHARS_PAGE = 50
EMBED_MODEL = "text-embedding-3-small"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--owner-email", action="append", default=[])
    ap.add_argument("--owner-name", action="append", default=[])
    ap.add_argument("--category", default=None, help="default: export folder name")
    ap.add_argument("--keep-quotes", action="store_true", help="do not strip quoted reply history")
    ap.add_argument("--dry-run", action="store_true", help="default; accepted for clarity")
    ap.add_argument("--apply", action="store_true", help="write to the database")
    a = ap.parse_args(argv)
    a.apply = bool(a.apply and not a.dry_run)
    return a


class ExportFiles:
    """Filesystem access confined to the export dir."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _safe(self, rel) -> Path | None:
        if not rel:
            return None
        # Manifest paths are repo-root-relative (e.g. data/Bella/DOE/extracted_text/…),
        # so try CWD-relative (repo root when run from there) in addition to root-relative.
        for base in (self.root, Path.cwd()):
            p = (base / str(rel)).resolve()
            if (p == self.root or self.root in p.parents) and p.is_file():
                return p
        return None

    def read_text(self, rel):
        p = self._safe(rel)
        return p.read_text(encoding="utf-8", errors="replace") if p else None

    def list_attachment_files(self):
        d = self.root / "attachments"
        return sorted(f.name for f in d.iterdir() if f.is_file()) if d.is_dir() else []

    def read_pdf_pages(self, name):
        """Native text per page (same rule as handler._parse_pdf_pages, minus OCR). None if unavailable."""
        p = self._safe(f"attachments/{name}")
        if not p:
            return None
        try:
            import fitz  # PyMuPDF
            with fitz.open(p) as doc:
                return [page.get_text() for page in doc]
        except Exception:
            return None

    def attachment_text_files(self, msg):
        """{nn: relative path of extracted .md} from extracted_text/<ts>_<msgid>/NN_*.md."""
        tp = msg.get("email_text_path")
        out = {}
        if not tp:
            return out
        # email_text_path is repo-root-relative; resolve from CWD (repo root).
        d = (Path.cwd() / str(tp)).resolve().parent
        if d.is_dir() and (d == self.root or self.root in d.parents):
            for f in sorted(d.iterdir()):
                m = re.match(r"(\d+)_", f.name)
                if f.suffix == ".md" and m:
                    out.setdefault(int(m.group(1)), str(f.relative_to(self.root)))
        return out


def make_ai():
    import openai
    import tiktoken
    import json
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    enc = tiktoken.encoding_for_model(EMBED_MODEL)

    def llm_json(system, user):
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini", temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            d = json.loads(r.choices[0].message.content)
            return d if isinstance(d, dict) else {}
        except Exception as _llm_exc:
            print(f"  LLM call failed: {type(_llm_exc).__name__}: {_llm_exc}", file=sys.stderr)
            return {}

    def embed(texts):
        out = []
        for i in range(0, len(texts), 100):
            out.extend(e.embedding for e in client.embeddings.create(model=EMBED_MODEL, input=texts[i:i + 100]).data)
        return out

    return llm_json, embed, enc.encode, enc.decode


def main(argv=None) -> int:
    a = parse_args(argv)
    root = Path(a.export_dir)
    category = a.category or root.resolve().name
    manifest = root / "manifest.jsonl"
    if not manifest.is_file():
        print("manifest.jsonl not found in export dir", file=sys.stderr)
        return 2
    with manifest.open(encoding="utf-8", errors="replace") as fh:
        msgs, base = gi.read_manifest(fh)
    counts = gi.plan_counts(msgs, base, a.owner_email, a.owner_name)
    mode = "APPLY" if a.apply else "DRY RUN"
    print(f"[{mode}] " + " ".join(f"{k}={v}" for k, v in counts.items()))
    if not a.apply:
        print("Nothing written. Re-run with --apply to import.")
        return 0

    import psycopg2
    llm_json, embed, encode, decode = make_ai()
    files = ExportFiles(root)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    done = {"imported": 0, "exists": 0, "failed": 0, "attachments": 0}
    try:
        cur = conn.cursor()
        v2.check_case_owner(cur, a.user_id, a.case_id)  # fail closed before anything else
        conn.rollback()
        for m in msgs:
            try:
                r = gi.import_message(
                    conn, msg=m, user_id=a.user_id, case_id=a.case_id, category=category,
                    owner_emails=a.owner_email, owner_names=a.owner_name, keep_quotes=a.keep_quotes,
                    read_text=files.read_text, read_pdf_pages=files.read_pdf_pages,
                    list_attachment_files=files.list_attachment_files,
                    attachment_text_files=files.attachment_text_files,
                    llm_json=llm_json, embed=embed, encode=encode, decode=decode)
                conn.commit()
                done["exists" if r["status"] == "exists" else "imported"] += 1
                done["attachments"] += r.get("attachments", 0)
            except Exception as exc:
                conn.rollback()
                done["failed"] += 1
                print(f"FAILED message_id={m['message_id']} ({type(exc).__name__})", file=sys.stderr)
    finally:
        conn.close()
    print("[DONE] " + " ".join(f"{k}={v}" for k, v in done.items()))
    return 1 if done["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
