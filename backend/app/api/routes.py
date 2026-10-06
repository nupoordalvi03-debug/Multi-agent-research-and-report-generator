from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .handlers import ApiError, Handlers


class ResearchIn(BaseModel):
    query: str = Field(min_length=10, max_length=500)
    hypothesis: str = Field(default="", max_length=1000)
    source_mode: str = "Hybrid"
    report_type: str = "Academic Research Report"
    citation_style: str = "IEEE"
    depth: str = "Standard"
    client_request_id: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, pattern="^(live|demo)$")


class SearchTestIn(BaseModel):
    query: str = Field(min_length=3, max_length=200)


def build_router(h: Handlers) -> APIRouter:
    r = APIRouter(prefix="/api")

    def wrap(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ApiError as e:
            return JSONResponse(e.body(), status_code=e.status)

    async def awrap(coro):
        try:
            return await coro
        except ApiError as e:
            return JSONResponse(e.body(), status_code=e.status)

    @r.get("/health")
    def health():
        return {"ok": True}

    @r.get("/settings")
    def settings():
        return h.settings()

    @r.post("/diagnostics")
    async def diagnostics(target: str = "all"):
        return await awrap(h.diagnostics(target))

    @r.post("/search/test")
    async def search_test(body: SearchTestIn):
        return await awrap(h.search_test(body.model_dump()))

    @r.post("/research", status_code=201)
    async def create(body: ResearchIn, request: Request):
        client = request.client.host if request.client else "unknown"
        return await awrap(h.create(body.model_dump(), client))

    @r.get("/research/history")  # declared before /{rid}
    def history():
        return h.history()

    @r.get("/research/{rid}")
    def get(rid: str):
        return wrap(h.get, rid)

    @r.get("/research/{rid}/status")
    def status(rid: str, after: int = 0):
        return wrap(h.status, rid, after)

    @r.get("/research/{rid}/llm-calls")
    def llm_calls(rid: str):
        return wrap(h.llm_calls, rid)

    @r.get("/research/{rid}/sources")
    def sources(rid: str):
        return wrap(h.sources, rid)

    @r.get("/research/{rid}/evidence")
    def evidence(rid: str):
        return wrap(h.evidence, rid)

    @r.get("/research/{rid}/report")
    def report(rid: str):
        return wrap(h.report, rid)

    @r.post("/research/{rid}/retry")
    async def retry(rid: str):
        return await awrap(h.retry(rid))

    @r.get("/research/{rid}/export/{fmt}")
    def export(rid: str, fmt: str):
        try:
            data, mime, name = h.export(rid, fmt)
        except ApiError as e:
            return JSONResponse(e.body(), status_code=e.status)
        return Response(content=data, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return r
