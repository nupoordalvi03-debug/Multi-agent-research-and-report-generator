"""Framework-independent request handlers (wired to FastAPI in routes.py)."""
from __future__ import annotations

import dataclasses
import json
import re
import time
from collections import defaultdict, deque

from ..db import Database
from ..models.research import CITATION_STYLES, DEPTHS, REPORT_TYPES, SOURCE_MODES, ResearchRequest
from ..services import report_service as rs
from ..workflow.orchestrator import Orchestrator
from ..workflow.state import AGENT_LABELS, AGENTS, WorkflowState

ID_RE = re.compile(r"^[a-f0-9]{12}$")
CLIENT_REQ_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "", info: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.info = status, message, code, info

    def body(self) -> dict:
        out: dict = {"detail": self.message}
        if self.code:
            out["code"] = self.code
        if self.info:
            out["info"] = self.info
        return out


class RateLimiter:
    def __init__(self, limit: int, window_s: int):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise ApiError(429, "Too many research requests. Please wait a minute.")
        q.append(now)


class Handlers:
    def __init__(self, db: Database, orch: Orchestrator):
        self.db, self.orch = db, orch
        self.limiter = RateLimiter(limit=10, window_s=60)

    # ---------------------------------------------------------------- helpers
    def _session(self, sid: str) -> dict:
        if not ID_RE.match(sid or ""):
            raise ApiError(400, "Invalid research id.")
        s = self.db.get_session(sid)
        if not s:
            raise ApiError(404, "Research session not found.")
        return s

    def _agents(self, sid: str) -> list[dict]:
        runs = {r["agent"]: r for r in self.db.get_agent_runs(sid)}
        return [{**runs[a], "label": AGENT_LABELS[a]} for a in AGENTS if a in runs]

    def _llm_summary(self, s: dict) -> dict:
        calls = self.db.get_llm_calls(s["id"])
        by_agent: dict[str, int] = {}
        for c in calls:
            by_agent[c["agent"]] = by_agent.get(c["agent"], 0) + 1
        return {"provider": s.get("llm_provider"), "model": s.get("llm_model"), "requests": len(calls),
                "successful": sum(1 for c in calls if c["category"] == "ok"),
                "rate_limited": sum(1 for c in calls if c["category"] == "rate_limit"),
                "requests_by_agent": by_agent}

    def _status_block(self, s: dict) -> dict:
        alive = self.orch.is_running(s["id"]) or self.orch.held_elsewhere(s["id"])
        active = s["status"] in WorkflowState.ACTIVE
        return {"id": s["id"], "status": s["status"], "mode": s["mode"], "failed_stage": s["failed_stage"],
                "error": s["error"], "error_info": json.loads(s["error_info"]) if s.get("error_info") else None,
                "llm": self._llm_summary(s), "quality_score": s["quality_score"],
                "revision_count": s["revision_count"], "task_alive": alive,
                "can_retry": (s["status"] == WorkflowState.FAILED and not alive) or (active and not alive),
                "created_at": s["created_at"], "updated_at": s["updated_at"], "completed_at": s["completed_at"]}

    def _counts(self, sid: str) -> dict:
        claims = self.db.get_items("claims", sid)
        return {"sources": len(self.db.get_items("sources", sid)),
                "evidence": len(self.db.get_items("evidence", sid)),
                "claims": len(claims), "claims_verified": sum(1 for c in claims if c["include_in_report"]),
                "report_versions": len(self.db.get_reports(sid))}

    # ---------------------------------------------------------------- endpoints
    def settings(self) -> dict:
        cached = self.orch.preflight._cache.get((self.orch.live_llm().provider, self.orch.live_llm().model)) \
            if self.orch.live_llm() else None
        return {**self.orch.settings.public_view(),
                "llm_last_check": cached[1] if cached else None,
                "options": {"source_modes": SOURCE_MODES, "report_types": REPORT_TYPES,
                            "citation_styles": CITATION_STYLES, "depths": DEPTHS}}

    async def diagnostics(self, target: str = "all") -> dict:
        """Real connectivity checks. LLM and Search are separate services and are checked independently."""
        from ..services.diagnostics import check_search
        if target not in ("all", "llm", "search"):
            raise ApiError(400, "target must be all, llm or search")
        self.limiter.check("diagnostics")
        s = self.orch.settings
        out: dict = {"llm": None, "search": None}
        if target in ("all", "llm"):
            self.orch.preflight.invalidate()  # an explicit check always asks the provider again
            out["llm"] = await self.orch.check_live_ready() if s.llm_configured else {
                "ok": False, "category": "not_configured", "title": "LLM NOT CONFIGURED",
                "message": s.llm_status()["problem"], "checked": "config"}
            if out["llm"].get("code") == "search_not_configured":  # LLM itself is fine; report its own health
                out["llm"] = await self.orch.preflight.check(dataclasses.replace(s, tinyfish_api_key="x" * 24),
                                                             self.orch.live_llm())
        if target in ("all", "search"):
            out["search"] = await check_search(self.orch.settings)
        return out

    async def search_test(self, body: dict) -> dict:
        """Run ONE TinyFish search without any LLM, to verify web search independently."""
        q = str(body.get("query", "")).strip()
        if not 3 <= len(q) <= 200:
            raise ApiError(422, "Query must be 3-200 characters.")
        search = self.orch.search_client()
        if search is None:
            raise ApiError(409, self.orch.settings.search_status()["problem"], code="search_not_configured")
        self.limiter.check("search-test")
        from ..services.errors import ServiceError
        try:
            hits = await search.search(q, domain_type="web")
        except ServiceError as e:
            raise ApiError(502, f"TinyFish search failed: {e}", code="search_failed")
        return {"provider": "TinyFish", "query": q, "results": [
            {"title": h.title, "url": h.url, "snippet": h.snippet[:200], "site": h.site_name} for h in hits[:8]]}

    async def create(self, body: dict, client: str) -> dict:
        req = ResearchRequest(
            query=str(body.get("query", "")).strip(), hypothesis=str(body.get("hypothesis", "") or "").strip(),
            source_mode=body.get("source_mode") or "Hybrid",
            report_type=body.get("report_type") or "Academic Research Report",
            citation_style=body.get("citation_style") or "IEEE", depth=body.get("depth") or "Standard")
        errors = req.validate()
        if errors:
            raise ApiError(422, " ".join(errors))
        crid = body.get("client_request_id") or None
        if crid is not None and not CLIENT_REQ_RE.match(str(crid)):
            raise ApiError(422, "client_request_id must be 8-64 letters, digits, '-' or '_'.")
        requested = body.get("mode") or "live"
        if requested not in ("live", "demo"):
            raise ApiError(422, "mode must be 'live' or 'demo'.")
        mode = "demo" if (self.orch.settings.force_demo or requested == "demo") else "live"
        if crid and self.db.query_one("SELECT id FROM sessions WHERE client_request_id=?", (crid,)):
            sid, created = self.orch.create_session(req.to_dict(), crid, mode)  # existing; starts nothing
            return {"id": sid, "created": created, **self._status_block(self.db.get_session(sid))}
        if mode == "live":
            await self._require_live_ready()
        self.limiter.check(client)
        sid, created = self.orch.create_session(req.to_dict(), crid, mode)
        return {"id": sid, "created": created, **self._status_block(self.db.get_session(sid))}

    async def _require_live_ready(self) -> None:
        """Gate: the Research Planner is never started unless the LLM is configured AND reachable."""
        pf = await self.orch.check_live_ready()
        if pf["ok"]:
            return
        status = 409 if pf["category"] in ("not_configured", "auth", "quota_exhausted", "billing",
                                           "model_unavailable", "bad_request") else 503
        raise ApiError(status, pf["message"] if pf["category"] == "not_configured" else f"{pf['title']} ({pf['message']})",
                       code=pf.get("code") or "llm_unavailable",
                       info={**{k: pf.get(k) for k in ("category", "title", "hint", "problem_code", "retry_after_s", "checked")},
                             "reason": pf["message"]})

    def get(self, sid: str) -> dict:
        s = self._session(sid)
        return {"session": {k: s[k] for k in ("id", "query", "hypothesis", "source_mode", "report_type",
                                              "citation_style", "depth", "mode")},
                **self._status_block(s), "plan": self.db.get_stage_output(sid, "planning"),
                "research_stats": (self.db.get_stage_output(sid, "research") or {}).get("stats"),
                "critique": self.db.get_stage_output(sid, "critique"),
                "agents": self._agents(sid), "counts": self._counts(sid)}

    def llm_calls(self, sid: str) -> list[dict]:
        self._session(sid)
        return self.db.get_llm_calls(sid)

    def status(self, sid: str, after: int = 0) -> dict:
        """READ-ONLY. Polling this endpoint never starts, restarts or advances any agent."""
        s = self._session(sid)
        return {**self._status_block(s), "agents": self._agents(sid), "counts": self._counts(sid),
                "events": self.db.get_events(sid, after_id=max(0, after))}

    def sources(self, sid: str) -> dict:
        self._session(sid)
        research = self.db.get_stage_output(sid, "research") or {}
        reports = self.db.get_reports(sid)
        cmap = reports[-1]["citation_map"] if reports else {}
        srcs = self.db.get_items("sources", sid)
        for x in srcs:
            x["cited_as"] = cmap.get(x["source_id"])
        return {"sources": srcs, "stats": research.get("stats"),
                "unavailable_subtopics": research.get("unavailable_subtopics", [])}

    def evidence(self, sid: str) -> dict:
        self._session(sid)
        ver = self.db.get_stage_output(sid, "verification") or {}
        return {"evidence": self.db.get_items("evidence", sid), "claims": self.db.get_items("claims", sid),
                "contradictions": ver.get("contradictions", []), "gaps": ver.get("gaps", []),
                "stats": ver.get("stats")}

    def report(self, sid: str) -> dict:
        s = self._session(sid)
        bundle = rs.final_bundle(self.db, sid)
        if not bundle:
            raise ApiError(404, "No report has been written yet.")
        return {"report": bundle["report"], "is_final": bundle["is_final"] and s["status"] == WorkflowState.COMPLETED,
                "citation_style": s["citation_style"], "mode": s["mode"],
                "critique": bundle["critique"], "critiques": self.db.get_critiques(sid),
                "versions": [r["version"] for r in self.db.get_reports(sid)]}

    def history(self) -> list[dict]:
        return [{"id": s["id"], "query": s["query"], "status": s["status"], "mode": s["mode"],
                 "report_type": s["report_type"], "depth": s["depth"], "quality_score": s["quality_score"],
                 "created_at": s["created_at"], "completed_at": s["completed_at"]}
                for s in self.db.list_sessions(200)]

    async def retry(self, sid: str) -> dict:
        sess = self._session(sid)
        if sess["mode"] == "live" and not self.orch.is_running(sid):
            await self._require_live_ready()  # don't resume into a broken/missing LLM
        try:
            stage = self.orch.retry(sid)
        except RuntimeError as e:
            raise ApiError(409, str(e))
        return {"retrying_from": stage, **self._status_block(self.db.get_session(sid))}

    def export(self, sid: str, fmt: str) -> tuple[bytes, str, str]:
        s = self._session(sid)
        if s["status"] != WorkflowState.COMPLETED:
            raise ApiError(409, "The report can be exported once the workflow has completed.")
        bundle = rs.final_bundle(self.db, sid)
        if not bundle:
            raise ApiError(404, "No report available.")
        try:
            if fmt == "pdf":
                return rs.to_pdf(bundle), "application/pdf", rs.safe_filename(bundle["report"]["title"], "pdf")
            if fmt == "docx":
                return (rs.to_docx(bundle),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        rs.safe_filename(bundle["report"]["title"], "docx"))
            if fmt == "markdown":
                return (rs.to_markdown(bundle).encode("utf-8"), "text/markdown; charset=utf-8",
                        rs.safe_filename(bundle["report"]["title"], "md"))
        except Exception as e:
            raise ApiError(500, f"Export failed: {type(e).__name__}")
        raise ApiError(400, "Unknown export format.")
