import { ApiError } from "./client";
import { ContractError } from "./schema";
import type { KnowledgePage } from "./knowledge";
import type { ProjectSourceRef } from "./projectOutcomes";

export type MemoryScope = "global" | "project" | "meeting";
export type MemoryScene = "" | "general" | "decision" | "project" | "interview" | "brainstorm";
export type MemoryStatus = "candidate" | "active" | "disabled" | "deleted" | "needs_review";
export interface UserMemory { id: string; scope: MemoryScope; scopeId: string; scene: MemoryScene; kind: "term" | "preference"; key: string; value: string; aliases: string[]; sourceRefs: ProjectSourceRef[]; origin: string; status: MemoryStatus; revision: number; conflictingMemoryIds?: string[]; reconciliationJobId?: string | null }
export interface MemoryInput { scope: MemoryScope; scopeId: string; scene: MemoryScene; kind: "term" | "preference"; key: string; value: string; aliases: string[] }
export interface MemoryChanges { status?: "active" | "disabled" | "deleted"; value?: string; aliases?: string[]; replaceActiveId?: string; replaceActiveRevision?: number }
export interface MemoryPreview { projectId: string | null; scene: string; preferences: { response_length?: "brief" | "standard"; suggestion_policy?: "off" | "low_frequency" | "standard" }; sources: { id: string; revision: number; scope: MemoryScope; scene: MemoryScene }[] }
export interface MemoryApi {
  list(scope: MemoryScope, scopeId: string, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<UserMemory>>;
  create(input: MemoryInput): Promise<UserMemory>;
  update(id: string, revision: number, changes: MemoryChanges): Promise<UserMemory>;
  export(scope: MemoryScope, scopeId: string): Promise<unknown>;
  preview(projectId: string | null, scene: string, signal?: AbortSignal): Promise<MemoryPreview>;
}
function record(raw: unknown): Record<string, unknown> { if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ContractError("记忆数据格式错误"); return raw as Record<string, unknown>; }
function str(value: unknown): string { if (typeof value !== "string") throw new ContractError("记忆文本字段无效"); return value; }
function int(value: unknown): number { if (!Number.isSafeInteger(value) || typeof value !== "number" || value < 1) throw new ContractError("记忆版本字段无效"); return value; }
function scope(value: unknown): MemoryScope { if (value !== "global" && value !== "project" && value !== "meeting") throw new ContractError("记忆范围无效"); return value; }
function scene(value: unknown): MemoryScene { if (!["", "general", "decision", "project", "interview", "brainstorm"].includes(String(value))) throw new ContractError("记忆场景无效"); return value as MemoryScene; }
function strings(raw: unknown): string[] { if (!Array.isArray(raw)) throw new ContractError("记忆列表无效"); return raw.map(str); }
function sources(raw: unknown): ProjectSourceRef[] {
  if (!Array.isArray(raw)) throw new ContractError("记忆来源列表无效");
  return raw.map((value) => { const x = record(value); if (x.source_type !== "document") throw new ContractError("记忆来源类型无效"); return { sourceType: "document", sourceId: str(x.source_id), documentId: str(x.document_id), projectId: str(x.project_id), meetingId: null, title: str(x.title), versionId: str(x.version_id), version: int(x.version), contentHash: str(x.content_hash), quote: str(x.quote), locator: record(x.locator), ordinal: typeof x.ordinal === "number" ? x.ordinal : undefined }; });
}
function parse(raw: unknown): UserMemory { const x = record(raw); if (x.kind !== "term" && x.kind !== "preference") throw new ContractError("记忆类型无效"); if (!["candidate", "active", "disabled", "deleted", "needs_review"].includes(String(x.status))) throw new ContractError("记忆状态无效"); return { id: str(x.id), scope: scope(x.scope), scopeId: str(x.scope_id), scene: scene(x.scene), kind: x.kind, key: str(x.memory_key), value: str(x.value), aliases: strings(x.aliases), sourceRefs: sources(x.source_refs), origin: str(x.origin), status: x.status as MemoryStatus, revision: int(x.revision), conflictingMemoryIds: x.conflicts_with_ids === undefined ? [] : strings(x.conflicts_with_ids), reconciliationJobId: x.reconciliation_job_id === undefined || x.reconciliation_job_id === null ? null : str(x.reconciliation_job_id) }; }
export class HttpMemoryApi implements MemoryApi {
  private readonly base: string;
  constructor(base = "") { this.base = base.trim().replace(/\/+$/, ""); }
  private async request(path: string, init: RequestInit = {}) { const response = await fetch(`${this.base}/v2/knowledge${path}`, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } }); const raw = await response.json().catch(() => null); if (!response.ok) { const detail = raw && typeof raw === "object" ? (raw as { detail?: unknown }).detail : null; throw new ApiError(response.status, typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `记忆操作未完成（${response.status}）`, raw); } return record(raw); }
  async list(scope: MemoryScope, scopeId: string, cursor?: string | null, signal?: AbortSignal) { const q = new URLSearchParams({ scope, scope_id: scopeId, limit: "50" }); if (cursor) q.set("cursor", cursor); const x = await this.request(`/memories?${q}`, { signal }); if (!Array.isArray(x.items) || typeof x.has_more !== "boolean") throw new ContractError("记忆分页字段无效"); return { items: x.items.map(parse), nextCursor: typeof x.next_cursor === "string" ? x.next_cursor : null, hasMore: x.has_more }; }
  async create(input: MemoryInput) { return parse(await this.request("/memories", { method: "POST", body: JSON.stringify({ scope: input.scope, scope_id: input.scopeId, scene: input.scene, kind: input.kind, memory_key: input.key, value: input.value, aliases: input.aliases, source_refs: [], origin: "user" }) })); }
  async update(id: string, revision: number, changes: MemoryChanges) { return parse(await this.request(`/memories/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ expected_revision: revision, status: changes.status, value: changes.value, aliases: changes.aliases, replace_active_id: changes.replaceActiveId, replace_active_revision: changes.replaceActiveRevision }) })); }
  async export(scope: MemoryScope, scopeId: string) { const x = await this.request(`/memories/export?${new URLSearchParams({ scope, scope_id: scopeId })}`); if (x.format !== "talktrace.memory.v1" || !Array.isArray(x.items)) throw new ContractError("记忆导出格式无效"); x.items.forEach(parse); return x; }
  async preview(projectId: string | null, sceneValue: string, signal?: AbortSignal): Promise<MemoryPreview> { const q = new URLSearchParams({ scene: sceneValue }); if (projectId) q.set("project_id", projectId); const x = await this.request(`/memory-preview?${q}`, { signal }); const preferences = record(x.preferences); if (preferences.response_length !== undefined && !["brief", "standard"].includes(String(preferences.response_length))) throw new ContractError("答复风格偏好无效"); if (preferences.suggestion_policy !== undefined && !["off", "low_frequency", "standard"].includes(String(preferences.suggestion_policy))) throw new ContractError("建议频率偏好无效"); if (!Array.isArray(x.sources)) throw new ContractError("偏好来源无效"); return { projectId: typeof x.project_id === "string" ? x.project_id : null, scene: str(x.scene), preferences: preferences as MemoryPreview["preferences"], sources: x.sources.map((raw) => { const s = record(raw); return { id: str(s.id), revision: int(s.revision), scope: scope(s.scope), scene: scene(s.scene) }; }) }; }
}
