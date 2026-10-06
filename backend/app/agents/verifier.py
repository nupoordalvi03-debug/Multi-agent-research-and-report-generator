"""Agent 3 — Verifier: builds CLAIM → EVIDENCE → SOURCE links, checks support and source quality,
surfaces contradictions and removes unsupported claims. It does not re-run research."""
from __future__ import annotations

import json
import re

from ..models.evidence import Claim
from ..models.research import ResearchPlan
from .base import AgentContext, AgentFailure, AgentResult, llm_json

SYSTEM = """You are the Verifier agent. You receive evidence items extracted from real sources, grouped by
subtopic. For EACH subtopic, formulate the factual claims the evidence supports and judge support strictly.
Return JSON:
{"claims": [
   {"subtopic_id": "T1", "text": "a precise, hedged factual claim", "evidence_ids": ["E001", ...],
    "status": "supported | partially_supported | contested | unsupported",
    "confidence": "high | medium | low",
    "notes": "why; mention disagreement, small samples, indirect evidence"}],
 "contradictions": [{"subtopic_id": "T1", "description": "...", "evidence_ids": ["E..", "E.."]}]
}
Rules:
- Only cite evidence_ids from the input, and only evidence from the same subtopic.
- A claim must be fully entailed by its cited evidence.
- 'supported' needs clear, direct evidence; one weak/indirect source => partially_supported.
- If reliable sources disagree, create a 'contested' claim describing both sides and add a contradiction.
- Do not generalise beyond the evidence. 2-5 claims per subtopic that has evidence."""

MAX_EVIDENCE_PER_REQUEST = 45  # normally the whole verification is ONE LLM request


NEG = re.compile(r"\b(no significant|did not|not significantly|impair|reduc|worse|negative|decline|interfer|detriment)", re.I)
POS = re.compile(r"\b(improv|enhanc|increas|better|benefit|positive|associated with better)", re.I)


def _polarity(t: str) -> int:
    n, p = bool(NEG.search(t)), bool(POS.search(t))
    return -1 if n and not p else 1 if p and not n else (-1 if n else 0)


def _demo_claims(items: list[dict], st_title: str) -> dict:
    """DEMO MODE: group simulated evidence by polarity; each group's best sentence becomes a claim."""
    groups: dict[int, list[dict]] = {1: [], -1: [], 0: []}
    for e in items:
        groups[_polarity(e["summary"])].append(e)
    claims, contradictions = [], []
    for pol, evs in groups.items():
        if evs:
            text = evs[0]["summary"].rstrip(".")
            claims.append({"text": f"{text} (simulated).", "evidence_ids": [e["evidence_id"] for e in evs[:4]],
                           "status": "supported" if len({e["source_id"] for e in evs}) > 1 else "partially_supported",
                           "confidence": "medium", "notes": "DEMO MODE — rule-based verification."})
    if groups[1] and groups[-1]:
        contradictions.append({"description": f"Simulated sources disagree about {st_title.lower()}: some report "
                               f"benefits while others report null or negative effects.",
                               "evidence_ids": [groups[1][0]["evidence_id"], groups[-1][0]["evidence_id"]]})
        for c in claims[:2]:
            c["status"] = "contested"
    return {"claims": claims, "contradictions": contradictions}


def apply_rules(raw_claims: list, ev_by_id: dict, src_by_id: dict, subtopic_id: str, start: int) -> list[Claim]:
    """Deterministic checks layered over the LLM judgement."""
    out: list[Claim] = []
    order = {"high": 2, "medium": 1, "low": 0}
    for rc in raw_claims:
        if not isinstance(rc, dict) or not str(rc.get("text", "")).strip():
            continue
        eids = [e for e in (rc.get("evidence_ids") or []) if e in ev_by_id]
        status = rc.get("status") if rc.get("status") in {"supported", "partially_supported", "contested", "unsupported"} else "partially_supported"
        conf = rc.get("confidence") if rc.get("confidence") in order else "low"
        flags: list[str] = []
        if not eids:
            status, conf = "unsupported", "low"
            flags.append("no_valid_evidence")
        sids = sorted({ev_by_id[e]["source_id"] for e in eids})
        tiers = [src_by_id[s]["credibility_tier"] for s in sids if s in src_by_id]
        if len(sids) == 1:
            flags.append("single_source")
            if conf == "high":
                conf = "medium"
        if tiers and min(tiers) >= 4:
            flags.append("low_tier_only")
            conf = "low"
            if status == "supported":
                status = "partially_supported"
        if eids and all(ev_by_id[e]["from_snippet_only"] for e in eids):
            flags.append("snippet_only")
            conf = "low"
        if eids and not any(ev_by_id[e]["quote_verified"] for e in eids):
            flags.append("no_verbatim_quote")
            if order[conf] > 1:
                conf = "medium"
        out.append(Claim(
            claim_id=f"C{start + len(out):02d}", text=str(rc["text"]).strip()[:600],
            subtopic_id=subtopic_id, status=status, confidence=conf, evidence_ids=eids,
            source_ids=sids, notes=str(rc.get("notes", ""))[:600], flags=flags,
            include_in_report=status != "unsupported"))
    return out


