"""Central LLM service. EVERY agent's LLM request goes through LLMClient.complete_json, which owns:
pacing (shared per provider/model), timeouts, error classification (rate limit vs quota vs billing
vs auth vs network), Retry-After handling, exponential backoff with jitter, bounded retries,
malformed-output repair and request logging. Agents never implement their own retries."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from .errors import ServiceError
from .http_util import parse_retry_after
from .rate_gate import get_gate
from .transport import http_request

log = logging.getLogger("llm")

RETRYABLE = {"rate_limit", "server", "timeout", "network"}
BACKOFF = [(5, 10), (15, 30), (30, 60)]  # seconds, attempt 1 / 2 / 3 (jittered)

CATEGORY_TEXT = {
    "not_configured": ("No LLM API key configured.",
                       "An LLM API key is required for autonomous planning, verification, report writing and critique. "
                       "Add one in backend/.env (see Settings) and restart the backend."),
    "auth": ("LLM API key is invalid (authentication error).",
             "The provider rejected the key (HTTP 401/403 or 'API key not valid'). Check the key in backend/.env, "
             "make sure it belongs to the selected LLM_PROVIDER, and restart the backend. Retrying will not help."),
    "rate_limit": ("LLM rate limit reached.",
                   "A temporary per-minute limit. The system waits and retries automatically when the provider allows "
                   "another request; if it still fails, wait a minute and click Retry research."),
    "quota_exhausted": ("LLM API quota/credits exhausted. Adding retries will not solve this.",
                        "The daily/monthly quota for this model is used up. Wait for the quota to reset, choose another "
                        "LLM_MODEL, or upgrade the plan."),
    "billing": ("LLM API quota/credits exhausted. Adding retries will not solve this.",
                "The provider account has no remaining credit or billing is not set up (OpenAI reports this as HTTP 429 "
                "'insufficient_quota'). Add credit/billing or use a different provider key."),
    "model_unavailable": ("LLM model unavailable.",
                          "This key cannot use the configured model. Set LLM_MODEL in backend/.env to a model your account can use."),
    "timeout": ("Network timeout contacting the LLM provider.", "The provider did not answer in time; it was retried a limited number of times."),
    "network": ("Cannot reach the LLM provider (network error).", "Check your internet connection, VPN, firewall or proxy."),
    "server": ("LLM provider temporarily unavailable.", "The provider reported a server error or overload; it was retried a limited number of times."),
    "bad_request": ("LLM rejected the request.", "The provider rejected the request format."),
    "malformed": ("Model response parsing error.", "The model did not return valid JSON twice in a row. Retry, or use a stronger LLM_MODEL."),
}


class LLMError(ServiceError):
    def __init__(self, message: str, *, category: str, provider: str, model: str, status: int | None = None,
                 retry_after: float | None = None, detail: str = "", attempts: int = 1, reason: str = ""):
        super().__init__(message, retryable=category in RETRYABLE, kind="LLM", status=status, detail=detail)
        self.reason = reason or message
        self.category, self.provider, self.model = category, provider, model
        self.retry_after, self.attempts = retry_after, attempts

    def info(self) -> dict:
        title, hint = CATEGORY_TEXT.get(self.category, ("LLM error", ""))
        nxt = None
        if self.retry_after:
            nxt = (datetime.now(timezone.utc) + timedelta(seconds=self.retry_after)).isoformat(timespec="seconds")
        return {"category": self.category, "title": title, "hint": hint, "provider": self.provider,
                "model": self.model, "http_status": self.status, "retry_after_s": self.retry_after,
                "retry_possible_at": nxt, "attempts": self.attempts, "detail": str(self)[:400]}


def extract_json(text: str) -> Any:
    """Parse model output that should be JSON, tolerating code fences and stray prose."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = t.find(open_c), t.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Model output was not valid JSON")


def _dur(s: str) -> float | None:
    """Parse '37s', '1.5s', '6m0s', '20ms' style durations."""
    if not s:
        return None
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", s):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


