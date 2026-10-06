# Multi-Agent Research & Report Generator

A working multi-agent AI research system (AI PBL project). Five agents — **Research Planner →
Researcher → Verifier → Report Writer → Report Critic** — pass structured outputs to each other,
use the real TinyFish Search and Fetch APIs, verify claims against fetched evidence, and produce a
cited report exportable as PDF, DOCX and Markdown.

## Quick start

### 1. Backend (Python 3.10+)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
# edit .env (see "API keys" below); with no keys the app runs in DEMO MODE
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend (Node 18+)

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173  (proxies /api to :8000)
```

### 3. Run the tests (no keys or network needed)

```bash
cd backend
python -m unittest discover -s tests -v
```

## API keys

Edit `backend/.env`. Keys are read only by the backend; the frontend never sees them.

| Variable | Required for live mode | Notes |
|---|---|---|
| `TINYFISH_API_KEY` | Yes | Free at https://agent.tinyfish.ai/api-keys (Search and Fetch are free) |
| `LLM_PROVIDER` | Recommended | `openai`, `gemini`, `anthropic` or `none`. Auto-selected only when exactly one LLM key is set |
| `LLM_MODEL` | No | Defaults: `gpt-4.1-mini`, `gemini-2.5-flash`, `claude-sonnet-4-5`. OpenAI reasoning models (`gpt-5*`, `o3`, `o4-mini`) also work: `temperature` is omitted automatically |
| `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` | One of them | Matches the provider |
| `OPENAI_BASE_URL` | No | Any OpenAI-compatible endpoint (Groq, OpenRouter, Ollama) |
| `DEMO_MODE` | No | `1` makes every session DEMO MODE — SIMULATED AI |
| `DEMO_DELAY_S` | No | Pacing of simulated steps so the demo is watchable (default 0.6) |
| `LLM_MAX_RPM`, `LLM_MAX_CONCURRENCY` | No | Shared LLM budget for all agents (default 8/min, 1 at a time). Keep below your plan's limit |
| `LLM_MAX_RETRIES`, `LLM_MAX_WAIT_S` | No | Retries for temporary errors only (2, i.e. 3 attempts) and the longest wait honoured (90s) |
| `TINYFISH_SEARCH_RPM` | No | Search pacing (25/min; TinyFish allows 30) |
| `MAX_CONCURRENT_SESSIONS`, `AUTO_RESUME` | No | Sessions run one at a time (1); resume after restart (1) |
| `STAGE_TIMEOUT_S`, `STAGE_MAX_ATTEMPTS`, `LLM_TIMEOUT_S` | No | Reliability limits (900s, 2, 120s) |

### Check your keys before a demo

```bash
cd backend
python -m app.check_live
```

The same check is the **Check connections** button on the Settings page. It makes one small real
request to TinyFish Search, TinyFish Fetch and the LLM. For each service it reports PASS, FAIL or
SKIP with a specific reason, such as a rejected key, an unavailable model or a rate limit.

### Search and LLM are separate services

| Service | Variable | Used for |
|---|---|---|
| TinyFish | `TINYFISH_API_KEY` | Web search and page fetch only. Never used as an LLM key; the app refuses it if you paste it into an LLM variable |
| LLM | `LLM_PROVIDER` + `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` | Planning, evidence extraction, verification, writing, critique |

**No LLM key? The app still starts.**
- The header shows **LLM not configured**.
- Settings shows the LLM card as "⚠ API key required" and the TinyFish card as "✓ Connected", and you can run test searches there.
- **Start research is blocked before the Research Planner**, with: *"Please configure an LLM API key before starting autonomous research."* No session is created and no LLM request is sent.
- You can still run an explicit **DEMO MODE — SIMULATED AI** session. It uses sample data, no LLM, `[DEMO]` titles and `.invalid` URLs, and is labelled on every screen and export.

**Before any research starts**, a preflight runs:
1. A configuration check with no network call. It catches a missing key, a placeholder like `your_key_here`, a reused TinyFish key, or several keys with no `LLM_PROVIDER` set.
2. A cached health check: one tiny real request, no retries.

Only if both pass does the Research Planner start.

**Where keys come from.** A non-empty value in `backend/.env` overrides a system environment variable. A key that exists only in your system environment (for example `OPENAI_API_KEY` set for another project) is still detected, but the startup log and Settings page say "from system environment" and warn you. Set `LLM_PROVIDER=none` to ignore it.

**Startup audit.** The backend logs `LLM Provider: configured / not configured`, `LLM Model: …` and `LLM API Key: present (VAR, from .env | system environment) / missing`. It never logs the key.

## How the five agents collaborate

```
USER QUERY ─▶ Orchestrator (state machine, not an AI agent)
   │
   ├─ 1 Research Planner  ── plan JSON: subtopics, queries, evidence criteria ──▶
   ├─ 2 Researcher        ── sources[] + evidence[] (quotes verified verbatim) ──▶
   ├─ 3 Verifier          ── claims[] linked to evidence, contradictions, gaps ──▶
   ├─ 4 Report Writer     ── report with [Sxx] markers → styled citations      ──▶
   └─ 5 Report Critic     ── score 0–100 + issues ─┐
                                 ▲                 │ fails? revise (max 2 cycles)
                                 └── Writer.revise ┘
                                         ▼
                                   FINAL REPORT ─▶ PDF / DOCX / Markdown
