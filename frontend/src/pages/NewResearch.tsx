import { FormEvent, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { go } from "../lib/router";
import type { AppSettings } from "../lib/types";
import { DemoBanner, ErrorNote, LlmNotConfigured } from "../components/Badges";

const SAMPLE = {
  query: "Does listening to music while studying improve concentration and learning?",
  hypothesis: "Listening to suitable music may improve mood and concentration for some students, but music with lyrics or high complexity may negatively affect learning and memory.",
};
const DEPTH_HINT: Record<string, string> = {
  Quick: "3 subtopics, about 6 sources", Standard: "3 to 5 subtopics, 5 to 10 sources", Comprehensive: "5 to 8 subtopics, 10 to 20 sources",
};

function newRequestId(): string {
  const c = (globalThis as any).crypto;
  return c?.randomUUID ? c.randomUUID() : `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export function NewResearch({ settings }: { settings: AppSettings | null }) {
  const [f, setF] = useState({ query: "", hypothesis: "", source_mode: "Hybrid", report_type: "Academic Research Report", citation_style: "IEEE", depth: "Standard" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Synchronous guard: a double-click fires two submit events before React re-renders the disabled button.
  const submitting = useRef(false);
  // Idempotency key for this form: if the same request reaches the server twice, it returns the same session.
  const requestId = useRef(newRequestId());
  const o = settings?.options;
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    requestId.current = newRequestId(); // a changed form is a new request
    setF({ ...f, [k]: e.target.value });
  };

  const [blocked, setBlocked] = useState<{ title: string; message: string; hint?: string } | null>(null);
  const llmMissing = !!settings && !settings.force_demo && !settings.llm.configured;
  const searchMissing = !!settings && !settings.force_demo && !settings.search.configured;
  const liveBlocked = llmMissing || searchMissing;

  const submit = async (e: FormEvent, mode: "live" | "demo" = "live") => {
    e.preventDefault();
    if (submitting.current) return;
    if (mode === "live" && liveBlocked) return;
    if (f.query.trim().length < 10) { setErr("Write a research question of at least 10 characters."); return; }
    submitting.current = true;
    setBusy(true); setErr(null); setBlocked(null);
    try {
      const r = await api.create({ ...f, client_request_id: requestId.current, mode });
      go(`/research/${r.id}/activity`);
    } catch (e: any) {
      if (e instanceof ApiError && e.info) {
        setBlocked({ title: e.info.title || "Research could not start", message: e.info.reason || e.message, hint: e.info.hint });
      } else {
        setErr(e.message);
      }
      setBusy(false); submitting.current = false;
      requestId.current = newRequestId();
    }
  };

  return (
    <div className="page narrow">
      <h1>New research</h1>
      <p className="muted">Five agents will plan, search, verify, write and critique. You can leave this page; progress is saved on the server.</p>
      {settings?.mode === "demo" && <DemoBanner />}
      {llmMissing && <LlmNotConfigured problem={settings?.llm.problem} />}
      {!llmMissing && searchMissing && (
        <div className="llm-missing" role="alert"><p><strong>SEARCH NOT CONFIGURED</strong></p><p>{settings?.search.problem}</p>
          <p><a href="#/settings">Open Settings</a></p></div>
      )}
      <form className="form" onSubmit={(e) => submit(e, "live")}>
        <label className="field">
          <span>Research question</span>
          <textarea rows={3} maxLength={500} required value={f.query} onChange={set("query")} placeholder="What do you want to find out?" />
        </label>
        <label className="field">
          <span>Hypothesis <em className="muted">(optional)</em></span>
          <textarea rows={3} maxLength={1000} value={f.hypothesis} onChange={set("hypothesis")} placeholder="What do you expect the evidence to show?" />
        </label>
        <button type="button" className="btn btn-quiet sample" onClick={() => setF({ ...f, ...SAMPLE })}>Use the PBL demo question</button>
        <div className="grid2">
          <label className="field"><span>Source target</span>
            <select value={f.source_mode} onChange={set("source_mode")}>{(o?.source_modes ?? ["Hybrid"]).map((x) => <option key={x}>{x}</option>)}</select>
          </label>
          <label className="field"><span>Report type</span>
            <select value={f.report_type} onChange={set("report_type")}>{(o?.report_types ?? ["Academic Research Report"]).map((x) => <option key={x}>{x}</option>)}</select>
          </label>
          <label className="field"><span>Citation style</span>
            <select value={f.citation_style} onChange={set("citation_style")}>{(o?.citation_styles ?? ["IEEE"]).map((x) => <option key={x}>{x}</option>)}</select>
          </label>
          <fieldset className="field">
            <legend>Research depth</legend>
            <div className="seg">
              {(o?.depths ?? ["Quick", "Standard", "Comprehensive"]).map((d) => (
                <label key={d} className={f.depth === d ? "on" : ""}>
                  <input type="radio" name="depth" value={d} checked={f.depth === d} onChange={set("depth")} />{d}
                </label>
              ))}
            </div>
            <small className="muted">{DEPTH_HINT[f.depth]}</small>
          </fieldset>
        </div>
        {err && <ErrorNote message={err} />}
        {blocked && (
          <div className="error-note" role="alert">
            <p><strong>{blocked.title}</strong></p><p>{blocked.message}</p>
            {blocked.hint && <p className="muted">{blocked.hint}</p>}
            <p className="muted">No research session was created and no agent was started.</p>
          </div>
        )}
        <div className="submit-row">
          <button className="btn btn-primary btn-lg" disabled={busy || liveBlocked}
            title={liveBlocked ? "Configure an LLM API key first (see Settings)" : ""}>
            {busy ? "Checking LLM and starting…" : "Start research"}
          </button>
          {llmMissing && (
            <button type="button" className="btn btn-lg btn-demo" disabled={busy} onClick={(e) => submit(e as any, "demo")}>
              Start in DEMO MODE — SIMULATED AI
            </button>
          )}
        </div>
        {llmMissing && <p className="muted small">Demo mode uses sample data and no LLM. It is labelled everywhere and is never real research.</p>}
      </form>
    </div>
  );
}
