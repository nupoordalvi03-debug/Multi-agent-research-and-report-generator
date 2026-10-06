"""DEMO MODE ONLY — simulated search & fetch providers.

Every simulated source uses the reserved `.invalid` top-level domain (RFC 2606) and a
"[DEMO]" title prefix so it can never be mistaken for real research."""
from __future__ import annotations

import asyncio
import hashlib
import re

from .fetch_service import FetchedPage
from .search_service import SearchHit
from .text_util import keywords

KINDS = [  # (path prefix -> tier assigned by credibility.classify, label, publisher)
    ("journal", "Simulated peer-reviewed study", "Demo Journal of Applied Research"),
    ("university", "Simulated university research brief", "Demo State University"),
    ("preprint", "Simulated preprint", "Demo Preprint Server"),
    ("report", "Simulated institutional report", "Demo Research Institute"),
    ("blog", "Simulated blog post", "Demo Blog Network"),
]

POSITIVE = [
    "In this simulated dataset, participants in the {topic} condition showed a modest improvement in task performance compared with a silent control group.",
    "The simulated analysis reports that the {topic} condition was associated with better self-reported mood and focus during low-demand tasks.",
]
NEGATIVE = [
    "In contrast, the simulated experiment found that the {topic} condition did not significantly improve performance and was associated with reduced recall for complex material.",
    "This simulated review notes that the {topic} condition may impair comprehension when the background stimulus competes for the same cognitive resources.",
]
NEUTRAL = [
    "The simulated authors caution that individual differences, such as baseline preference and task familiarity, moderated outcomes in the {topic} condition.",
    "Sample sizes in this simulated work on the {topic} condition were small, so the reported effects should be interpreted with caution.",
]


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


class DemoSearch:
    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def search(self, query: str, *, domain_type: str = "web", **_kw) -> list[SearchHit]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if "zzznoresults" in query.lower():  # deterministic hook used by the empty-result test
            return []
        hits = []
        base = _h(query + domain_type)
        for i in range(4):
            kind, label, pub = KINDS[(base + i) % len(KINDS)]
            if domain_type == "research_paper" and kind == "blog":
                kind, label, pub = KINDS[0]
            topic = " ".join(keywords(query, 8)) or query
            hits.append(SearchHit(
                title=f"[DEMO] {label}: {topic}",
                url=f"https://demo.invalid/{kind}/{_slug(query)}-{i + 1}",
                snippet=f"[DEMO MODE] Simulated search result. Not a real publication.",
                site_name="demo.invalid", publisher=pub, domain_type=domain_type,
                authors=[f"Demo Author {chr(65 + (base + i) % 26)}"] if kind != "blog" else [],
                year=2018 + (base + i) % 7 if kind in ("journal", "preprint") else None,
                position=i + 1))
        return hits


class DemoFetch:
    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def fetch(self, urls: list[str], purpose: str = "") -> dict[str, FetchedPage]:
        if self.delay:
            await asyncio.sleep(self.delay * 3)
        out = {}
        for u in urls:
            seed = _h(u)
            if seed % 11 == 0:  # simulate a realistic fraction of fetch failures
                out[u] = FetchedPage(url=u, final_url=u, ok=False, error="simulated_timeout")
                continue
            topic_words = u.rsplit("/", 1)[-1].rsplit("-", 1)[0].replace("-", " ")
            topic = " ".join((keywords(topic_words, 6) or ["topic"])[:3])
            polarity = seed % 3
            paras = ["[DEMO MODE] This page is simulated and is not a real source."]
            pool = (POSITIVE if polarity == 0 else NEGATIVE if polarity == 1 else NEUTRAL)
            paras += [p.format(topic=topic) for p in pool]
            paras.append(NEUTRAL[seed % 2].format(topic=topic))
            out[u] = FetchedPage(url=u, final_url=u, ok=True, title="", text="\n\n".join(paras),
                                 published_date="", author="")
        return out
