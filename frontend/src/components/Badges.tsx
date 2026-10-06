import { useEffect, useState } from "react";
import { STATUS_TEXT, TIER_HINT, TIER_TEXT } from "../lib/format";
import type { ErrorInfo, WaitInfo } from "../lib/types";

export function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status.toLowerCase()}`}>{STATUS_TEXT[status] ?? status}</span>;
}

export function TierChip({ tier }: { tier: number }) {
  return <span className={`tier tier-${tier}`} title={TIER_HINT[tier]}>{TIER_TEXT[tier]}</span>;
}

const CLAIM_TEXT: Record<string, string> = {
  supported: "Supported", partially_supported: "Partly supported", contested: "Contested", unsupported: "Unsupported",
};
export function ClaimStatus({ status }: { status: string }) {
  return <span className={`claim-status cs-${status}`}>{CLAIM_TEXT[status] ?? status}</span>;
}

export function DemoBanner() {
  return (
    <div className="demo-banner" role="note">
      <strong>DEMO MODE — SIMULATED AI.</strong> No LLM is used. The plan, sources, claims and report are sample
      data from a simulator, clearly marked and not real research.
    </div>
  );
}

export function LlmNotConfigured({ problem }: { problem?: string | null }) {
  return (
    <div className="llm-missing" role="alert">
      <p><strong>LLM NOT CONFIGURED</strong></p>
      <p>An LLM API key is required for autonomous planning, verification, report writing, and critique.</p>
      {problem && <p className="muted">{problem}</p>}
      <p><a href="#/settings">Open Settings for setup instructions</a></p>
    </div>
  );
}

export function ErrorNote({ message, onRetry, busy }: { message: string; onRetry?: () => void; busy?: boolean }) {
  return (
    <div className="error-note" role="alert">
      <p>{message}</p>
      {onRetry && <button className="btn btn-primary" onClick={onRetry} disabled={busy}>{busy ? "Retrying…" : "Retry research"}</button>}
    </div>
  );
}

const WAITING_HELPS = new Set(["rate_limit", "timeout", "network", "server", "malformed"]);

export function LlmFailure({ info, failedAgent, onRetry, busy }: {
  info: ErrorInfo; failedAgent: string; onRetry: () => void; busy: boolean;
}) {
  const ready = info.retry_possible_at ? new Date(info.retry_possible_at) : null;
  return (
    <div className={`error-note cat-${info.category}`} role="alert">
      <p><strong>{info.title}</strong></p>
      <dl className="err-facts">
        <dt>Provider</dt><dd>{info.provider} / {info.model}</dd>
        <dt>Stopped at</dt><dd>{info.agent_label || failedAgent}</dd>
        <dt>Requests tried</dt><dd>{info.attempts}{info.http_status ? ` (last: HTTP ${info.http_status})` : ""}</dd>
        {ready && <><dt>Retry possible</dt><dd>{ready > new Date() ? `after ${ready.toLocaleTimeString()}` : "now"}</dd></>}
      </dl>
      <p>{info.hint}</p>
      <p className="muted">{WAITING_HELPS.has(info.category)
        ? "Retry research resumes from this agent; completed agents are not run again."
        : "Fix the cause first: retrying now will fail the same way."}</p>
      <button className="btn btn-primary" onClick={onRetry} disabled={busy}>{busy ? "Retrying…" : "Retry research"}</button>
    </div>
  );
}

export function WaitingNotice({ wait }: { wait: WaitInfo }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const left = Math.max(0, Math.round((new Date(wait.until).getTime() - now) / 1000));
  if (wait.category === "pacing") {
    return <div className="wait-note" role="status"><p>{wait.agent_label} is pacing its requests to stay under the provider limit: next LLM request in {left}s.</p></div>;
  }
  return (
    <div className="wait-note" role="status">
      <p><strong>{wait.category === "rate_limit" ? "LLM rate limit reached." : `LLM ${wait.category.replace("_", " ")}.`}</strong>{" "}
        The system will retry automatically when the provider allows another request.</p>
      <p className="muted">
        {wait.provider} / {wait.model}, {wait.agent_label}. Retry {Math.max(1, wait.retry)} of {wait.max_retries}{" "}
        {left > 0 ? `in ${left}s` : "now"}{wait.retry_after_s ? ` (provider asked for ${Math.round(wait.retry_after_s)}s)` : ""}.
      </p>
    </div>
  );
}
