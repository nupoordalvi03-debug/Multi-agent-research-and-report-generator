"""Rule-based credibility tiering (spec §4). Transparent and explainable for a PBL demo."""
from __future__ import annotations

from .url_safety import domain_of

TIER1_DOMAINS = {
    "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "who.int",
    "cdc.gov", "nature.com", "science.org", "thelancet.com", "nejm.org", "bmj.com", "cell.com",
    "sciencedirect.com", "springer.com", "link.springer.com", "wiley.com", "onlinelibrary.wiley.com",
    "tandfonline.com", "sagepub.com", "journals.sagepub.com", "apa.org", "psycnet.apa.org",
    "frontiersin.org", "plos.org", "journals.plos.org", "pnas.org", "jamanetwork.com",
    "academic.oup.com", "cambridge.org", "mdpi.com", "doi.org", "iso.org", "nist.gov", "ietf.org",
    "w3.org", "ieee.org", "ieeexplore.ieee.org", "acm.org", "dl.acm.org", "oecd.org",
    "worldbank.org", "un.org", "europa.eu", "data.gov", "census.gov", "nasa.gov", "nsf.gov",
}
TIER2_DOMAINS = {
    "arxiv.org", "biorxiv.org", "medrxiv.org", "psyarxiv.com", "osf.io", "ssrn.com",
    "papers.ssrn.com", "researchgate.net", "semanticscholar.org", "scholar.archive.org",
    "core.ac.uk", "eric.ed.gov", "jstor.org", "aclanthology.org", "openreview.net",
    "proceedings.neurips.cc", "proceedings.mlr.press", "hal.science", "zenodo.org",
}
TIER3_DOMAINS = {
    "mckinsey.com", "gartner.com", "deloitte.com", "pwc.com", "bcg.com", "accenture.com",
    "forrester.com", "statista.com", "hbr.org", "reuters.com", "apnews.com", "bbc.com",
    "bbc.co.uk", "nytimes.com", "theguardian.com", "economist.com", "ft.com", "wsj.com",
    "scientificamerican.com", "newscientist.com", "theconversation.com", "wired.com",
    "technologyreview.com", "npr.org", "britannica.com", "psychologytoday.com",
}
TIER4_HINTS = ("medium.com", "substack.com", "reddit.com", "quora.com", "blogspot.",
               "wordpress.com", "tumblr.com", "stackexchange.com", "linkedin.com", "facebook.com",
               "pinterest.", "youtube.com", "tiktok.com", "twitter.com", "x.com")


def _match(domain: str, pool: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in pool)


def classify(url: str, *, from_research_index: bool = False) -> tuple[int, str]:
    """Return (tier, source_type)."""
    d = domain_of(url)
    if d.endswith("demo.invalid"):  # DEMO MODE: tier encoded in the simulated path
        from urllib.parse import urlparse
        kind = urlparse(url).path.strip("/").split("/")[0]
        return {"journal": (1, "demo_peer_reviewed"), "university": (1, "demo_university"),
                "preprint": (2, "demo_preprint"), "report": (3, "demo_report")}.get(kind, (4, "demo_blog"))
    if any(h in d for h in TIER4_HINTS):
        return 4, "blog/forum"
    if _match(d, {"ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "eric.ed.gov"}):
        return 1, "peer_reviewed"
    if d.endswith(".gov") or ".gov." in d or d.endswith(".mil") or _match(d, {"who.int", "un.org", "europa.eu", "oecd.org", "worldbank.org"}):
        return 1, "government"
    if _match(d, {"iso.org", "nist.gov", "ietf.org", "w3.org", "ieee.org"}):
        return 1, "standards"
    if _match(d, TIER1_DOMAINS):
        return 1, "peer_reviewed"
    if d.endswith(".edu") or ".ac." in d or ".edu." in d:
        return 1, "university"
    if _match(d, TIER2_DOMAINS):
        return 2, "academic_repository"
    if from_research_index:
        return 2, "academic_paper"
    if _match(d, TIER3_DOMAINS):
        return 3, "industry/news"
    if d.endswith(".org"):
        return 3, "organization"
    return 4, "web"
