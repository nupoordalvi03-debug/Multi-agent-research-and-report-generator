"""Single HTTP entry point for all external calls.

Uses httpx (installed from requirements.txt). If httpx is missing it falls back to the standard
library so the exact same request/response handling can still run (used by the sandbox test rig).
Raises TimeoutError for timeouts and ConnectionError for network failures; never raises on HTTP
status codes — callers classify status codes themselves."""
from __future__ import annotations

import asyncio
import json as _json
import socket
import urllib.error
import urllib.parse
import urllib.request


async def http_request(method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
                       json_body=None, timeout: float = 60) -> tuple[int, dict[str, str], str]:
    headers = dict(headers or {})
    try:
        import httpx
    except ImportError:
        httpx = None
    if httpx is not None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(method, url, headers=headers, params=params, json=json_body)
            return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.text
        except httpx.TimeoutException as e:
            raise TimeoutError(f"timed out after {timeout:.0f}s") from e
        except httpx.HTTPError as e:
            raise ConnectionError(type(e).__name__) from e
    return await asyncio.to_thread(_urllib_request, method, url, headers, params, json_body, timeout)


def _urllib_request(method, url, headers, params, json_body, timeout):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    if json_body is not None:
        data = _json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read().decode("utf-8", "replace")
    except (socket.timeout, TimeoutError) as e:
        raise TimeoutError(f"timed out after {timeout:.0f}s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(f"timed out after {timeout:.0f}s") from e
        raise ConnectionError(str(e.reason)[:120]) from e
