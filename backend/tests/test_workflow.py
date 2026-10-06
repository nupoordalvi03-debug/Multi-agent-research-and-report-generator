"""Run:  python -m unittest discover -s tests -v   (from backend/)
No network or API keys needed. Live-mode code runs against provider-accurate fakes (see fake_providers.py)."""
import asyncio
import dataclasses
import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("DEMO_MODE", "1")
os.environ["DEMO_DELAY_S"] = "0"

from app.agents.base import Services
from app.api.handlers import ApiError, Handlers
from app.config import load_settings
from app.db import Database
from app.services.citation_service import apply_citations, render_markers
from app.services.errors import ServiceError
from app.services.llm_service import LLMClient, LLMError, classify, extract_json
from app.services.url_safety import is_safe_public_url, normalize_url
from app.workflow.orchestrator import Orchestrator
from fake_providers import (FailingFetch, FailingSearch, FakeFetch, FakeLLMTransport, FakeSearch, HangingSearch,
                            SleepRecorder, SwitchableSearch, gemini_429, openai_401, openai_429,
                            openai_insufficient_quota)

DEMO_Q = {"query": "Does listening to music while studying improve concentration and learning?",
          "hypothesis": "Listening to suitable music may improve mood and concentration for some students, but "
                        "music with lyrics or high complexity may negatively affect learning and memory.",
          "source_mode": "Hybrid", "report_type": "Academic Research Report",
          "citation_style": "IEEE", "depth": "Standard"}
AGENT_NAMES = ["planner", "researcher", "verifier", "writer", "critic"]


def settings(**kw):
    # Demo unless a test asks for live: backend/.env (loaded at import, overriding os.environ) may set
    # DEMO_MODE=0 with real keys, which would otherwise send real LLM/TinyFish requests from these tests.
    kw.setdefault("force_demo", True)
    return dataclasses.replace(load_settings(), stage_timeout_s=kw.pop("timeout", 20),
                               stage_max_attempts=kw.pop("attempts", 2), **kw)


TF_KEY = "tf_live_0123456789abcdefghijk"
OPENAI_KEY = "sk-proj-0123456789abcdefghijklmnop"


def live_settings(**kw):
    base = dict(force_demo=False, tinyfish_api_key=TF_KEY, llm_provider="openai", llm_model="gpt-test",
                openai_api_key=OPENAI_KEY, google_api_key="", anthropic_api_key="", llm_keys_present=("openai",),
                llm_disabled=False, openai_base_url="")
    base.update(kw)
    return settings(**base)


def no_llm_settings(**kw):
    """TinyFish present, NO LLM key at all (the user's situation)."""
    return live_settings(llm_provider="", llm_model="", openai_api_key="", llm_keys_present=(), **kw)


def run(coro):
    return asyncio.run(coro)


async def finish(orch, sid, limit=60):
    await asyncio.wait_for(orch.tasks[sid], timeout=limit)


def make_llm(transport, sleeper=None, provider="openai", **kw):
    return LLMClient(provider, "gpt-test" if provider == "openai" else "gemini-test", OPENAI_KEY,
                     transport=transport, sleep=sleeper or SleepRecorder(), max_rpm=kw.pop("max_rpm", 0), **kw)


def live_orch(db, transport=None, search=None, fetch=None, sleeper=None, **skw):
    transport = transport or FakeLLMTransport()
    llm = make_llm(transport, sleeper)
    orch = Orchestrator(db, live_settings(**skw), services_factory=lambda mode: Services(
        llm=llm, search=search or FakeSearch(), fetch=fetch or FakeFetch(), demo=False))
    return orch, transport


def new_live_session(db, orch, sid="abcdef123456"):
    db.create_session(sid, DEMO_Q, "live", "QUEUED", llm_provider="openai", llm_model="gpt-test")
    db.init_agent_runs(sid, AGENT_NAMES)
    orch.start(sid)
    return sid


def calls_by_agent(db, sid):
    out = {}
    for c in db.get_llm_calls(sid):
        out[c["agent"]] = out.get(c["agent"], 0) + 1
    return out


