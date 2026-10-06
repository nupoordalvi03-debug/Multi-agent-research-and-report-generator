"""Environment-based configuration. No secrets are ever hardcoded or logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


ENV_FILE = BACKEND_DIR / ".env"
# Where each variable came from: ".env" | "system environment" (filled in by _load_dotenv)
ENV_SOURCES: dict[str, str] = {}
ENV_FILE_STATUS = {"exists": False, "path": str(ENV_FILE)}


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader.
    - Reads UTF-8 with or without BOM (Windows Notepad adds one, which used to hide the first variable).
    - Strips inline comments (`KEY=value  # note`) and an optional `export ` prefix.
    - A non-empty value in backend/.env overrides a system environment variable of the same name, because
      .env is this project's explicit configuration. Empty values in .env do not erase system values, but
      the source of every key is recorded and reported, so a stray system key can never be used silently."""
    for k in os.environ:
        ENV_SOURCES[k] = "system environment"
    if not path.exists():
        return
    ENV_FILE_STATUS["exists"] = True
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[:1] in value[1:]:
            value = value[1:value.index(value[0], 1)]  # quoted: keep everything inside the quotes
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
            if value.startswith("#"):
                value = ""
        if not key:
            continue
        if value:
            os.environ[key] = value
            ENV_SOURCES[key] = ".env"
        elif key not in os.environ:
            os.environ[key] = ""


_load_dotenv(ENV_FILE)

PLACEHOLDER_HINTS = ("your", "paste", "here", "xxxx", "<", ">", "changeme", "example", "replace", "api_key", "insert")
KEY_VARS = {"openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
KEY_FORMATS = {"openai": ("sk-",), "gemini": ("AIza",), "anthropic": ("sk-ant-",)}
PROVIDER_NAMES = {"openai": "OpenAI", "gemini": "Google Gemini", "anthropic": "Anthropic"}


def looks_like_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return bool(v) and (len(v) < 20 or any(h in v for h in PLACEHOLDER_HINTS))


DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-sonnet-4-5",
}


@dataclass(frozen=True)
class Settings:
    tinyfish_api_key: str
    llm_provider: str
    llm_model: str
    openai_api_key: str
    openai_base_url: str
    google_api_key: str
    anthropic_api_key: str
    database_path: str
    force_demo: bool
    cors_origins: list[str]
    # Stage limits
    stage_timeout_s: int
    stage_max_attempts: int
    llm_timeout_s: int
    max_concurrent_sessions: int
    demo_delay_s: float = 0.0
    provider_source: str = ""        # how the provider was chosen (for startup log / settings page)
    llm_keys_present: tuple = ()
    llm_max_rpm: float = 8
    llm_max_concurrency: int = 1
    llm_max_retries: int = 3
    llm_max_wait_s: float = 90
    tinyfish_search_rpm: float = 25
    tinyfish_search_url: str = "https://api.search.tinyfish.ai"
    tinyfish_fetch_url: str = "https://api.fetch.tinyfish.ai"
    auto_resume: bool = True
    llm_disabled: bool = False             # LLM_PROVIDER=none
    key_sources: tuple = ()                # ((VAR, source), ...)

    @property
    def llm_key(self) -> str:
        return {"openai": self.openai_api_key, "gemini": self.google_api_key,
                "anthropic": self.anthropic_api_key}.get(self.llm_provider, "")

    def _source(self, var: str) -> str | None:
        return dict(self.key_sources).get(var)

    def llm_status(self) -> dict:
        """Configuration audit for the LLM (no network). `configured` is True only when a provider is
        selected AND its own key is present AND the key is not a placeholder / the TinyFish key."""
        p = self.llm_provider
        key_var = KEY_VARS.get(p)
        key = self.llm_key
        st = {"configured": False, "provider": p or None, "provider_name": PROVIDER_NAMES.get(p),
              "model": self.llm_model or None, "key_var": key_var, "key_present": bool(key),
              "key_source": self._source(key_var) if key_var and key else None,
              "key_format_ok": None, "provider_source": self.provider_source or None,
              "other_keys_present": [KEY_VARS[x] for x in self.llm_keys_present if x != p],
              "problem": None, "problem_code": None}

        def bad(code: str, msg: str) -> dict:
            return {**st, "problem_code": code, "problem": msg}
        if self.llm_disabled:
            return bad("disabled", "LLM_PROVIDER=none: the LLM is switched off in backend/.env.")
        if not p:
            if len(self.llm_keys_present) > 1:
                return bad("provider_ambiguous", "Several LLM keys are set (" + ", ".join(KEY_VARS[x] for x in self.llm_keys_present)
                           + "). Set LLM_PROVIDER to choose one.")
            return bad("no_key", "No LLM API key configured. Set OPENAI_API_KEY, GOOGLE_API_KEY or ANTHROPIC_API_KEY "
                                 "(and LLM_PROVIDER) in backend/.env.")
        if p not in KEY_VARS:
            return bad("provider_invalid", f"LLM_PROVIDER='{p}' is not supported. Use openai, gemini or anthropic.")
        if not key:
            return bad("no_key", f"No LLM API key configured: LLM_PROVIDER={p} but {key_var} is empty.")
        if self.tinyfish_api_key and key.strip() == self.tinyfish_api_key.strip():
            return bad("tinyfish_key_reused", f"{key_var} contains your TinyFish key. TinyFish is the web search "
                                              f"provider, not an LLM; {key_var} needs a {PROVIDER_NAMES[p]} key.")
        if looks_like_placeholder(key):
            return bad("placeholder_key", f"{key_var} looks like a placeholder, not a real key. Paste your real "
                                          f"{PROVIDER_NAMES[p]} API key.")
        st["key_format_ok"] = key.startswith(KEY_FORMATS[p]) or (p == "openai" and bool(self.openai_base_url))
        st["configured"] = True
        return st

    def search_status(self) -> dict:
        k = self.tinyfish_api_key
        st = {"configured": False, "provider": "TinyFish", "key_var": "TINYFISH_API_KEY", "key_present": bool(k),
              "key_source": self._source("TINYFISH_API_KEY") if k else None, "problem": None, "problem_code": None}
        if not k:
            return {**st, "problem_code": "no_key", "problem": "TINYFISH_API_KEY is not set (needed for web search/fetch)."}
        if looks_like_placeholder(k):
            return {**st, "problem_code": "placeholder_key", "problem": "TINYFISH_API_KEY looks like a placeholder."}
        return {**st, "configured": True}

    @property
    def llm_configured(self) -> bool:
        return self.llm_status()["configured"]

    @property
    def search_configured(self) -> bool:
        return self.search_status()["configured"]

    def missing_for_live(self) -> list[str]:
        out = []
        if not self.search_configured:
            out.append(self.search_status()["problem"])
        if not self.llm_configured:
            out.append(self.llm_status()["problem"])
        return out

    @property
    def demo_mode(self) -> bool:
        """Demo is never an automatic fallback any more: only DEMO_MODE=1 (or an explicit per-session choice)."""
        return self.force_demo

    def public_view(self) -> dict:
        """Safe-to-expose settings: statuses and booleans only, never key values."""
        llm, search = self.llm_status(), self.search_status()
        return {
            "mode": "demo" if self.force_demo else ("live" if llm["configured"] and search["configured"] else "not_configured"),
            "force_demo": self.force_demo,
            "llm": llm, "search": search,
            "missing_for_live": self.missing_for_live(),
            "env_file_found": ENV_FILE_STATUS["exists"],
            "llm_provider": self.llm_provider or None,
            "llm_model": self.llm_model or None,
            "provider_source": self.provider_source,
            "rate_limits": {"llm_max_rpm": self.llm_max_rpm, "llm_max_concurrency": self.llm_max_concurrency,
                            "llm_max_retries": self.llm_max_retries, "llm_max_wait_s": self.llm_max_wait_s,
                            "tinyfish_search_rpm": self.tinyfish_search_rpm},
            "keys_configured": {
                "TINYFISH_API_KEY": bool(self.tinyfish_api_key),
                "OPENAI_API_KEY": bool(self.openai_api_key),
                "GOOGLE_API_KEY": bool(self.google_api_key),
                "ANTHROPIC_API_KEY": bool(self.anthropic_api_key),
            },
            "openai_base_url_custom": bool(self.openai_base_url),
            "stage_timeout_s": self.stage_timeout_s,
            "stage_max_attempts": self.stage_max_attempts,
        }


