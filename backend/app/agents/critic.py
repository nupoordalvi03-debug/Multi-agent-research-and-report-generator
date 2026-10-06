"""Agent 5 — Report Critic: scores the report 0-100 using deterministic checks plus (in live mode)
an LLM review against the verified claims. Serious problems trigger a bounded revision loop."""
from __future__ import annotations

import json
import re

from ..services.citation_service import MARKER_RE
from ..services.text_util import sentences
from .base import AgentContext, AgentFailure, AgentResult, llm_json

PASS_SCORE = 70
SYSTEM = """You are the Report Critic agent. Evaluate the draft report strictly against the verified claims.
Check: completeness, logical structure, accuracy versus claims, unsupported statements (anything not
backed by a listed claim), citation correctness (each [Sxx] must match the claim's source_ids),
source quality, hidden contradictions, repetition, grammar, readability, and whether the report
actually answers the research question.
Return JSON:
{"score": 0-100, "answers_question": true|false, "summary": "2-3 sentence assessment",
 "dimension_scores": {"completeness":0-10,"structure":0-10,"accuracy":0-10,"citations":0-10,
                      "source_quality":0-10,"readability":0-10},
 "issues": [{"severity":"critical|major|minor","category":"...","section":"...",
             "description":"...","suggestion":"..."}]}
Use 'critical' only for fabricated/unsupported facts, wrong citations, or not answering the question."""

CITED_SECTIONS = ("finding", "discussion", "benefit", "thematic", "comparative", "analysis", "key findings")


def deterministic_checks(report: dict, depth: str) -> list[dict]:
    issues: list[dict] = []
    words = 0
    all_sents: dict[str, str] = {}
    for s in report["sections"]:
        content = s["content"]
        words += len(content.split())
        if len(content) < 40 or content.startswith("This section could not be generated"):
            issues.append({"severity": "major", "category": "completeness", "section": s["heading"],
                           "description": "Section is empty or too short.",
                           "suggestion": "Write this section from the verified claims."})
        if any(k in s["heading"].lower() for k in CITED_SECTIONS) and not MARKER_RE.search(content):
            issues.append({"severity": "major", "category": "citations", "section": s["heading"],
                           "description": "Evidence-bearing section contains no citations.",
                           "suggestion": "Cite the source IDs that support each finding."})
        for sent in sentences(MARKER_RE.sub("", content)):
            key = re.sub(r"\W+", " ", sent.lower()).strip()
            if len(key) > 60 and key in all_sents and all_sents[key] != s["heading"]:
                issues.append({"severity": "minor", "category": "repetition", "section": s["heading"],
                               "description": f"Sentence repeated from '{all_sents[key]}'.",
                               "suggestion": "Remove or rephrase the repeated sentence."})
            all_sents.setdefault(key, s["heading"])
    if report.get("removed_citations"):
        issues.append({"severity": "major", "category": "citations", "section": "Report",
                       "description": f"Draft cited unverified source IDs: {', '.join(report['removed_citations'])}.",
                       "suggestion": "Cite only sources attached to verified claims."})
    if not report.get("references"):
        issues.append({"severity": "critical", "category": "citations", "section": "References",
                       "description": "Report has no references.", "suggestion": "Cite verified sources."})
    min_words = {"Quick": 300, "Standard": 500, "Comprehensive": 900}[depth]
    if words < min_words:
        issues.append({"severity": "major", "category": "completeness", "section": "Report",
                       "description": f"Report is short ({words} words; expected ≥{min_words}).",
                       "suggestion": "Expand findings and discussion using the verified claims."})
    return issues


async def run(ctx: AgentContext, report: dict, verification: dict, cycle: int) -> AgentResult:
    req = ctx.request
    ctx.report(f"Report Critic is evaluating draft v{report['version']} (structure, citations, accuracy)…", 15)
    det = deterministic_checks(report, req["depth"])
    if ctx.demo:
        await ctx.demo_pause(3)
        llm_part = {"score": 85, "answers_question": True, "summary": "DEMO MODE — rule-based critique only.",
                    "dimension_scores": {}, "issues": []}
    else:
        claims = [{k: c[k] for k in ("claim_id", "text", "status", "confidence", "source_ids")}
                  for c in verification["claims"] if c["include_in_report"]]
        payload = {"research_question": req["query"], "hypothesis": req.get("hypothesis"),
                   "report_type": req["report_type"], "verified_claims": claims,
                   "contradictions": verification["contradictions"],
                   "report": {"title": report["title"], "sections": report["sections"]}}
        ctx.report("Report Critic is checking claims against citations…", 45)
        llm_part = await llm_json(ctx, SYSTEM, json.dumps(payload), temperature=0.0,
                                  purpose=f"critique v{report['version']}")
        if not isinstance(llm_part, dict) or not isinstance(llm_part.get("score"), (int, float)):
            raise AgentFailure("Critic returned malformed JSON (no numeric score).")
    llm_issues = [i for i in (llm_part.get("issues") or []) if isinstance(i, dict) and i.get("description")]
    for i in llm_issues:
        i["severity"] = i.get("severity") if i.get("severity") in ("critical", "major", "minor") else "minor"
        i["source"] = "llm"
    for i in det:
        i["source"] = "rule"
    penalty = sum({"critical": 20, "major": 8, "minor": 2}[i["severity"]] for i in det)
    score = max(0, min(100, int(round(float(llm_part["score"]) - penalty))))
    issues = det + llm_issues
    critical = [i for i in issues if i["severity"] == "critical"]
    passed = score >= PASS_SCORE and not critical and llm_part.get("answers_question", True) is not False
    result = {"version": report["version"], "cycle": cycle, "score": score, "passed": passed,
              "answers_question": bool(llm_part.get("answers_question", True)),
              "summary": str(llm_part.get("summary", ""))[:800],
              "dimension_scores": llm_part.get("dimension_scores") or {}, "issues": issues,
              "rule_penalty": penalty}
    ctx.metrics(quality_score=score, issues=len(issues))
    verdict = "passed" if passed else "needs revision"
    ctx.report(f"Report Critic scored v{report['version']} at {score}/100 — {verdict} "
               f"({len(issues)} issue(s)).", 95, "success" if passed else "warning")
    return AgentResult(True, "CRITIQUE_COMPLETED", result, metrics={"quality_score": score})
