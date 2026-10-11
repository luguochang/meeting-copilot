import { ApiError } from "./client";
import { ContractError } from "./schema";
import type { KnowledgePage } from "./knowledge";

export type MemoryReconcileMode = "off" | "explicit_only";
export interface MemoryReconcileSettings { projectId: string; mode: MemoryReconcileMode; revision: number; updatedAtMs: number }
export interface MemoryReconcileJob {
  id: string; projectId: string; documentId: string; documentTitle: string; sourceVersionId: string; sourceVersion: number;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled"; errorCode: string | null; errorMessage: string | null;
  createdAtMs: number; updatedAtMs: number; attempts: number; resultMemoryIds: string[]; canRetry: boolean; requiresResume: boolean;
}
export interface MemoryReconcileApi {
  settings(projectId: string, signal?: AbortSignal): Promise<MemoryReconcileSettings>;
  configure(projectId: string, mode: MemoryReconcileMode, revision: number): Promise<MemoryReconcileSettings>;
  list(projectId: string, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<MemoryReconcileJob>>;
  request(documentId: string, versionId: string): Promise<MemoryReconcileJob>;
  retry(jobId: string, versionId: string): Promise<MemoryReconcileJob>;
  cancel(jobId: string): Promise<MemoryReconcileJob>;
}
function object(raw: unknown): Record<string, unknown> { if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ContractError("记忆整理响应格式错误"); return raw as Record<string, unknown>; }
function text(raw: unknown): string { if (typeof raw !== "string") throw new ContractError("记忆整理文本字段错误"); return raw; }
function number(raw: unknown, minimum = 0): number { if (typeof raw !== "number" || !Number.isSafeInteger(raw) || raw < minimum) throw new ContractError("记忆整理版本字段错误"); return raw; }
function nullable(raw: unknown): string | null { if (raw === null) return null; return text(raw); }
function boolean(raw: unknown): boolean { if (typeof raw !== "boolean") throw new ContractError("记忆整理状态字段错误"); return raw; }
function settings(raw: unknown): MemoryReconcileSettings { const x = object(raw); if (x.mode !== "off" && x.mode !== "explicit_only") throw new ContractError("记忆整理模式无效"); return { projectId: text(x.project_id), mode: x.mode, revision: number(x.revision), updatedAtMs: number(x.updated_at_ms) }; }
function job(raw: unknown): MemoryReconcileJob {
  const x = object(raw);
  if (!["pending", "running", "succeeded", "failed", "cancelled"].includes(String(x.status)) || !Array.isArray(x.result_memory_ids) || x.result_memory_ids.length > 6) throw new ContractError("记忆整理作业状态无效");
  return { id: text(x.id), projectId: text(x.project_id), documentId: text(x.document_id), documentTitle: text(x.document_title), sourceVersionId: text(x.source_version_id), sourceVersion: number(x.source_version, 1), status: x.status as MemoryReconcileJob["status"], errorCode: nullable(x.error_code), errorMessage: nullable(x.error_message), createdAtMs: number(x.created_at_ms), updatedAtMs: number(x.updated_at_ms), attempts: number(x.attempts), resultMemoryIds: x.result_memory_ids.map(text), canRetry: boolean(x.can_retry), requiresResume: boolean(x.requires_resume) };
}
export class HttpMemoryReconcileApi implements MemoryReconcileApi {
  private readonly base: string;
  constructor(base = "") { this.base = base.trim().replace(/\/+$/, ""); }
  private async call(path: string, init: RequestInit = {}) {
    const response = await fetch(`${this.base}/v2/knowledge${path}`, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
    const raw: unknown = await response.json().catch(() => null);
    if (!response.ok) { const detail = raw && typeof raw === "object" ? (raw as { detail?: unknown }).detail : null; throw new ApiError(response.status, typeof detail === "string" ? detail : "记忆整理未完成，请刷新来源或配置后重试。", raw); }
    return object(raw);
  }
  async settings(projectId: string, signal?: AbortSignal) { return settings((await this.call(`/projects/${encodeURIComponent(projectId)}/memory-reconcile-settings`, { signal })).settings); }
  async configure(projectId: string, mode: MemoryReconcileMode, revision: number) { return settings((await this.call(`/projects/${encodeURIComponent(projectId)}/memory-reconcile-settings`, { method: "PUT", body: JSON.stringify({ mode, expected_revision: revision }) })).settings); }
  async list(projectId: string, cursor?: string | null, signal?: AbortSignal) { const params = new URLSearchParams({ project_id: projectId, limit: "20" }); if (cursor) params.set("cursor", cursor); const x = await this.call(`/memory-reconcile/jobs?${params}`, { signal }); if (!Array.isArray(x.items) || typeof x.has_more !== "boolean") throw new ContractError("记忆整理分页字段错误"); return { items: x.items.map(job), hasMore: x.has_more, nextCursor: nullable(x.next_cursor) }; }
  async request(documentId: string, versionId: string) { return job((await this.call(`/documents/${encodeURIComponent(documentId)}/memory-reconcile`, { method: "POST", body: JSON.stringify({ expected_version_id: versionId }) })).job); }
  async retry(jobId: string, versionId: string) { return job((await this.call(`/memory-reconcile/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST", body: JSON.stringify({ expected_version_id: versionId }) })).job); }
  async cancel(jobId: string) { return job((await this.call(`/memory-reconcile/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" })).job); }
}
