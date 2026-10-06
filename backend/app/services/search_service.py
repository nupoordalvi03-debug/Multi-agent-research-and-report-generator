"""TinyFish Search API wrapper (GET https://api.search.tinyfish.ai)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ServiceError
from .http_util import request_json
from .rate_gate import get_gate

SEARCH_URL = "https://api.search.tinyfish.ai"


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    site_name: str = ""
    date: str = ""
    publisher: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    year: int | None = None
    cited_by_count: int | None = None
    pdf_url: str = ""
    domain_type: str = "web"
    position: int = 0


class TinyFishSearch:
    def __init__(self, api_key: str, *, url: str = SEARCH_URL, rpm: float = 25):
        self._key, self.url, self.rpm = api_key, url, rpm

    async def search(self, query: str, *, domain_type: str = "web", purpose: str = "",
                     include_domains: list[str] | None = None,
                     exclude_domains: list[str] | None = None) -> list[SearchHit]:
        params: dict = {"query": query[:400], "language": "en"}
        if domain_type != "web":
            params["domain_type"] = domain_type
        if purpose:
            params["purpose"] = purpose[:1900]
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        if exclude_domains:
            params["exclude_domains"] = ",".join(exclude_domains)
        gate = get_gate(("tinyfish-search",), self.rpm, 3)  # TinyFish default: 30 searches/min per key
        await gate.acquire()
        try:
            data = await request_json("GET", self.url, kind="Search", params=params, timeout=45,
                                      headers={"X-API-Key": self._key})
        except ServiceError as e:
            if e.status == 429:
                gate.cooldown(30)
            raise
        finally:
            gate.release()
        if not isinstance(data, dict):
            raise ServiceError("Search API returned an unexpected payload.", kind="search")
        hits = []
        for r in data.get("results") or []:
            if not r.get("url"):
                continue
            authors = r.get("authors") or []
            hits.append(SearchHit(
                title=(r.get("title") or "").strip(), url=r["url"].strip(),
                snippet=(r.get("snippet") or "").strip(), site_name=r.get("site_name") or "",
                date=r.get("date") or "", publisher=r.get("publisher") or "",
                authors=[a for a in authors if isinstance(a, str)], venue=r.get("venue") or "",
                year=r.get("year") if isinstance(r.get("year"), int) else None,
                cited_by_count=r.get("cited_by_count"), pdf_url=r.get("pdf_url") or "",
                domain_type=domain_type, position=r.get("position") or 0))
        return hits
