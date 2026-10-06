import type { AgentRun, Counts, StatusBlock } from "../lib/types";
import { duration, time } from "../lib/format";

function handoff(a: AgentRun, counts: Counts, s: StatusBlock): string | null {
  if (a.status !== "completed") return null;
  const m = a.metrics || {};
  switch (a.agent) {
    case "planner": return `${m.subtopics ?? "?"} subtopics and ${m.queries ?? "?"} search queries`;
    case "researcher": return `${m.sources ?? counts.sources} sources and ${m.evidence ?? counts.evidence} evidence items`;
    case "verifier": return `${m.claims_verified ?? counts.claims_verified} verified claims, ${m.contradictions ?? 0} contradictions`;
    case "writer": return `Draft v${counts.report_versions || 1} with ${m.citations ?? "?"} cited sources`;
    case "critic": return s.status === "COMPLETED" ? `Final report, quality ${s.quality_score}/100` : `Score ${m.quality_score ?? "?"}/100`;
  }
  return null;
}

export function AgentRelay({ agents, counts, status }: { agents: AgentRun[]; counts: Counts; status: StatusBlock }) {
  return (
    <ol className="relay" aria-label="Agent workflow">
      <li className="relay-endpoint">Research question</li>
      {agents.map((a, i) => {
        const pkt = handoff(a, counts, status);
        return (
          <li key={a.agent} className={`relay-node rn-${a.status}`}>
            <div className="rn-head">
              <span className="rn-index">{i + 1}</span>
              <div className="rn-title">
                <strong>{a.label}</strong>
                <span className="rn-state">
                  {a.status === "completed" ? "Completed" : a.status === "running" ? "Working" : a.status === "failed" ? "Failed" : "Waiting"}
                  {a.attempts > 1 ? `, attempt ${a.attempts}` : ""}
                </span>
              </div>
            </div>
            {a.status === "running" && (
              <div className="rn-bar" role="progressbar" aria-valuenow={a.progress} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${Math.max(4, a.progress)}%` }} />
              </div>
            )}
            {a.message && a.status !== "completed" && <p className="rn-msg">{a.message}</p>}
            {a.status === "failed" && a.error && <p className="rn-err">{a.error}</p>}
            {a.started_at && (
              <p className="rn-times">
                Started {time(a.started_at)}
                {a.completed_at ? `, finished ${time(a.completed_at)} (${duration(a.started_at, a.completed_at)})` : ""}
              </p>
            )}
            {pkt && <div className="rn-packet"><span aria-hidden>↓</span> {pkt}</div>}
          </li>
        );
      })}
      <li className={`relay-endpoint ${status.status === "COMPLETED" ? "done" : ""}`}>
        {status.status === "COMPLETED" ? "Final report ready" : "Final report"}
      </li>
    </ol>
  );
}
