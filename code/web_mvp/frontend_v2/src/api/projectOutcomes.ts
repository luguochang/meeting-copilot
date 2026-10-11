import { ApiError } from "./client";
import { ContractError } from "./schema";
import type { KnowledgePage, MeetingKnowledgeSelection } from "./knowledge";
import { HttpPreparationBriefApi, type PreparationBriefApi } from "./preparationBrief";

export interface ProjectSourceRef {
  sourceType: "document" | "review_document" | "meeting_entity"; sourceId: string; projectId: string;
  meetingId: string | null; documentId?: string; title?: string; versionId?: string; version: number; contentHash: string;
  quote: string; locator: Record<string, unknown>; ordinal?: number; versionKind?: string;
}
export type ProjectItemType = "decision" | "task" | "question";
export type ProjectItemStatus = "active" | "done" | "cancelled" | "unknown" | "discarded" | "superseded" | "needs_review";
export interface ProjectStateItem { id: string; projectId: string; type: ProjectItemType; text: string; status: ProjectItemStatus; owner: string | null; due: string | null; sourceMeetingId: string | null; sourceRefs: ProjectSourceRef[]; origin: string; revision: number; supersedesId: string | null }
export interface ProjectChange { action: "create" | "supersede" | "update" | "discard"; itemId?: string; type?: ProjectItemType; text?: string; status?: ProjectItemStatus; owner?: string | null; due?: string | null; sourceRefs: ProjectSourceRef[] }
export interface ProjectProposal { id: string; projectId: string; meetingId: string | null; origin: string; baseRevision: number; changes: ProjectChange[]; status: "pending" | "confirmed" | "rejected" | "needs_review"; revision: number; isStale: boolean }
export interface ProjectWiki { projectId: string; revision: number; markdown: string; items: ProjectStateItem[]; truncated: boolean }
export interface MeetingProjectState { projectId: string | null; revision: number | null; decisions: ProjectStateItem[]; unfinishedTasks: ProjectStateItem[]; openQuestions: ProjectStateItem[]; truncated: boolean }
export interface ProjectBriefPreview extends MeetingProjectState {
  projectId: string; projectName: string; revision: number; folderId: string | null; documentIds: string[] | null;
  generatedAtMs: number; needsReview: ProjectStateItem[];
  needsReviewDocuments: { id: string; title: string; revision: number; sourceStatus: "needs_review" | "deleted"; activeVersion: number }[];
}
export interface ProjectItemInput { type: ProjectItemType; text: string; status: "active" | "done" | "cancelled" | "unknown" | "discarded"; owner?: string | null; due?: string | null; sourceRefs?: ProjectSourceRef[]; sourceMeetingId?: string | null }
export interface ProjectOutcomesApi {
  preparation?: PreparationBriefApi;
  listState(projectId: string, status?: string | null, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<ProjectStateItem> & { revision: number }>;
  createState(projectId: string, input: ProjectItemInput, expectedProjectRevision: number): Promise<{ item: ProjectStateItem; revision: number }>;
  updateState(id: string, changes: Omit<Partial<ProjectItemInput>, "type" | "sourceMeetingId">, revision: number, projectRevision: number): Promise<{ item: ProjectStateItem; revision: number }>;
  history(id: string, beforeRevision?: number | null): Promise<{ items: { snapshot: ProjectStateItem; reason: string }[]; nextBeforeRevision: number | null }>;
  listProposals(projectId: string, cursor?: string | null, signal?: AbortSignal): Promise<KnowledgePage<ProjectProposal>>;
  createProposal(projectId: string, changes: ProjectChange[], projectRevision: number, meetingId?: string | null): Promise<ProjectProposal>;
  confirmProposal(id: string, revision: number, projectRevision: number): Promise<{ proposal: ProjectProposal; items: ProjectStateItem[]; revision: number }>;
  rejectProposal(id: string, revision: number): Promise<ProjectProposal>;
  getWiki(projectId: string, signal?: AbortSignal): Promise<ProjectWiki>;
  getMeetingState(meetingId: string, signal?: AbortSignal): Promise<MeetingProjectState>;
  previewBrief?(selection: MeetingKnowledgeSelection, signal?: AbortSignal): Promise<ProjectBriefPreview>;
  getSource(reference: Pick<ProjectSourceRef, "sourceId" | "sourceType" | "projectId" | "meetingId">, signal?: AbortSignal): Promise<ProjectSourceRef>;
}
function record(raw: unknown): Record<string, unknown> { if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ContractError("项目数据格式错误"); return raw as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== "string") throw new ContractError("项目文本字段无效"); return value; }
function number(value: unknown): number { if (typeof value !== "number" || !Number.isSafeInteger(value)) throw new ContractError("项目版本字段无效"); return value; }
function nullable(value: unknown): string | null { return typeof value === "string" ? value : null; }
function array<T>(value: unknown, parse: (x: unknown) => T): T[] { if (!Array.isArray(value)) throw new ContractError("项目列表字段无效"); return value.map(parse); }
export function sourceRefPayload(value: ProjectSourceRef) { return { source_type: value.sourceType, source_id: value.sourceId, project_id: value.projectId, ...(value.meetingId ? { meeting_id: value.meetingId } : {}), ...(value.documentId ? { document_id: value.documentId } : {}), ...(value.title ? { title: value.title } : {}), ...(value.versionId ? { version_id: value.versionId } : {}), ...(value.ordinal !== undefined ? { ordinal: value.ordinal } : {}), ...(value.versionKind ? { version_kind: value.versionKind } : {}), version: value.version, content_hash: value.contentHash, quote: value.quote, locator: value.locator }; }
export function parseSource(value: unknown): ProjectSourceRef {
  const x = record(value);
  const kind = x.source_type;
  if (kind !== "document" && kind !== "review_document" && kind !== "meeting_entity") throw new ContractError("项目来源类型无效");
  const location = record(x.locator);
  const version = number(x.version);
  if (version < 1 || !text(x.content_hash)) throw new ContractError("项目来源版本无效");
  if (kind === "document") {
    text(x.document_id); text(x.title); text(x.version_id);
  } else if (!nullable(x.meeting_id)) throw new ContractError("会议来源缺少原会议");
  // Frozen proposal evidence may omit a position. The read-source endpoint
  // supplies the actual ordinal; never invent a position for historical refs.
  let ordinal: number | undefined;
  if (x.ordinal !== undefined && x.ordinal !== null) {
    if (typeof x.ordinal !== "number" || !Number.isSafeInteger(x.ordinal) || x.ordinal < 0) throw new ContractError("项目来源位置无效");
    ordinal = x.ordinal;
  }
  return {
    sourceType: kind, sourceId: text(x.source_id), projectId: text(x.project_id), meetingId: nullable(x.meeting_id),
    ...(typeof x.document_id === "string" ? { documentId: x.document_id } : {}),
    ...(typeof x.title === "string" ? { title: x.title } : {}),
    ...(typeof x.version_id === "string" ? { versionId: x.version_id } : {}),
    ...(ordinal !== undefined ? { ordinal } : {}),
    ...(typeof x.version_kind === "string" ? { versionKind: x.version_kind } : {}),
    version, contentHash: text(x.content_hash), quote: text(x.quote), locator: location,
  };
}
function parseItem(value: unknown): ProjectStateItem { const x = record(value); const type = x.type; const status = x.status; if (type !== "decision" && type !== "task" && type !== "question") throw new ContractError("项目事项类型无效"); if (!["active", "done", "cancelled", "unknown", "discarded", "superseded", "needs_review"].includes(String(status))) throw new ContractError("项目事项状态无效"); return { id: text(x.id), projectId: text(x.project_id), type, text: text(x.text), status: status as ProjectItemStatus, owner: nullable(x.owner), due: nullable(x.due), sourceMeetingId: nullable(x.source_meeting_id), sourceRefs: array(x.source_refs, parseSource), origin: text(x.origin), revision: number(x.revision), supersedesId: nullable(x.supersedes_id) }; }
function parseChange(value: unknown): ProjectChange { const x = record(value); const action = x.action ?? "create"; if (!["create", "supersede", "update", "discard"].includes(String(action))) throw new ContractError("候选变更类型无效"); return { action: action as ProjectChange["action"], ...(typeof x.item_id === "string" ? { itemId: x.item_id } : {}), ...(typeof x.type === "string" ? { type: x.type as ProjectItemType } : {}), ...(typeof x.text === "string" ? { text: x.text } : {}), ...(typeof x.status === "string" ? { status: x.status as ProjectItemStatus } : {}), ...("owner" in x ? { owner: nullable(x.owner) } : {}), ...("due" in x ? { due: nullable(x.due) } : {}), sourceRefs: array(x.source_refs ?? [], parseSource) }; }
function parseProposal(value: unknown): ProjectProposal { const x = record(value); const status = x.status; if (!["pending", "confirmed", "rejected", "needs_review"].includes(String(status))) throw new ContractError("候选审核状态无效"); return { id: text(x.id), projectId: text(x.project_id), meetingId: nullable(x.meeting_id), origin: text(x.origin), baseRevision: number(x.base_revision), changes: array(x.changes, parseChange), status: status as ProjectProposal["status"], revision: number(x.revision), isStale: x.is_stale === true }; }
function parsePage<T>(value: unknown, parse: (x: unknown) => T): KnowledgePage<T> { const x = record(value); if (typeof x.has_more !== "boolean") throw new ContractError("项目分页字段无效"); return { items: array(x.items, parse), nextCursor: nullable(x.next_cursor), hasMore: x.has_more }; }
function changePayload(input: ProjectChange) { return { action: input.action, item_id: input.itemId, type: input.type, text: input.text, status: input.status, owner: input.owner, due: input.due, source_refs: input.sourceRefs.map(sourceRefPayload) }; }
export class HttpProjectOutcomesApi implements ProjectOutcomesApi {
  private readonly baseUrl: string;
  readonly preparation: PreparationBriefApi;
  constructor(baseUrl = "") { this.baseUrl = baseUrl.trim().replace(/\/+$/, ""); this.preparation = new HttpPreparationBriefApi(baseUrl); }
  private async request(path: string, init: RequestInit = {}) { const response = await fetch(`${this.baseUrl}/v2/knowledge${path}`, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } }); const raw = await response.json().catch(() => null); if (!response.ok) { const detail = raw && typeof raw === "object" ? (raw as { detail?: unknown }).detail : null; throw new ApiError(response.status, typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `项目操作失败（${response.status}）`, raw); } return record(raw); }
  private query(path: string, values: Record<string, string | number | null | undefined>) { const q = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== null) q.set(key, String(value)); }); return `${path}?${q}`; }
  async listState(projectId: string, status?: string | null, cursor?: string | null, signal?: AbortSignal) { const x = await this.request(this.query(`/projects/${encodeURIComponent(projectId)}/state`, { status, cursor, limit: 25 }), { signal }); return { ...parsePage(x, parseItem), revision: number(x.project_revision) }; }
  async createState(projectId: string, input: ProjectItemInput, expectedProjectRevision: number) { const x = await this.request(`/projects/${encodeURIComponent(projectId)}/state`, { method: "POST", body: JSON.stringify({ type: input.type, text: input.text, status: input.status, owner: input.owner, due: input.due, source_meeting_id: input.sourceMeetingId, source_refs: (input.sourceRefs ?? []).map(sourceRefPayload), expected_project_revision: expectedProjectRevision }) }); return { item: parseItem(x.item), revision: number(x.project_revision) }; }
  async updateState(id: string, changes: Omit<Partial<ProjectItemInput>, "type" | "sourceMeetingId">, revision: number, projectRevision: number) { const x = await this.request(`/state/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ text: changes.text, status: changes.status, owner: changes.owner, due: changes.due, ...(changes.sourceRefs !== undefined ? { source_refs: changes.sourceRefs.map(sourceRefPayload) } : {}), expected_revision: revision, expected_project_revision: projectRevision }) }); return { item: parseItem(x.item), revision: number(x.project_revision) }; }
  async history(id: string, beforeRevision?: number | null) { const x = await this.request(this.query(`/state/${encodeURIComponent(id)}/history`, { before_revision: beforeRevision, limit: 25 })); return { items: array(x.items, (item) => { const h = record(item); return { snapshot: parseItem(h.snapshot), reason: text(h.reason) }; }), nextBeforeRevision: typeof x.next_before_revision === "number" ? x.next_before_revision : null }; }
  async listProposals(projectId: string, cursor?: string | null, signal?: AbortSignal) { return parsePage(await this.request(this.query(`/projects/${encodeURIComponent(projectId)}/proposals`, { cursor, limit: 25 }), { signal }), parseProposal); }
  async createProposal(projectId: string, changes: ProjectChange[], projectRevision: number, meetingId?: string | null) { return parseProposal((await this.request(`/projects/${encodeURIComponent(projectId)}/proposals`, { method: "POST", body: JSON.stringify({ changes: changes.map(changePayload), origin: "user", meeting_id: meetingId ?? null, expected_project_revision: projectRevision }) })).proposal); }
  async confirmProposal(id: string, revision: number, projectRevision: number) { const x = await this.request(`/proposals/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: revision, expected_project_revision: projectRevision }) }); return { proposal: parseProposal(x.proposal), items: array(x.items, parseItem), revision: number(x.project_revision) }; }
  async rejectProposal(id: string, revision: number) { return parseProposal((await this.request(`/proposals/${encodeURIComponent(id)}/reject`, { method: "POST", body: JSON.stringify({ expected_revision: revision }) })).proposal); }
  async getWiki(projectId: string, signal?: AbortSignal) { const x = await this.request(`/projects/${encodeURIComponent(projectId)}/wiki`, { signal }); return { projectId: text(x.project_id), revision: number(x.project_revision), markdown: text(x.markdown), items: array(x.items, parseItem), truncated: x.truncated === true }; }
  async getMeetingState(meetingId: string, signal?: AbortSignal) { const x = await this.request(`/meetings/${encodeURIComponent(meetingId)}/project-state`, { signal }); return { projectId: nullable(x.project_id), revision: typeof x.project_revision === "number" ? x.project_revision : null, decisions: array(x.decisions, parseItem), unfinishedTasks: array(x.unfinished_tasks, parseItem), openQuestions: array(x.open_questions, parseItem), truncated: x.truncated === true }; }
  async previewBrief(selection: MeetingKnowledgeSelection, signal?: AbortSignal): Promise<ProjectBriefPreview> {
    if (!selection.projectId) throw new ContractError("会前简报需要先选择项目");
    const x = await this.request(`/projects/${encodeURIComponent(selection.projectId)}/brief-preview`, { method: "POST", signal, body: JSON.stringify({ folder_id: selection.folderId, document_ids: selection.documentIds, limit: 20 }) });
    const decisions = array(x.decisions, parseItem); const unfinishedTasks = array(x.unfinished_tasks, parseItem); const openQuestions = array(x.open_questions, parseItem); const needsReview = array(x.needs_review, parseItem);
    if (decisions.some((item) => item.type !== "decision" || item.status !== "active") || unfinishedTasks.some((item) => item.type !== "task" || !["active", "unknown"].includes(item.status)) || openQuestions.some((item) => item.type !== "question" || !["active", "unknown"].includes(item.status)) || needsReview.some((item) => item.status !== "needs_review")) throw new ContractError("会前简报包含未确认或过期结论");
    const documentIds = x.document_ids === null ? null : array(x.document_ids, text);
    const result: ProjectBriefPreview = { projectId: text(x.project_id), projectName: text(x.project_name), revision: number(x.project_revision), folderId: nullable(x.folder_id), documentIds, generatedAtMs: number(x.generated_at_ms), decisions, unfinishedTasks, openQuestions, needsReview, needsReviewDocuments: array(x.needs_review_documents, (value) => { const document = record(value); if (document.source_status !== "needs_review" && document.source_status !== "deleted") throw new ContractError("待复核资料状态无效"); return { id: text(document.id), title: text(document.title), revision: number(document.revision), sourceStatus: document.source_status, activeVersion: number(document.active_version) }; }), truncated: x.truncated === true };
    if (result.projectId !== selection.projectId || result.folderId !== selection.folderId || JSON.stringify(result.documentIds === null ? null : [...result.documentIds].sort()) !== JSON.stringify(selection.documentIds === null ? null : [...new Set(selection.documentIds)].sort()) || [...decisions, ...unfinishedTasks, ...openQuestions, ...needsReview].some((item) => item.projectId !== result.projectId || item.sourceRefs.some((source) => source.projectId !== result.projectId))) throw new ContractError("会前简报返回的资料范围不一致");
    return result;
  }
  async getSource(reference: Pick<ProjectSourceRef, "sourceId" | "sourceType" | "projectId" | "meetingId">, signal?: AbortSignal) { const x = await this.request(this.query(`/outcome-sources/${encodeURIComponent(reference.sourceId)}`, { project_id: reference.projectId, source_type: reference.sourceType, meeting_id: reference.meetingId }), { signal }); return parseSource(x.source); }
}
