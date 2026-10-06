"""Test doubles that speak the real wire formats (OpenAI / Gemini JSON, TinyFish JSON).
Used by unit tests (as an injected transport) and by fake_provider_server.py (over real HTTP)."""
from __future__ import annotations

import asyncio
import json
import re
import threading

from app.services.fetch_service import FetchedPage
from app.services.search_service import SearchHit
from app.services.errors import ServiceError

PAGES = {
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC100/": "Background music with lyrics reduced reading comprehension scores in a sample of 120 undergraduates compared with silence. Instrumental music showed no significant difference from silence.",
    "https://www.frontiersin.org/articles/10.3389/fpsyg.2020.01/full": "Participants who listened to preferred calm instrumental music reported improved mood and arousal. Mood improvements were associated with better performance on a spatial task.",
    "https://www.apa.org/news/music-study": "A meta-analysis found that background music has a small negative effect on memory tasks. Effects varied with music complexity and individual working memory capacity.",
    "https://someblog.medium.com/music-focus": "I always study better with lo-fi beats, it is amazing for focus.",
}
SNIPPET = "Researchers examined how background music affects reading comprehension and memory in university students across two experiments."


def agent_of(system: str) -> str:
    for key, agent in (("Research Planner", "planner"), ("extracting evidence", "researcher"),
                       ("Verifier agent", "verifier"), ("Report Writer", "writer"), ("Report Critic", "critic"),
                       ("connectivity test", "diagnostics")):
        if key in system:
            return agent
    return "unknown"


class Brain:
    """Scripted 'model'. Deliberately misbehaves (fabricated quote, invented claim, bogus citation)
    so the anti-fabrication guards can be verified."""
    def __init__(self):
        self.critic_calls = 0
        self.lock = threading.Lock()

    def answer(self, system: str, user: str) -> dict:
        data = json.loads(user) if user.strip().startswith("{") else {}
        a = agent_of(system)
        if a == "diagnostics":
            return {"ok": True}
        if a == "planner":
            return {"research_question": data["research_question"], "key_concepts": ["music", "attention"],
                    "subtopics": [{"title": t, "rationale": "r", "queries": [f"music {t}", f"music {t}"],
                                   "evidence_needed": "e", "source_types": ["peer-reviewed"]}
                                  for t in ["attention", "memory", "lyrics vs instrumental"]],
                    "required_source_types": ["peer-reviewed"], "evidence_criteria": ["empirical"]}
        if a == "researcher":
            out = []
            for d in data["documents"]:
                if "lo-fi" in d["text"]:
                    out.append({"doc": d["doc"], "relevant": False, "evidence": []})
                    continue
                first = re.split(r"(?<=\.)\s", d["text"])[0]
                out.append({"doc": d["doc"], "relevant": True, "relevance": 0.9, "evidence": [
                    {"subtopic_id": "T1", "summary": first, "quote": first},
                    {"subtopic_id": "T2", "summary": "Fabricated finding",
                     "quote": "This sentence does not exist in the source at all."}]})
            return {"documents": out}
        if a == "verifier":
            claims, contra = [], []
            for st in data["subtopics"]:
                ids = [e["evidence_id"] for e in data["evidence"] if e["subtopic_id"] == st["id"]]
                if not ids:
                    continue
                claims += [{"subtopic_id": st["id"], "text": f"Claim about {st['title']}", "evidence_ids": ids[:2],
                            "status": "supported", "confidence": "high", "notes": "n"},
                           {"subtopic_id": st["id"], "text": "Invented claim", "evidence_ids": ["E999"],
                            "status": "supported", "confidence": "high"}]
                if len(ids) >= 2:
                    contra.append({"subtopic_id": st["id"], "description": "sources disagree", "evidence_ids": ids[:2]})
            return {"claims": claims, "contradictions": contra}
        if a == "writer":
            headings = json.loads(system.split("in order: ")[1].split("\n")[0])
            body = " ".join(f"{c['text']} [{c['source_ids'][0]}]." for c in data["verified_claims"])
            return {"title": "Music and Studying",
                    "sections": [{"heading": h, "content": (body + " Bogus citation [S99]. ") * 3} for h in headings]}
        if a == "critic":
            with self.lock:
                self.critic_calls += 1
                n = self.critic_calls
            if n == 1:
                return {"score": 62, "answers_question": True, "summary": "Needs work",
                        "issues": [{"severity": "major", "category": "accuracy", "section": "Findings",
                                    "description": "Overstated", "suggestion": "Hedge"}]}
            return {"score": 90, "answers_question": True, "summary": "Good", "issues": []}
        raise AssertionError(f"unexpected prompt: {system[:60]}")