# ============================================================================ demo mode
class DemoWorkflowTests(unittest.TestCase):
    def test_full_demo_workflow_and_exports(self):
        async def go():
            db = Database(":memory:")
            orch = Orchestrator(db, settings())
            h = Handlers(db, orch)
            created = await h.create(DEMO_Q, "test")
            sid = created["id"]
            self.assertEqual(created["mode"], "demo")
            await finish(orch, sid)
            st = h.status(sid)
            self.assertEqual(st["status"], "COMPLETED", st["error"])
            self.assertEqual([a["completion_state"] for a in st["agents"]],
                             ["PLANNING_COMPLETED", "RESEARCH_COMPLETED", "VERIFICATION_COMPLETED",
                              "REPORT_COMPLETED", "CRITIQUE_COMPLETED"])
            self.assertEqual(st["llm"]["requests"], 0)  # demo never calls an LLM
            srcs = h.sources(sid)["sources"]
            self.assertGreaterEqual(len(srcs), 5)
            self.assertTrue(all(s["is_demo"] and s["url"].startswith("https://demo.invalid/") for s in srcs))
            rep = h.report(sid)
            self.assertTrue(all(r["source_id"] in {s["source_id"] for s in srcs} for r in rep["report"]["references"]))
            self.assertTrue(h.export(sid, "pdf")[0].startswith(b"%PDF"))
            self.assertTrue(h.export(sid, "docx")[0].startswith(b"PK"))
            self.assertIn("DEMO MODE", h.export(sid, "markdown")[0].decode())
            self.assertEqual(h.history()[0]["id"], sid)
        run(go())

    def test_input_validation(self):
        async def go():
            db = Database(":memory:")
            h = Handlers(db, Orchestrator(db, settings()))
            with self.assertRaises(ApiError) as e:
                await h.create({"query": "short"}, "t")
            self.assertEqual(e.exception.status, 422)
            with self.assertRaises(ApiError):
                await h.create({**DEMO_Q, "citation_style": "Chicago"}, "t")
            with self.assertRaises(ApiError):
                await h.create({**DEMO_Q, "client_request_id": "bad id!"}, "t")
            with self.assertRaises(ApiError) as e:
                h.get("../../etc")
            self.assertEqual(e.exception.status, 400)
        run(go())

    def test_empty_search_results_fail_cleanly(self):
        async def go():
            db = Database(":memory:")
            orch = Orchestrator(db, settings())
            sid, _ = orch.create_session({**DEMO_Q, "query": "zzznoresults quantum basket weaving outcomes"})
            await finish(orch, sid)
            s = db.get_session(sid)
            self.assertEqual((s["status"], s["failed_stage"]), ("FAILED", "research"))
            self.assertIn("no usable results", s["error"])
            runs = {r["agent"]: r for r in db.get_agent_runs(sid)}
            self.assertEqual(runs["researcher"]["attempts"], 1)
        run(go())


