from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Database
from ..services.errors import ServiceError


@dataclass
class AgentResult:
    success: bool
    completion_state: str = ""
    data: Any = None
    error: str = ""
    retryable: bool = True
    metrics: dict = field(default_factory=dict)


class AgentFailure(Exception):
    """info: structured error from the LLM service (category, provider, retry time...).
    When info is present the LLM service has ALREADY retried, so the orchestrator must not retry again."""
    def __init__(self, message: str, retryable: bool = True, info: dict | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.info = info


@dataclass
class Services:
    """Injected dependencies. In demo mode the search/fetch are simulated and llm is None."""
    llm: Any | None
    search: Any
    fetch: Any
    demo: bool
    demo_delay: float = 0.0  # DEMO MODE pacing so the workflow is watchable


@dataclass
class AgentContext:
    session_id: str
    request: dict
    db: Database
    services: Services
    agent: str
    _progress_cb: Callable[[str, int, str, str], None] | None = None

    @property
    def demo(self) -> bool:
        return self.services.demo

    def report(self, message: str, progress: int | None = None, level: str = "info") -> None:
        """Record a real backend event and update this agent's live status."""
        self.db.add_event(self.session_id, self.agent, level, message)
        fields: dict = {"message": message[:300]}
        if progress is not None:
            fields["progress"] = max(0, min(99, int(progress)))  # 100 only on real completion
        self.db.update_agent_run(self.session_id, self.agent, **fields)

    async def demo_pause(self, factor: float = 1.0) -> None:
        if self.services.demo and self.services.demo_delay > 0:
            import asyncio
            await asyncio.sleep(self.services.demo_delay * factor)

    def metrics(self, **m: Any) -> None:
        run = next((r for r in self.db.get_agent_runs(self.session_id) if r["agent"] == self.agent), None)
        cur = run["metrics"] if run else {}
        cur.update(m)
        self.db.update_agent_run(self.session_id, self.agent, metrics=cur)


def require_llm(ctx: AgentContext):
    if ctx.services.llm is None:
        raise AgentFailure("No LLM configured for live mode.", retryable=False)
    return ctx.services.llm


async def llm_json(ctx: AgentContext, system: str, user: str, temperature: float = 0.2, *,
                   purpose: str = "main") -> Any:
    """The ONLY way agents talk to the LLM. Retries/backoff live in the central LLM service."""
    from datetime import datetime, timedelta, timezone

    from ..services.llm_service import LLMError
    from ..workflow.state import AGENT_LABELS
    llm = require_llm(ctx)
    label = AGENT_LABELS.get(ctx.agent, ctx.agent)

    def on_wait(w: dict) -> None:
        until = (datetime.now(timezone.utc) + timedelta(seconds=w["wait_s"])).isoformat(timespec="seconds")
        if w["category"] == "pacing":
            msg = f"{label} is waiting {w['wait_s']:.0f}s for its turn (pacing to {getattr(llm, 'max_rpm', '?')} LLM requests/min)."
            level = "info"
        elif w["category"] == "rate_limit":
            msg = (f"LLM rate limit reached ({w['provider']}). The system will retry automatically when the provider "
                   f"allows another request: retry {max(1, w['retry'])} of {w['max_retries']} in {w['wait_s']:.0f}s.")
            level = "warning"
        else:
            msg = (f"LLM {w['category'].replace('_', ' ')} ({w['provider']}): retry {w['retry']} of "
                   f"{w['max_retries']} in {w['wait_s']:.0f}s.")
            level = "warning"
        ctx.db.add_event(ctx.session_id, ctx.agent, level, msg)
        ctx.db.update_agent_run(ctx.session_id, ctx.agent, message=msg[:300])
        ctx.metrics(waiting={**w, "until": until, "agent_label": label})

    def record(c: dict) -> None:
        ctx.db.add_llm_call(ctx.session_id, {**c, "provider": llm.provider, "model": llm.model})

    try:
        return await llm.complete_json(system, user, temperature=temperature, on_wait=on_wait, record=record,
                                       meta={"session": ctx.session_id, "agent": ctx.agent, "purpose": purpose})
    except LLMError as e:
        info = {**e.info(), "agent": ctx.agent, "agent_label": label}
        raise AgentFailure(str(e), retryable=False, info=info) from e
    except ServiceError as e:
        raise AgentFailure(str(e), retryable=e.retryable) from e
    finally:
        ctx.metrics(waiting=None)
