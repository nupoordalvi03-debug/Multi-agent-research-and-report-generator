"""TinyFish Fetch API wrapper (POST https://api.fetch.tinyfish.ai). Max 10 URLs per request."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ServiceError
from .http_util import request_json
from .url_safety import is_safe_public_url

FETCH_URL = "https://api.fetch.tinyfish.ai"


@dataclass
class FetchedPage:
    url: str
    final_url: str
    ok: bool
    title: str = ""
    text: str = ""
    author: str = ""
    published_date: str = ""
    description: str = ""
    error: str = ""


class TinyFishFetch:
    def __init__(self, api_key: str, *, url: str = FETCH_URL):
        self._key, self.url = api_key, url

    async def fetch(self, urls: list[str], purpose: str = "") -> dict[str, FetchedPage]:
        out: dict[str, FetchedPage] = {}
        safe = []
        for u in urls:
            if is_safe_public_url(u):
                safe.append(u)
            else:
                out[u] = FetchedPage(url=u, final_url=u, ok=False, error="invalid_url")
        for i in range(0, len(safe), 10):
            batch = safe[i:i + 10]
            body: dict = {"urls": batch, "format": "markdown", "per_url_timeout_ms": 45000}
            if purpose.strip():
                body["purpose"] = purpose[:1900]
            try:
                data = await request_json("POST", self.url, kind="Fetch", json_body=body,
                                          timeout=150, attempts=2,
                                          headers={"X-API-Key": self._key})
            except ServiceError as e:
                for u in batch:
                    out[u] = FetchedPage(url=u, final_url=u, ok=False, error=str(e))
                continue
            for r in data.get("results") or []:
                text = r.get("text") if isinstance(r.get("text"), str) else ""
                out[r.get("url", "")] = FetchedPage(
                    url=r.get("url", ""), final_url=r.get("final_url") or r.get("url", ""),
                    ok=bool(text.strip()), title=r.get("title") or "", text=text or "",
                    author=r.get("author") or "", published_date=r.get("published_date") or "",
                    description=r.get("description") or "",
                    error="" if text.strip() else "empty_content")
            for e in data.get("errors") or []:
                out[e.get("url", "")] = FetchedPage(url=e.get("url", ""), final_url=e.get("url", ""),
                                                    ok=False, error=e.get("error", "fetch_error"))
        for u in urls:  # every requested URL gets an answer
            out.setdefault(u, FetchedPage(url=u, final_url=u, ok=False, error="no_result"))
        return out
