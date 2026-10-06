from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Evidence:
    evidence_id: str        # E001 ...
    source_id: str
    subtopic_id: str
    summary: str            # paraphrase of what the source says
    quote: str = ""         # verbatim excerpt; only kept if found in fetched text
    quote_verified: bool = False
    from_snippet_only: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Claim:
    claim_id: str           # C01 ...
    text: str
    subtopic_id: str
    status: str             # supported | partially_supported | contested | unsupported
    confidence: str         # high | medium | low
    evidence_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    notes: str = ""
    flags: list[str] = field(default_factory=list)  # single_source, low_tier_only, snippet_only ...
    include_in_report: bool = True

    def to_dict(self) -> dict:
        return asdict(self)