# ---------------------------------------------------------------- provider-shaped responses
def ok_openai(obj) -> tuple[int, dict, str]:
    return 200, {"content-type": "application/json"}, json.dumps(
        {"choices": [{"message": {"content": obj if isinstance(obj, str) else json.dumps(obj)}}]})


def ok_gemini(obj) -> tuple[int, dict, str]:
    return 200, {}, json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]})


def openai_429(retry_after: float | None = None) -> tuple[int, dict, str]:
    h = {"retry-after": str(retry_after)} if retry_after is not None else {}
    return 429, h, json.dumps({"error": {"message": "Rate limit reached for gpt-4.1-mini on requests per min (RPM): Limit 3, Used 3.",
                                         "type": "requests", "code": "rate_limit_exceeded"}})


def openai_insufficient_quota() -> tuple[int, dict, str]:
    return 429, {}, json.dumps({"error": {"message": "You exceeded your current quota, please check your plan and billing details.",
                                          "type": "insufficient_quota", "code": "insufficient_quota"}})


def openai_401() -> tuple[int, dict, str]:
    return 401, {}, json.dumps({"error": {"message": "Incorrect API key provided: sk-te****.", "code": "invalid_api_key"}})


def gemini_429(quota_id: str, retry_delay: str | None = "12s", quota_value: str = "10") -> tuple[int, dict, str]:
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": quota_id, "quotaValue": quota_value}]}]
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return 429, {}, json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "You exceeded your current quota, please check your plan and billing details.", "details": details}})


class FakeLLMTransport:
    """Async transport compatible with LLMClient. `script[agent]` is a list of canned error responses
    returned (in order) before the brain answers; `always[agent]` is returned forever."""
    def __init__(self, brain: Brain | None = None, fmt: str = "openai"):
        self.brain = brain or Brain()
        self.fmt = fmt
        self.script: dict[str, list] = {}
        self.always: dict[str, tuple] = {}
        self.requests: list[dict] = []

    def _parse(self, body: dict) -> tuple[str, str]:
        if "messages" in body and "contents" not in body:
            msgs = body["messages"]
            if isinstance(body.get("system"), str):  # anthropic shape
                return body["system"], msgs[0]["content"]
            return msgs[0]["content"], msgs[1]["content"]
        return body["systemInstruction"]["parts"][0]["text"], body["contents"][0]["parts"][0]["text"]

    async def __call__(self, method, url, *, headers=None, json_body=None, timeout=60, params=None):
        await asyncio.sleep(0)
        system, user = self._parse(json_body)
        agent = agent_of(system)
        self.requests.append({"agent": agent, "body": json_body})
        if agent in self.always:
            return self.always[agent]
        if self.script.get(agent):
            return self.script[agent].pop(0)
        obj = self.brain.answer(system, user)
        return ok_gemini(obj) if self.fmt == "gemini" else ok_openai(obj)

    def count(self, agent: str | None = None) -> int:
        return sum(1 for r in self.requests if agent is None or r["agent"] == agent)


class SleepRecorder:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, s: float):
        self.calls.append(s)
        await asyncio.sleep(0)


# ---------------------------------------------------------------- TinyFish fakes (in-process)
class FakeSearch:
    def __init__(self):
        self.calls = 0

    async def search(self, query, *, domain_type="web", **kw):
        self.calls += 1
        return [SearchHit(title=f"Study {i}: music and {query[:20]}", url=u, snippet=SNIPPET,
                          domain_type=domain_type, position=i + 1,
                          authors=["Jane Q. Smith", "Ravi Kumar"] if "pmc" in u else [],
                          year=2019 if "pmc" in u else None) for i, u in enumerate(PAGES)]


class FakeFetch:
    async def fetch(self, urls, purpose=""):
        return {u: FetchedPage(url=u, final_url=u, ok=True, text=PAGES.get(u, "")) for u in urls}


class SwitchableSearch:
    def __init__(self, inner):
        self.inner = inner

    async def search(self, *a, **k):
        return await self.inner.search(*a, **k)


class FailingSearch:
    async def search(self, *a, **k):
        raise ServiceError("Search API returned HTTP 503: unavailable", kind="search")


class FailingFetch:
    async def fetch(self, *a, **k):
        raise ServiceError("Fetch API timed out after 150s.", kind="timeout")


class HangingSearch:
    async def search(self, *a, **k):
        await asyncio.sleep(3600)
