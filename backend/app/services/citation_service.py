"""Converts canonical [Sxx] source markers into IEEE / APA / MLA citations and builds references.

Reports are stored with [Sxx] markers so every citation stays traceable to a collected source.
Markers that don't correspond to an allowed (verified) source are stripped and logged."""
from __future__ import annotations

import re
from datetime import datetime

MARKER_RE = re.compile(r"\[(S\d{2,3})\]")
MARKER_GROUP_RE = re.compile(r"(?:\[S\d{2,3}\](?:[ \t]*,?[ \t]*(?=\[S))?)+")


def year_of(date: str) -> str:
    m = re.search(r"(19|20)\d{2}", date or "")
    return m.group(0) if m else ""


def _split_name(name: str) -> tuple[list[str], str]:
    name = re.sub(r"\s+", " ", name.strip())
    if "," in name:  # "Smith, John"
        last, _, first = name.partition(",")
        return first.split(), last.strip()
    parts = name.split(" ")
    return parts[:-1], parts[-1] if parts else ""


def _initials(given: list[str]) -> str:
    return " ".join(g[0].upper() + "." for g in given if g and g[0].isalpha())


def surname(name: str) -> str:
    return _split_name(name)[1]


def _ieee_author(name: str) -> str:
    given, last = _split_name(name)
    ini = _initials(given)
    return f"{ini} {last}".strip()


def _apa_author(name: str) -> str:
    given, last = _split_name(name)
    ini = _initials(given)
    return f"{last}, {ini}".strip().rstrip(",")


def _join(items: list[str], conj: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + f", {conj} {items[-1]}"


def _accessed(src: dict, fmt: str) -> str:
    try:
        d = datetime.fromisoformat(src.get("retrieved_at", ""))
    except ValueError:
        return ""
    return d.strftime(fmt).replace(" 0", " ")


def short_title(title: str) -> str:
    words = title.split()
    return " ".join(words[:4]) + ("…" if len(words) > 4 else "")


def in_text_label(src: dict, style: str, number: int) -> str:
    if style == "IEEE":
        return f"[{number}]"
    authors = src.get("authors") or []
    if authors:
        if len(authors) == 1:
            who = surname(authors[0])
        elif len(authors) == 2 and style == "APA":
            who = f"{surname(authors[0])} & {surname(authors[1])}"
        elif len(authors) == 2:
            who = f"{surname(authors[0])} and {surname(authors[1])}"
        else:
            who = f"{surname(authors[0])} et al."
    else:
        who = src.get("publisher") or f"\u201c{short_title(src.get('title', 'Untitled'))}\u201d"
    if style == "APA":
        return f"({who}, {year_of(src.get('publication_date', '')) or 'n.d.'})"
    return f"({who})"


def reference_entry(src: dict, style: str, number: int) -> str:
    title = src.get("title") or "Untitled"
    authors = src.get("authors") or []
    pub = src.get("publisher") or ""
    date = src.get("publication_date") or ""
    url = src.get("url", "")
    if style == "IEEE":
        who = _join([_ieee_author(a) for a in authors[:6]], "and") + (" et al." if len(authors) > 6 else "")
        parts = [f"[{number}]"]
        if who:
            parts.append(who + ",")
        parts.append(f"\u201c{title},\u201d")
        if pub:
            parts.append(pub + ",")
        if date:
            parts.append(date + ".")
        acc = _accessed(src, "%b. %d, %Y")
        parts.append(f"[Online]. Available: {url}" + (f" (accessed {acc})." if acc else ""))
        return " ".join(parts)
    if style == "APA":
        if authors:
            names = [_apa_author(a) for a in authors[:20]]
            who = _join(names, "&")
        else:
            who = pub or title
        yr = year_of(date) or "n.d."
        if not authors:  # title moves to author position
            return f"{who}. ({yr}). {'' if who == title else title + '. '}{url}".replace(". . ", ". ")
        return f"{who} ({yr}). {title}. {pub + '. ' if pub else ''}{url}"
    # MLA 9
    if authors:
        given, last = _split_name(authors[0])
        first = f"{last}, {' '.join(given)}".strip().rstrip(",")
        who = first if len(authors) == 1 else (
            f"{first}, and {authors[1]}" if len(authors) == 2 else f"{first}, et al")
        lead = who.rstrip(".") + ". "
    else:
        lead = ""
    acc = _accessed(src, "%d %b. %Y")
    return (f"{lead}\u201c{title}.\u201d {pub + ', ' if pub else ''}{date + ', ' if date else ''}"
            f"{url}." + (f" Accessed {acc}." if acc else ""))


def apply_citations(sections: list[dict], sources: dict[str, dict], allowed: set[str],
                    style: str) -> dict:
    """Return {citation_map, labels, references, removed}. Sections are cleaned in place."""
    removed: list[str] = []
    order: list[str] = []
    for sec in sections:
        def keep(m: re.Match) -> str:
            sid = m.group(1)
            if sid in allowed and sid in sources:
                if sid not in order:
                    order.append(sid)
                return m.group(0)
            removed.append(sid)
            return ""
        sec["content"] = MARKER_RE.sub(keep, sec["content"])
        sec["content"] = re.sub(r"[ \t]+([.,;:])", r"\1", sec["content"])
        sec["content"] = re.sub(r"[ \t]{2,}", " ", sec["content"])
    citation_map = {sid: i + 1 for i, sid in enumerate(order)}
    labels = {sid: in_text_label(sources[sid], style, n) for sid, n in citation_map.items()}
    refs = [{"source_id": sid, "number": n, "text": reference_entry(sources[sid], style, n)}
            for sid, n in citation_map.items()]
    if style in ("APA", "MLA"):
        refs.sort(key=lambda r: r["text"].lower().lstrip("\u201c"))
    return {"citation_map": citation_map, "labels": labels, "references": refs,
            "removed": sorted(set(removed))}


def render_markers(text: str, labels: dict[str, str], style: str) -> str:
    """Replace canonical markers with style labels. Adjacent IEEE markers become [1], [2]."""
    def group(m: re.Match) -> str:
        sids = MARKER_RE.findall(m.group(0))
        lab = [labels[s] for s in sids if s in labels]
        if not lab:
            return ""
        trailing = ""
        if style == "IEEE":
            return ", ".join(lab) + trailing
        if len(lab) == 1:
            return lab[0] + trailing
        return "(" + "; ".join(l.strip("()") for l in lab) + ")" + trailing
    return MARKER_GROUP_RE.sub(group, text)
