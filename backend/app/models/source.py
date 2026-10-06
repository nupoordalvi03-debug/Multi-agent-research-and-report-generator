from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Source:
    source_id: str              # S01, S02 ...
    title: str
    url: str
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    publication_date: str = ""  # only ever copied from API metadata or verified page text
    source_type: str = "web"    # peer_reviewed | government | university | standards | preprint | news | industry | blog | web | demo
    credibility_tier: int = 4
    retrieved_at: str = ""
    relevance_score: float = 0.0
    subtopic_ids: list[str] = field(default_factory=list)
    snippet: str = ""
    fetch_status: str = "pending"  # fetched | snippet_only | failed
    fetch_error: str = ""
    content_chars: int = 0
    is_demo: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
