import type { AppSettings, Diagnostics, SearchTest, EvidenceResponse, HistoryItem, ReportResponse, ResearchDetail, SourcesResponse, StatusBlock, StatusResponse } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: string = "", public info: any = null) { super(message); }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError(0, "Cannot reach the backend. Is the FastAPI server running on port 8000?");
  }
  const text = await res.text();
  const body = text ? safeJson(text) : null;
  if (!res.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d
      : Array.isArray(d) ? d.map((x: any) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ")
      : `Request failed (HTTP ${res.status})`;
    throw new ApiError(res.status, msg, body?.code ?? "", body?.info ?? null);
  }
  return body as T;
}

function safeJson(t: string): any { try { return JSON.parse(t); } catch { return null; } }

export interface NewResearch { query: string; hypothesis: string; source_mode: string; report_type: string; citation_style: string; depth: string; client_request_id?: string; mode?: "live" | "demo"; }

export const api = {
  settings: () => req<AppSettings>("/settings"),
  diagnostics: (target: "all" | "llm" | "search") => req<Diagnostics>(`/diagnostics?target=${target}`, { method: "POST" }),
  searchTest: (query: string) => req<SearchTest>("/search/test", { method: "POST", body: JSON.stringify({ query }) }),
  create: (b: NewResearch) => req<StatusBlock>("/research", { method: "POST", body: JSON.stringify(b) }),
  get: (id: string) => req<ResearchDetail>(`/research/${id}`),
  status: (id: string, after: number) => req<StatusResponse>(`/research/${id}/status?after=${after}`),
  sources: (id: string) => req<SourcesResponse>(`/research/${id}/sources`),
  evidence: (id: string) => req<EvidenceResponse>(`/research/${id}/evidence`),
  report: (id: string) => req<ReportResponse>(`/research/${id}/report`),
  history: () => req<HistoryItem[]>("/research/history"),
  retry: (id: string) => req<StatusBlock>(`/research/${id}/retry`, { method: "POST" }),
  exportUrl: (id: string, fmt: "pdf" | "docx" | "markdown") => `/api/research/${id}/export/${fmt}`,
};