def classify(provider: str, status: int, headers: dict, text: str) -> tuple[str, float | None, str]:
    """Return (category, retry_after_seconds, short_reason)."""
    low = (text or "").lower()
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    code = str(err.get("code") or "").lower()
    etype = str(err.get("type") or err.get("status") or "").lower()
    msg = str(err.get("message") or "")[:300]
    retry_after = parse_retry_after(headers)

    # Gemini puts structured quota info and RetryInfo in error.details
    quota_ids, quota_values = [], []
    for d in err.get("details") or []:
        if not isinstance(d, dict):
            continue
        t = str(d.get("@type", ""))
        if t.endswith("RetryInfo") and retry_after is None:
            retry_after = _dur(str(d.get("retryDelay", "")))
        if t.endswith("QuotaFailure"):
            for v in d.get("violations") or []:
                quota_ids.append(str(v.get("quotaId") or v.get("quotaMetric") or ""))
                quota_values.append(str(v.get("quotaValue", "")))

    if status in (401, 403) or "api key not valid" in low or "invalid_api_key" in low or "incorrect api key" in low \
            or "authentication_error" in low or (provider == "gemini" and status == 403 and "permission" in low):
        return "auth", None, msg or "API key rejected"
    # OpenAI: code "insufficient_quota", or newer code "credit_balance_exhausted" with type "insufficient_quota"
    if "credit balance is too low" in low or "insufficient_quota" in (code, etype) \
            or code == "credit_balance_exhausted" or status == 402 \
            or "billing_not_active" in low or "billing account" in low and status in (400, 403):
        return "billing", None, msg or "billing/credits unavailable"
    if status == 429 or "resource_exhausted" in etype:
        ids = " ".join(quota_ids).lower()
        if quota_values and all(v == "0" for v in quota_values):
            return "quota_exhausted", None, "this model has a quota of 0 on your plan (not available on the free tier)"
        if "perday" in ids or "per_day" in ids or "requests per day" in low or "tokens per day" in low \
                or "(rpd)" in low or "(tpd)" in low or "daily" in low:
            return "quota_exhausted", retry_after, msg or "daily quota exhausted"
        if retry_after is None and provider == "openai":
            retry_after = _dur(headers.get("x-ratelimit-reset-requests", "")) or _dur(headers.get("x-ratelimit-reset-tokens", ""))
        return "rate_limit", retry_after, msg or "rate limited"
    if status == 404 or (status == 400 and "model" in low and any(k in low for k in ("not found", "does not exist", "not supported", "is not available"))):
        return "model_unavailable", None, msg or "model not available"
    if status in (500, 502, 503, 504, 529) or "overloaded" in low:
        return "server", retry_after, msg or f"HTTP {status}"
    return "bad_request", None, msg or f"HTTP {status}"


def _no_temperature(provider: str, model: str) -> bool:
    """OpenAI reasoning models (o1/o3/o4, gpt-5 family except *-chat*) only accept the default."""
    m = model.lower()
    return provider == "openai" and (re.match(r"^o\d", m) is not None or (m.startswith("gpt-5") and "chat" not in m))


Transport = Callable[..., Awaitable[tuple[int, dict, str]]]
WaitCallback = Callable[[dict], None]
RecordCallback = Callable[[dict], None]


