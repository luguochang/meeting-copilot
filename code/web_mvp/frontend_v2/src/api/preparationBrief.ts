import { ApiError } from "./client";
import { ContractError } from "./schema";
import { parseSource, type ProjectSourceRef } from "./projectOutcomes";
import { parseDocumentSourceEvidence } from "./sourceEvidence";
import type { KnowledgePage, MeetingKnowledgeSelection } from "./knowledge";

export type PreparationScene = "general" | "decision" | "project" | "interview" | "brainstorm";
export type PreparationSource = ProjectSourceRef | { sourceType: "project_state"; sourceId: string; projectId: string; revision: number; quote: string; sourceRefs: ProjectSourceRef[] };
export interface PreparationLine { text: string; basis: "source" | "user_goal"; sourceRefs: PreparationSource[] }
export interface PreparationResult { agenda: PreparationLine[]; questions: PreparationLine[]; conditions: PreparationLine[] }
export interface PreparationJob {
  id: string; projectId: string; folderId: string | null; documentIds: string[] | null;
  goal: string; role: string; focus: string[]; scene: PreparationScene;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled"; revision: number; inputHash: string;
  baseProjectRevision: number; attempts: number; requiresResume: boolean; deferCount: number; nextAttemptAtMs: number | null;
  errorCode: string | null; errorMessage: string | null; result: PreparationResult | null; evidenceStatus: "valid" | "stale";
  contextTruncated: boolean; createdAtMs: number; updatedAtMs: number; completedAtMs: number | null;
}
export interface PreparationInput { selection: MeetingKnowledgeSelection; goal: string; role: string; focus: string[]; scene: PreparationScene }
export interface PreparationBriefApi {
  list(projectId: string, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<PreparationJob>>;
  get(id: string, signal?: AbortSignal): Promise<PreparationJob>;
  start(input: PreparationInput, projectRevision: number, signal?: AbortSignal): Promise<PreparationJob>;
  cancel(id: string, signal?: AbortSignal): Promise<PreparationJob>;
  retry(job: PreparationJob, projectRevision: number, signal?: AbortSignal): Promise<PreparationJob>;
}
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw new ContractError("会前准备数据无效"); return value as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== "string") throw new ContractError("会前准备文本无效"); return value; }
function integer(value: unknown, minimum = 0): number { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) throw new ContractError("会前准备版本无效"); return value; }
function nullableText(value: unknown): string | null { return value === null ? null : text(value); }
function nullableInteger(value: unknown): number | null { return value === null ? null : integer(value); }
function array<T>(value: unknown, parse: (item: unknown) => T, maximum: number): T[] { if (!Array.isArray(value) || value.length > maximum) throw new ContractError("会前准备列表无效"); return value.map(parse); }
function bool(value: unknown): boolean { if (typeof value !== "boolean") throw new ContractError("会前准备状态无效"); return value; }
function source(value: unknown): PreparationSource {
  const x = object(value);
  if (x.source_type === "document" && x.context_revision !== undefined) {
    const reference = parseDocumentSourceEvidence(value);
    if (reference.sourceType !== "document") throw new ContractError("项目准备来源必须是项目资料");
    return { ...reference, meetingId: null };
  }
  if (x.source_type !== "project_state") return parseSource(value);
  return { sourceType: "project_state", sourceId: text(x.source_id), projectId: text(x.project_id), revision: integer(x.revision, 1), quote: text(x.quote), sourceRefs: array(x.source_refs, parseSource, 6) };
}
function line(value: unknown): PreparationLine {
  const x = object(value); const basis = x.basis;
  if (basis !== "source" && basis !== "user_goal") throw new ContractError("会前准备依据无效");
  const result: PreparationLine = { text: text(x.text), basis, sourceRefs: array(x.source_refs, source, 12) };
  if (!result.text.trim() || result.text.length > 240 || (basis === "source" && !result.sourceRefs.length) || (basis === "user_goal" && result.sourceRefs.length)) throw new ContractError("会前准备缺少有效依据");
  return result;
}
export function parsePreparationJob(value: unknown): PreparationJob {
  const x = object(value);
  if (!["pending", "running", "succeeded", "failed", "cancelled"].includes(String(x.status)) || !["general", "decision", "project", "interview", "brainstorm"].includes(String(x.scene)) || !["valid", "stale"].includes(String(x.evidence_status))) throw new ContractError("会前准备状态无效");
  let result: PreparationResult | null = null;
  if (x.result !== null) {
    const r = object(x.result); result = { agenda: array(r.agenda, line, 4), questions: array(r.questions, line, 4), conditions: array(r.conditions, line, 4) };
    if (result.conditions.some((item) => item.basis !== "source") || [...result.agenda, ...result.questions, ...result.conditions].reduce((count, item) => count + item.sourceRefs.length, 0) > 12) throw new ContractError("会前准备条件必须来自真实来源");
  }
  const job: PreparationJob = { id: text(x.id), projectId: text(x.project_id), folderId: nullableText(x.folder_id), documentIds: x.document_ids === null ? null : array(x.document_ids, text, 3000), goal: text(x.goal), role: text(x.role), focus: array(x.focus, text, 8), scene: x.scene as PreparationScene, status: x.status as PreparationJob["status"], revision: integer(x.revision, 1), inputHash: text(x.input_hash), baseProjectRevision: integer(x.base_project_revision, 1), attempts: integer(x.attempts), requiresResume: bool(x.requires_resume), deferCount: integer(x.defer_count), nextAttemptAtMs: nullableInteger(x.next_attempt_at_ms), errorCode: nullableText(x.error_code), errorMessage: nullableText(x.error_message), result, evidenceStatus: x.evidence_status as PreparationJob["evidenceStatus"], contextTruncated: bool(x.context_truncated), createdAtMs: integer(x.created_at_ms), updatedAtMs: integer(x.updated_at_ms), completedAtMs: nullableInteger(x.completed_at_ms) };
  if (!job.inputHash || !job.goal.trim() || (result !== null && job.status !== "succeeded") || (result && [...result.agenda, ...result.questions, ...result.conditions].some((item) => item.sourceRefs.some((ref) => ref.projectId !== job.projectId || (ref.sourceType === "project_state" && ref.sourceRefs.some((nested) => nested.projectId !== job.projectId)))))) throw new ContractError("会前准备来源或结果不一致");
  return job;
}
export class HttpPreparationBriefApi implements PreparationBriefApi {
  private readonly base: string;
  constructor(baseUrl = "") { this.base = baseUrl.trim().replace(/\/+$/, ""); }
  private async request(path: string, init: RequestInit = {}) {
    const controller = new AbortController(); const abort = () => controller.abort(init.signal?.reason);
    if (init.signal?.aborted) abort(); else init.signal?.addEventListener("abort", abort, { once: true });
    const timer = window.setTimeout(() => controller.abort(new DOMException("读取会前准备超时", "TimeoutError")), 8_000);
    try {
      const response = await fetch(`${this.base}/v2/knowledge${path}`, { ...init, signal: controller.signal, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
      const raw = await response.json().catch(() => null);
      if (!response.ok) { const detail = raw && typeof raw === "object" ? (raw as { detail?: unknown }).detail : null; throw new ApiError(response.status, typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `会前准备操作失败（${response.status}）`, raw); }
      return object(raw);
    } finally { window.clearTimeout(timer); init.signal?.removeEventListener("abort", abort); }
  }
  async list(projectId: string, cursor?: string | null, signal?: AbortSignal) {
    const query = new URLSearchParams({ project_id: projectId, limit: "20" }); if (cursor) query.set("cursor", cursor);
    const x = await this.request(`/preparation-jobs?${query}`, { signal }); const items = array(x.items, parsePreparationJob, 20);
    if (items.some((job) => job.projectId !== projectId)) throw new ContractError("会前准备返回了其他项目任务");
    return { items, hasMore: bool(x.has_more), nextCursor: nullableText(x.next_cursor) };
  }
  async get(id: string, signal?: AbortSignal) { const job = parsePreparationJob((await this.request(`/preparation-jobs/${encodeURIComponent(id)}`, { signal })).job); if (job.id !== id) throw new ContractError("会前准备任务不匹配"); return job; }
  async start(input: PreparationInput, projectRevision: number, signal?: AbortSignal) {
    if (!input.selection.projectId) throw new ContractError("请先选择本次项目");
    const payload = { folder_id: input.selection.folderId, document_ids: input.selection.documentIds, goal: input.goal, role: input.role, focus: input.focus, scene: input.scene, expected_project_revision: projectRevision };
    const job = parsePreparationJob((await this.request(`/projects/${encodeURIComponent(input.selection.projectId)}/preparation-jobs`, { method: "POST", signal, body: JSON.stringify(payload) })).job);
    if (!matchesPreparation(job, input)) throw new ContractError("会前准备任务输入与本次选择不一致"); return job;
  }
  async cancel(id: string, signal?: AbortSignal) { const job = parsePreparationJob((await this.request(`/preparation-jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", signal })).job); if (job.id !== id) throw new ContractError("会前准备取消任务不匹配"); return job; }
  async retry(job: PreparationJob, projectRevision: number, signal?: AbortSignal) { const result = parsePreparationJob((await this.request(`/preparation-jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST", signal, body: JSON.stringify({ expected_revision: job.revision, expected_input_hash: job.inputHash, expected_project_revision: projectRevision }) })).job); if (result.id !== job.id) throw new ContractError("会前准备重试任务不匹配"); return result; }
}
export function matchesPreparation(job: PreparationJob, input: PreparationInput): boolean {
  const ids = (value: string[] | null) => value === null ? null : [...new Set(value)].sort();
  return job.projectId === input.selection.projectId && job.folderId === input.selection.folderId && JSON.stringify(ids(job.documentIds)) === JSON.stringify(ids(input.selection.documentIds)) && job.goal === input.goal.trim() && job.role === input.role.trim() && JSON.stringify(job.focus) === JSON.stringify(input.focus) && job.scene === input.scene;
}