def _chunks(subtopics, evidence) -> list[list]:
    """Group subtopics so each LLM request carries at most MAX_EVIDENCE_PER_REQUEST items."""
    groups, cur, n = [], [], 0
    for st in subtopics:
        k = sum(1 for e in evidence if e["subtopic_id"] == st.id)
        if not k:
            continue
        if cur and n + k > MAX_EVIDENCE_PER_REQUEST:
            groups.append(cur)
            cur, n = [], 0
        cur.append(st)
        n += k
    if cur:
        groups.append(cur)
    return groups


async def run(ctx: AgentContext, plan_dict: dict, research: dict) -> AgentResult:
    plan = ResearchPlan.from_dict(plan_dict)
    evidence = research["evidence"]
    sources = research["sources"]
    ev_by_id = {e["evidence_id"]: e for e in evidence}
    src_by_id = {s["source_id"]: s for s in sources}
    claims: list[Claim] = []
    contradictions: list[dict] = []
    gaps: list[dict] = []
    ctx.report(f"Verifier is checking {len(evidence)} evidence items from {len(sources)} sources…", 5)
    for st in plan.subtopics:
        if not any(e["subtopic_id"] == st.id for e in evidence):
            gaps.append({"subtopic_id": st.id, "title": st.title,
                         "note": "Evidence unavailable: no relevant sources were found for this subtopic."})
            ctx.report(f"Verifier: no evidence for “{st.title}” — marked as a gap.", level="warning")

    def add_contradictions(raw_list, default_sid=None):
        for c in raw_list or []:
            if isinstance(c, dict):
                eids = [e for e in (c.get("evidence_ids") or []) if e in ev_by_id]
                if len(eids) >= 2:
                    contradictions.append({"subtopic_id": c.get("subtopic_id") or default_sid or ev_by_id[eids[0]]["subtopic_id"],
                                           "description": str(c.get("description", ""))[:500], "evidence_ids": eids,
                                           "source_ids": sorted({ev_by_id[e]["source_id"] for e in eids})})

    if ctx.demo:
        n = len(plan.subtopics)
        for i, st in enumerate(plan.subtopics):
            items = [e for e in evidence if e["subtopic_id"] == st.id]
            if not items:
                continue
            ctx.report(f"Verifier is checking claims for “{st.title}”…", 5 + int(85 * i / n))
            await ctx.demo_pause(1.5)
            raw = _demo_claims(items, st.title)
            new = apply_rules(raw.get("claims") or [], ev_by_id, src_by_id, st.id, len(claims) + 1)
            claims += new
            add_contradictions(raw.get("contradictions"), st.id)
    else:
        groups = _chunks(plan.subtopics, evidence)
        valid_ids = {s.id for s in plan.subtopics}
        for gi, group in enumerate(groups):
            ids = {s.id for s in group}
            ctx.report(f"Verifier is checking claims for {len(group)} subtopic(s) in one LLM request "
                       f"({gi + 1} of {len(groups)})…", 10 + int(80 * gi / len(groups)))
            payload = {"research_question": plan.research_question,
                       "subtopics": [{"id": s.id, "title": s.title} for s in group],
                       "evidence": [{"evidence_id": e["evidence_id"], "subtopic_id": e["subtopic_id"],
                                     "source_id": e["source_id"],
                                     "source_tier": src_by_id[e["source_id"]]["credibility_tier"],
                                     "source_type": src_by_id[e["source_id"]]["source_type"],
                                     "from_snippet_only": e["from_snippet_only"],
                                     "summary": e["summary"][:300], "quote": (e["quote"] or "")[:250] or None}
                                    for e in evidence if e["subtopic_id"] in ids]}
            raw = await llm_json(ctx, SYSTEM, json.dumps(payload), temperature=0.0,
                                 purpose=f"verify {gi + 1}/{len(groups)}")
            if not isinstance(raw, dict):
                raise AgentFailure("Verifier returned a non-object JSON.")
            by_sub: dict[str, list] = {}
            for rc in raw.get("claims") or []:
                if not isinstance(rc, dict):
                    continue
                sid = rc.get("subtopic_id")
                if sid not in valid_ids:  # infer from the cited evidence
                    cited = [ev_by_id[e]["subtopic_id"] for e in (rc.get("evidence_ids") or []) if e in ev_by_id]
                    sid = max(set(cited), key=cited.count) if cited else group[0].id
                by_sub.setdefault(sid, []).append(rc)
            for st in plan.subtopics:
                if st.id in by_sub:
                    claims += apply_rules(by_sub[st.id], ev_by_id, src_by_id, st.id, len(claims) + 1)
            add_contradictions(raw.get("contradictions"))

    kept = [c for c in claims if c.include_in_report]
    if not kept:
        raise AgentFailure("Insufficient evidence: no claim could be verified against collected sources.")
    removed = len(claims) - len(kept)
    stats = {s: sum(1 for c in claims if c.status == s) for s in
             ("supported", "partially_supported", "contested", "unsupported")}
    ctx.metrics(claims=len(claims), claims_verified=len(kept), claims_removed=removed,
                contradictions=len(contradictions), gaps=len(gaps))
    ctx.report(f"Verifier kept {len(kept)} claims, removed {removed} unsupported, "
               f"found {len(contradictions)} contradiction(s).", 98, "success")
    return AgentResult(True, "VERIFICATION_COMPLETED", {
        "claims": [c.to_dict() for c in claims], "contradictions": contradictions,
        "gaps": gaps, "stats": stats}, metrics={"claims_verified": len(kept)})
