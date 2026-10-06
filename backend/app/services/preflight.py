"""LLM preflight: decides whether a research workflow may start. Runs BEFORE any session is created,
so a missing or broken LLM never reaches the Research Planner (no Planner -> fail -> retry loop)."""
from __future__ import annotations

import time

from ..config import Settings
from .llm_service import CATEGORY_TEXT

OK_TTL_S = 600         # a successful health check is trusted for 10 minutes
FAIL_TTL_S = 60        # failures are remembered briefly so repeated clicks don't hit the provider


class Preflight:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    async def check(self, settings: Settings, llm, *, record=None) -> dict:
        """Returns {"ok": bool, "code", "category", "title", "message", "hint", "checked": "config"|"cached"|"live"}."""
        st = settings.llm_status()
        if not st["configured"] or llm is None:
            title, hint = CATEGORY_TEXT["not_configured"]
            code = st["problem_code"] or "no_key"
            return {"ok": False, "code": "llm_not_configured", "category": "not_configured",
                    "problem_code": code, "title": "LLM NOT CONFIGURED",
                    "message": "Please configure an LLM API key before starting autonomous research. "
                               + (st["problem"] or title),
                    "hint": hint, "checked": "config", "llm": st}
        if not settings.search_configured:
            ss = settings.search_status()
            return {"ok": False, "code": "search_not_configured", "category": "not_configured", "title": "SEARCH NOT CONFIGURED",
                    "message": ss["problem"], "hint": "Set TINYFISH_API_KEY in backend/.env and restart the backend.",
                    "checked": "config", "llm": st}
        key = (llm.provider, llm.model)
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return {**hit[1], "checked": "cached"}
        h = await llm.health_check(record=record)
        res = {"ok": h["ok"], "code": "ok" if h["ok"] else "llm_unavailable", "category": h["category"],
               "title": h["title"], "message": h["message"], "hint": h["hint"], "llm": st,
               "retry_after_s": h.get("retry_after_s"), "checked": "live"}
        ttl = OK_TTL_S if h["ok"] else min(FAIL_TTL_S, max(5.0, h.get("retry_after_s") or FAIL_TTL_S))
        self._cache[key] = (time.monotonic() + ttl, res)
        return res
