import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { ACTIVE, dateTime } from "../lib/format";
import { go } from "../lib/router";
import type { AgentEvent, EvidenceResponse, ReportResponse, ResearchDetail, SourcesResponse } from "../lib/types";
import { AgentRelay } from "../components/AgentRelay";
import { DemoBanner, ErrorNote, LlmFailure, StatusPill, WaitingNotice } from "../components/Badges";
import { TraceDrawer } from "../components/TraceDrawer";
import { ActivityLog, ClaimsView, CritiqueView, Empty, PlanView, ReportView, SourcesTable } from "../components/Views";

const TABS = [
  ["overview", "Research plan"], ["activity", "Agent activity"], ["sources", "Sources"],
  ["claims", "Evidence & claims"], ["report", "Report"], ["critique", "Critique"],
] as const;
const POLL_MS = 1500;

export function Workspace({ id, tab }: { id: string; tab: string }) {
  const [detail, setDetail] = useState<ResearchDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [sources, setSources] = useState<SourcesResponse | null>(null);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connLost, setConnLost] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const lastEvent = useRef(0);
  const lastCountsKey = useRef("");

  const loadArtifacts = useCallback(async () => {
    const d = await api.get(id);
    const [s, e, r] = await Promise.all([
      api.sources(id), api.evidence(id),
      d.counts.report_versions > 0 ? api.report(id).catch(() => null) : Promise.resolve(null),
    ]);
    setDetail(d); setSources(s); setEvidence(e); setReport(r);
    return d;
  }, [id]);

  // Initial load (also how a refreshed page recovers: the backend owns all state)
  useEffect(() => {
    let cancelled = false;
    lastEvent.current = 0; lastCountsKey.current = "";
    setEvents([]); setDetail(null); setLoadError(null);
    loadArtifacts().catch((err) => !cancelled && setLoadError(err.message));
    return () => { cancelled = true; };
  }, [id, loadArtifacts]);

  const active = detail ? ACTIVE.has(detail.status) : true;

  // Poll the backend state machine; the UI only reflects what the backend reports.
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const st = await api.status(id, lastEvent.current);
        if (stop) return;
        setConnLost(false);
        if (st.events.length) {
          lastEvent.current = st.events[st.events.length - 1].id;
          setEvents((prev) => [...prev, ...st.events].slice(-600));
        }
        const key = JSON.stringify([st.status, st.counts, st.agents.map((a) => a.status)]);
        setDetail((d) => (d ? { ...d, ...st, agents: st.agents, counts: st.counts } : d));
        if (key !== lastCountsKey.current) {
          lastCountsKey.current = key;
          await loadArtifacts();
        }
        if (ACTIVE.has(st.status)) {
          timer = window.setTimeout(tick, POLL_MS);
        }
      } catch {
        if (stop) return;
        setConnLost(true);
        timer = window.setTimeout(tick, POLL_MS * 2);
      }
    };
    tick();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [id, loadArtifacts, active]);

  const doRetry = async () => {
    setRetrying(true);
    try {
      await api.retry(id);
      await loadArtifacts();
    } catch (e: any) {
      setLoadError(e.message);
    } finally { setRetrying(false); }
  };

  if (loadError && !detail) return <div className="page"><ErrorNote message={loadError} /><button className="btn" onClick={() => go("/history")}>Open research history</button></div>;
  if (!detail) return <div className="page"><p className="muted">Loading research session…</p></div>;

  const s = detail.session;
  const waiting = detail.agents.map((a) => a.metrics?.waiting).find((w) => w && w.until) ?? null;
  const failedAgentLabel = detail.agents.find((a) => a.status === "failed")?.label ?? "";
  const labels = report?.report.labels ?? {};
  const setTab = (t: string) => go(`/research/${id}/${t}`);

  return (
    <div className="workspace">
      {s.mode === "demo" && <div className="ws-banner"><DemoBanner /></div>}
      {connLost && <div className="ws-banner"><p className="conn">Lost contact with the backend. Reconnecting…</p></div>}

      <aside className="ws-left" aria-label="Research information">
        <StatusPill status={detail.status} />
        <h2 className="ws-query">{s.query}</h2>
        {s.hypothesis && <p className="ws-hyp"><span className="muted">Hypothesis.</span> {s.hypothesis}</p>}
        {waiting && ACTIVE.has(detail.status) && <WaitingNotice wait={waiting} />}
        <dl className="facts">
          <dt>Source mode</dt><dd>{s.source_mode}</dd>
          <dt>Report type</dt><dd>{s.report_type}</dd>
          <dt>Citations</dt><dd>{s.citation_style}</dd>
          <dt>Depth</dt><dd>{s.depth}</dd>
          {s.mode === "live" && detail.llm?.provider && <><dt>LLM</dt><dd>{detail.llm.provider} / {detail.llm.model}</dd></>}
          {s.mode === "live" && <><dt>LLM requests</dt><dd>{detail.llm?.requests ?? 0}{detail.llm?.rate_limited ? `, ${detail.llm.rate_limited} rate-limited` : ""}</dd></>}
          <dt>Started</dt><dd>{dateTime(detail.created_at)}</dd>
          {detail.completed_at && <><dt>Completed</dt><dd>{dateTime(detail.completed_at)}</dd></>}
        </dl>
        <dl className="tallies">
          <div><dt>Sources found</dt><dd>{detail.counts.sources}</dd></div>
          <div><dt>Evidence items</dt><dd>{detail.counts.evidence}</dd></div>
          <div><dt>Claims verified</dt><dd>{detail.counts.claims_verified}<small>/{detail.counts.claims}</small></dd></div>
          <div><dt>Quality score</dt><dd>{detail.quality_score ?? "—"}{detail.quality_score != null && <small>/100</small>}</dd></div>
        </dl>
        {detail.status === "FAILED" && (detail.error_info
          ? <LlmFailure info={detail.error_info} failedAgent={failedAgentLabel} onRetry={doRetry} busy={retrying} />
          : <ErrorNote message={detail.error || "The workflow failed."} onRetry={doRetry} busy={retrying} />)}
        {detail.status !== "FAILED" && detail.can_retry && (
          <ErrorNote message="This session stopped unexpectedly (for example the server restarted mid-stage)." onRetry={doRetry} busy={retrying} />
        )}
      </aside>

      <main className="ws-center">
        <nav className="tabs" role="tablist">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        <div className="tab-body">
          {tab === "overview" && <PlanView plan={detail.plan} />}
          {tab === "activity" && <ActivityLog events={events} />}
          {tab === "sources" && <SourcesTable data={sources} onTrace={setTrace} />}
          {tab === "claims" && <ClaimsView data={evidence} sources={sources?.sources ?? []} plan={detail.plan} citeLabels={labels} onTrace={setTrace} />}
          {tab === "report" && <ReportView data={report} id={id} completed={detail.status === "COMPLETED"} onTrace={setTrace} />}
          {tab === "critique" && (report ? <CritiqueView critique={report.critique} critiques={report.critiques} /> : <Empty>The Report Critic hasn't reviewed a draft yet.</Empty>)}
        </div>
      </main>

      <aside className="ws-right" aria-label="Agent workflow">
        <h3>Agent workflow</h3>
        <AgentRelay agents={detail.agents} counts={detail.counts} status={detail} />
      </aside>

      {trace && (
        <TraceDrawer sid={trace} label={labels[trace]} sources={sources?.sources ?? []} evidence={evidence?.evidence ?? []}
          claims={evidence?.claims ?? []} onClose={() => setTrace(null)} />
      )}
    </div>
  );
}