# ============================================================================ spec tests 1-7
class RateLimitAndExecutionTests(unittest.TestCase):
    # ---- Test 1 + Test 7: normal request; complete live workflow with minimal LLM requests
    def test_1_and_7_complete_live_workflow_minimal_llm_calls(self):
        async def go():
            db = Database(":memory:")
            orch, tr = live_orch(db)
            sid = new_live_session(db, orch)
            await finish(orch, sid)
            s = db.get_session(sid)
            self.assertEqual(s["status"], "COMPLETED", s["error"])
            self.assertEqual([r["completion_state"] for r in sorted(db.get_agent_runs(sid), key=lambda r: AGENT_NAMES.index(r["agent"]))],
                             ["PLANNING_COMPLETED", "RESEARCH_COMPLETED", "VERIFICATION_COMPLETED",
                              "REPORT_COMPLETED", "CRITIQUE_COMPLETED"])
            by = calls_by_agent(db, sid)
            # planner 1; researcher 1 batch for 3 docs; verifier 1 for all subtopics;
            # writer draft + 1 revision; critic 2 reviews (first one requests a revision)
            self.assertEqual(by, {"planner": 1, "researcher": 1, "verifier": 1, "writer": 2, "critic": 2})
            self.assertEqual(tr.count(), 7)  # audit log == actual HTTP requests
            # anti-fabrication guards still hold
            ev = db.get_items("evidence", sid)
            self.assertTrue(all(not e["quote"] for e in ev if e["summary"] == "Fabricated finding"))
            claims = db.get_items("claims", sid)
            self.assertTrue(all(not c["include_in_report"] for c in claims if c["text"] == "Invented claim"))
            reports = db.get_reports(sid)
            self.assertIn("S99", reports[0]["removed_citations"])
            self.assertEqual(len(db.get_items("sources", sid)), 3)
        run(go())

    # ---- Test 2: simulated 429 -> waits (Retry-After / backoff), never immediate
    def test_2_429_waits_with_retry_after_and_backoff(self):
        async def go():
            tr, sl = FakeLLMTransport(), SleepRecorder()
            tr.script["diagnostics"] = [openai_429(retry_after=7)]
            out = await make_llm(tr, sl).complete_json("connectivity test JSON", '{"x":1}')
            self.assertEqual(out, {"ok": True})
            self.assertEqual(tr.count(), 2)
            self.assertTrue(7.45 <= sl.calls[-1] <= 9.0, sl.calls)  # Retry-After respected (+jitter)
        run(go())  # each case in its own event loop: the shared gate (fake clock) must start fresh

        async def go2():
            tr, sl = FakeLLMTransport(), SleepRecorder()
            tr.script["diagnostics"] = [openai_429(), openai_429()]
            await make_llm(tr, sl).complete_json("connectivity test JSON", "{}")
            backoffs = [w for w in sl.calls if w > 0.5]
            self.assertEqual(len(backoffs), 2)       # attempt 1 -> wait -> attempt 2 -> wait -> attempt 3
            for w, (lo, hi) in zip(backoffs, [(5, 10), (15, 30)]):
                self.assertTrue(lo - 0.05 <= w <= hi, backoffs)  # exponential backoff with jitter
        run(go2())

        async def go3():
            tr, sl = FakeLLMTransport(fmt="gemini"), SleepRecorder()
            tr.script["diagnostics"] = [gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "12s")]
            await make_llm(tr, sl, provider="gemini").complete_json("connectivity test JSON", "{}")
            self.assertTrue(12.45 <= max(sl.calls) <= 14, sl.calls)  # Gemini RetryInfo respected
        run(go3())

    def test_2b_retries_are_bounded_and_long_waits_not_blocked(self):
        async def go():
            tr, sl = FakeLLMTransport(), SleepRecorder()
            tr.always["diagnostics"] = openai_429()
            with self.assertRaises(LLMError) as e:
                await make_llm(tr, sl).complete_json("connectivity test JSON", "{}")
            self.assertEqual(e.exception.category, "rate_limit")
            self.assertEqual(tr.count(), 3)  # attempt 1, 2, 3 then fail gracefully; never more
            tr, sl = FakeLLMTransport(), SleepRecorder()
            tr.always["diagnostics"] = openai_429(retry_after=600)
            with self.assertRaises(LLMError) as e:
                await make_llm(tr, sl).complete_json("connectivity test JSON", "{}")
            self.assertEqual(tr.count(), 1)  # provider wants 10 min: report it, don't block for 10 min
            self.assertEqual(e.exception.info()["retry_after_s"], 600)
            self.assertIsNotNone(e.exception.info()["retry_possible_at"])
        run(go())

    def test_2c_shared_cooldown_pauses_concurrent_callers(self):
        async def go():
            tr, sl = FakeLLMTransport(), SleepRecorder()
            tr.script["diagnostics"] = [openai_429(retry_after=6)]
            llm = make_llm(tr, sl, max_rpm=1000, max_concurrency=2)
            await asyncio.gather(llm.complete_json("connectivity test JSON", "{}"),
                                 llm.complete_json("connectivity test JSON", "{}"))
            self.assertEqual(tr.count(), 3)
        run(go())

    def test_workflow_rate_limit_not_double_retried(self):
        async def go():
            db = Database(":memory:")
            tr = FakeLLMTransport()
            tr.always["planner"] = openai_429(retry_after=1)
            orch, _ = live_orch(db, transport=tr)
            sid = new_live_session(db, orch)
            await finish(orch, sid)
            s = db.get_session(sid)
            self.assertEqual((s["status"], s["failed_stage"]), ("FAILED", "planning"))
            self.assertEqual(tr.count("planner"), 3)  # NOT 3 x stage attempts
            info = json.loads(s["error_info"])
            self.assertEqual(info["category"], "rate_limit")
            self.assertEqual(info["provider"], "openai")
            self.assertEqual(info["agent_label"], "Research Planner")
            planner = next(r for r in db.get_agent_runs(sid) if r["agent"] == "planner")
            self.assertEqual(planner["attempts"], 1)
            self.assertTrue(any("will retry automatically" in e["message"] for e in db.get_events(sid)))
        run(go())

    # ---- Test 3: polling never causes LLM requests
    def test_3_status_polling_never_triggers_llm(self):
        async def go():
            db = Database(":memory:")
            orch, tr = live_orch(db)
            h = Handlers(db, orch)
            sid = new_live_session(db, orch)
            while orch.is_running(sid):          # poll aggressively DURING the run
                for _ in range(20):
                    h.status(sid, 0)
                    h.get(sid)
                await asyncio.sleep(0)
            for _ in range(200):                  # and a lot after it
                h.status(sid, 0); h.get(sid); h.sources(sid); h.evidence(sid); h.report(sid)
            self.assertEqual(tr.count(), 7)
            self.assertEqual(len(db.get_llm_calls(sid)), 7)
        run(go())

    # ---- Test 4: duplicate submit -> one workflow
    def test_4_duplicate_submit_creates_one_workflow(self):
        async def go():
            db = Database(":memory:")
            orch, tr = live_orch(db)
            h = Handlers(db, orch)
            body = {**DEMO_Q, "client_request_id": "req-12345678"}
            a, b = await h.create(body, "c"), await h.create(body, "c")
            self.assertEqual(a["id"], b["id"])
            self.assertTrue(a["created"])
            self.assertFalse(b["created"])
            self.assertEqual(len(db.list_sessions()), 1)
            self.assertFalse(orch.start(a["id"]))  # already running -> no second task
            await finish(orch, a["id"])
            self.assertEqual(tr.count("planner"), 1)
            self.assertEqual(len(orch.tasks), 1)
        run(go())

    def test_4b_one_runner_per_session_across_processes(self):
        async def go():
            path = os.path.join(tempfile.mkdtemp(), "lease.db")
            db = Database(path)
            a, tra = live_orch(db)
            b, trb = live_orch(Database(path))
            sid = "aaaabbbbcccc"
            db.create_session(sid, DEMO_Q, "live", "QUEUED")
            db.init_agent_runs(sid, AGENT_NAMES)
            self.assertTrue(db.try_acquire_lease(sid, a.runner_id, 60))  # process A owns it
            self.assertEqual(await b.resume_interrupted(), [])         # B must not adopt it
            b.start(sid)
            await finish(b, sid)                                        # B's task exits immediately
            self.assertEqual(trb.count(), 0)
            self.assertTrue(any("will not start a duplicate" in e["message"] for e in db.get_events(sid)))
            db.execute("UPDATE sessions SET lease_until=? WHERE id=?", (time.time() - 1, sid))  # A died
            self.assertEqual(await b.resume_interrupted(), [sid])
            await finish(b, sid)
            self.assertEqual(db.get_session(sid)["status"], "COMPLETED")
            self.assertEqual(db.lease_holder(sid), (None, None))      # released afterwards
            # same agent twice in one process is refused
            b._active_agents.add(("x" * 12, "planner"))
            self.assertIsNone(await b._stage("x" * 12, "planning", "planner", "PLANNING", None, None, {}))
        run(go())

    # ---- Test 5: persistent quota / billing / auth -> clear error, no retry storm
    def test_5_quota_billing_auth_are_not_retried(self):
        async def go():
            cases = [
                ("gemini", gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "40s"), "quota_exhausted"),
                ("gemini", gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", None, "0"), "quota_exhausted"),
                ("openai", openai_insufficient_quota(), "billing"),
                ("openai", openai_401(), "auth"),
            ]
            for provider, resp, expected in cases:
                db = Database(":memory:")
                tr = FakeLLMTransport(fmt=provider)
                tr.always["planner"] = resp
                llm = make_llm(tr, provider=provider)
                orch = Orchestrator(db, live_settings(), services_factory=lambda m, llm=llm: Services(
                    llm=llm, search=FakeSearch(), fetch=FakeFetch(), demo=False))
                h = Handlers(db, orch)
                sid = new_live_session(db, orch)
                await finish(orch, sid)
                st = h.status(sid)
                self.assertEqual(st["status"], "FAILED")
                self.assertEqual(st["error_info"]["category"], expected, (provider, expected))
                self.assertTrue(st["error_info"]["hint"])
                self.assertTrue(st["can_retry"])
                self.assertEqual(tr.count("planner"), 1, expected)  # exactly one request, no retries
                self.assertNotIn(OPENAI_KEY, json.dumps(st))        # never expose secrets
        run(go())

    # ---- Test 6: retry resumes from the failed agent, completed stages are reused
    def test_6_retry_resumes_from_failed_agent(self):
        async def go():
            db = Database(":memory:")
            tr = FakeLLMTransport()
            tr.always["verifier"] = openai_insufficient_quota()
            orch, _ = live_orch(db, transport=tr)
            h = Handlers(db, orch)
            sid = new_live_session(db, orch)
            await finish(orch, sid)
            self.assertEqual(db.get_session(sid)["failed_stage"], "verification")
            del tr.always["verifier"]          # user added credit
            self.assertEqual((await h.retry(sid))["retrying_from"], "verification")
            with self.assertRaises(ApiError):
                await h.retry(sid)             # second click while running -> 409
            await finish(orch, sid)
            self.assertEqual(db.get_session(sid)["status"], "COMPLETED")
            self.assertEqual(tr.count("planner"), 1)      # not redone
            self.assertEqual(tr.count("researcher"), 1)   # not redone
            self.assertEqual(tr.count("verifier"), 2)     # failed once, succeeded on retry
            runs = {r["agent"]: r for r in db.get_agent_runs(sid)}
            self.assertEqual(runs["planner"]["attempts"], 1)
        run(go())


