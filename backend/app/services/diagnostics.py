"""Connection check: one minimal real call to each external service, with actionable messages.
Used by POST /api/diagnostics (Settings page button) and `python -m app.check_live`."""
from __future__ import annotations

import asyncio
import time

from ..config import Settings
from .errors import ServiceError


async def _timed(name: str, coro, timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        msg = await asyncio.wait_for(coro, timeout=timeout)
        return {"name": name, "status": "ok", "message": msg, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except asyncio.TimeoutError:
        return {"name": name, "status": "failed", "message": f"No response within {timeout:.0f}s.",
                "latency_ms": int(timeout * 1000)}
    except ServiceError as e:
        return {"name": name, "status": "failed", "message": str(e), "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:  # never leak secrets: only the exception type and message
        return {"name": name, "status": "failed", "message": f"{type(e).__name__}: {str(e)[:200]}",
                "latency_ms": int((time.monotonic() - t0) * 1000)}


async def check(search=None, fetch=None, llm=None, *, settings: Settings | None = None, timeout: float = 45) -> dict:
    results = []

    async def do_search():
        hits = await search.search("effects of sleep on memory consolidation", domain_type="web")
        if not hits:
            raise ServiceError("Search worked but returned 0 results.")
        return f"{len(hits)} results, e.g. {hits[0].url[:80]}"

    async def do_fetch():
        pages = await fetch.fetch(["https://en.wikipedia.org/wiki/Memory_consolidation"])
        page = next(iter(pages.values()))
        if not page.ok or not page.text.strip():
            raise ServiceError(f"Fetch returned no content for the test page: {page.error or 'empty text'}")
        return f"Fetched {len(page.text):,} characters"

    async def do_llm():
        out = await llm.complete_json("You are a connectivity test. Reply in JSON.",
                                      'Return exactly this JSON object: {"ok": true}', temperature=0.0)
        if not isinstance(out, dict) or out.get("ok") is not True:
            raise ServiceError(f"LLM replied but not with the expected JSON: {str(out)[:120]}")
        return f"{llm.provider} / {llm.model} returned valid JSON"

    missing = settings.missing_for_live() if settings else []
    for name, obj, fn, key in [("TinyFish Search", search, do_search, "TINYFISH_API_KEY"),
                               ("TinyFish Fetch", fetch, do_fetch, "TINYFISH_API_KEY"),
                               ("LLM", llm, do_llm, "LLM")]:
        if obj is None:
            if settings is not None:
                why = settings.search_status()["problem"] if key != "LLM" else settings.llm_status()["problem"]
            else:
                why = None
            results.append({"name": name, "status": "skipped", "latency_ms": 0,
                            "message": f"Not configured: {why}" if why else "Not configured."})
        else:
            results.append(await _timed(name, fn(), timeout))
    ok = all(r["status"] == "ok" for r in results)
    return {"ready_for_live": ok, "results": results}


def live_objects(settings: Settings):
    """Build real clients from whatever keys are present (each independently)."""
    from .factory import build_llm, build_tinyfish
    search, fetch = build_tinyfish(settings)
    return search, fetch, build_llm(settings, max_retries=0)  # None when the LLM is not configured


async def check_search(settings: Settings) -> dict:
    """TinyFish only (no LLM involved): one search + one fetch."""
    from .factory import build_tinyfish
    st = settings.search_status()
    search, fetch = build_tinyfish(settings)
    if search is None:
        return {"ok": False, "provider": "TinyFish", "status": "not_configured", "message": st["problem"], "results": []}
    out = await check(search, fetch, None)
    rs = [r for r in out["results"] if r["name"].startswith("TinyFish")]
    ok = all(r["status"] == "ok" for r in rs)
    return {"ok": ok, "provider": "TinyFish", "status": "connected" if ok else "failed",
            "message": "; ".join(f"{r['name']}: {r['message']}" for r in rs), "results": rs}
