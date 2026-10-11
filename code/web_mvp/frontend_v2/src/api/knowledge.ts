import { ApiError } from "./client";
import { ContractError } from "./schema";
import { HttpMaterialsApi } from "./materials";
import type { MeetingAttachmentSourceEvidence } from "../domain/events";

export interface KnowledgePage<T> { items: T[]; nextCursor: string | null; hasMore: boolean }
export interface KnowledgeProject { id: string; name: string; description: string; revision: number; archivedAtMs: number | null }
export interface KnowledgeFolder { id: string; projectId: string; parentId: string | null; name: string; revision: number }
export type KnowledgeDocumentStatus = "draft" | "active" | "disabled" | "deleted";
export type KnowledgeSourceStatus = "current" | "needs_review" | "deleted";
export interface KnowledgeImportResult { document: KnowledgeDocument; version: KnowledgeVersion; created: boolean; alreadyImported: boolean }
export interface KnowledgeDocument {
  id: string; projectId: string; folderId: string | null; kind: string; title: string;
  sourceMeetingId: string | null; filename: string | null; activeVersionId: string | null;
  status: KnowledgeDocumentStatus; importStatus: string; errorCode: string | null; errorMessage: string | null; revision: number;
  sourceStatus?: KnowledgeSourceStatus; sourceBinding?: { sourceKind: string; sourceId: string } | null;
}
export interface KnowledgeLocator { type: string; page?: number; paragraph?: number; [key: string]: unknown }
export interface KnowledgeSpan { id: string; versionId: string; ordinal: number; text: string; locator: KnowledgeLocator }
export interface KnowledgeVersion { id: string; documentId: string; number: number; content: string; contentHash: string; origin: string; confirmedAtMs: number | null; spans: KnowledgeSpan[]; sourceStatus?: KnowledgeSourceStatus }
export interface KnowledgeVersionMetadata { id: string; documentId: string; number: number; origin: string; confirmedAtMs: number | null }
export interface KnowledgeDetail { document: KnowledgeDocument; version: KnowledgeVersion | null; latestDraftVersionId: string | null }
export interface KnowledgeJob { id: string; documentId: string; projectId: string; type: string; status: string; progress: string; attempt: number; errorCode: string | null; errorMessage: string | null; resultVersionId: string | null }
export interface KnowledgeSource { sourceType: "document"; sourceId: string; documentId: string; projectId: string; title: string; versionId: string; version: number; contentHash: string; ordinal: number; quote: string; locator: KnowledgeLocator }
export interface MeetingKnowledgeSelection { projectId: string | null; folderId: string | null; documentIds: string[] | null }
export const EMPTY_KNOWLEDGE_SELECTION: MeetingKnowledgeSelection = { projectId: null, folderId: null, documentIds: null };
export interface MeetingKnowledgeContext extends MeetingKnowledgeSelection { meetingId: string; revision: number; selectionMode: "none" | "project" | "documents" }
export interface KnowledgeApi {
  getMeetingAttachmentSource?(meetingId: string, sourceId: string): Promise<MeetingAttachmentSourceEvidence & { sourceState?: string; staleReason?: string }>;
  exportProject?(projectId: string, signal?: AbortSignal): Promise<void>;
  importNote?(noteId: string, input: { projectId: string; expectedSourceVersion: number; folderId?: string | null; expectedDocumentRevision?: number }): Promise<KnowledgeImportResult>;
  importFinal?(meetingId: string, input: { documentKind: "minutes" | "decisions" | "action_items" | "risks" | "transcript"; expectedSourceVersion: number; folderId?: string | null; expectedDocumentRevision?: number }): Promise<KnowledgeImportResult>;
  getMeetingContext(meetingId: string, signal?: AbortSignal): Promise<MeetingKnowledgeContext>;
  saveMeetingContext(meetingId: string, value: MeetingKnowledgeSelection, expectedRevision?: number): Promise<MeetingKnowledgeContext>;
  getMeetingSource(id: string, meetingId: string, signal?: AbortSignal): Promise<KnowledgeSource>;
  listProjects(cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<KnowledgeProject>>;
  createProject(name: string, description?: string): Promise<KnowledgeProject>;
  updateProject(id: string, revision: number, changes: { name?: string; description?: string; archived?: boolean }): Promise<KnowledgeProject>;
  listFolders(projectId: string, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<KnowledgeFolder>>;
  createFolder(projectId: string, name: string, parentId: string | null): Promise<KnowledgeFolder>;
  updateFolder(id: string, revision: number, changes: { name?: string; parentId?: string | null }): Promise<KnowledgeFolder>;
  listDocuments(query: { projectId: string; folderId?: string | null; query?: string; cursor?: string | null; limit?: number }, signal?: AbortSignal): Promise<KnowledgePage<KnowledgeDocument>>;
  importDocument(file: File, projectId: string, folderId: string | null, title?: string): Promise<{ document: KnowledgeDocument; job: KnowledgeJob }>;
  createDocument(input: { projectId: string; folderId: string | null; title: string; content: string; kind: "note" | "meeting_document"; sourceMeetingId?: string | null }): Promise<{ document: KnowledgeDocument; version: KnowledgeVersion }>;
  getDocument(id: string, signal?: AbortSignal): Promise<KnowledgeDetail>;
  listVersions(id: string, beforeNumber?: number | null): Promise<KnowledgePage<KnowledgeVersionMetadata>>;
  getVersion(id: string, versionId: string, signal?: AbortSignal): Promise<KnowledgeVersion>;
  saveVersion(id: string, content: string, expectedRevision: number, baseVersionId?: string): Promise<{ document: KnowledgeDocument; version: KnowledgeVersion }>;
  confirmVersion(id: string, versionId: string, expectedRevision: number): Promise<KnowledgeDocument>;
  updateDocument(id: string, expectedRevision: number, changes: { title?: string; folderId?: string | null; status?: KnowledgeDocumentStatus }): Promise<KnowledgeDocument>;
  listJobs(projectId: string, documentId?: string | null, signal?: AbortSignal): Promise<KnowledgePage<KnowledgeJob>>;
  getJob(id: string, signal?: AbortSignal): Promise<KnowledgeJob>;
  retryJob(id: string): Promise<KnowledgeJob>;
  cancelJob(id: string): Promise<KnowledgeJob>;
  getSource(id: string, projectId: string, signal?: AbortSignal): Promise<KnowledgeSource>;
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ContractError(`${field} 必须是对象`);
  return value as Record<string, unknown>;
}
function string(value: unknown, field: string): string { if (typeof value !== "string") throw new ContractError(`${field} 必须是文本`); return value; }
function nullable(value: unknown): string | null { return typeof value === "string" ? value : null; }
function number(value: unknown, field: string): number { if (typeof value !== "number" || !Number.isFinite(value)) throw new ContractError(`${field} 必须是数值`); return value; }
function timestamp(value: unknown): number | null { return typeof value === "number" ? value : null; }
function sourceStatus(value: unknown): KnowledgeSourceStatus | undefined { if (value === undefined) return undefined; if (value !== "current" && value !== "needs_review" && value !== "deleted") throw new ContractError("资料来源状态无效"); return value; }
function sourceBinding(value: unknown): KnowledgeDocument["sourceBinding"] { if (value === undefined) return undefined; if (value === null) return null; const x = record(value, "source_binding"); return { sourceKind: string(x.source_kind, "source_binding.source_kind"), sourceId: string(x.source_id, "source_binding.source_id") }; }
function locator(value: unknown): KnowledgeLocator { const item = record(value ?? {}, "locator"); return { ...item, type: typeof item.type === "string" ? item.type : "converted_text" }; }
function parseProject(value: unknown): KnowledgeProject { const x = record(value, "project"); return { id: string(x.id, "project.id"), name: string(x.name, "project.name"), description: typeof x.description === "string" ? x.description : "", revision: number(x.revision, "project.revision"), archivedAtMs: timestamp(x.archived_at_ms) }; }
function parseFolder(value: unknown): KnowledgeFolder { const x = record(value, "folder"); return { id: string(x.id, "folder.id"), projectId: string(x.project_id, "folder.project_id"), parentId: nullable(x.parent_id), name: string(x.name, "folder.name"), revision: number(x.revision, "folder.revision") }; }
function parseDocument(value: unknown): KnowledgeDocument {
  const x = record(value, "document"); const status = string(x.status, "document.status");
  if (!["draft", "active", "disabled", "deleted"].includes(status)) throw new ContractError("未知资料状态");
  return { sourceStatus: sourceStatus(x.source_status), sourceBinding: sourceBinding(x.source_binding), id: string(x.id, "document.id"), projectId: string(x.project_id, "document.project_id"), folderId: nullable(x.folder_id), kind: string(x.kind, "document.kind"), title: string(x.title, "document.title"), sourceMeetingId: nullable(x.source_meeting_id), filename: nullable(x.filename), activeVersionId: nullable(x.active_version_id), status: status as KnowledgeDocumentStatus, importStatus: string(x.import_status, "document.import_status"), errorCode: nullable(x.error_code), errorMessage: nullable(x.error_message), revision: number(x.revision, "document.revision") };
}
function parseVersionMetadata(value: unknown): KnowledgeVersionMetadata { const x = record(value, "version"); return { id: string(x.id, "version.id"), documentId: string(x.document_id, "version.document_id"), number: number(x.number, "version.number"), origin: string(x.origin, "version.origin"), confirmedAtMs: timestamp(x.confirmed_at_ms) }; }
function parseVersion(value: unknown): KnowledgeVersion {
  const x = record(value, "version");
  return { ...parseVersionMetadata(x), sourceStatus: sourceStatus(x.source_status), content: string(x.content, "version.content"), contentHash: string(x.content_hash, "version.content_hash"), spans: Array.isArray(x.spans) ? x.spans.map((v) => { const s = record(v, "span"); return { id: string(s.id, "span.id"), versionId: string(s.version_id, "span.version_id"), ordinal: number(s.ordinal, "span.ordinal"), text: string(s.text, "span.text"), locator: locator(s.locator) }; }) : [] };
}
function parseJob(value: unknown): KnowledgeJob { const x = record(value, "job"); return { id: string(x.id, "job.id"), documentId: string(x.document_id, "job.document_id"), projectId: string(x.project_id, "job.project_id"), type: string(x.type, "job.type"), status: string(x.status, "job.status"), progress: typeof x.progress === "string" ? x.progress : "", attempt: number(x.attempt, "job.attempt"), errorCode: nullable(x.error_code), errorMessage: nullable(x.error_message), resultVersionId: nullable(x.result_version_id) }; }
function page<T>(value: unknown, parse: (item: unknown) => T): KnowledgePage<T> { const x = record(value, "page"); if (!Array.isArray(x.items) || typeof x.has_more !== "boolean") throw new ContractError("资料分页返回格式错误"); return { items: x.items.map(parse), nextCursor: nullable(x.next_cursor), hasMore: x.has_more }; }

function parseMeetingContext(value: unknown): MeetingKnowledgeContext {
  const x = record(value, "context"); const mode = x.selection_mode;
  if (mode !== "none" && mode !== "project" && mode !== "documents") throw new ContractError("会议资料范围无效");
  if (!Array.isArray(x.selected_document_ids) || x.selected_document_ids.some((id) => typeof id !== "string")) throw new ContractError("会议资料选择无效");
  return { meetingId: string(x.meeting_id, "context.meeting_id"), projectId: nullable(x.project_id), folderId: nullable(x.folder_id), documentIds: mode === "documents" ? x.selected_document_ids as string[] : null, selectionMode: mode, revision: number(x.context_revision, "context.context_revision") };
}
function parseSource(value: unknown): KnowledgeSource {
  const x = record(value, "source"); if (x.source_type !== "document") throw new ContractError("引用不是资料来源");
  return { sourceType: "document", sourceId: string(x.source_id, "source.source_id"), documentId: string(x.document_id, "source.document_id"), projectId: string(x.project_id, "source.project_id"), title: string(x.title, "source.title"), versionId: string(x.version_id, "source.version_id"), version: number(x.version, "source.version"), contentHash: string(x.content_hash, "source.content_hash"), ordinal: number(x.ordinal, "source.ordinal"), quote: string(x.quote, "source.quote"), locator: locator(x.locator) };
}
export class HttpKnowledgeApi implements KnowledgeApi {
  getMeetingAttachmentSource(meetingId: string, sourceId: string) { return new HttpMaterialsApi(this.baseUrl).source(meetingId, sourceId); }
  private readonly baseUrl: string;
  constructor(baseUrl = "") { this.baseUrl = baseUrl.trim().replace(/\/+$/, ""); }
  async exportProject(projectId: string, signal?: AbortSignal): Promise<void> {
    const controller = new AbortController();
    const abort = () => controller.abort(signal?.reason);
    if (signal?.aborted) abort(); else signal?.addEventListener("abort", abort, { once: true });
    const timer = window.setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(`${this.baseUrl}/v2/knowledge/projects/${encodeURIComponent(projectId)}/export`, { headers: { Accept: "application/zip" }, signal: controller.signal });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
        const publicMessage = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `项目资料导出失败（${response.status}）`;
        throw new ApiError(response.status, publicMessage, body);
      }
      if (response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/zip") throw new ContractError("项目资料导出格式错误，请重试。");
      const filename = response.headers.get("content-disposition")?.match(/filename="(project-[a-f0-9]{16}\.zip)"/)?.[1] ?? "talktrace-project.zip";
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      try {
        link.href = objectUrl; link.download = filename; document.body.append(link); link.click();
      } finally { link.remove(); URL.revokeObjectURL(objectUrl); }
    } catch (error) {
      if (controller.signal.aborted && !signal?.aborted) throw new Error("项目资料导出超时，请稍后重试。");
      throw error;
    } finally { window.clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  private parseImport(raw: unknown): KnowledgeImportResult { const x = record(raw, "import"); if (typeof x.created !== "boolean" || typeof x.already_imported !== "boolean") throw new ContractError("来源导入格式无效"); return { document: parseDocument(x.document), version: parseVersion(x.version), created: x.created, alreadyImported: x.already_imported }; }
  async importNote(noteId: string, input: { projectId: string; expectedSourceVersion: number; folderId?: string | null; expectedDocumentRevision?: number }) { return this.parseImport(await this.request(`/notes/${encodeURIComponent(noteId)}/import`, { method: "POST", body: JSON.stringify({ project_id: input.projectId, expected_source_version: input.expectedSourceVersion, folder_id: input.folderId, expected_document_revision: input.expectedDocumentRevision }) })); }
  async importFinal(meetingId: string, input: { documentKind: "minutes" | "decisions" | "action_items" | "risks" | "transcript"; expectedSourceVersion: number; folderId?: string | null; expectedDocumentRevision?: number }) { return this.parseImport(await this.request(`/meetings/${encodeURIComponent(meetingId)}/import-final`, { method: "POST", body: JSON.stringify({ document_kind: input.documentKind, expected_source_version: input.expectedSourceVersion, folder_id: input.folderId, expected_document_revision: input.expectedDocumentRevision }) })); }
  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const multipart = init.body instanceof FormData;
    const response = await fetch(`${this.baseUrl}/v2/knowledge${path}`, { ...init, headers: { Accept: "application/json", ...(init.body && !multipart ? { "Content-Type": "application/json" } : {}), ...init.headers } });
    const body = await response.json().catch(() => null);
    if (!response.ok) { const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : null; throw new ApiError(response.status, typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `资料操作失败（${response.status}）`, body); }
    return body;
  }
  private query(path: string, values: Record<string, string | number | null | undefined>): string { const params = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") params.set(key, String(value)); }); return `${path}?${params}`; }
  async listProjects(cursor?: string | null, signal?: AbortSignal) { return page(await this.request(this.query("/projects", { limit: 100, cursor, include_archived: "true" }), { signal }), parseProject); }
  async createProject(name: string, description = "") { return parseProject(record(await this.request("/projects", { method: "POST", body: JSON.stringify({ name, description }) }), "result").project); }
  async updateProject(id: string, expectedRevision: number, changes: { name?: string; description?: string; archived?: boolean }) { return parseProject(record(await this.request(`/projects/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ ...changes, expected_revision: expectedRevision }) }), "result").project); }
  async listFolders(projectId: string, cursor?: string | null, signal?: AbortSignal) { return page(await this.request(this.query("/folders", { project_id: projectId, limit: 100, cursor }), { signal }), parseFolder); }
  async createFolder(projectId: string, name: string, parentId: string | null) { return parseFolder(record(await this.request("/folders", { method: "POST", body: JSON.stringify({ project_id: projectId, name, parent_id: parentId }) }), "result").folder); }
  async updateFolder(id: string, expectedRevision: number, changes: { name?: string; parentId?: string | null }) { return parseFolder(record(await this.request(`/folders/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ ...(changes.name !== undefined ? { name: changes.name } : {}), ...(changes.parentId !== undefined ? { parent_id: changes.parentId } : {}), expected_revision: expectedRevision }) }), "result").folder); }
  async listDocuments(query: { projectId: string; folderId?: string | null; query?: string; cursor?: string | null; limit?: number }, signal?: AbortSignal) { return page(await this.request(this.query("/documents", { project_id: query.projectId, folder_id: query.folderId, query: query.query, cursor: query.cursor, limit: query.limit ?? 25, status: "all" }), { signal }), parseDocument); }
  async importDocument(file: File, projectId: string, folderId: string | null, title?: string) { const body = new FormData(); body.set("file", file); body.set("project_id", projectId); if (folderId) body.set("folder_id", folderId); if (title) body.set("title", title); const x = record(await this.request("/documents", { method: "POST", body }), "result"); return { document: parseDocument(x.document), job: parseJob(x.job) }; }
  async createDocument(input: { projectId: string; folderId: string | null; title: string; content: string; kind: "note" | "meeting_document"; sourceMeetingId?: string | null }) { const x = record(await this.request("/documents/manual", { method: "POST", body: JSON.stringify({ project_id: input.projectId, folder_id: input.folderId, title: input.title, content: input.content, kind: input.kind, source_meeting_id: input.sourceMeetingId ?? null }) }), "result"); return { document: parseDocument(x.document), version: parseVersion(x.version) }; }
  async getDocument(id: string, signal?: AbortSignal) { const x = record(await this.request(`/documents/${encodeURIComponent(id)}`, { signal }), "result"); return { document: parseDocument(x.document), version: x.version ? parseVersion(x.version) : null, latestDraftVersionId: nullable(x.latest_draft_version_id) }; }
  async listVersions(id: string, beforeNumber?: number | null) { const x = record(await this.request(this.query(`/documents/${encodeURIComponent(id)}/versions`, { before_number: beforeNumber, limit: 25 })), "versions"); if (!Array.isArray(x.items)) throw new ContractError("资料版本列表格式错误"); const next = typeof x.next_before_number === "number" ? x.next_before_number : null; return { items: x.items.map(parseVersionMetadata), nextCursor: next === null ? null : String(next), hasMore: next !== null }; }
  async getVersion(id: string, versionId: string, signal?: AbortSignal) { return parseVersion(record(await this.request(`/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}`, { signal }), "result").version); }
  async saveVersion(id: string, content: string, expectedRevision: number, baseVersionId?: string) { const x = record(await this.request(`/documents/${encodeURIComponent(id)}/versions`, { method: "POST", body: JSON.stringify({ content, expected_revision: expectedRevision, ...(baseVersionId ? { base_version_id: baseVersionId } : {}) }) }), "result"); return { document: parseDocument(x.document), version: parseVersion(x.version) }; }
  async confirmVersion(id: string, versionId: string, expectedRevision: number) { return parseDocument(record(await this.request(`/documents/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify({ version_id: versionId, expected_revision: expectedRevision }) }), "result").document); }
  async updateDocument(id: string, expectedRevision: number, changes: { title?: string; folderId?: string | null; status?: KnowledgeDocumentStatus }) { return parseDocument(record(await this.request(`/documents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ ...(changes.title !== undefined ? { title: changes.title } : {}), ...(changes.folderId !== undefined ? { folder_id: changes.folderId } : {}), ...(changes.status !== undefined ? { status: changes.status } : {}), expected_revision: expectedRevision }) }), "result").document); }
  async listJobs(projectId: string, documentId?: string | null, signal?: AbortSignal) { return page(await this.request(this.query("/jobs", { project_id: projectId, document_id: documentId, limit: 100 }), { signal }), parseJob); }
  async getJob(id: string, signal?: AbortSignal) { return parseJob(record(await this.request(`/jobs/${encodeURIComponent(id)}`, { signal }), "result").job); }
  async retryJob(id: string) { return parseJob(record(await this.request(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }), "result").job); }
  async cancelJob(id: string) { return parseJob(record(await this.request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }), "result").job); }
  async getSource(id: string, projectId: string, signal?: AbortSignal) { return parseSource(record(await this.request(this.query(`/sources/${encodeURIComponent(id)}`, { project_id: projectId }), { signal }), "result").source); }
  async getMeetingSource(id: string, meetingId: string, signal?: AbortSignal) { return parseSource(record(await this.request(this.query(`/sources/${encodeURIComponent(id)}`, { meeting_id: meetingId }), { signal }), "result").source); }
  async getMeetingContext(meetingId: string, signal?: AbortSignal) { return parseMeetingContext(record(await this.request(`/meetings/${encodeURIComponent(meetingId)}/context`, { signal }), "result").context); }
  async saveMeetingContext(meetingId: string, value: MeetingKnowledgeSelection, expectedRevision?: number) { return parseMeetingContext(record(await this.request(`/meetings/${encodeURIComponent(meetingId)}/context`, { method: "PUT", body: JSON.stringify({ project_id: value.projectId, folder_id: value.folderId, document_ids: value.documentIds, ...(expectedRevision !== undefined ? { expected_revision: expectedRevision } : {}) }) }), "result").context); }
}
