"""HTTP helper for the TinyFish APIs: classified errors, Retry-After aware backoff with jitter."""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from .errors import ServiceError
from .transport import http_request

RETRY_STATUSES = {429, 500, 502, 503, 504}
BACKOFF = [(3, 6), (10, 20)]
MAX_WAIT_S = 60


def parse_retry_after(headers: dict) -> float | None:
    v = (headers or {}).get("retry-after")
    if not v:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        try:
            return max(0.0, (parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


async def request_json(method: str, url: str, *, kind: str, headers: dict | None = None,
                       params: dict | None = None, json_body: Any = None, timeout: float = 60,
                       attempts: int = 3, sleep=asyncio.sleep) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            status, rh, text = await http_request(method, url, headers=headers, params=params,
                                                  json_body=json_body, timeout=timeout)
        except TimeoutError as e:
            if attempt < attempts:
                await sleep(random.uniform(*BACKOFF[min(attempt, len(BACKOFF)) - 1]))
                continue
            raise ServiceError(f"{kind} API timed out after {timeout:.0f}s.", kind="timeout") from e
        except ConnectionError as e:
            if attempt < attempts:
                await sleep(random.uniform(*BACKOFF[min(attempt, len(BACKOFF)) - 1]))
                continue
            raise ServiceError(f"Network error contacting {kind} API ({e}). Check your internet connection.",
                               kind="network") from e
        if status in RETRY_STATUSES and attempt < attempts:
            ra = parse_retry_after(rh)
            wait = ra + random.uniform(0.5, 2) if ra is not None else random.uniform(*BACKOFF[min(attempt, len(BACKOFF)) - 1])
            if wait <= MAX_WAIT_S:
                await sleep(wait)
                continue
        if status == 401:
            raise ServiceError(f"{kind} API rejected the API key (401). Check your .env.",
                               retryable=False, kind="config", status=401)
        if status == 429:
            raise ServiceError(f"{kind} API rate limit reached after {attempts} attempt(s).",
                               kind="rate_limit", status=429, detail=text[:1000])
        if status >= 400:
            detail = text[:200].replace("\n", " ")
            raise ServiceError(f"{kind} API returned HTTP {status}: {detail}",
                               retryable=status >= 500, kind=kind, status=status, detail=text[:1000])
        try:
            return json.loads(text)
        except ValueError as e:
            raise ServiceError(f"{kind} API returned a non-JSON response.", kind=kind) from e
    raise ServiceError(f"{kind} API failed.", kind=kind)
