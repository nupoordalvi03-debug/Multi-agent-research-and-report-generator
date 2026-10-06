"""URL validation and normalisation (blocks private/loopback hosts and non-http schemes)."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "fbclid", "gclid", "ref", "mc_cid", "mc_eid"}


def is_safe_public_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or len(url) > 2000:
        return False
    host = p.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local") \
            or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    except ValueError:
        pass  # hostname, not an IP literal
    return True


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING_PARAMS])
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", query, ""))


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