# ============================================================================ other reliability
class ReliabilityTests(unittest.TestCase):
    def test_search_api_failure_then_retry(self):
        async def go():
            db = Database(":memory:")
            search = SwitchableSearch(FailingSearch())
            orch, tr = live_orch(db, search=search)
            h = Handlers(db, orch)
            sid = new_live_session(db, orch, "a1b2c3d4e5f6")
            await finish(orch, sid, limit=90)
            s = db.get_session(sid)
            self.assertEqual((s["status"], s["failed_stage"]), ("FAILED", "research"))
            self.assertIn("All searches failed", s["error"])
            search.inner = FakeSearch()
            await h.retry(sid)
            await finish(orch, sid)
            self.assertEqual(db.get_session(sid)["status"], "COMPLETED")
            self.assertEqual(tr.count("planner"), 1)
        run(go())

    def test_fetch_failure_falls_back_to_snippets(self):
        async def go():
            db = Database(":memory:")
            orch, _ = live_orch(db, fetch=FailingFetch())
            sid = new_live_session(db, orch, "0a0b0c0d0e0f")
            await finish(orch, sid)
            s = db.get_session(sid)
            self.assertEqual(s["status"], "COMPLETED", s["error"])
            self.assertTrue(all(x["fetch_status"] == "snippet_only" for x in db.get_items("sources", sid)))
            claims = [c for c in db.get_items("claims", sid) if c["include_in_report"]]
            self.assertTrue(claims and all("snippet_only" in c["flags"] for c in claims))
        run(go())

    def test_stage_timeout_never_hangs(self):
        async def go():
            db = Database(":memory:")
            orch, _ = live_orch(db, search=HangingSearch(), timeout=1, attempts=1)
            sid = new_live_session(db, orch, "ffffffffffff")
            await finish(orch, sid, limit=10)
            self.assertIn("timed out", db.get_session(sid)["error"])
        run(go())

    def test_resume_after_server_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "t.db")

        async def first_server():
            db = Database(path)
            orch = Orchestrator(db, settings(demo_delay_s=0.05))  # paced so the "crash" lands mid-research
            sid, _ = orch.create_session(DEMO_Q)
            while db.get_stage_output(sid, "planning") is None:
                await asyncio.sleep(0.01)
            self.assertEqual(db.lease_holder(sid)[0], orch.runner_id)
            orch.tasks[sid].cancel()
            try:
                await orch.tasks[sid]
            except asyncio.CancelledError:
                pass
            self.assertEqual(db.lease_holder(sid), (None, None))  # released on graceful shutdown
            self.assertEqual(db.get_session(sid)["status"], "RESEARCHING")
            return sid
        sid = run(first_server())

        async def second_server():
            db = Database(path)
            orch = Orchestrator(db, settings())
            self.assertEqual(await orch.resume_interrupted(), [sid])
            await finish(orch, sid)
            self.assertEqual(db.get_session(sid)["status"], "COMPLETED")
            self.assertEqual(next(r for r in db.get_agent_runs(sid) if r["agent"] == "planner")["attempts"], 1)
        run(second_server())