```

| Agent | Input | What it actually does | Output state |
|---|---|---|---|
| Research Planner | query, hypothesis, settings | LLM decomposes the question into 3–8 subtopics with deduplicated queries | `PLANNING_COMPLETED` |
| Researcher | plan | Runs TinyFish searches per subtopic (strategy depends on Source Target Mode), dedupes by normalized URL and title, ranks by credibility tier + relevance, fetches pages (≤10 per call), extracts evidence with the LLM, stops when the target is met (max two rounds) | `RESEARCH_COMPLETED` |
| Verifier | plan, sources, evidence | LLM judges support per subtopic; code then enforces rules: unknown evidence IDs are dropped, claims without evidence become `unsupported` and are removed, single-source / low-tier / snippet-only claims are downgraded | `VERIFICATION_COMPLETED` |
| Report Writer | verified claims, contradictions, gaps, methodology facts | Writes the selected report structure citing `[Sxx]`; code strips citations to anything not attached to a verified claim and builds references from collected metadata | `REPORT_COMPLETED` |
| Report Critic | report, verified claims | LLM review plus automatic checks (empty sections, uncited findings, invalid citations, repetition, length); pass = score ≥ 70 with no critical issue | `CRITIQUE_COMPLETED` |

### Anti-fabrication guarantees (enforced in code, not just prompts)

- Source metadata (authors, publisher, date) is copied only from TinyFish responses; the LLM never supplies it. Missing dates show as "unavailable" / "n.d."
- An evidence quote is kept only if it appears verbatim (after whitespace/markdown normalisation) in the fetched text.
- A claim must cite evidence IDs that exist; otherwise it is marked unsupported and excluded.
- The report can only cite sources attached to included claims; other markers are removed and the critic is told.
- References are generated by code from collected sources, so every reference is a real retrieved URL.

## LLM rate limits, quotas and request budget

All five agents call the LLM through one service (`app/services/llm_service.py`). It is the only
place that sends requests, waits or retries.

**Request budget.** A Standard run makes about 7 LLM requests in total:

| Agent | Requests |
|---|---|
| Research Planner | 1 |
| Researcher | 1 per 4 sources |
| Verifier | 1 for all subtopics |
| Report Writer | 1, plus 1 per revision |
| Report Critic | 1 per draft |

Progress messages, status updates and formatting never use the LLM.

**Pacing.** One shared budget (`LLM_MAX_RPM`, `LLM_MAX_CONCURRENCY`) covers every agent and every session. Requests are spaced so a free tier is not exceeded in the first place.

**What happens on each kind of error:**

| Situation | Detected from | Behaviour |
|---|---|---|
| Temporary rate limit | HTTP 429 per-minute | Waits for `Retry-After` (or Gemini `RetryInfo`), otherwise backoff with jitter: 5–10s, then 15–30s. At most 3 attempts in total, then fails gracefully. All other callers pause too (shared cooldown). The UI shows a live countdown |
| Missing key / placeholder / TinyFish key used as LLM key | Configuration check (no network) | Blocked before the Planner, 0 requests |
| Daily/monthly quota exhausted | Gemini `PerDay` quota id, "requests per day", quota value 0 | 1 request, no retries; explains that waiting won't help |
| Billing / credits | OpenAI `insufficient_quota` (arrives as HTTP 429!), Anthropic "credit balance is too low" | 1 request, no retries |
| Invalid API key | 401, "API key not valid" | 1 request, no retries |
| Model unavailable | 404 / model not found | 1 request; asks you to set `LLM_MODEL` |
| Network / timeout / provider overload | Connection errors, 5xx, 529 | Limited retries with backoff |

**No double retrying.** When the LLM service gives up, the orchestrator does not re-run the stage. The UI shows the category, provider, agent, attempts and when a retry is possible, plus a **Retry research** button. The button resumes from the failed agent and reuses completed ones.

**One workflow per research.**
- Polling `/status` is read-only.
- Each new-research form sends a `client_request_id`, so a double-click or resent request returns the same session. The form also has a synchronous guard.
- A per-session database lease means only one backend process can run a session. A second server, or an orphaned `--reload` worker, cannot run it twice.
- Within a process, one session plus one agent equals at most one execution.

**Audit trail.** Every real request is written to the `llm_calls` table and logged, like this:

```
llm: [089c9bd46d07/planner/plan] openai/gpt-4.1-mini attempt=1 -> HTTP 429 rate_limit retry-after=7s; waiting 7.6s before retry 1/3
llm: [089c9bd46d07/planner/plan] openai/gpt-4.1-mini attempt=2 -> 200 in 812ms
```

You can view the trail with `GET /api/research/{id}/llm-calls`. The startup log states which provider, model and key variable all agents use, and the configured limits.

**If you keep hitting limits on a free tier:**
- Lower `LLM_MAX_RPM` (e.g. 5).
- Use Quick depth.
- Avoid running `check_live` repeatedly; each run uses a request.
- Or use a paid key.

## Reliability design

- **Backend is the source of truth.** Each stage runs through `Orchestrator._stage`: timeout (`asyncio.wait_for`), bounded retries (non-retryable errors such as a bad API key or "no results" fail immediately), persisted output, then an explicit hand-off to the next stage.
- **100% means done.** Agents can report at most 99% progress; 100% is only set by the orchestrator after the agent returns a successful structured result.
- **Recovery.** All outputs are stored in SQLite. Refreshing the page reloads state from the API. If the server restarts mid-run, `resume_interrupted()` continues from the last saved stage without re-running completed agents.
- **Retry.** `POST /api/research/{id}/retry` resumes from the failed stage, reusing earlier outputs.
- **Model differences handled.** Models that reject `temperature` and OpenAI-compatible endpoints without JSON mode are detected from the 400 response, then the request is retried once without that parameter. A missing model produces a "set LLM_MODEL" message instead of repeated retries.
- **No infinite loops.** At most 2 revision cycles; the best-scoring draft is finalized.
- **Rate limits.** HTTP 429/5xx are retried with backoff; LLM calls are limited to 3 concurrent; search concurrency is 3.

## Project structure

```
backend/
  app/
    main.py                 FastAPI app, CORS, startup recovery
    check_live.py           `python -m app.check_live` connection check
    config.py               environment settings (.env loader, demo detection)
    db.py                   SQLite schema and persistence
    agents/                 base.py, planner.py, researcher.py, verifier.py, writer.py, critic.py
    services/               search_service.py, fetch_service.py (TinyFish), llm_service.py,
                            citation_service.py (IEEE/APA/MLA), report_service.py (exports),
                            credibility.py (tiering), url_safety.py, demo_data.py, http_util.py
    workflow/               orchestrator.py, state.py
    models/                 research.py, source.py, evidence.py, report.py
    api/                    routes.py (FastAPI), handlers.py (framework-independent logic)
  tests/test_workflow.py    12 tests, no network needed
  tests/stdlib_server.py    test harness serving the same handlers without FastAPI
