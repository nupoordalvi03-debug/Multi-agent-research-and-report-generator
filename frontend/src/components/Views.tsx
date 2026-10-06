import { useMemo, useState, type ReactNode } from "react";
import type { AgentEvent, Claim, Critique, EvidenceResponse, Plan, ReportResponse, Source, SourcesResponse } from "../lib/types";
import { domain, time } from "../lib/format";
import { ClaimStatus, TierChip } from "./Badges";
import { RichText } from "./RichText";

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function PlanView({ plan }: { plan: Plan | null }) {
  if (!plan) return <Empty>The Research Planner hasn't finished yet. Its plan will appear here.</Empty>;
  return (
    <div className="plan">
      <p className="lede">{plan.research_question}</p>
      {plan.key_concepts.length > 0 && <p className="muted">Key concepts: {plan.key_concepts.join(", ")}</p>}
      <ol className="subtopics">
        {plan.subtopics.map((s) => (
          <li key={s.id}>
            <h4><span className="tid">{s.id}</span> {s.title}</h4>
            {s.rationale && <p>{s.rationale}</p>}
            <p className="muted">Evidence needed: {s.evidence_needed}</p>
            <div className="queries">{s.queries.map((q) => <span key={q} className="query">{q}</span>)}</div>
          </li>
        ))}
      </ol>
      {plan.evidence_criteria.length > 0 && (
        <>
          <h4>Evidence acceptance criteria</h4>
          <ul>{plan.evidence_criteria.map((c) => <li key={c}>{c}</li>)}</ul>
        </>
      )}
      {plan.scope_notes && <p className="muted">{plan.scope_notes}</p>}
    </div>
  );
}

export function ActivityLog({ events }: { events: AgentEvent[] }) {
  if (!events.length) return <Empty>No backend events yet.</Empty>;
  return (
    <ol className="log">
      {[...events].reverse().map((e) => (
        <li key={e.id} className={`log-${e.level}`}>
          <time>{time(e.ts)}</time>
          <span className="log-agent">{e.agent}</span>
          <span>{e.message}</span>
        </li>
      ))}
    </ol>
  );
}

