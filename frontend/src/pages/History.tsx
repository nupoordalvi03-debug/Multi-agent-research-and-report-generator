import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { go } from "../lib/router";
import type { HistoryItem } from "../lib/types";
import { ErrorNote, StatusPill } from "../components/Badges";

export function History() {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.history().then(setItems).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="page">
      <h1>Research history</h1>
      {err && <ErrorNote message={err} />}
      {items && items.length === 0 && (
        <p className="muted">No research yet. <button className="link-btn" onClick={() => go("/new")}>Start your first question.</button></p>
      )}
      {items && items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Question</th><th>Status</th><th>Type</th><th>Depth</th><th>Score</th><th>Started</th></tr></thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => go(`/research/${r.id}`)}>
                  <td><button className="link-btn" onClick={() => go(`/research/${r.id}`)}>{r.query}</button>{r.mode === "demo" && <span className="demo-tag">demo</span>}</td>
                  <td><StatusPill status={r.status} /></td>
                  <td>{r.report_type}</td><td>{r.depth}</td>
                  <td className="num">{r.quality_score ?? "—"}</td>
                  <td>{dateTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
