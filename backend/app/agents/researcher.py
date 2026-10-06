"""Agent 2 — Researcher: real search + fetch, de-duplication, credibility ranking and
evidence extraction. Metadata is copied only from API responses, never invented by the LLM.
Quotes are kept only if they appear verbatim in the fetched text."""
from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass, field

from ..db import now_iso
from ..models.evidence import Evidence
from ..models.research import DEPTH_LIMITS, ResearchPlan
from ..models.source import Source
from ..services.credibility import classify
from ..services.errors import ServiceError
from ..services.search_service import SearchHit
from ..services.text_util import keywords, overlap_score, quote_in_text, sentences, truncate
from ..services.url_safety import domain_of, is_safe_public_url, normalize_url
from .base import AgentContext, AgentFailure, AgentResult, llm_json

ACADEMIC_DOMAINS = ["ncbi.nlm.nih.gov", "frontiersin.org", "sciencedirect.com", "springer.com",
                    "apa.org", "arxiv.org", "plos.org", "nature.com", "tandfonline.com", "wiley.com"]
STANDARDS_DOMAINS = ["iso.org", "nist.gov", "ietf.org", "w3.org", "ieee.org", "who.int", "oecd.org",
                     "europa.eu"]
LOW_QUALITY = ["pinterest.com", "quora.com", "facebook.com", "tiktok.com", "instagram.com"]
TIER_WEIGHT = {1: 1.0, 2: 0.8, 3: 0.55, 4: 0.2}

EXTRACT_SYSTEM = """You are the Researcher agent extracting evidence from SEVERAL source documents at once.
Only use information present in each document's text. Never add outside knowledge.
Return JSON with one entry per input document, in any order:
{"documents": [
  {"doc": 1, "relevant": true|false, "relevance": 0.0-1.0,
   "evidence": [
     {"subtopic_id": "T1", "summary": "one-sentence faithful paraphrase of a finding/statement in THAT document",
      "quote": "a short passage copied EXACTLY, character for character, from THAT document's text"}
   ]}
]}
Rules: at most 4 evidence items per document; prefer empirical findings, statistics, conclusions; each quote
must be verbatim (20-300 characters) from the same document. Irrelevant documents: relevant=false, evidence=[]."""

EXTRACT_BATCH_DOCS = 4       # documents per LLM request (was 1 request per document)
EXTRACT_DOC_CHARS = 5000     # per-document text budget inside a batched request


def strategies(mode: str, query: str) -> list[dict]:
    """How each subtopic query is searched, based on the Source Target Mode."""
    if mode == "Academic":
        return [{"q": query, "domain_type": "research_paper"},
                {"q": query + " study", "domain_type": "web", "include": ACADEMIC_DOMAINS}]
    if mode == "Web":
        return [{"q": query, "domain_type": "web"}]
    if mode == "Industry Analysis":
        return [{"q": query + " industry report", "domain_type": "web"},
                {"q": query, "domain_type": "news"}]
    if mode == "Official Standards & Whitepapers":
        return [{"q": query, "domain_type": "web", "include": STANDARDS_DOMAINS},
                {"q": query + " whitepaper", "domain_type": "web"}]
    return [{"q": query, "domain_type": "research_paper"}, {"q": query, "domain_type": "web"}]  # Hybrid


@dataclass
class Candidate:
    hit: SearchHit
    norm_url: str
    subtopic_ids: list[str]
    tier: int
    source_type: str
    score: float
    queries: list[str] = field(default_factory=list)


def _publisher(hit: SearchHit, fetched_title: str = "") -> str:
    return hit.venue or hit.publisher or hit.site_name or domain_of(hit.url)


def _pub_date(hit: SearchHit, page_date: str) -> str:
    if hit.date:
        return hit.date[:10] if len(hit.date) >= 10 and hit.date[4] == "-" else hit.date
    if hit.year:
        return str(hit.year)
    return (page_date or "")[:10]