class LLMCompatibilityTests(unittest.TestCase):
    def test_reasoning_model_omits_temperature(self):
        async def go():
            tr = FakeLLMTransport()
            llm = LLMClient("openai", "gpt-5-mini", "k", transport=tr, max_rpm=0, sleep=SleepRecorder())
            await llm.complete_json("connectivity test JSON", "{}")
            self.assertNotIn("temperature", tr.requests[0]["body"])
            tr2 = FakeLLMTransport()
            llm2 = LLMClient("openai", "gpt-5-chat-latest", "k", transport=tr2, max_rpm=0)
            await llm2.complete_json("connectivity test JSON", "{}")
            self.assertIn("temperature", tr2.requests[0]["body"])
        run(go())

    def test_parameter_rejection_adapts_once(self):
        async def go():
            tr = FakeLLMTransport()
            tr.script["diagnostics"] = [(400, {}, json.dumps({"error": {"message": "Unsupported value: 'temperature' does not support 0.2", "param": "temperature"}})),
                                        (400, {}, "response_format json_object is not supported")]
            llm = make_llm(tr)
            self.assertEqual(await llm.complete_json("connectivity test JSON", "{}"), {"ok": True})
            self.assertEqual(tr.count(), 3)
            self.assertNotIn("temperature", tr.requests[-1]["body"])
            self.assertNotIn("response_format", tr.requests[-1]["body"])
        run(go())

    def test_missing_model_and_network(self):
        async def go():
            tr = FakeLLMTransport()
            tr.always["diagnostics"] = (404, {}, '{"error": {"message": "The model `gpt-9` does not exist"}}')
            with self.assertRaises(LLMError) as e:
                await make_llm(tr).complete_json("connectivity test JSON", "{}")
            self.assertEqual(e.exception.category, "model_unavailable")
            self.assertEqual(tr.count(), 1)

            async def down(*a, **k):
                raise ConnectionError("getaddrinfo failed")
            sl = SleepRecorder()
            with self.assertRaises(LLMError) as e:
                await make_llm(down, sl).complete_json("x JSON", "{}")
            self.assertEqual(e.exception.category, "network")
            self.assertEqual(len([w for w in sl.calls if w > 0.5]), 2)  # limited retry with backoff
        run(go())

    def test_classifier(self):
        self.assertEqual(classify("anthropic", 400, {}, '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}')[0], "billing")
        # OpenAI's current wording for an account with no credit (seen live): must not be treated as a rate limit
        self.assertEqual(classify("openai", 429, {}, '{"error":{"message":"You have no credits remaining. Add credits to continue using the API.","type":"insufficient_quota","code":"credit_balance_exhausted"}}')[0], "billing")
        self.assertEqual(classify("anthropic", 429, {"retry-after": "20"}, '{"error":{"type":"rate_limit_error"}}')[:2], ("rate_limit", 20.0))
        self.assertEqual(classify("anthropic", 529, {}, '{"error":{"type":"overloaded_error"}}')[0], "server")
        self.assertEqual(classify("gemini", 400, {}, '{"error":{"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT"}}')[0], "auth")
        self.assertEqual(classify("openai", 429, {}, '{"error":{"message":"Rate limit reached for gpt-4.1-mini on requests per day (RPD): Limit 200"}}')[0], "quota_exhausted")
        self.assertEqual(classify("openai", 429, {"x-ratelimit-reset-requests": "1m30s"}, '{"error":{"code":"rate_limit_exceeded"}}')[:2], ("rate_limit", 90.0))

    def test_diagnostics(self):
        from app.services.diagnostics import check

        class OkLLM:
            provider, model = "openai", "fake"
            async def complete_json(self, *a, **k):
                return {"ok": True}
        out = run(check(FakeSearch(), FakeFetch(), OkLLM()))
        self.assertEqual([r["status"] for r in out["results"]], ["ok", "failed", "ok"])
        out = run(check(FailingSearch(), None, None, settings=settings()))
        self.assertEqual(out["results"][0]["status"], "failed")
        self.assertEqual(out["results"][2]["status"], "skipped")


