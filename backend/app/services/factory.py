"""Builds the configured clients. The ONLY place that maps settings -> provider/model/key."""
from __future__ import annotations

from ..config import Settings


def build_llm(s: Settings, **overrides):
    """Return an LLMProvider ONLY if the LLM is fully configured; otherwise None (nothing to retry)."""
    from .llm_service import PROVIDERS
    if not s.llm_status()["configured"]:
        return None
    kw = dict(base_url=s.openai_base_url if s.llm_provider == "openai" else "", timeout=s.llm_timeout_s,
              max_rpm=s.llm_max_rpm, max_concurrency=s.llm_max_concurrency, max_retries=s.llm_max_retries,
              max_wait_s=s.llm_max_wait_s)
    kw.update(overrides)
    return PROVIDERS[s.llm_provider](s.llm_model, s.llm_key, **kw)


def build_tinyfish(s: Settings):
    from .fetch_service import TinyFishFetch
    from .search_service import TinyFishSearch
    if not s.search_configured:
        return None, None
    return (TinyFishSearch(s.tinyfish_api_key, url=s.tinyfish_search_url, rpm=s.tinyfish_search_rpm),
            TinyFishFetch(s.tinyfish_api_key, url=s.tinyfish_fetch_url))


def log_config(log, s: Settings) -> None:
    """Startup configuration audit. Reports presence and SOURCE of each key, never its value."""
    from ..config import ENV_FILE_STATUS
    llm, search = s.llm_status(), s.search_status()
    mode = ("DEMO MODE — SIMULATED AI (DEMO_MODE=1)" if s.force_demo else
            "LIVE mode" if llm["configured"] and search["configured"] else "LLM NOT CONFIGURED — research is blocked until an LLM key is set"
            if not llm["configured"] else "SEARCH NOT CONFIGURED")
    log.info("Configuration file: %s (%s)", ENV_FILE_STATUS["path"], "found" if ENV_FILE_STATUS["exists"] else "NOT FOUND")
    log.info("Starting in %s", mode)
    log.info("LLM Provider: %s", f"configured ({llm['provider']}, via {llm['provider_source']})" if llm["provider"] else "not configured")
    log.info("LLM Model:    %s", f"configured ({llm['model']})" if llm["provider"] and llm["model"] else "not configured")
    log.info("LLM API Key:  %s", (f"present ({llm['key_var']}, from {llm['key_source']})" if llm["key_present"]
                                  else f"missing ({llm['key_var'] or 'OPENAI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY'})"))
    if llm["key_present"] and llm["key_source"] == "system environment":
        log.warning("LLM key %s comes from a SYSTEM environment variable, not backend/.env. If you did not intend to "
                    "use it, remove it from your system environment or set LLM_PROVIDER=none.", llm["key_var"])
    if llm["key_format_ok"] is False:
        log.warning("%s does not look like a %s key; the health check will confirm.", llm["key_var"], llm["provider_name"])
    if llm["problem"]:
        log.warning("LLM status: %s", llm["problem"])
    for var in llm.get("other_keys_present", []):
        log.info("Note: %s is also set but not used (LLM_PROVIDER=%s).", var, llm["provider"])
    log.info("Search Provider: TinyFish — key %s%s (used ONLY for web search/fetch, never as an LLM key)",
             "present" if search["key_present"] else "missing",
             f", from {search['key_source']}" if search["key_source"] else "")
    if search["problem"]:
        log.warning("Search status: %s", search["problem"])
    log.info("LLM limits: max %s requests/min, %d concurrent, up to %d attempts for temporary errors only "
             "(backoff 5-10s, 15-30s; Retry-After respected up to %ss); TinyFish search max %s/min",
             s.llm_max_rpm, s.llm_max_concurrency, s.llm_max_retries + 1, s.llm_max_wait_s, s.tinyfish_search_rpm)
    if s.tinyfish_search_url != "https://api.search.tinyfish.ai" or s.tinyfish_fetch_url != "https://api.fetch.tinyfish.ai":
        log.info("TinyFish endpoints overridden: search=%s fetch=%s", s.tinyfish_search_url, s.tinyfish_fetch_url)
    if s.openai_base_url and s.llm_provider == "openai":
        log.info("OpenAI-compatible base URL: %s", s.openai_base_url)