frontend/
  src/pages/                Home, NewResearch, Workspace, History, Settings
  src/components/           AgentRelay, Views (plan, activity, sources, claims, report, critique),
                            TraceDrawer, RichText, Badges
  src/lib/                  api client, types, hash router, formatting
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/research` | Start a session (validated, rate-limited to 10/min per client) |
| GET | `/api/research/history` | All sessions |
| GET | `/api/research/{id}` | Session, plan, agents, counts, final critique |
| GET | `/api/research/{id}/status?after=N` | Live polling: state, agents, new events |
| GET | `/api/research/{id}/sources` | Sources with tiers, retrieval status, citation numbers |
| GET | `/api/research/{id}/evidence` | Evidence, claims, contradictions, gaps |
| GET | `/api/research/{id}/report` | Final (or in-progress) report, all critiques |
| POST | `/api/research/{id}/retry` | Resume from the failed stage |
| GET | `/api/research/{id}/export/{pdf,docx,markdown}` | Downloads (after completion) |
| POST | `/api/diagnostics` | Connection check (one tiny real call per service) |
| GET | `/api/settings`, `/api/health` | Safe config view, health check |

## Testing performed

Automated (`python -m unittest discover -s tests -v`, 32 tests, all passing).

No-LLM-key tests:
- With a TinyFish key and no LLM key, research is blocked with zero LLM requests and zero sessions, while search still works.
- Placeholder keys, a TinyFish key in an LLM variable, ambiguous keys and `LLM_PROVIDER=none` are all detected without network access.
- An invalid key is blocked by the preflight after one request, even across repeated clicks.
- A live session resumed after its key was removed fails cleanly without running the Planner.
- The startup audit reports each key's source and never its value. Rate-limit tests use
provider-accurate fakes in `tests/fake_providers.py`:

- A simulated 429 with `Retry-After: 7` waits 7.5–9 s.
- With no Retry-After header, the waits fall within the jittered backoff ranges.
- Gemini's `RetryInfo` delay is honoured.
- A persistent 429 makes exactly 4 requests, never more.
- A provider asking for a 10-minute wait is reported immediately instead of blocking.
- A shared cooldown pauses concurrent callers.
- 200+ status polls create zero LLM requests.
- A duplicate submit creates one workflow.
- A second backend process cannot run a leased session.
- Daily quota, quota of 0, billing and invalid keys each make exactly 1 request.
- Retry resumes from the failed agent without re-running completed ones.
- A complete live workflow makes exactly 7 LLM requests.

End-to-end live-mode rig: `tests/fake_provider_server.py` serves the OpenAI and TinyFish wire formats with scripted 429/quota responses. The real backend ran against it over HTTP, driven from a browser using the development build with React StrictMode.

Earlier coverage:

1. Full demo workflow: all five completion states, every reference maps to a collected source, PDF/DOCX/Markdown exports valid.
2. Live-mode code paths with a scripted fake LLM and fake TinyFish that deliberately misbehave: a fabricated quote is discarded, an invented claim citing a non-existent evidence ID is marked unsupported and excluded, a bogus `[S99]` citation is stripped and flagged by the critic, a failing first critique triggers exactly one revision, duplicate planner queries are removed, a blog source is excluded, and author/date metadata comes only from the API.
3. Search API failure (HTTP 503 on every call): bounded retries, clear error, then a successful retry once the API recovers, without re-running the planner.
4. Fetch API failure: falls back to search snippets, with resulting claims flagged `snippet_only` and low confidence.
5. Empty search results: fails immediately with an actionable message (no pointless retries).
6. Stage timeout: a hanging search fails cleanly after the timeout.
7. Server crash mid-research: a new server instance resumes and completes without re-running the planner.
8. LLM compatibility against reproduced provider errors:
   - GPT-5 models get no `temperature`; `gpt-5-chat-latest` keeps it.
   - An unexpected "temperature unsupported" 400 is retried once without it, and the client remembers this.
   - A local OpenAI-compatible server without JSON mode works.
   - An unknown model gives an actionable error.
   - The connection check reports pass, fail and skipped correctly.
9. Input validation, URL safety (private IPs, `file:`, metadata endpoints), JSON repair of malformed model output, IEEE/APA/MLA formatting, and that settings never expose key values.

Browser tests (Playwright against the real orchestrator in demo mode): created the PBL demo query
from the form, watched agents progress, **refreshed mid-research and confirmed recovery**, opened
every workspace tab, traced a citation to its source and evidence, downloaded all three exports,
checked history and settings, triggered a failure and used the Retry button, checked form
validation, and confirmed no horizontal overflow at 390px width.

## Known limitations

- Provider rate limits differ per account and change over time; the defaults suit a free tier. Raise `LLM_MAX_RPM` for paid keys.

- The live TinyFish + LLM path was developed against TinyFish's published API docs and tested with fakes; run the PBL demo question once with your keys to confirm end-to-end (see below).
- Evidence quality depends on the LLM; quotes are verified mechanically, but paraphrased summaries are only as faithful as the model (the Verifier flags claims lacking verbatim quotes).
- Credibility tiers are rule-based by domain; a university blog would be rated like a university paper.
- Paywalled pages often return only abstracts or fail to fetch; the system falls back to snippets and says so.
- Live updates use polling (1.5s), which is simple and reliable but not instant.
- Single-process design: sessions run as asyncio tasks inside the API server.

## First live run checklist

1. Put `TINYFISH_API_KEY` and one LLM key in `backend/.env`, run `python -m app.check_live` until all three lines say PASS, then restart the backend. The header should say "Live".
2. New research → "Use the PBL demo question" → Hybrid, Academic Research Report, IEEE, Standard → Start.
3. Watch the Agent workflow panel; check the Sources tab for real URLs and tiers, and Evidence & claims for verified quotes.
4. If a stage fails, the error explains why (bad key, rate limit, no results) and Retry resumes from that stage.

## Future improvements

Server-sent events instead of polling; DOI/Crossref lookup to enrich academic metadata; PDF
full-text parsing for open-access papers; embedding-based relevance ranking; per-claim NLI
entailment check as a second verifier pass; user-editable report sections; Docker compose.