export function SourcesTable({ data, onTrace }: { data: SourcesResponse | null; onTrace: (sid: string) => void }) {
  const [tier, setTier] = useState<number | 0>(0);
  if (!data || !data.sources.length) return <Empty>Sources appear once the Researcher finishes collecting them.</Empty>;
  const rows = data.sources.filter((s) => !tier || s.credibility_tier === tier);
  const st = data.stats;
  return (
    <div>
      {st && (
        <p className="muted stats-line">
          {st.searches} searches run, {st.candidates} unique candidates, {st.duplicates_removed} duplicates removed,
          {" "}{st.fetched} pages fetched, {st.fetch_failed} fetch failures.
        </p>
      )}
      <div className="filters" role="group" aria-label="Filter by credibility tier">
        {[0, 1, 2, 3, 4].map((t) => (
          <button key={t} className={`chip ${tier === t ? "on" : ""}`} onClick={() => setTier(t)}>
            {t ? `Tier ${t} (${data.sources.filter((s) => s.credibility_tier === t).length})` : `All (${data.sources.length})`}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>ID</th><th>Source</th><th>Credibility</th><th>Date</th><th>Retrieval</th><th>Relevance</th><th>Cited</th></tr></thead>
          <tbody>
            {rows.map((s: Source) => (
              <tr key={s.source_id}>
                <td><button className="link-btn" onClick={() => onTrace(s.source_id)}>{s.source_id}</button></td>
                <td className="src-cell">
                  <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title}</a>
                  <span className="muted">{[s.authors.slice(0, 3).join(", "), s.publisher || domain(s.url)].filter(Boolean).join(", ")}</span>
                </td>
                <td><TierChip tier={s.credibility_tier} /><span className="muted block">{s.source_type}</span></td>
                <td>{s.publication_date || <span className="muted">unavailable</span>}</td>
                <td>{s.fetch_status === "fetched" ? "Full page" : <span className="warn-text" title={s.fetch_error}>Snippet only</span>}</td>
                <td className="num">{s.relevance_score.toFixed(2)}</td>
                <td className="num">{s.cited_as ? `[${s.cited_as}]` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.unavailable_subtopics.length > 0 && (
        <p className="warn-text">Evidence unavailable for subtopic(s): {data.unavailable_subtopics.join(", ")}.</p>
      )}
    </div>
  );
}

const FLAG_TEXT: Record<string, string> = {
  single_source: "single source", low_tier_only: "low-tier sources only", snippet_only: "snippet evidence only",
  no_verbatim_quote: "no verbatim quote", no_valid_evidence: "no valid evidence",
};

export function ClaimsView({ data, sources, plan, citeLabels, onTrace }: {
  data: EvidenceResponse | null; sources: Source[]; plan: Plan | null; citeLabels: Record<string, string>; onTrace: (sid: string) => void;
}) {
  const [showRemoved, setShowRemoved] = useState(false);
  const subs = useMemo(() => Object.fromEntries((plan?.subtopics || []).map((s) => [s.id, s.title])), [plan]);
  if (!data || !data.claims.length) return <Empty>Claims appear once the Verifier has checked the evidence.</Empty>;
  const ev = Object.fromEntries(data.evidence.map((e) => [e.evidence_id, e]));
  const src = Object.fromEntries(sources.map((s) => [s.source_id, s]));
  const claims = data.claims.filter((c) => showRemoved || c.include_in_report);
  const removed = data.claims.length - data.claims.filter((c) => c.include_in_report).length;
  return (
    <div>
      <p className="muted stats-line">
        {data.stats && Object.entries(data.stats).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ")}.
        {" "}Each claim below links to the evidence and source behind it.
      </p>
      {removed > 0 && (
        <label className="toggle"><input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} /> Show {removed} claim(s) removed as unsupported</label>
      )}
      {data.contradictions.length > 0 && (
        <section className="callout callout-warn">
          <h4>Disagreements between sources</h4>
          <ul>{data.contradictions.map((c, i) => <li key={i}>{c.description} <span className="muted">({c.source_ids.join(", ")})</span></li>)}</ul>
        </section>
      )}
      {data.gaps.length > 0 && (
        <section className="callout">
          <h4>Evidence gaps</h4>
          <ul>{data.gaps.map((g) => <li key={g.subtopic_id}>{g.title}: {g.note}</li>)}</ul>
        </section>
      )}
      <ol className="claims">
        {claims.map((c: Claim) => (
          <li key={c.claim_id} className={c.include_in_report ? "" : "removed"}>
            <div className="claim-head">
              <span className="tid">{c.claim_id}</span>
              <ClaimStatus status={c.status} />
              <span className="muted">{c.confidence} confidence{subs[c.subtopic_id] ? `, ${subs[c.subtopic_id]}` : ""}</span>
            </div>
            <p className="claim-text">{c.text}</p>
            {c.flags.length > 0 && <p className="flags">{c.flags.map((f) => <span key={f} className="flag">{FLAG_TEXT[f] ?? f}</span>)}</p>}
            {c.notes && <p className="muted">{c.notes}</p>}
            <div className="chain">
              {c.evidence_ids.map((eid) => {
                const e = ev[eid]; if (!e) return null; const s = src[e.source_id];
                return (
                  <div key={eid} className="chain-row">
                    <span className="tid">{eid}</span>
                    <div>
                      <p>{e.quote ? <q>{e.quote}</q> : e.summary}</p>
                      <p className="muted">
                        {e.quote_verified ? "Verbatim quote checked against source text" : "Paraphrase"}, from{" "}
                        <button className="link-btn" onClick={() => onTrace(e.source_id)}>{e.source_id}</button>
                        {s ? ` (${domain(s.url)}, tier ${s.credibility_tier})` : ""}
                        {citeLabels[e.source_id] ? `, cited as ${citeLabels[e.source_id]}` : ", not cited in report"}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function CritiqueView({ critique, critiques }: { critique: Critique | null; critiques: Critique[] }) {
  if (!critiques.length) return <Empty>The Report Critic hasn't reviewed a draft yet.</Empty>;
  const final = critique;
  return (
    <div>
      {final && (
        <div className="score-row">
          <div className={`score ${final.passed ? "pass" : "fail"}`}><span>{final.score}</span>/100</div>
          <div>
            <p><strong>{final.passed ? "Passed quality review" : "Finalized after maximum revisions"}</strong></p>
            <p className="muted">Final report is draft v{final.final_version}. {final.revisions ?? 0} revision cycle(s) of a maximum 2.</p>
          </div>
        </div>
      )}
      {critiques.map((c) => (
        <section key={c.version} className="crit-round">
          <h4>Draft v{c.version}: {c.score}/100, {c.passed ? "passed" : "revision requested"}</h4>
          {c.summary && <p>{c.summary}</p>}
          {Object.keys(c.dimension_scores || {}).length > 0 && (
            <p className="muted">{Object.entries(c.dimension_scores).map(([k, v]) => `${k} ${v}/10`).join(", ")}</p>
          )}
          {c.issues.length ? (
            <ul className="issues">
              {c.issues.map((i, k) => (
                <li key={k} className={`sev-${i.severity}`}>
                  <span className="sev">{i.severity}</span> <strong>{i.section}</strong>: {i.description}
                  {i.suggestion && <span className="muted"> Suggested fix: {i.suggestion}</span>}
                  <span className="muted"> ({i.source === "rule" ? "automatic check" : "LLM review"})</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No issues raised.</p>}
        </section>
      ))}
    </div>
  );
}

export function ReportView({ data, id, completed, onTrace }: {
  data: ReportResponse | null; id: string; completed: boolean; onTrace: (sid: string) => void;
}) {
  if (!data) return <Empty>The report appears here once the Report Writer finishes its first draft.</Empty>;
  const r = data.report;
  return (
    <article className="report">
      <div className="report-tools">
        <span className="muted">
          {data.is_final ? `Final report (draft v${r.version})` : `Working draft v${r.version}, not final yet`}. Select a citation to trace it.
        </span>
        <div className="export">
          {(["pdf", "docx", "markdown"] as const).map((f) => (
            completed
              ? <a key={f} className="btn btn-quiet" href={`/api/research/${id}/export/${f}`} download>{f === "markdown" ? "Markdown" : f.toUpperCase()}</a>
              : <button key={f} className="btn btn-quiet" disabled title="Available when the workflow completes">{f === "markdown" ? "Markdown" : f.toUpperCase()}</button>
          ))}
        </div>
      </div>
      {data.mode === "demo" && <p className="demo-inline">DEMO MODE — SIMULATED AI. Sample data, not real sources or real AI research.</p>}
      <h1>{r.title}</h1>
      {r.sections.map((s) => (
        <section key={s.heading}>
          <h2>{s.heading}</h2>
          <RichText content={s.content} labels={r.labels} style={data.citation_style} onCite={onTrace} />
        </section>
      ))}
      <section>
        <h2>References</h2>
        <ol className="refs">
          {r.references.map((ref) => (
            <li key={ref.source_id}><button className="link-btn" onClick={() => onTrace(ref.source_id)}>{ref.source_id}</button> {ref.text}</li>
          ))}
        </ol>
      </section>
    </article>
  );
}
