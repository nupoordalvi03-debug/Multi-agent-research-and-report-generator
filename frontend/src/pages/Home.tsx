import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { go } from "../lib/router";
import type { AppSettings, HistoryItem } from "../lib/types";
import { DemoBanner, LlmNotConfigured, StatusPill } from "../components/Badges";

const PIPE = [
  ["Research Planner", "Breaks the question into subtopics and search queries"],
  ["Researcher", "Searches and fetches real pages, ranks sources by credibility, extracts evidence"],
  ["Verifier", "Links every claim to evidence, flags weak support and contradictions"],
  ["Report Writer", "Writes only from verified claims, citing collected sources"],
  ["Report Critic", "Scores the draft and sends it back for up to two revisions"],
];

export function Home({ settings }: { settings: AppSettings | null }) {
  const [recent, setRecent] = useState<HistoryItem[] | null>(null);
  useEffect(() => { api.history().then((h) => setRecent(h.slice(0, 5))).catch(() => setRecent([])); }, []);
  return (
    <div className="page">
      <section className="hero">
        <h1>Ask a research question. Five agents hand it to each other until it becomes a cited report.</h1>
        <ol className="pipe">
          {PIPE.map(([n, d], i) => (
            <li key={n}><span className="pipe-n">{i + 1}</span><strong>{n}</strong><span>{d}</span></li>
          ))}
        </ol>
        <button className="btn btn-primary btn-lg" onClick={() => go("/new")}>Start new research</button>
      </section>
      {settings?.mode === "demo" && <DemoBanner />}
      {settings && !settings.force_demo && !settings.llm.configured && <LlmNotConfigured problem={settings.llm.problem} />}
      <section className="recent">
        <h2>Recent research</h2>
        {recent === null ? <p className="muted">Loading…</p> : recent.length === 0 ? (
          <p className="muted">Nothing yet. Your first research session will appear here.</p>
        ) : (
          <ul className="hist">
            {recent.map((r) => (
              <li key={r.id}><button className="hist-row" onClick={() => go(`/research/${r.id}`)}>
                <StatusPill status={r.status} /><span className="hist-q">{r.query}</span>
                <span className="muted">{dateTime(r.created_at)}</span>
              </button></li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
