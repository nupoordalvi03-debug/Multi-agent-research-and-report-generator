"""TEST RIG ONLY: a local HTTP server speaking the OpenAI chat-completions and TinyFish Search/Fetch wire
formats, with scriptable 429 / quota responses. Lets the REAL backend run end-to-end in live mode,
over real HTTP, where the real providers can't be reached.   python tests/fake_provider_server.py 9100"""
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))
from fake_providers import PAGES, SNIPPET, Brain, agent_of  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s PROVIDER %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger()
brain = Brain()
lock = threading.Lock()
STATE = {"script": {}, "always": {}, "count": {}}

ERRORS = {
    "rate_minute": (429, {"Retry-After": "7"}, {"error": {"message": "Rate limit reached for gpt-4.1-mini on requests per min (RPM): Limit 3, Used 3, Requested 1. Please try again in 7s.", "type": "requests", "code": "rate_limit_exceeded"}}),
    "quota_day": (429, {}, {"error": {"message": "Rate limit reached for gpt-4.1-mini on requests per day (RPD): Limit 200, Used 200, Requested 1.", "type": "requests", "code": "rate_limit_exceeded"}}),
    "insufficient_quota": (429, {}, {"error": {"message": "You exceeded your current quota, please check your plan and billing details.", "type": "insufficient_quota", "code": "insufficient_quota"}}),
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/search":
            if not self.headers.get("X-API-Key"):
                return self._send(401, {"error": "missing api key"})
            log.info("TinyFish search: %s", parse_qs(u.query).get("query", [""])[0][:60])
            q = parse_qs(u.query).get("query", [""])[0]
            res = [{"position": i + 1, "site_name": urlparse(p).hostname, "title": f"Study {i}: music and {q[:20]}",
                    "snippet": SNIPPET, "url": p} for i, p in enumerate(PAGES)]
            return self._send(200, {"query": q, "results": res, "total_results": len(res), "page": 0})
        if u.path == "/stats":
            return self._send(200, STATE["count"])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if u.path == "/control":
            with lock:
                STATE["script"] = {k: list(v) for k, v in (body.get("script") or {}).items()}
                STATE["always"] = body.get("always") or {}
            log.info("control: %s", body)
            return self._send(200, {"ok": True})
        if u.path == "/fetch":
            res = [{"url": x, "final_url": x, "title": None, "format": "markdown",
                    "text": PAGES.get(x) or f"Fake page content for {x}. Memory consolidation is studied in sleep research."}
                   for x in body["urls"]]
            log.info("TinyFish fetch: %d url(s)", len(body["urls"]))
            return self._send(200, {"results": res, "errors": []})
        if u.path == "/v1/chat/completions":
            system, user = body["messages"][0]["content"], body["messages"][1]["content"]
            agent = agent_of(system)
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or len(auth) < 20 or "invalid" in auth:
                log.info("chat request from %-10s -> 401 invalid_api_key", agent)
                return self._send(401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error",
                                                  "code": "invalid_api_key"}})
            if "nocredit" in auth:
                log.info("chat request from %-10s -> 429 insufficient_quota (no credits)", agent)
                return self._send(429, {"error": {"message": "You exceeded your current quota, please check your plan and billing details.",
                                                  "type": "insufficient_quota", "code": "insufficient_quota"}})
            with lock:
                n = STATE["count"][agent] = STATE["count"].get(agent, 0) + 1
                err = STATE["always"].get(agent) or (STATE["script"].get(agent) or [None]).pop(0) if (STATE["always"].get(agent) or STATE["script"].get(agent)) else None
            if err:
                code, hdr, payload = ERRORS[err]
                log.info("chat request #%d from %-10s -> %d %s %s", n, agent, code, err, hdr)
                return self._send(code, payload, hdr)
            log.info("chat request #%d from %-10s -> 200", n, agent)
            ans = brain.answer(system, user)
            return self._send(200, {"choices": [{"message": {"content": json.dumps(ans)}}]})
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9100), H).serve_forever()
