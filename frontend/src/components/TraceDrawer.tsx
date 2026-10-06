import { useEffect, useRef } from "react";
import type { Claim, Evidence, Source } from "../lib/types";
import { ClaimStatus, TierChip } from "./Badges";

export function TraceDrawer({ sid, label, sources, evidence, claims, onClose }: {
  sid: string; label?: string; sources: Source[]; evidence: Evidence[]; claims: Claim[]; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const src = sources.find((s) => s.source_id === sid);
  const ev = evidence.filter((e) => e.source_id === sid);
  const cl = claims.filter((c) => c.source_ids.includes(sid) && c.include_in_report);
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label={`Traceability for ${sid}`} tabIndex={-1} ref={ref} onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h3>Citation {label} traces to {sid}</h3>
          <button className="btn btn-quiet" onClick={onClose}>Close</button>
        </div>
        <p className="trace-chain">Report claim → citation {label} → source {sid} → {ev.length} evidence item(s)</p>
        {src ? (
          <section className="trace-block">
            <h4>Source</h4>
            <p><a href={src.url} target="_blank" rel="noreferrer noopener">{src.title}</a></p>
            <p className="muted">{[src.authors.join(", "), src.publisher, src.publication_date || "date unavailable"].filter(Boolean).join(", ")}</p>
            <p><TierChip tier={src.credibility_tier} /> <span className="muted">{src.source_type}, {src.fetch_status === "fetched" ? "full page fetched" : "search snippet only"}</span></p>
          </section>
        ) : <p>Source not found.</p>}
        <section className="trace-block">
          <h4>Claims relying on this source</h4>
          {cl.length ? cl.map((c) => <div key={c.claim_id} className="trace-claim"><ClaimStatus status={c.status} /> <span>{c.text}</span></div>) : <p className="muted">None.</p>}
        </section>
        <section className="trace-block">
          <h4>Evidence extracted</h4>
          {ev.map((e) => (
            <div key={e.evidence_id} className="trace-ev">
              <p>{e.summary}</p>
              {e.quote && <blockquote>{e.quote}</blockquote>}
              <p className="muted">{e.evidence_id}, {e.quote_verified ? "quote verified against fetched text" : "paraphrase only (no verifiable quote)"}{e.from_snippet_only ? ", from search snippet" : ""}</p>
            </div>
          ))}
        </section>
      </aside>
    </div>
  );
}