class LLMProvider:
    """Provider-independent interface used by all agents:
         generate(system, user)            -> text
         generate_structured(system, user) -> parsed JSON (with one repair attempt)
         health_check()                    -> {"ok", "category", "title", "message", "latency_ms"}
    The base class owns pacing, classification, backoff and logging; adapters only describe their
    wire format. An adapter can only be created WITH a key, so a missing key never reaches the network."""
    provider = "base"

    def __init__(self, model: str, api_key: str, *, base_url: str = "", timeout: float = 120,
                 max_rpm: float = 8, max_concurrency: int = 1, max_retries: int = 2, max_wait_s: float = 90,
                 transport: Transport | None = None, sleep: Callable[[float], Awaitable] = asyncio.sleep):
        if not (api_key or "").strip():
            raise ValueError(f"{type(self).__name__} requires an API key; create it only when one is configured")
        self.model, self._key = model, api_key
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.timeout = timeout
        self.max_rpm, self.max_concurrency = max_rpm, max_concurrency
        self.max_retries, self.max_wait_s = max_retries, max_wait_s
        self._transport = transport or http_request
        self._sleep = sleep
        self.send_temperature = True
        self.json_mode = True

    # ---- adapter hooks ------------------------------------------------------------------
    def build_request(self, system: str, user: str, temperature: float) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def parse_text(self, data: dict) -> str:
        raise NotImplementedError

    # ---- engine -------------------------------------------------------------------------
    async def _answer(self, system: str, user: str, temperature: float, meta: dict,
                      on_wait: WaitCallback | None, record: RecordCallback | None,
                      max_retries: int | None = None) -> str:
        max_retries = self.max_retries if max_retries is None else max_retries
        gate = get_gate(("llm", self.provider, self.model), self.max_rpm, self.max_concurrency)
        tag = f"[{meta.get('session', '-')}/{meta.get('agent', '-')}/{meta.get('purpose', '-')}]"
        retry = 0
        adapted = 0
        announced = False
        while True:
            def gate_wait(delay: float, reason: str):
                if on_wait and not announced:
                    on_wait({"category": "rate_limit" if reason == "cooldown" else "pacing", "wait_s": round(delay, 1),
                             "retry": retry, "max_retries": max_retries, "provider": self.provider,
                             "model": self.model})
            await gate.acquire(self._sleep, gate_wait)
            announced = False
            t0 = time.monotonic()
            url, headers, body = self.build_request(system, user, temperature)
            try:
                try:
                    status, rh, text = await self._transport("POST", url, headers=headers, json_body=body,
                                                             timeout=self.timeout)
                except TimeoutError:
                    status, rh, text = -1, {}, "timeout"
                except ConnectionError as e:
                    status, rh, text = -2, {}, str(e)
            finally:
                gate.release()
            ms = int((time.monotonic() - t0) * 1000)
            if status == 200:
                try:
                    out = self.parse_text(json.loads(text))
                except ValueError:
                    out = text
                log.info("%s %s/%s attempt=%d -> 200 in %dms", tag, self.provider, self.model, retry + 1, ms)
                if record:
                    record({**meta, "attempt": retry + 1, "status": 200, "category": "ok", "latency_ms": ms, "wait_s": 0})
                return out

            if status == -1:
                category, retry_after, reason = "timeout", None, f"no response within {self.timeout:.0f}s"
            elif status == -2:
                category, retry_after, reason = "network", None, text
            else:
                category, retry_after, reason = classify(self.provider, status, rh, text)

            low = text.lower()
            if category == "bad_request" and adapted < 2:
                changed = False
                if "temperature" in low and self.send_temperature:
                    self.send_temperature, changed = False, True
                if any(k in low for k in ("response_format", "json_object", "responsemimetype")) and self.json_mode:
                    self.json_mode, changed = False, True
                if changed:
                    adapted += 1
                    log.info("%s %s/%s -> 400 parameter not supported; resending without it", tag, self.provider, self.model)
                    if record:
                        record({**meta, "attempt": retry + 1, "status": status, "category": "adapted", "latency_ms": ms, "wait_s": 0})
                    continue

            # Only genuinely temporary failures are retried; auth/quota/billing/model errors never are.
            can_retry = category in RETRYABLE and retry < max_retries
            wait = 0.0
            if can_retry:
                lo, hi = BACKOFF[min(retry, len(BACKOFF) - 1)]
                wait = retry_after + random.uniform(0.5, 2.0) if retry_after is not None else random.uniform(lo, hi)
                if wait > self.max_wait_s:
                    can_retry = False
            log.warning("%s %s/%s attempt=%d -> HTTP %s %s%s%s", tag, self.provider, self.model, retry + 1,
                        status if status > 0 else "-", category,
                        f" retry-after={retry_after:.0f}s" if retry_after else "",
                        f"; waiting {wait:.1f}s before attempt {retry + 2}/{max_retries + 1}" if can_retry
                        else ("; not retryable" if category not in RETRYABLE else "; giving up"))
            if record:
                record({**meta, "attempt": retry + 1, "status": status if status > 0 else None, "category": category,
                        "latency_ms": ms, "wait_s": round(wait, 1) if can_retry else 0})
            if not can_retry:
                raise LLMError(f"{CATEGORY_TEXT.get(category, ('LLM error',))[0]} ({self.provider}/{self.model}: {reason})",
                               category=category, provider=self.provider, model=self.model,
                               status=status if status > 0 else None, retry_after=retry_after,
                               detail=text[:1000], attempts=retry + 1, reason=f"{self.provider}/{self.model}: {reason}")
            if on_wait:
                on_wait({"category": category, "wait_s": round(wait, 1), "retry": retry + 1,
                         "max_retries": max_retries, "provider": self.provider, "model": self.model,
                         "retry_after_s": retry_after, "reason": reason[:200]})
            if category == "rate_limit":
                gate.cooldown(wait)
                announced = True
            else:
                await self._sleep(wait)
            retry += 1

    async def generate(self, system: str, user: str, *, temperature: float = 0.2, meta: dict | None = None,
                       on_wait: WaitCallback | None = None, record: RecordCallback | None = None) -> str:
        return await self._answer(system, user, temperature, dict(meta or {}), on_wait, record)

    async def generate_structured(self, system: str, user: str, *, temperature: float = 0.2, meta: dict | None = None,
                                  on_wait: WaitCallback | None = None, record: RecordCallback | None = None) -> Any:
        """One logical request returning parsed JSON. Malformed output gets ONE corrective re-ask."""
        meta = dict(meta or {})
        text = await self._answer(system, user, temperature, meta, on_wait, record)
        try:
            return extract_json(text)
        except ValueError:
            log.warning("[%s/%s] malformed JSON from model; asking once more", meta.get("session"), meta.get("agent"))
            meta["purpose"] = f"{meta.get('purpose', '')}:repair"
            text = await self._answer(system, user + "\n\nYour previous reply was not valid JSON. Return ONLY one "
                                      "valid JSON object, no prose, no code fences.", 0.0, meta, on_wait, record)
            try:
                return extract_json(text)
            except ValueError as e:
                raise LLMError("Model response parsing error: the LLM returned malformed JSON twice.",
                               category="malformed", provider=self.provider, model=self.model) from e

    complete_json = generate_structured  # backwards-compatible name

    async def health_check(self, record: RecordCallback | None = None) -> dict:
        """ONE tiny real request, no retries, no backoff. Used before any research starts."""
        t0 = time.monotonic()
        try:
            out = extract_json(await self._answer("You are a connectivity test. Reply in JSON.",
                                                  'Return exactly this JSON object: {"ok": true}', 0.0,
                                                  {"agent": "preflight", "purpose": "health"}, None, record,
                                                  max_retries=0))
            ok = isinstance(out, dict) and out.get("ok") is True
            return {"ok": ok, "category": "ok" if ok else "malformed",
                    "title": "LLM reachable" if ok else CATEGORY_TEXT["malformed"][0],
                    "message": f"{self.provider}/{self.model} answered" if ok else "Unexpected reply to the health check.",
                    "hint": "" if ok else CATEGORY_TEXT["malformed"][1],
                    "latency_ms": int((time.monotonic() - t0) * 1000), "retry_after_s": None}
        except LLMError as e:
            info = e.info()
            return {"ok": False, "category": e.category, "title": info["title"], "message": e.reason[:300],
                    "hint": info["hint"], "latency_ms": int((time.monotonic() - t0) * 1000),
                    "retry_after_s": e.retry_after}
        except ValueError:
            return {"ok": False, "category": "malformed", "title": CATEGORY_TEXT["malformed"][0],
                    "message": "Health check reply was not JSON.", "hint": CATEGORY_TEXT["malformed"][1],
                    "latency_ms": int((time.monotonic() - t0) * 1000), "retry_after_s": None}


