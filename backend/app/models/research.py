from __future__ import annotations

from dataclasses import dataclass, field, asdict

SOURCE_MODES = ["Hybrid", "Academic", "Web", "Industry Analysis", "Official Standards & Whitepapers"]
REPORT_TYPES = ["Academic Research Report", "Technical Report", "Literature Review",
                "Comparative Analysis", "Executive Summary"]
CITATION_STYLES = ["IEEE", "APA", "MLA"]
DEPTHS = ["Quick", "Standard", "Comprehensive"]

# Research budget per depth (spec §11)
DEPTH_LIMITS = {
    "Quick": {"subtopics": (3, 3), "queries_per_subtopic": 1, "target_sources": 6, "max_sources": 8},
    "Standard": {"subtopics": (3, 5), "queries_per_subtopic": 2, "target_sources": 8, "max_sources": 10},
    "Comprehensive": {"subtopics": (5, 8), "queries_per_subtopic": 2, "target_sources": 15, "max_sources": 20},
}


@dataclass
class ResearchRequest:
    query: str
    hypothesis: str = ""
    source_mode: str = "Hybrid"
    report_type: str = "Academic Research Report"
    citation_style: str = "IEEE"
    depth: str = "Standard"

    def validate(self) -> list[str]:
        errors = []
        q = self.query.strip()
        if len(q) < 10:
            errors.append("Research query must be at least 10 characters.")
        if len(q) > 500:
            errors.append("Research query must be at most 500 characters.")
        if len(self.hypothesis) > 1000:
            errors.append("Hypothesis must be at most 1000 characters.")
        for value, allowed, name in [
            (self.source_mode, SOURCE_MODES, "source_mode"),
            (self.report_type, REPORT_TYPES, "report_type"),
            (self.citation_style, CITATION_STYLES, "citation_style"),
            (self.depth, DEPTHS, "depth"),
        ]:
            if value not in allowed:
                errors.append(f"{name} must be one of: {', '.join(allowed)}")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SubTopic:
    id: str
    title: str
    rationale: str
    queries: list[str]
    evidence_needed: str
    source_types: list[str] = field(default_factory=list)


@dataclass
class ResearchPlan:
    research_question: str
    key_concepts: list[str]
    subtopics: list[SubTopic]
    required_source_types: list[str]
    evidence_criteria: list[str]
    scope_notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ResearchPlan":
        return ResearchPlan(
            research_question=d["research_question"],
            key_concepts=list(d.get("key_concepts", [])),
            subtopics=[SubTopic(**s) for s in d["subtopics"]],
            required_source_types=list(d.get("required_source_types", [])),
            evidence_criteria=list(d.get("evidence_criteria", [])),
            scope_notes=d.get("scope_notes", ""),
        )