def load_settings() -> Settings:
    keys = tuple(p for p, var in (("openai", "OPENAI_API_KEY"), ("gemini", "GOOGLE_API_KEY"),
                                  ("anthropic", "ANTHROPIC_API_KEY")) if os.getenv(var, "").strip())
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    disabled = provider in {"none", "off", "disabled"}
    if disabled:
        provider = ""
    source = "LLM_PROVIDER" if provider else ""
    if not provider and not disabled and len(keys) == 1:  # exactly one key: unambiguous
        provider, source = keys[0], f"auto (only {keys[0]} key set)"
    # with several keys and no LLM_PROVIDER we do NOT guess; missing_for_live() reports it
    model_env = os.getenv("LLM_MODEL", "").strip()
    model = model_env or DEFAULT_MODELS.get(provider, "")
    if provider and not model_env:
        source += ", default model"
    return Settings(
        tinyfish_api_key=os.getenv("TINYFISH_API_KEY", "").strip(),
        llm_provider=provider,
        llm_model=model,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
        google_api_key=os.getenv("GOOGLE_API_KEY", "").strip(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        database_path=os.getenv("DATABASE_PATH", str(BACKEND_DIR / "data" / "research.db")),
        force_demo=os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"},
        cors_origins=[o.strip() for o in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
        stage_timeout_s=int(os.getenv("STAGE_TIMEOUT_S", "900")),
        stage_max_attempts=int(os.getenv("STAGE_MAX_ATTEMPTS", "2")),
        llm_timeout_s=int(os.getenv("LLM_TIMEOUT_S", "120")),
        max_concurrent_sessions=int(os.getenv("MAX_CONCURRENT_SESSIONS", "1")),
        demo_delay_s=float(os.getenv("DEMO_DELAY_S", "0.6")),
        provider_source=source,
        llm_keys_present=keys,
        llm_max_rpm=float(os.getenv("LLM_MAX_RPM", "8")),
        llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "1")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        llm_max_wait_s=float(os.getenv("LLM_MAX_WAIT_S", "90")),
        tinyfish_search_rpm=float(os.getenv("TINYFISH_SEARCH_RPM", "25")),
        tinyfish_search_url=os.getenv("TINYFISH_SEARCH_URL", "https://api.search.tinyfish.ai").strip(),
        tinyfish_fetch_url=os.getenv("TINYFISH_FETCH_URL", "https://api.fetch.tinyfish.ai").strip(),
        auto_resume=os.getenv("AUTO_RESUME", "1").strip().lower() not in {"0", "false", "no"},
        llm_disabled=disabled,
        key_sources=tuple((v, ENV_SOURCES.get(v, "system environment")) for v in
                          ("TINYFISH_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")
                          if os.getenv(v, "").strip()),
    )


settings = load_settings()
