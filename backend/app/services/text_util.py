from __future__ import annotations

import re

STOPWORDS = set("""a an the and or but if of to in on for with by from at as is are was were be been being
does do did doing has have had having it its this that these those which who whom what when where why how
can could should would may might will shall not no yes than then so such into about over under between
while during after before more most less least very much many some any each other own same only also just
students student people""".split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")


def keywords(text: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for w in WORD_RE.findall(text.lower()):
        w = w.strip("'-")
        if len(w) > 2 and w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen[:limit]


def overlap_score(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    low = text.lower()
    hits = sum(1 for t in terms if t in low or t.rstrip("s") in low)
    return hits / len(terms)


def normalize_for_match(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[\u2018\u2019\u201c\u201d\"'`*_#>\[\]()]", "", t)
    t = re.sub(r"[^a-z0-9%.,;: -]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """True when the quote appears (modulo punctuation/markdown/whitespace) in the source text."""
    q = normalize_for_match(quote)
    if len(q) < 20:
        return False
    return q in normalize_for_match(text)


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", re.sub(r"[#>*_`]+", " ", text))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"\u201c])", text)
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 400]


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"
