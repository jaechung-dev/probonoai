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
MIN_OCR_ALPHANUM_CHARS = 15  # discard OCR output with fewer non-whitespace alphanumeric chars (catches signature noise)
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
    ap.add_argument("--force-reocr", default=None,
                    help="comma-separated external_ids to force re-extraction/re-import "
                         "regardless of existing content (e.g. msgid#04,msgid#03)")
    a = ap.parse_args(argv)
    a.apply = bool(a.apply and not a.dry_run)
    a.force_reocr = set(a.force_reocr.split(",")) if a.force_reocr else set()
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
        # Attachments may be nested in per-message subdirs (<ts>_<msgid>/)
        d = self.root / "attachments"
        return sorted(f.name for f in d.rglob("*") if f.is_file()) if d.is_dir() else []

    def _find_attachment(self, name) -> "Path | None":
        """Locate an attachment file by name anywhere under attachments/."""
        d = self.root / "attachments"
        if not d.is_dir():
            return None
        matches = [f for f in d.rglob(name) if f.is_file()]
        return matches[0] if matches else None

    def read_attachment_pages(self, name) -> "tuple[list[str], bool]":
        """Extract text from any attachment type.
        Returns (pages, numbered): numbered=True for PDF (per-page), False for single pseudo-page.
        Returns ([], False) if nothing extractable or on any error; logs reason to stderr."""
        p = self._find_attachment(name)
        if not p:
            print(f"  [WARN] attachment not found in fs: {name}", file=sys.stderr)
            return [], False
        ext = p.suffix.lower()
        try:
            if ext == ".pdf":
                pages = self._read_pdf_pages(p)
                if not any(t for t in pages):
                    print(f"  [WARN] OCR empty ({len(pages)}p): {p.name}", file=sys.stderr)
                return pages, True
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"):
                text = self._ocr_image(p)
                if not text:
                    print(f"  [WARN] OCR empty: {p.name}", file=sys.stderr)
                return ([text] if text else []), False
            elif ext == ".docx":
                text = self._extract_docx(p)
                if not text:
                    print(f"  [WARN] DOCX empty: {p.name}", file=sys.stderr)
                return ([text] if text else []), False
            elif ext == ".eml":
                text = self._extract_eml(p)
                if not text:
                    print(f"  [WARN] EML empty: {p.name}", file=sys.stderr)
                return ([text] if text else []), False
            else:
                print(f"  [WARN] unsupported extension {ext!r}: {p.name}", file=sys.stderr)
                return [], False
        except Exception as exc:
            print(f"  [WARN] extraction error {p.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return [], False

    def _read_pdf_pages(self, p) -> "list[str]":
        """Per-page text with OCR fallback for image-only pages (fitz + Tesseract at 300 DPI)."""
        import fitz
        pages = []
        with fitz.open(p) as doc:
            for page in doc:
                text = page.get_text().strip()
                if len(text) < MIN_PDF_CHARS_PAGE:
                    try:
                        tp = page.get_textpage_ocr(dpi=300, full=True)
                        text = page.get_text(textpage=tp).strip()
                    except Exception:
                        pass
                pages.append(text)
        return pages

    def _ocr_image(self, p) -> "str | None":
        """OCR a raster image (PNG/JPEG/GIF/etc.) using fitz + Tesseract at 300 DPI.
        Returns None if the result has fewer than MIN_OCR_ALPHANUM_CHARS alphanumeric chars."""
        import fitz
        with fitz.open(p) as doc:
            if not doc:
                return None
            page = doc[0]  # hold reference so the Page object isn't GC'd between calls
            tp = page.get_textpage_ocr(dpi=300, full=True)
            text = page.get_text(textpage=tp).strip()
        if not text:
            return None
        alphanum_count = len(re.sub(r"[^a-zA-Z0-9]", "", text))
        if alphanum_count < MIN_OCR_ALPHANUM_CHARS:
            print(f"  [OCR-DISCARD] {p.name}: {alphanum_count} alphanum chars < {MIN_OCR_ALPHANUM_CHARS}",
                  file=sys.stderr)
            return None
        return text

    def _extract_docx(self, p) -> "str | None":
        """Extract text from a DOCX file using python-docx."""
        import docx
        d = docx.Document(str(p))
        text = "\n\n".join(para.text for para in d.paragraphs if para.text.strip())
        return text or None

    def _extract_eml(self, p) -> "str | None":
        """Extract text from an embedded EML using stdlib email."""
        import email as _email
        data = p.read_bytes()
        msg = _email.message_from_bytes(data)
        headers = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h])
        body_parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                body_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
        text = f"{headers}\n\n{''.join(body_parts)}".strip()
        return text or None

    def attachment_text_files(self, msg):
        """{nn: relative path of extracted .md} from extracted_text/<ts>_<msgid>/NN_*.md."""
        tp = msg.get("email_text_path")
        if not tp:
            return {}
        # Derive the per-message dir name from email_text_path (parent of email.md).
        # This avoids CWD dependency: works regardless of where the script is invoked from.
        msg_dir = Path(str(tp)).parent.name
        d = self.root / "extracted_text" / msg_dir
        if not d.is_dir():
            return {}
        out = {}
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

    if a.force_reocr and not a.apply:
        # Preview: extract files for force-reocr targets and print per-page char counts
        files = ExportFiles(root)
        found = set()
        for m in msgs:
            for idx, entry in enumerate(m.get("attachments", []), start=1):
                ext_id = f"{m['message_id']}#{idx:02d}"
                if ext_id not in a.force_reocr:
                    continue
                found.add(ext_id)
                orig = gi.find_attachment_file(files.list_attachment_files(), entry, m["message_id"], idx - 1)
                if not orig:
                    print(f"[PREVIEW] {ext_id}: FILE NOT FOUND in fs (manifest entry={entry!r})")
                    continue
                pages, numbered = files.read_attachment_pages(orig)
                total = sum(len(t) for t in pages)
                print(f"[PREVIEW] {ext_id}: {orig}  ({len(pages)} pages, {total} chars total)")
                for i, text in enumerate(pages, 1):
                    print(f"  page {i}: {len(text)} chars")
                if not pages:
                    print(f"  → no text extracted")
        for missing in a.force_reocr - found:
            print(f"[PREVIEW] {missing}: not found in manifest")
        return 0

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
                    read_text=files.read_text, read_attachment_pages=files.read_attachment_pages,
                    list_attachment_files=files.list_attachment_files,
                    attachment_text_files=files.attachment_text_files,
                    force_reocr=a.force_reocr,
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