class UnitTests(unittest.TestCase):
    def test_url_safety(self):
        for bad in ["http://127.0.0.1/x", "http://10.0.0.5/", "file:///etc/passwd", "http://localhost:8000",
                    "http://169.254.169.254/latest", "javascript:alert(1)"]:
            self.assertFalse(is_safe_public_url(bad), bad)
        self.assertEqual(normalize_url("https://www.Example.com/a/?utm_source=x#frag"), normalize_url("http://example.com/a"))

    def test_extract_json(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure! {"a": [1,2]} hope that helps'), {"a": [1, 2]})
        with self.assertRaises(ValueError):
            extract_json("no json here")

    def test_citation_styles(self):
        src = {"S01": {"title": "Music and memory", "authors": ["Jane Q. Smith", "Ravi Kumar", "Li Wei"],
                       "publisher": "Frontiers in Psychology", "publication_date": "2020-03-01",
                       "url": "https://x.org/a", "retrieved_at": "2026-10-05T10:00:00+00:00"},
               "S02": {"title": "Noise at work", "authors": [], "publisher": "WHO", "publication_date": "",
                       "url": "https://who.int/b", "retrieved_at": "2026-10-05T10:00:00+00:00"}}
        for style, expect in [("IEEE", "[1], [2]"), ("APA", "(Smith et al., 2020; WHO, n.d.)"), ("MLA", "(Smith et al.; WHO)")]:
            secs = [{"heading": "F", "content": "Finding [S01][S02]. Bad [S07]."}]
            out = apply_citations(secs, src, {"S01", "S02"}, style)
            self.assertEqual(out["removed"], ["S07"])
            self.assertIn(expect, render_markers(secs[0]["content"], out["labels"], style), style)

    def test_settings(self):
        s = live_settings(tinyfish_api_key="tf_live_SECRET00000000000000", openai_api_key="sk-proj-SECRET0000000000000000")
        self.assertNotIn("SECRET", json.dumps(s.public_view()))
        self.assertEqual(s.public_view()["mode"], "live")
        self.assertIn("TINYFISH_API_KEY", " ".join(dataclasses.replace(s, tinyfish_api_key="").missing_for_live()))


class NoLLMKeyTests(unittest.TestCase):
    """The user's situation: TinyFish key present, NO LLM key. Research must be blocked BEFORE the Planner."""

    def test_no_llm_key_blocks_before_planner_with_zero_llm_requests(self):
        async def go():
            db = Database(":memory:")
            orch = Orchestrator(db, no_llm_settings())   # default services: nothing is injected
            h = Handlers(db, orch)
            view = h.settings()
            self.assertEqual(view["mode"], "not_configured")
            self.assertFalse(view["llm"]["configured"])
            self.assertEqual(view["llm"]["problem_code"], "no_key")
            self.assertTrue(view["search"]["configured"])
            self.assertIsNone(orch.live_llm())            # no provider object is ever created
            with self.assertRaises(ApiError) as e:
                await h.create({**DEMO_Q, "client_request_id": "nokey-000001"}, "c")
            self.assertEqual(e.exception.status, 409)
            self.assertEqual(e.exception.code, "llm_not_configured")
            self.assertIn("Please configure an LLM API key before starting autonomous research", e.exception.message)
            self.assertNotIn("rate limit", e.exception.message.lower())
            self.assertEqual(db.list_sessions(), [])      # no session, so no Planner, no retries
            # explicit, clearly labelled demo is still possible
            d = await h.create({**DEMO_Q, "mode": "demo"}, "c")
            self.assertEqual(d["mode"], "demo")
            await finish(orch, d["id"])
            ev = " ".join(x["message"] for x in db.get_events(d["id"]))
            self.assertIn("DEMO MODE — SIMULATED AI", ev)
            self.assertNotIn("Retrying", ev)
            self.assertEqual(db.get_llm_calls(d["id"]), [])
        run(go())

    def test_search_works_without_llm(self):
        async def go():
            db = Database(":memory:")
            orch = Orchestrator(db, no_llm_settings())
            orch.search_client = lambda: FakeSearch()
            out = await Handlers(db, orch).search_test({"query": "music and memory"})
            self.assertEqual(out["provider"], "TinyFish")
            self.assertTrue(out["results"])
        run(go())

    def test_config_problems_are_detected_without_network(self):
        cases = {
            "placeholder_key": live_settings(openai_api_key="your_openai_key_here"),
            "tinyfish_key_reused": live_settings(llm_provider="gemini", google_api_key=TF_KEY, openai_api_key="",
                                                 llm_keys_present=("gemini",)),
            "provider_ambiguous": live_settings(llm_provider="", llm_keys_present=("openai", "gemini"),
                                                google_api_key="AIzaSy0123456789abcdefghijk"),
            "disabled": live_settings(llm_provider="", llm_disabled=True),
            "no_key": live_settings(llm_provider="gemini", google_api_key="", llm_keys_present=("openai",)),
        }
        from app.services.factory import build_llm
        for code, s in cases.items():
            st = s.llm_status()
            self.assertFalse(st["configured"], code)
            self.assertEqual(st["problem_code"], code)
            self.assertIsNone(build_llm(s), code)
        with self.assertRaises(ValueError):
            LLMClient("openai", "gpt-test", "")         # an adapter cannot exist without a key

    def test_invalid_key_blocked_by_preflight_one_request(self):
        async def go():
            db = Database(":memory:")
            tr = FakeLLMTransport()
            tr.always["diagnostics"] = openai_401()
            orch, _ = live_orch(db, transport=tr)
            h = Handlers(db, orch)
            for i in range(3):                           # repeated clicks
                with self.assertRaises(ApiError) as e:
                    await h.create({**DEMO_Q, "client_request_id": f"badkey-00000{i}"}, "c")
                self.assertIn("LLM API key is invalid", e.exception.message)
                self.assertEqual(e.exception.info["category"], "auth")
            self.assertEqual(tr.count(), 1)              # one health request, cached; never retried
            self.assertEqual(db.list_sessions(), [])
        run(go())

    def test_live_session_resumed_after_key_removed_fails_cleanly(self):
        async def go():
            db = Database(":memory:")
            orch = Orchestrator(db, no_llm_settings())
            sid = new_live_session(db, orch, "dddddddddddd")
            await finish(orch, sid)
            st = Handlers(db, orch).status(sid)
            self.assertEqual(st["status"], "FAILED")
            self.assertEqual(st["error_info"]["category"], "not_configured")
            self.assertIn("No LLM API key configured", st["error"])
            self.assertEqual(st["llm"]["requests"], 0)
            planner = next(a for a in st["agents"] if a["agent"] == "planner")
            self.assertEqual(planner["attempts"], 0)    # the Planner never ran
            self.assertFalse(any("Retrying" in e["message"] for e in st["events"]))
        run(go())

    def test_startup_audit_reports_presence_and_source_never_value(self):
        import io
        import logging
        from app.services.factory import log_config
        buf = io.StringIO()
        lg = logging.getLogger("audit-test")
        lg.handlers = [logging.StreamHandler(buf)]
        lg.setLevel(logging.INFO)
        s = live_settings(key_sources=(("OPENAI_API_KEY", "system environment"), ("TINYFISH_API_KEY", ".env")))
        log_config(lg, s)
        out = buf.getvalue()
        self.assertIn("LLM Provider: configured (openai", out)
        self.assertIn("LLM API Key:  present (OPENAI_API_KEY, from system environment)", out)
        self.assertIn("SYSTEM environment variable", out)
        self.assertNotIn(OPENAI_KEY, out)
        self.assertNotIn(TF_KEY, out)
        buf.truncate(0)
        log_config(lg, no_llm_settings())
        self.assertIn("LLM API Key:  missing", buf.getvalue())
        self.assertIn("LLM NOT CONFIGURED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
