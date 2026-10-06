"""FastAPI entry point:  uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.handlers import Handlers
from .api.routes import build_router
from .config import settings
from .db import Database
from .services.factory import log_config
from .workflow.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

db = Database(settings.database_path)
orchestrator = Orchestrator(db, settings)
handlers = Handlers(db, orchestrator)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    s = settings
    log_config(log, s)
    if s.auto_resume:
        resumed = await orchestrator.resume_interrupted()
        if resumed:
            log.info("Resumed %d interrupted session(s): %s", len(resumed), ", ".join(resumed))
    wd = asyncio.get_running_loop().create_task(orchestrator.watchdog())
    yield
    wd.cancel()
    running = [t for t in orchestrator.tasks.values() if not t.done()]
    for t in running:
        t.cancel()
    # let cancelled workflows run their cleanup (release session leases) before the loop closes,
    # so a restarted backend can resume them immediately
    await asyncio.gather(*running, return_exceptions=True)


app = FastAPI(title="Multi-Agent Research & Report Generator", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(build_router(handlers))