def heuristic_extract(text: str, subtopics: list[dict], fallback_sid: str) -> list[dict]:
    """Non-LLM extractor (used in demo mode): verbatim sentences with highest term overlap."""
    out = []
    sents = [s for s in sentences(text) if not s.startswith("[DEMO MODE]")]
    for st in subtopics:
        terms = keywords(st["title"] + " " + st.get("query_terms", ""), 10)
        scored = sorted(((overlap_score(s, terms), s) for s in sents), reverse=True)
        for sc, s in scored[:1]:
            if sc > 0:
                out.append({"subtopic_id": st["id"], "summary": s, "quote": s})
    if not out and sents:
        out.append({"subtopic_id": fallback_sid, "summary": sents[0], "quote": sents[0]})
    seen, uniq = set(), []
    for e in out:
        if e["quote"] not in seen:
            seen.add(e["quote"])
            uniq.append(e)
    return uniq[:4]


async def run(ctx: AgentContext, plan_dict: dict) -> AgentResult:
    req = ctx.request
    plan = ResearchPlan.from_dict(plan_dict)
    limits = DEPTH_LIMITS[req["depth"]]
    target, max_sources = limits["target_sources"], limits["max_sources"]
    q_terms = keywords(req["query"], 10)
    search = ctx.services.search
    fetch = ctx.services.fetch

    # ---------- 1. Search every subtopic ------------------------------------------------
    jobs = []
    seen_queries: set[tuple] = set()
    for st in plan.subtopics:
        for q in st.queries:
            for strat in strategies(req["source_mode"], q):
                key = (strat["q"].lower(), strat["domain_type"], tuple(strat.get("include", [])))
                if key not in seen_queries:
                    seen_queries.add(key)
                    jobs.append((st, strat))
    ctx.report(f"Researcher is running {len(jobs)} searches across {len(plan.subtopics)} subtopics…", 5)

    candidates: dict[str, Candidate] = {}
    sem = asyncio.Semaphore(3)
    errors: list[str] = []
    done = 0

    async def do_search(st, strat):
        nonlocal done
        async with sem:
            try:
                hits = await search.search(
                    strat["q"], domain_type=strat["domain_type"], include_domains=strat.get("include"),
                    exclude_domains=LOW_QUALITY,
                    purpose=f"Evidence for a research report on: {req['query'][:300]}")
            except ServiceError as e:
                errors.append(str(e))
                hits = []
                if not e.retryable:
                    raise AgentFailure(str(e), retryable=False)
            done += 1
            label = "academic sources" if strat["domain_type"] == "research_paper" else (
                "news sources" if strat["domain_type"] == "news" else "the web")
            ctx.report(f"Researcher searched {label} for “{strat['q'][:60]}” — {len(hits)} results.",
                       5 + int(35 * done / max(1, len(jobs))))
            terms = keywords(st.title + " " + " ".join(st.queries), 10) + q_terms
            for h in hits:
                if not is_safe_public_url(h.url) or not h.title:
                    continue
                nu = normalize_url(h.url)
                tier, stype = classify(h.url, from_research_index=h.domain_type == "research_paper")
                rel = overlap_score(h.title + " " + h.snippet, terms)
                pos_bonus = max(0.0, 0.1 - 0.01 * (h.position or 10))
                score = 0.5 * TIER_WEIGHT[tier] + 0.4 * rel + pos_bonus
                c = candidates.get(nu)
                if c:
                    if st.id not in c.subtopic_ids:
                        c.subtopic_ids.append(st.id)
                    c.score = max(c.score, score) + 0.05  # found by several queries
                    if not c.hit.authors and h.authors:
                        c.hit = h
                else:
                    candidates[nu] = Candidate(h, nu, [st.id], tier, stype, score, [strat["q"]])

    await asyncio.gather(*(do_search(st, s) for st, s in jobs))
    # de-duplicate by near-identical title as well as URL
    by_title: dict[str, Candidate] = {}
    for c in sorted(candidates.values(), key=lambda c: (c.tier, -c.score)):
        tnorm = re.sub(r"[^a-z0-9]+", " ", c.hit.title.lower()).strip()
        tkey = tnorm if len(tnorm) >= 30 else ""  # only long titles are reliable duplicate signals
        if tkey and tkey in by_title:
            other = by_title[tkey]
            for sid in c.subtopic_ids:
                if sid not in other.subtopic_ids:
                    other.subtopic_ids.append(sid)
            continue
        by_title[tkey or c.norm_url] = c
    pool = list(by_title.values())
    dupes = len(candidates) - len(pool)
    ctx.metrics(searches=len(jobs), search_errors=len(errors), candidates=len(pool),
                duplicates_removed=dupes)
    if not pool:
        if errors and len(errors) == len(jobs):
            raise AgentFailure(f"All searches failed. Last error: {errors[-1]}")
        raise AgentFailure("Search returned no usable results for any subtopic. "
                           "Try rephrasing the query or switching Source Target Mode.", retryable=False)
    ctx.report(f"Researcher found {len(pool)} unique candidate sources ({dupes} duplicates removed).", 42)

    # ---------- 2. Select candidates: best per subtopic, prefer higher tiers --------------
    def pick(pool_left: list[Candidate], n: int) -> list[Candidate]:
        chosen: list[Candidate] = []
        per_sub = max(1, math.ceil(n / len(plan.subtopics)))
        for st in plan.subtopics:
            mine = sorted([c for c in pool_left if st.id in c.subtopic_ids and c not in chosen],
                          key=lambda c: -c.score)
            better_exists = any(c.tier <= 3 for c in mine)
            for c in mine:
                if len([x for x in chosen if st.id in x.subtopic_ids]) >= per_sub:
                    break
                if c.tier == 4 and better_exists:
                    continue  # don't use low-quality sources when better ones exist
                chosen.append(c)
        rest = sorted([c for c in pool_left if c not in chosen and c.tier <= 3], key=lambda c: -c.score)
        chosen += rest[:max(0, n - len(chosen))]
        return chosen[:n]

    # ---------- 3. Fetch + extract, at most two rounds ------------------------------------
    sources: list[Source] = []
    evidence: list[Evidence] = []
    remaining = pool[:]
    fetched_ok = fetch_failed = 0
    llm_requests = 0
    sub_dicts = [{"id": s.id, "title": s.title, "query_terms": " ".join(s.queries)} for s in plan.subtopics]

    for round_no in (1, 2):
        need = max_sources - len(sources)
        batch = pick(remaining, need + (2 if round_no == 1 else 0))
        if not batch:
            break
        remaining = [c for c in remaining if c not in batch]
        ctx.report(f"Researcher is fetching {len(batch)} pages (round {round_no})…", 45 if round_no == 1 else 75)
        try:
            pages = await fetch.fetch([c.hit.url for c in batch],
                                      purpose=f"Extract evidence about: {req['query'][:300]}")
        except ServiceError as e:
            ctx.report(f"Fetch API error: {e}. Falling back to search snippets.", level="warning")
            pages = {}

        docs = []  # (candidate, page, fetched_ok, text)
        for c in batch:
            page = pages.get(c.hit.url)
            ok = bool(page and page.ok and page.text.strip())
            fetched_ok += ok
            fetch_failed += not ok
            text = page.text if ok else c.hit.snippet
            if len(text.strip()) >= 40:
                docs.append((c, page, ok, text))

        extracted: list[tuple] = []  # (candidate, page, ok, text, relevance, items)
        if ctx.demo:
            for c, page, ok, text in docs:
                await ctx.demo_pause(1)
                subs = [s for s in sub_dicts if s["id"] in c.subtopic_ids] or sub_dicts
                items = heuristic_extract(text, subs, c.subtopic_ids[0])
                if items:
                    extracted.append((c, page, ok, text, 0.6, items))
        else:
            groups = [docs[i:i + EXTRACT_BATCH_DOCS] for i in range(0, len(docs), EXTRACT_BATCH_DOCS)]
            for gi, group in enumerate(groups):
                ctx.report(f"Researcher is extracting evidence from {len(group)} sources "
                           f"(LLM request {gi + 1} of {len(groups)}, round {round_no})…",
                           50 + int(40 * (gi / max(1, len(groups)))))
                user = json.dumps({
                    "research_question": req["query"],
                    "subtopics": [{"id": s["id"], "title": s["title"]} for s in sub_dicts],
                    "documents": [{"doc": i + 1, "title": c.hit.title, "url": c.hit.url,
                                   "likely_subtopics": c.subtopic_ids, "text": truncate(text, EXTRACT_DOC_CHARS)}
                                  for i, (c, _p, _ok, text) in enumerate(group)]})
                try:
                    raw = await llm_json(ctx, EXTRACT_SYSTEM, user, temperature=0.0,
                                         purpose=f"extract r{round_no} batch {gi + 1}/{len(groups)}")
                    llm_requests += 1
                except AgentFailure as e:
                    if e.info and e.info.get("category") == "malformed":
                        ctx.report(f"Skipped one batch of {len(group)} sources: model output was not valid JSON.",
                                   level="warning")
                        continue
                    raise  # rate limit / quota / auth: stop now instead of firing more requests
                entries = raw.get("documents") if isinstance(raw, dict) else None
                for ent in entries if isinstance(entries, list) else []:
                    if not isinstance(ent, dict) or not isinstance(ent.get("doc"), int):
                        continue
                    idx = ent["doc"] - 1
                    if not (0 <= idx < len(group)) or not ent.get("relevant"):
                        continue
                    items = ent.get("evidence") if isinstance(ent.get("evidence"), list) else []
                    rel = ent.get("relevance")
                    rel = float(rel) if isinstance(rel, (int, float)) else 0.5
                    if items:
                        c, page, ok, text = group[idx]
                        extracted.append((c, page, ok, text, rel, items))

        for c, page, ok, text, rel, items in extracted:
            if len(sources) >= max_sources:
                break
            sid = f"S{len(sources) + 1:02d}"
            authors = c.hit.authors[:12] or ([page.author] if page and page.author and len(page.author) < 120 else [])
            src = Source(
                source_id=sid, title=(c.hit.title or (page.title if page else "") or c.hit.url)[:300],
                url=(page.final_url if page and ok else c.hit.url), authors=authors,
                publisher=_publisher(c.hit), publication_date=_pub_date(c.hit, page.published_date if page else ""),
                source_type=c.source_type, credibility_tier=c.tier, retrieved_at=now_iso(),
                relevance_score=round(0.5 * c.score + 0.5 * max(0.0, min(1.0, rel)), 3),
                subtopic_ids=c.subtopic_ids, snippet=c.hit.snippet[:500],
                fetch_status="fetched" if ok else "snippet_only",
                fetch_error="" if ok else (page.error if page else "not_fetched"),
                content_chars=len(text), is_demo=ctx.demo)
            kept = 0
            for it in items[:4]:
                if not isinstance(it, dict) or not str(it.get("summary", "")).strip():
                    continue
                st_id = it.get("subtopic_id") if it.get("subtopic_id") in [s.id for s in plan.subtopics] else c.subtopic_ids[0]
                quote = str(it.get("quote", "")).strip()
                verified = quote_in_text(quote, text)
                evidence.append(Evidence(
                    evidence_id=f"E{len(evidence) + 1:03d}", source_id=sid, subtopic_id=st_id,
                    summary=str(it["summary"]).strip()[:600],
                    quote=quote[:400] if verified else "", quote_verified=verified,
                    from_snippet_only=not ok))
                kept += 1
            if kept:
                sources.append(src)
                ctx.report(f"Researcher extracted {kept} evidence item(s) from {domain_of(src.url)} "
                           f"(tier {src.credibility_tier}).", min(90, 50 + 3 * len(sources)))
        ctx.metrics(sources=len(sources), evidence=len(evidence), fetched=fetched_ok, fetch_failed=fetch_failed,
                    llm_requests=llm_requests)
        covered = {e.subtopic_id for e in evidence}
        if len(sources) >= target and len(covered) == len(plan.subtopics):
            break  # sufficient evidence collected; stop researching

    if not sources:
        raise AgentFailure("No relevant evidence could be extracted from any source.")
    covered = {e.subtopic_id for e in evidence}
    unavailable = [s.id for s in plan.subtopics if s.id not in covered]
    for sid in unavailable:
        st = next(s for s in plan.subtopics if s.id == sid)
        ctx.report(f"Evidence unavailable for subtopic {sid} “{st.title}”.", level="warning")
    tiers = {t: sum(1 for s in sources if s.credibility_tier == t) for t in (1, 2, 3, 4)}
    ctx.metrics(sources=len(sources), evidence=len(evidence), tiers=tiers, unavailable_subtopics=unavailable)
    ctx.report(f"Researcher found {len(sources)} relevant sources and {len(evidence)} evidence items.", 98, "success")
    return AgentResult(True, "RESEARCH_COMPLETED", {
        "sources": [s.to_dict() for s in sources],
        "evidence": [e.to_dict() for e in evidence],
        "unavailable_subtopics": unavailable,
        "stats": {"searches": len(jobs), "search_errors": len(errors), "candidates": len(pool),
                  "duplicates_removed": dupes, "fetched": fetched_ok, "fetch_failed": fetch_failed,
                  "tiers": tiers},
    }, metrics={"sources": len(sources), "evidence": len(evidence)})
