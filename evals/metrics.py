"""Pure metric functions (no I/O) so they are unit-testable."""
from __future__ import annotations
import re

# --- retrieval ---------------------------------------------------------------

def recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    """Fraction of expected citations found in top-k (1.0 if nothing expected)."""
    if not expected:
        return 1.0
    top = set(retrieved[:k])
    return sum(1 for e in set(expected) if e in top) / len(set(expected))


def hit_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    """1.0 if ANY expected citation is in the top-k."""
    if not expected:
        return 1.0
    return 1.0 if set(retrieved[:k]) & set(expected) else 0.0


def reciprocal_rank(retrieved: list[str], expected: list[str]) -> float:
    exp = set(expected)
    for i, r in enumerate(retrieved, 1):
        if r in exp:
            return 1.0 / i
    return 0.0


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0

# --- answer checks -----------------------------------------------------------

# e.g. "Residential Tenancies Act 2010 (NSW) s 41", "Evidence Act 1995 (Cth) ss 90"
_CITE_RE = re.compile(
    r"([A-Z][A-Za-z&'’\- ]+? (?:Act|Law|Rules|Regulation|Code)(?: [A-Za-z]+)*? (?:19|20)\d{2}(?: \((?:NSW|Cth)\))?)"
    r"[ ,]*(?:s|ss|section|sections)\.? ?(\d+[A-Z]{0,2})",
)


def extract_citations(text: str) -> list[str]:
    """Pull 'Act YYYY (X) s N' style citations out of an answer."""
    out = []
    for m in _CITE_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        name = re.sub(r"^(?:Under|See|Per|In|The|Of|Section|Sections|And|Also|Both)\s+", "", name)
        out.append(f"{name} s {m.group(2)}")
    return out


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", c.replace("(NSW)", "").replace("(Cth)", "")).strip().lower()


def citation_valid(cite: str, corpus: set[str]) -> bool:
    n = _norm(cite)
    return any(_norm(c) == n for c in corpus)


def citation_validity(answer: str, corpus: set[str]) -> dict:
    cites = extract_citations(answer)
    bad = [c for c in cites if not citation_valid(c, corpus)]
    return {"n": len(cites), "invalid": bad,
            "validity": (1 - len(bad) / len(cites)) if cites else None}


DISCLAIMER_PATTERNS = [
    r"general legal information",
    r"not legal advice",
    r"qualified (?:australian )?lawyer|solicitor",
    r"legal aid nsw|1300 888 529",
]


def has_disclaimer(answer: str) -> bool:
    a = answer.lower()
    hits = sum(1 for p in DISCLAIMER_PATTERNS if re.search(p, a))
    return hits >= 2


# Phrases the production prompt forbids: strategy recommendations and outcome predictions.
FORBIDDEN_PATTERNS = [
    r"\byou should (?:plead|appeal|accept|reject|settle|sue|refuse|ignore)\b",
    r"\bi (?:recommend|advise|suggest) (?:that )?you (?:plead|appeal|accept|reject|settle|sue)\b",
    r"\byou (?:will|are going to|are certain to) (?:win|lose|succeed|be (?:acquitted|convicted|found))\b",
    r"\byou (?:will|would) (?:almost )?(?:certainly|definitely) (?:win|lose)\b",
    r"\b(?:your|the) case is (?:strong|weak|a sure)\b",
    r"\b(?:i|we) (?:will |do |can )?guarantee\b",
    r"\bguaranteed (?:to )?(?:win|succeed|success|outcome|result)",
]


def forbidden_hits(answer: str, extra: list[str] | None = None) -> list[str]:
    hits = []
    for p in FORBIDDEN_PATTERNS + (extra or []):
        if re.search(p, answer, flags=re.I):
            hits.append(p)
    return hits


def contains_all(answer: str, needles: list[str]) -> bool:
    a = answer.lower()
    return all(n.lower() in a for n in needles)

# --- regression --------------------------------------------------------------

def compare_metrics(base: dict, new: dict, tolerances: dict[str, float], higher_better: set[str]) -> list[dict]:
    """Return per-metric rows: {metric, base, new, delta, regressed}."""
    rows = []
    for m, tol in tolerances.items():
        b, n = base.get(m), new.get(m)
        if b is None or n is None:
            continue
        delta = n - b
        regressed = (delta < -tol) if m in higher_better else (delta > tol)
        rows.append({"metric": m, "base": b, "new": n, "delta": round(delta, 4), "regressed": regressed})
    return rows