class OpenAIProvider(LLMProvider):
    provider = "openai"

    def __init__(self, model: str, api_key: str, **kw):
        super().__init__(model, api_key, **kw)
        self.send_temperature = not _no_temperature("openai", model)

    def build_request(self, system, user, temperature):
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}]}
        if self.send_temperature:
            body["temperature"] = temperature
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return ((self.base_url or "https://api.openai.com/v1") + "/chat/completions",
                {"Authorization": f"Bearer {self._key}"}, body)

    def parse_text(self, data):
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


class GeminiProvider(LLMProvider):
    provider = "gemini"

    def build_request(self, system, user, temperature):
        gen: dict = {"temperature": temperature}
        if self.json_mode:
            gen["responseMimeType"] = "application/json"
        return (f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                {"x-goog-api-key": self._key},
                {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen})

    def parse_text(self, data):
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason}).", category="bad_request",
                           provider=self.provider, model=self.model)
        return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))


class AnthropicProvider(LLMProvider):
    provider = "anthropic"

    def build_request(self, system, user, temperature):
        body = {"model": self.model, "max_tokens": 8000,
                "system": system + "\nRespond with a single JSON object only.",
                "messages": [{"role": "user", "content": user}]}
        if self.send_temperature:
            body["temperature"] = temperature
        return ("https://api.anthropic.com/v1/messages",
                {"x-api-key": self._key, "anthropic-version": "2023-06-01"}, body)

    def parse_text(self, data):
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


PROVIDERS: dict[str, type[LLMProvider]] = {"openai": OpenAIProvider, "gemini": GeminiProvider,
                                           "anthropic": AnthropicProvider}


def LLMClient(provider: str, model: str, api_key: str, **kw) -> LLMProvider:
    """Construct the adapter for `provider` (kept as a function for backwards compatibility)."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported LLM provider '{provider}'")
    return PROVIDERS[provider](model, api_key, **kw)
