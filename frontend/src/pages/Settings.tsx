import { FormEvent, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { AppSettings, CheckResult, SearchTest } from "../lib/types";

function Row({ k, v }: { k: string; v: ReactNode }) {
  return <tr><th>{k}</th><td>{v}</td></tr>;
}

function StatusLine({ ok, warn, text }: { ok: boolean; warn?: boolean; text: string }) {
  return <span className={ok ? "st-ok" : warn ? "st-warn" : "st-bad"}>{ok ? "✓" : "⚠"} {text}</span>;
}

function LlmCard({ s, onChanged }: { s: AppSettings; onChanged: (r: CheckResult) => void }) {
  const l = s.llm;
  const [res, setRes] = useState<CheckResult | null>(s.llm_last_check);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const check = async () => {
    setBusy(true); setErr(null);
    try { const d = await api.diagnostics("llm"); setRes(d.llm); if (d.llm) onChanged(d.llm); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const status = !l.configured
    ? <StatusLine ok={false} warn text={l.problem_code === "no_key" ? "API key required" : "Configuration problem"} />
    : res ? <StatusLine ok={res.ok} text={res.ok ? "Connected" : res.title || "Not reachable"} />
    : <StatusLine ok={false} warn text="Configured, not checked yet" />;
  return (
    <section className="cfg-card">
      <h2>LLM <span className="muted">(planning, verification, writing, critique)</span></h2>
      <table className="table compact"><tbody>
        <Row k="Provider" v={l.provider_name ?? "Not configured"} />
        <Row k="Model" v={l.provider ? l.model : "Not configured"} />
        <Row k="API key" v={l.key_present ? `Present (${l.key_var}, from ${l.key_source})` : "Missing"} />
        <Row k="Status" v={status} />
      </tbody></table>
      {l.problem && <p className="warn-text">{l.problem}</p>}
      {l.key_present && l.key_source === "system environment" && (
        <p className="warn-text">This key was not read from backend/.env; it comes from a system environment variable on this computer.
          If you did not mean to use it, remove that variable or set <code>LLM_PROVIDER=none</code>.</p>
      )}
      {l.key_format_ok === false && <p className="warn-text">{l.key_var} does not look like a {l.provider_name} key. The check below will confirm.</p>}
      {res && !res.ok && l.configured && <p className="st-bad-text">{res.message}{res.hint ? ` ${res.hint}` : ""}</p>}
      {err && <p className="warn-text">{err}</p>}
      <button className="btn btn-primary" onClick={check} disabled={busy || !l.configured}
        title={l.configured ? "Sends one tiny request, no retries" : "Configure a key first"}>
        {busy ? "Checking…" : "Check LLM connection"}
      </button>
    </section>
  );
}

function SearchCard({ s }: { s: AppSettings }) {
  const st = s.search;
  const [res, setRes] = useState<CheckResult | null>(null);
  const [q, setQ] = useState("effects of sleep on memory");
  const [test, setTest] = useState<SearchTest | null>(null);
  const [busy, setBusy] = useState<"" | "check" | "test">("");
  const [err, setErr] = useState<string | null>(null);
  const check = async () => {
    setBusy("check"); setErr(null);
    try { setRes((await api.diagnostics("search")).search); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const runTest = async (e: FormEvent) => {
    e.preventDefault(); setBusy("test"); setErr(null); setTest(null);
    try { setTest(await api.searchTest(q)); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const status = !st.configured ? <StatusLine ok={false} warn text="API key required" />
    : res ? <StatusLine ok={res.ok} text={res.ok ? "Connected" : "Failed"} />
    : <StatusLine ok={false} warn text="Configured, not checked yet" />;
  return (
    <section className="cfg-card">
      <h2>Search <span className="muted">(web search and page fetch)</span></h2>
      <table className="table compact"><tbody>
        <Row k="Provider" v="TinyFish" />
        <Row k="API key" v={st.key_present ? `Present (TINYFISH_API_KEY, from ${st.key_source})` : "Missing"} />
        <Row k="Status" v={status} />
      </tbody></table>
      {st.problem && <p className="warn-text">{st.problem}</p>}
      {res && <p className={res.ok ? "muted" : "st-bad-text"}>{res.message}</p>}
      <button className="btn btn-primary" onClick={check} disabled={!!busy || !st.configured}>{busy === "check" ? "Checking…" : "Check search connection"}</button>
      <form className="search-test" onSubmit={runTest}>
        <label className="field"><span>Try a search (no LLM involved)</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} minLength={3} maxLength={200} />
        </label>
        <button className="btn" disabled={!!busy || !st.configured}>{busy === "test" ? "Searching…" : "Search"}</button>
      </form>
      {err && <p className="warn-text">{err}</p>}
      {test && (
        <ol className="search-results">
          {test.results.map((r) => (
            <li key={r.url}><a href={r.url} target="_blank" rel="noreferrer noopener">{r.title || r.url}</a><span className="muted">{r.site}</span></li>
          ))}
          {!test.results.length && <li className="muted">No results.</li>}
        </ol>
      )}
    </section>
  );
}

export function Settings({ settings, error }: { settings: AppSettings | null; error: string | null }) {
  if (error) return <div className="page"><h1>Settings</h1><p className="warn-text">{error}</p></div>;
  if (!settings) return <div className="page"><p className="muted">Loading…</p></div>;
  return (
    <div className="page narrow">
      <h1>Settings</h1>
      <p className="explain">
        Search API and LLM API are separate services. <strong>TinyFish</strong> provides web search and page fetch.
        An <strong>LLM provider</strong> (OpenAI, Google Gemini or Anthropic) is required for AI planning, verification,
        report writing and critique. A TinyFish key is never used as an LLM key.
      </p>
      {settings.force_demo && <p className="demo-inline">DEMO_MODE=1 is set: every new session runs as DEMO MODE — SIMULATED AI.</p>}
      {!settings.env_file_found && <p className="warn-text">backend/.env was not found. Copy backend/.env.example to backend/.env.</p>}
      <div className="cfg-grid">
        <LlmCard s={settings} onChanged={() => {}} />
        <SearchCard s={settings} />
      </div>
      <h2>Configure an LLM provider</h2>
      <ol className="steps">
        <li>Get a key from one provider: Google Gemini (free tier: aistudio.google.com/apikey), OpenAI (paid credit:
          platform.openai.com/api-keys) or Anthropic (paid credit: console.anthropic.com).</li>
        <li>In <code>backend/.env</code> set, for example:<br />
          <code>LLM_PROVIDER=gemini</code><br /><code>GOOGLE_API_KEY=</code><em>your real key</em></li>
        <li>Keep <code>TINYFISH_API_KEY</code> as it is; it stays the search key.</li>
        <li>Restart the backend, then press <em>Check LLM connection</em>. Research is enabled once it shows Connected.</li>
        <li>Terminal alternative: <code>python -m app.check_live</code> in <code>backend/</code> (add <code>--search</code> to test TinyFish alone).</li>
      </ol>
      <p className="muted">Without an LLM key you can still test search above, or run a clearly labelled DEMO MODE — SIMULATED AI session from New research.</p>
    </div>
  );
}
