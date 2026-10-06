export function time(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(+d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(+d) ? "" : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
export function duration(a: string | null, b: string | null): string {
  if (!a) return "";
  const ms = (b ? +new Date(b) : Date.now()) - +new Date(a);
  if (!(ms >= 0)) return "";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
export function domain(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}
export const STATUS_TEXT: Record<string, string> = {
  QUEUED: "Queued", PLANNING: "Planning", RESEARCHING: "Researching", VERIFYING: "Verifying",
  WRITING: "Writing", CRITIQUING: "Critiquing", REVISING: "Revising", COMPLETED: "Completed", FAILED: "Failed",
};
export const ACTIVE = new Set(["QUEUED", "PLANNING", "RESEARCHING", "VERIFYING", "WRITING", "CRITIQUING", "REVISING"]);
export const TIER_TEXT: Record<number, string> = { 1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 4: "Tier 4" };
export const TIER_HINT: Record<number, string> = {
  1: "Peer-reviewed, government, university or standards body",
  2: "Academic repository, preprint or conference paper",
  3: "Industry report or established press",
  4: "Blog, forum or community post",
};
