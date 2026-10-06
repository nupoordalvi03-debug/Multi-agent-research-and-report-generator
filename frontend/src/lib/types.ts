export type WorkflowStatus =
  | "QUEUED" | "PLANNING" | "RESEARCHING" | "VERIFYING" | "WRITING"
  | "CRITIQUING" | "REVISING" | "COMPLETED" | "FAILED";

export interface AgentRun {
  agent: "planner" | "researcher" | "verifier" | "writer" | "critic";
  label: string;
  status: "waiting" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  started_at: string | null;
  completed_at: string | null;
  attempts: number;
  error: string | null;
  completion_state: string | null;
  metrics: Record<string, any>;
}

export interface AgentEvent { id: number; agent: string; level: "info" | "success" | "warning" | "error"; message: string; ts: string; }

export interface Counts { sources: number; evidence: number; claims: number; claims_verified: number; report_versions: number; }

export interface ErrorInfo {
  category: "rate_limit" | "quota_exhausted" | "billing" | "auth" | "model_unavailable" | "timeout" | "network" | "server" | "bad_request" | "malformed";
  title: string; hint: string; provider: string; model: string; agent: string; agent_label: string;
  http_status: number | null; retry_after_s: number | null; retry_possible_at: string | null; attempts: number; detail: string;
}
export interface WaitInfo {
  category: string; wait_s: number; until: string; retry: number; max_retries: number; provider: string; model: string;
  agent_label: string; retry_after_s?: number | null; reason?: string;
}
export interface LlmSummary { provider: string | null; model: string | null; requests: number; successful: number; rate_limited: number; requests_by_agent: Record<string, number>; }

export interface StatusBlock {
  id: string; status: WorkflowStatus; mode: "live" | "demo"; failed_stage: string | null; error: string | null;
  error_info: ErrorInfo | null; llm: LlmSummary;
  quality_score: number | null; revision_count: number; task_alive: boolean; can_retry: boolean;
  created_at: string; updated_at: string; completed_at: string | null;
}

export interface StatusResponse extends StatusBlock { agents: AgentRun[]; counts: Counts; events: AgentEvent[]; }

export interface SubTopic { id: string; title: string; rationale: string; queries: string[]; evidence_needed: string; source_types: string[]; }
export interface Plan { research_question: string; key_concepts: string[]; subtopics: SubTopic[]; required_source_types: string[]; evidence_criteria: string[]; scope_notes: string; }

export interface SessionInfo { id: string; query: string; hypothesis: string; source_mode: string; report_type: string; citation_style: string; depth: string; mode: "live" | "demo"; }

export interface Critique {
  version: number; score: number; passed: boolean; summary: string; answers_question: boolean;
  dimension_scores: Record<string, number>; rule_penalty: number;
  issues: { severity: "critical" | "major" | "minor"; category: string; section: string; description: string; suggestion: string; source: "llm" | "rule" }[];
  final_version?: number; revisions?: number; history?: { version: number; score: number; passed: boolean }[];
}

export interface ResearchDetail extends StatusBlock {
  session: SessionInfo; plan: Plan | null; research_stats: Record<string, any> | null;
  critique: Critique | null; agents: AgentRun[]; counts: Counts;
}

export interface Source {
  source_id: string; title: string; url: string; authors: string[]; publisher: string; publication_date: string;
  source_type: string; credibility_tier: 1 | 2 | 3 | 4; retrieved_at: string; relevance_score: number;
  subtopic_ids: string[]; snippet: string; fetch_status: "fetched" | "snippet_only" | "failed"; fetch_error: string;
  content_chars: number; is_demo: boolean; cited_as: number | null;
}

export interface Evidence { evidence_id: string; source_id: string; subtopic_id: string; summary: string; quote: string; quote_verified: boolean; from_snippet_only: boolean; }
export interface Claim { claim_id: string; text: string; subtopic_id: string; status: "supported" | "partially_supported" | "contested" | "unsupported"; confidence: "high" | "medium" | "low"; evidence_ids: string[]; source_ids: string[]; notes: string; flags: string[]; include_in_report: boolean; }
export interface Contradiction { subtopic_id: string; description: string; evidence_ids: string[]; source_ids: string[]; }
export interface Gap { subtopic_id: string; title: string; note: string; }

export interface EvidenceResponse { evidence: Evidence[]; claims: Claim[]; contradictions: Contradiction[]; gaps: Gap[]; stats: Record<string, number> | null; }
export interface SourcesResponse { sources: Source[]; stats: Record<string, any> | null; unavailable_subtopics: string[]; }

export interface Report {
  title: string; version: number; sections: { heading: string; content: string }[];
  citation_map: Record<string, number>; labels: Record<string, string>;
  references: { source_id: string; number: number; text: string }[]; removed_citations: string[];
}
export interface ReportResponse { report: Report; is_final: boolean; citation_style: string; mode: string; critique: Critique | null; critiques: Critique[]; versions: number[]; }

export interface HistoryItem { id: string; query: string; status: WorkflowStatus; mode: string; report_type: string; depth: string; quality_score: number | null; created_at: string; completed_at: string | null; }

export interface LlmStatus {
  configured: boolean; provider: string | null; provider_name: string | null; model: string | null; key_var: string | null;
  key_present: boolean; key_source: string | null; key_format_ok: boolean | null; provider_source: string | null;
  other_keys_present: string[]; problem: string | null; problem_code: string | null;
}
export interface SearchStatus { configured: boolean; provider: string; key_var: string; key_present: boolean; key_source: string | null; problem: string | null; problem_code: string | null; }
export interface CheckResult { ok: boolean; category?: string; title?: string; message: string; hint?: string; checked?: string; status?: string; }
export interface Diagnostics { llm: CheckResult | null; search: CheckResult | null; }
export interface SearchTest { provider: string; query: string; results: { title: string; url: string; snippet: string; site: string }[]; }

export interface AppSettings {
  mode: "live" | "demo" | "not_configured"; llm: LlmStatus; search: SearchStatus; env_file_found: boolean;
  llm_last_check: CheckResult | null; force_demo: boolean; missing_for_live: string[]; llm_provider: string | null; llm_model: string | null;
  keys_configured: Record<string, boolean>; openai_base_url_custom: boolean; stage_timeout_s: number; stage_max_attempts: number;
  options: { source_modes: string[]; report_types: string[]; citation_styles: string[]; depths: string[] };
}
