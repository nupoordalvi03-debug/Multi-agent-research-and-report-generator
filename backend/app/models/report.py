from __future__ import annotations

from dataclasses import dataclass, field, asdict

REPORT_TEMPLATES: dict[str, list[str]] = {
    "Academic Research Report": [
        "Abstract", "Introduction", "Research Question", "Hypothesis", "Methodology", "Findings",
        "Discussion", "Benefits / Positive Findings", "Limitations / Risks", "Conclusion"],
    "Technical Report": [
        "Executive Summary", "Introduction", "Background", "Methodology", "Technical Findings",
        "Analysis", "Risks and Limitations", "Recommendations", "Conclusion"],
    "Literature Review": [
        "Abstract", "Introduction", "Scope and Search Method", "Thematic Review of the Literature",
        "Points of Agreement", "Points of Disagreement", "Research Gaps", "Conclusion"],
    "Comparative Analysis": [
        "Executive Summary", "Introduction", "Comparison Criteria", "Methodology",
        "Comparative Findings", "Trade-offs", "Limitations", "Conclusion"],
    "Executive Summary": [
        "Summary", "Key Findings", "Evidence Strength", "Risks and Caveats", "Recommendations"],
}


@dataclass
class ReportSection:
    heading: str
    content: str  # markdown-lite: paragraphs separated by blank lines, "- " bullets, **bold**


@dataclass
class Report:
    title: str
    sections: list[ReportSection]
    version: int = 1
    citation_map: dict[str, int] = field(default_factory=dict)  # S03 -> 1 (IEEE numbering)
    references: list[dict] = field(default_factory=list)      # [{source_id, number, text}]
    removed_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Report":
        return Report(
            title=d["title"],
            sections=[ReportSection(**s) for s in d["sections"]],
            version=d.get("version", 1),
            citation_map=d.get("citation_map", {}),
            references=d.get("references", []),
            removed_citations=d.get("removed_citations", []),
        )
