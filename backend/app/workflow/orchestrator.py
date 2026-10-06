"""Orchestrator / Supervisor — the workflow controller (not an AI agent).

Every transition happens here, after an agent returns a structured AgentResult. Each stage has a
timeout, bounded retries and persisted output, so a session can resume after a refresh or restart
and can never sit at "100%" without the next stage being triggered."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import traceback
import uuid
from typing import Awaitable, Callable

from ..agents import critic, planner, researcher, verifier, writer
from ..agents.base import AgentContext, AgentFailure, AgentResult, Services
from ..config import Settings
from ..db import Database, now_iso
from ..services.demo_data import DemoFetch, DemoSearch
from .state import AGENT_LABELS, AGENTS, MAX_REVISIONS, STAGE_KEYS, STAGES, WorkflowState

log = logging.getLogger("orchestrator")
StageFn = Callable[[AgentContext], Awaitable[AgentResult]]


class Orchestrator:
    LEASE_TTL_S = 60          # a session lease expires 60s after its runner stops renewing it
    WATCHDOG_INTERVAL_S = 30

    def __init__(self, db: Database, settings: Settings,
                 services_factory: Callable[[str], Services] | None = None):
        self.db = db
        self.settings = settings
        self._services_factory = services_factory or self._default_services
        self.tasks: dict[str, asyncio.Task] = {}
        self._sem: asyncio.Semaphore | None = None
        self.runner_id = uuid.uuid4().hex  # identifies THIS backend process in session leases
        self._active_agents: set[tuple[str, str]] = set()
        self._services_cache: dict[str, Services] = {}
        from ..services.preflight import Preflight
        self.preflight = Preflight()

    # ------------------------------------------------------------------ services
    def _default_services(self, mode: str) -> Services:
        if mode == "demo":
            d = self.settings.demo_delay_s
            return Services(llm=None, search=DemoSearch(d), fetch=DemoFetch(d), demo=True, demo_delay=d)
        s = self.settings
        from ..services.factory import build_llm, build_tinyfish
        from ..services.llm_service import CATEGORY_TEXT
        llm = build_llm(s)
        if llm is None:  # never construct a provider without credentials, never retry a missing key
            st = s.llm_status()
            title, hint = CATEGORY_TEXT["not_configured"]
            raise AgentFailure(f"{title} {st['problem']}", retryable=False, info={
                "category": "not_configured", "title": title, "hint": hint, "provider": st["provider"] or "none",
                "model": st["model"] or "none", "http_status": None, "retry_after_s": None, "retry_possible_at": None,
                "attempts": 0, "detail": st["problem"], "problem_code": st["problem_code"]})
        search, fetch = build_tinyfish(s)
        if search is None:
            raise AgentFailure(s.search_status()["problem"], retryable=False)
        return Services(llm=llm, search=search, fetch=fetch, demo=False)

    def _services(self, mode: str) -> Services:
        """One client set per mode, shared by all sessions in this process (shared pacing budget)."""
        if mode not in self._services_cache:
            self._services_cache[mode] = self._services_factory(mode)
        return self._services_cache[mode]

    def search_client(self):
        """TinyFish search client, usable WITHOUT any LLM (search is a separate service)."""
        from ..services.factory import build_tinyfish
        return build_tinyfish(self.settings)[0]

    def live_llm(self):
        """The configured LLMProvider, or None when the LLM is not configured."""
        try:
            return self._services("live").llm
        except AgentFailure:
            return None

    async def check_live_ready(self) -> dict:
        def record(c: dict) -> None:
            pass  # preflight requests are logged by the LLM service; they belong to no session
        return await self.preflight.check(self.settings, self.live_llm(), record=record)

    # ------------------------------------------------------------------ public API
    def create_session(self, request: dict, client_request_id: str | None = None,
                       mode: str | None = None) -> tuple[str, bool]:
        """Returns (session_id, created). A repeated client_request_id returns the existing session and
        does NOT start another workflow (protects against double-clicks and resent requests).
        `mode` is explicit: "live" (real LLM + TinyFish) or "demo" (DEMO MODE — SIMULATED AI)."""
        sid = uuid.uuid4().hex[:12]
        mode = mode or ("demo" if self.settings.demo_mode else "live")
        s = self.settings
        got = self.db.create_session(sid, request, mode, WorkflowState.QUEUED, client_request_id=client_request_id,
                                     llm_provider=None if mode == "demo" else s.llm_provider,
                                     llm_model=None if mode == "demo" else s.llm_model)
        if got != sid:
            log.info("Duplicate submit (client_request_id=%s) -> existing session %s; no new workflow", client_request_id, got)
            return got, False
        self.db.init_agent_runs(sid, AGENTS)
        self.db.add_event(sid, "orchestrator", "info",
                          f"Session created in {mode.upper()} mode." + (
                              " DEMO MODE — SIMULATED AI: sample data, no LLM, not real research." if mode == "demo"
                              else f" LLM: {s.llm_provider} / {s.llm_model}."))
        self.start(sid)
        return sid, True

    def is_running(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return bool(t and not t.done())

    def held_elsewhere(self, sid: str) -> bool:
        runner, until = self.db.lease_holder(sid)
        return bool(runner and runner != self.runner_id and until and until > time.time())

    def start(self, sid: str) -> bool:
        if self.is_running(sid):
            return False
        task = asyncio.get_running_loop().create_task(self._run_guarded(sid))
        self.tasks[sid] = task
        return True

    def retry(self, sid: str) -> str:
        sess = self.db.get_session(sid)
        if not sess:
            raise KeyError(sid)
        if self.is_running(sid) or self.held_elsewhere(sid):
            raise RuntimeError("This session is still running.")
        if sess["status"] == WorkflowState.COMPLETED:
            raise RuntimeError("This session already completed.")
        stage = sess["failed_stage"] or self._first_incomplete_stage(sid)
        idx = STAGE_KEYS.index(stage)
        self.db.clear_downstream(sid, stage)
        for _, agent, _, _ in STAGES[idx:]:
            self.db.update_agent_run(sid, agent, status="waiting", progress=0, message="", error=None,
                                     started_at=None, completed_at=None, completion_state=None, metrics={})
        if stage in ("planning", "research", "verification", "writing"):
            self.db.update_session(sid, revision_count=0)
        self.db.update_session(sid, status=WorkflowState.QUEUED, failed_stage=None, error=None, error_info=None)
        done = [AGENT_LABELS[a] for _, a, _, _ in STAGES[:idx]]
        self.db.add_event(sid, "orchestrator", "info",
                          f"Retry requested — resuming from {AGENT_LABELS[STAGES[idx][1]]}"
                          + (f"; reusing completed: {', '.join(done)}." if done else "."))
        self.start(sid)
        return stage

    async def resume_interrupted(self) -> list[str]:
        """Continue active sessions whose runner died (crash/restart). Sessions leased by another live
        backend process are left alone, so two servers never run the same session."""
        resumed = []
        for s in self.db.list_sessions(1000):
            sid = s["id"]
            if s["status"] not in WorkflowState.ACTIVE or self.is_running(sid) or self.held_elsewhere(sid):
                continue
            for r in self.db.get_agent_runs(sid):
                if r["status"] == "running":
                    self.db.update_agent_run(sid, r["agent"], status="waiting",
                                             message="Interrupted by server restart; resuming.")
            self.db.add_event(sid, "orchestrator", "warning", "Backend restarted — resuming workflow from last saved stage.")
            self.start(sid)
            resumed.append(sid)
        return resumed

    async def watchdog(self) -> None:
        """Periodically adopt sessions whose lease expired (e.g. the other server process died)."""
        while True:
            await asyncio.sleep(self.WATCHDOG_INTERVAL_S)
            try:
                if self.settings.auto_resume:
                    await self.resume_interrupted()
            except Exception:
                log.exception("watchdog error")

    # ------------------------------------------------------------------ internals
    def _first_incomplete_stage(self, sid: str) -> str:
        for key in STAGE_KEYS:
            if self.db.get_stage_output(sid, key) is None:
                return key
        return "critique"

    def _fail(self, sid: str, stage: str, agent: str, error: str, info: dict | None = None) -> None:
        self.db.update_agent_run(sid, agent, status="failed", error=error[:500], message=f"Failed: {error[:200]}",
                                 metrics={**self._metrics(sid, agent), "waiting": None})
        self.db.update_session(sid, status=WorkflowState.FAILED, failed_stage=stage, error=error[:500],
                               error_info=json.dumps(info) if info else None)
        self.db.add_event(sid, "orchestrator", "error", f"Workflow stopped at {AGENT_LABELS[agent]}: {error[:300]}")

    def _metrics(self, sid: str, agent: str) -> dict:
        run = next((r for r in self.db.get_agent_runs(sid) if r["agent"] == agent), None)
        return dict(run["metrics"]) if run else {}

    async def _run_guarded(self, sid: str) -> None:
        if not self.db.try_acquire_lease(sid, self.runner_id, self.LEASE_TTL_S):
            log.warning("Session %s is leased by another backend process; not starting a duplicate run", sid)
            self.db.add_event(sid, "orchestrator", "warning",
                              "Another backend process is already running this session; this one will not start a duplicate.")
            return

        async def heartbeat():
            while True:
                await asyncio.sleep(self.LEASE_TTL_S / 3)
                self.db.renew_lease(sid, self.runner_id, self.LEASE_TTL_S)
        hb = asyncio.get_running_loop().create_task(heartbeat())
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.settings.max_concurrent_sessions)
        try:
            if self._sem.locked():
                self.db.add_event(sid, "orchestrator", "info",
                                  f"Queued: another research session is running (MAX_CONCURRENT_SESSIONS="
                                  f"{self.settings.max_concurrent_sessions}). It starts automatically.")
            async with self._sem:
                await self._run(sid)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # last-resort guard: never leave a session silently "running"
            log.error("Workflow %s crashed: %s", sid, traceback.format_exc(limit=5))
            sess = self.db.get_session(sid)
            if sess and sess["status"] not in WorkflowState.TERMINAL:
                stage = self._first_incomplete_stage(sid)
                agent = dict((s[0], s[1]) for s in STAGES)[stage]
                self._fail(sid, stage, agent, f"Internal error: {type(e).__name__}: {e}")
        finally:
            hb.cancel()
            self.db.release_lease(sid, self.runner_id)

    async def _stage(self, sid: str, stage: str, agent: str, state: str, fn: StageFn,
                     services: Services, request: dict) -> AgentResult | None:
        key = (sid, agent)
        if key in self._active_agents:  # session_id + agent -> at most one active execution
            log.error("Refusing duplicate execution of %s for session %s", agent, sid)
            return None
        self._active_agents.add(key)
        try:
            return await self._stage_inner(sid, stage, agent, state, fn, services, request)
        finally:
            self._active_agents.discard(key)

    async def _stage_inner(self, sid: str, stage: str, agent: str, state: str, fn: StageFn,
                           services: Services, request: dict) -> AgentResult | None:
        self.db.update_session(sid, status=state)
        run = next(r for r in self.db.get_agent_runs(sid) if r["agent"] == agent)
        self.db.update_agent_run(sid, agent, status="running", progress=1, error=None,
                                 started_at=run["started_at"] or now_iso(), completed_at=None)
        self.db.add_event(sid, "orchestrator", "info", f"Handing off to {AGENT_LABELS[agent]}.")
        attempts = self.settings.stage_max_attempts
        last_error, info = "", None
        for attempt in range(1, attempts + 1):
            self.db.update_agent_run(sid, agent, attempts=(run["attempts"] or 0) + attempt)
            ctx = AgentContext(sid, request, self.db, services, agent)
            retryable = True
            info = None
            try:
                result = await asyncio.wait_for(fn(ctx), timeout=self.settings.stage_timeout_s)
                if result.success:
                    self.db.update_agent_run(sid, agent, status="completed", progress=100,
                                             completion_state=result.completion_state,
                                             completed_at=now_iso(), message=f"{result.completion_state}")
                    self.db.add_event(sid, agent, "success", result.completion_state)
                    return result
                last_error, retryable = result.error or "Agent reported failure.", result.retryable
            except AgentFailure as e:
                # LLM errors arrive with `info`: the central LLM service already applied backoff/retries,
                # so the stage is NOT re-run (that would just multiply requests against the limit).
                last_error, retryable, info = str(e), (e.retryable and not e.info), e.info
            except asyncio.TimeoutError:
                last_error = f"{AGENT_LABELS[agent]} timed out after {self.settings.stage_timeout_s}s."
            except Exception as e:
                log.error("Agent %s error: %s", agent, traceback.format_exc(limit=5))
                last_error = f"Unexpected {type(e).__name__}: {e}"
            if info:  # the LLM service already made all its attempts; say so plainly
                n = info.get("attempts") or 0
                self.db.add_event(sid, agent, "error", f"{AGENT_LABELS[agent]} stopped after {n} LLM request(s): {last_error[:300]}")
            else:
                self.db.add_event(sid, agent, "error", f"Attempt {attempt} failed: {last_error[:300]}")
            if not retryable or attempt == attempts:
                break
            wait = random.uniform(5, 10)
            self.db.add_event(sid, "orchestrator", "warning",
                              f"Retrying {AGENT_LABELS[agent]} in {wait:.0f}s (attempt {attempt + 1}/{attempts})…")
            await asyncio.sleep(wait)
        self._fail(sid, stage, agent, last_error, info)
        return None

    async def _run(self, sid: str) -> None:
        sess = self.db.get_session(sid)
        if not sess or sess["status"] in WorkflowState.TERMINAL:
            return
        request = {k: sess[k] for k in ("query", "hypothesis", "source_mode", "report_type", "citation_style", "depth")}
        try:
            services = self._services(sess["mode"])
        except AgentFailure as e:
            stage = self._first_incomplete_stage(sid)
            agent = dict((s[0], s[1]) for s in STAGES)[stage]
            info = {**e.info, "agent": agent, "agent_label": AGENT_LABELS[agent]} if e.info else None
            self._fail(sid, stage, agent, str(e), info)
            return

        async def stage(key: str, fn: StageFn, persist: Callable[[dict], None] | None = None):
            cached = self.db.get_stage_output(sid, key)
            if cached is not None:
                return cached
            _, agent, state, _ = next(s for s in STAGES if s[0] == key)
            res = await self._stage(sid, key, agent, state, fn, services, request)
            if res is None:
                return None
            if persist:
                persist(res.data)
            self.db.save_stage_output(sid, key, res.data)  # persisted before the next hand-off
            return res.data

        plan = await stage("planning", lambda ctx: planner.run(ctx))
        if plan is None:
            return
        research = await stage("research", lambda ctx: researcher.run(ctx, plan), lambda d: (
            self.db.replace_items("sources", "source_id", sid, d["sources"]),
            self.db.replace_items("evidence", "evidence_id", sid, d["evidence"])))
        if research is None:
            return
        verification = await stage("verification", lambda ctx: verifier.run(ctx, plan, research),
                                   lambda d: self.db.replace_items("claims", "claim_id", sid, d["claims"]))
        if verification is None:
            return
        draft = await stage("writing", lambda ctx: writer.run(ctx, plan, research, verification),
                            lambda d: self.db.save_report(sid, d["version"], d))
        if draft is None:
            return
        await self._critique_loop(sid, request, services, plan, research, verification)

    async def _critique_loop(self, sid, request, services, plan, research, verification) -> None:
        if self.db.get_stage_output(sid, "critique") is not None:
            return
        reports = self.db.get_reports(sid)
        report = reports[-1]
        revisions = self.db.get_session(sid)["revision_count"] or 0
        history: list[tuple[int, dict, dict]] = []
        while True:
            cycle = revisions + 1
            res = await self._stage(sid, "critique", "critic", WorkflowState.CRITIQUING,
                                    lambda ctx: critic.run(ctx, report, verification, cycle), services, request)
            if res is None:
                return
            crit = res.data
            self.db.save_critique(sid, report["version"], crit)
            history.append((crit["score"], report, crit))
            if crit["passed"]:
                break
            if revisions >= MAX_REVISIONS:
                self.db.add_event(sid, "orchestrator", "warning",
                                  f"Maximum of {MAX_REVISIONS} revisions reached — finalizing the best-scoring draft.")
                break
            revisions += 1
            self.db.update_session(sid, revision_count=revisions)
            self.db.add_event(sid, "orchestrator", "info",
                              f"Critic requested changes (score {crit['score']}). Revision {revisions}/{MAX_REVISIONS}.")
            rev = await self._stage(sid, "critique", "writer", WorkflowState.REVISING,
                                    lambda ctx: writer.revise(ctx, report, crit, plan, research, verification),
                                    services, request)
            if rev is None:
                return
            report = rev.data
            self.db.save_report(sid, report["version"], report)
            self.db.save_stage_output(sid, "writing", report)
        best_score, best_report, best_crit = max(history, key=lambda h: (h[0], h[1]["version"]))
        final = {**best_crit, "final_version": best_report["version"], "revisions": revisions,
                 "history": [{"version": r["version"], "score": s, "passed": c["passed"]} for s, r, c in history]}
        self.db.save_stage_output(sid, "critique", final)
        self.db.update_session(sid, status=WorkflowState.COMPLETED, quality_score=best_score,
                               completed_at=now_iso())
        self.db.add_event(sid, "orchestrator", "success",
                          f"Workflow completed. Final report v{best_report['version']} — quality score {best_score}/100.")
