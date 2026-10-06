"""TEST HARNESS ONLY: serves the same Handlers over Python's stdlib http.server so the UI can be
tested where FastAPI cannot be installed. Production uses app/main.py (FastAPI + uvicorn).
Usage: python tests/stdlib_server.py <static_dir> [port]"""
import asyncio
import json
import logging
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.api.handlers import ApiError, Handlers  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.workflow.orchestrator import Orchestrator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
from app.services.factory import log_config  # noqa: E402
log_config(logging.getLogger("app"), settings)
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
db = Database(settings.database_path)
orch = Orchestrator(db, settings)
H = Handlers(db, orch)
asyncio.run_coroutine_threadsafe(orch.resume_interrupted(), loop).result()


def on_loop_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def on_loop(fn, *a):
    async def wrapper():
        return fn(*a)
    return asyncio.run_coroutine_threadsafe(wrapper(), loop).result()


class Req(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if self.command == "POST":  # mutating requests are logged so duplicate submits would be visible
            logging.getLogger("http").info("%s %s -> %s", self.command, self.path, a[1] if len(a) > 1 else "")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api(self, method):
        u = urlparse(self.path)
        parts = u.path.strip("/").split("/")[1:]
        try:
            if method == "GET" and parts == ["settings"]:
                return self._json(200, H.settings())
            if method == "POST" and parts == ["research"]:
                n = int(self.headers.get("Content-Length", 0))
                return self._json(201, on_loop_async(H.create(json.loads(self.rfile.read(n) or b"{}"), "127.0.0.1")))
            if method == "POST" and parts == ["diagnostics"]:
                target = parse_qs(u.query).get("target", ["all"])[0]
                return self._json(200, on_loop_async(H.diagnostics(target)))
            if method == "POST" and parts == ["search", "test"]:
                n = int(self.headers.get("Content-Length", 0))
                return self._json(200, on_loop_async(H.search_test(json.loads(self.rfile.read(n) or b"{}"))))
            if method == "GET" and parts == ["research", "history"]:
                return self._json(200, H.history())
            if len(parts) >= 2 and parts[0] == "research":
                rid, rest = parts[1], parts[2:]
                if method == "POST" and rest == ["retry"]:
                    return self._json(200, on_loop_async(H.retry(rid)))
                if method == "GET" and not rest:
                    return self._json(200, H.get(rid))
                if method == "GET" and rest == ["llm-calls"]:
                    return self._json(200, H.llm_calls(rid))
                if method == "GET" and rest == ["status"]:
                    after = int(parse_qs(u.query).get("after", ["0"])[0])
                    return self._json(200, on_loop(H.status, rid, after))
                if method == "GET" and len(rest) == 1 and rest[0] in ("sources", "evidence", "report"):
                    return self._json(200, getattr(H, rest[0])(rid))
                if method == "GET" and len(rest) == 2 and rest[0] == "export":
                    data, mime, name = H.export(rid, rest[1])
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
            self._json(404, {"detail": "Not found"})
        except ApiError as e:
            self._json(e.status, e.body())

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):
        return self._api("POST")


if __name__ == "__main__":
    static = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    os.chdir(static)
    ThreadingHTTPServer(("127.0.0.1", port), Req).serve_forever()
