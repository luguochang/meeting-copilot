import { ApiError } from "./client";
import { ContractError } from "./schema";
import { parseDocumentSourceEvidence } from "./sourceEvidence";
import type { MeetingAttachmentSourceEvidence } from "../domain/events";

export interface MaterialSet { id: string; state: "draft" | "bound" | "deleted"; meeting_id: string | null; revision: number; context_revision: number }
export interface MeetingAttachment {
  id: string; set_id: string; title: string; filename: string; purpose: string; status: string; import_status: string;
  active_version_id: string | null; latest_version_id: string | null; enabled: boolean; selected_span_ids: string[] | null;
  revision: number; content_revision: number; file_size_bytes: number; error_message: string | null;
}
export interface AttachmentVersion { id: string; attachment_id: string; number: number; content_hash: string; content_characters: number; origin: string; warnings: string[]; structure: Record<string, unknown> }
export interface AttachmentSpan { id: string; version_id: string; ordinal: number; text: string; locator: Record<string, unknown> }
export interface MaterialJob { id: string; status: string; error_message: string | null }
export interface AttachmentDetail { attachment: MeetingAttachment; version: AttachmentVersion | null; latest_version_id: string | null; job: MaterialJob | null }
export interface MaterialsApi {
  readonly storageScope?: string;
  create(): Promise<MaterialSet>;
  get(setId: string, signal?: AbortSignal): Promise<{ material_set: MaterialSet; attachments: MeetingAttachment[] }>;
  forMeeting(meetingId: string): Promise<MaterialSet>;
  getForMeeting(meetingId: string, signal?: AbortSignal): Promise<MaterialSet | null>;
  upload(setId: string, file: File): Promise<{ attachment: MeetingAttachment; job: MaterialJob; material_set?: MaterialSet }>;
  detail(setId: string, attachmentId: string, signal?: AbortSignal): Promise<AttachmentDetail>;
  spans(setId: string, attachmentId: string, versionId: string, offset?: number, signal?: AbortSignal): Promise<{ items: AttachmentSpan[]; has_more: boolean; next_offset: number | null; version?: AttachmentVersion | null }>;
  save(setId: string, attachmentId: string, content: string, expectedRevision: number, baseVersionId: string): Promise<{ attachment: MeetingAttachment; version: AttachmentVersion }>;
  use(setId: string, attachmentId: string, versionId: string | null, enabled: boolean, expectedRevision: number, selectedSpanIds?: string[] | null): Promise<{ attachment: MeetingAttachment; material_set: MaterialSet }>;
  retry(setId: string, jobId: string): Promise<MaterialJob>;
  cancel(setId: string, jobId: string): Promise<MaterialJob>;
  remove(setId: string, attachmentId: string, expectedRevision: number): Promise<{ attachment: MeetingAttachment; job: MaterialJob }>;
  removeDraft(setId: string, expectedRevision: number): Promise<MaterialSet>;
  source(meetingId: string, sourceId: string): Promise<MeetingAttachmentSourceEvidence & { sourceState?: string; staleReason?: string }>;
}
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw new ContractError("本场资料响应格式错误"); return value as Record<string, unknown>; }
function set(value: unknown): MaterialSet { const x = record(value); if (typeof x.id !== "string" || !["draft", "bound", "deleted"].includes(String(x.state)) || !Number.isSafeInteger(x.revision) || !Number.isSafeInteger(x.context_revision)) throw new ContractError("本场资料集合格式错误"); return x as unknown as MaterialSet; }
function attachment(value: unknown): MeetingAttachment { const x = record(value); if (typeof x.id !== "string" || typeof x.set_id !== "string" || typeof x.title !== "string" || !Number.isSafeInteger(x.revision) || typeof x.enabled !== "boolean") throw new ContractError("本场附件格式错误"); return x as unknown as MeetingAttachment; }
function version(value: unknown): AttachmentVersion | null { if (value == null) return null; const x = record(value); if (typeof x.id !== "string" || typeof x.content_hash !== "string" || !Array.isArray(x.warnings) || !Number.isSafeInteger(x.number)) throw new ContractError("附件版本格式错误"); return x as unknown as AttachmentVersion; }
function job(value: unknown): MaterialJob | null { if (value == null) return null; const x = record(value); if (typeof x.id !== "string" || typeof x.status !== "string") throw new ContractError("资料任务格式错误"); return x as unknown as MaterialJob; }

export class HttpMaterialsApi implements MaterialsApi {
  readonly storageScope: string;
  constructor(private baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    const service = new URL(this.baseUrl || "/", window.location.origin);
    this.storageScope = `${service.origin}${service.pathname.replace(/\/+$/, "")}`;
  }
  private async request(path: string, init: RequestInit = {}): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers: { Accept: "application/json", ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...init.headers } });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) { const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : null; const message = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string" ? detail.message : `本场资料操作失败（${response.status}）`; throw new ApiError(response.status, message, body); }
    return record(body);
  }
  private route(setId: string, attachmentId?: string) { return `/v2/material-sets/${encodeURIComponent(setId)}${attachmentId ? `/attachments/${encodeURIComponent(attachmentId)}` : ""}`; }
  async create() { return set((await this.request("/v2/material-sets", { method: "POST", body: "{}" })).material_set); }
  async get(setId: string, signal?: AbortSignal) { const x = await this.request(this.route(setId), { signal }); if (!Array.isArray(x.attachments)) throw new ContractError("本场附件列表格式错误"); return { material_set: set(x.material_set), attachments: x.attachments.map(attachment) }; }
  async forMeeting(meetingId: string) { return set((await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/material-set`, { method: "POST", body: "{}" })).material_set); }
  async getForMeeting(meetingId: string, signal?: AbortSignal) { const x = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/material-set`, { signal }); return x.material_set === null ? null : set(x.material_set); }
  async upload(setId: string, file: File) { const body = new FormData(); body.append("file", file); const x = await this.request(`${this.route(setId)}/attachments`, { method: "POST", body }); const task = job(x.job); if (!task) throw new ContractError("转换任务未返回编号"); return { attachment: attachment(x.attachment), job: task, ...(x.material_set ? { material_set: set(x.material_set) } : {}) }; }
  async detail(setId: string, attachmentId: string, signal?: AbortSignal) { const x = await this.request(this.route(setId, attachmentId), { signal }); return { attachment: attachment(x.attachment), version: version(x.version), latest_version_id: typeof x.latest_version_id === "string" ? x.latest_version_id : null, job: job(x.job) }; }
  async spans(setId: string, attachmentId: string, versionId: string, offset = 0, signal?: AbortSignal) { const x = await this.request(`${this.route(setId, attachmentId)}/versions/${encodeURIComponent(versionId)}/spans?offset=${offset}&limit=50`, { signal }); if (!Array.isArray(x.items) || typeof x.has_more !== "boolean" || (x.has_more && (!Number.isSafeInteger(x.next_offset) || (x.next_offset as number) <= offset))) throw new ContractError("正文分页格式错误"); const items = x.items.map((item) => { const s = record(item); if (typeof s.id !== "string" || typeof s.text !== "string" || !Number.isSafeInteger(s.ordinal)) throw new ContractError("正文段落格式错误"); return s as unknown as AttachmentSpan; }); const v = version(x.version); if (v && v.id !== versionId) throw new ContractError("正文版本与请求不一致"); return { items, has_more: x.has_more, next_offset: typeof x.next_offset === "number" ? x.next_offset : null, version: v }; }
  async save(setId: string, attachmentId: string, content: string, expectedRevision: number, baseVersionId: string) { const x = await this.request(`${this.route(setId, attachmentId)}/versions`, { method: "POST", body: JSON.stringify({ content, expected_revision: expectedRevision, base_version_id: baseVersionId }) }); const v = version(x.version); if (!v) throw new ContractError("保存正文未返回版本"); return { attachment: attachment(x.attachment), version: v }; }
  async use(setId: string, attachmentId: string, versionId: string | null, enabled: boolean, expectedRevision: number, selectedSpanIds: string[] | null = null) { const x = await this.request(`${this.route(setId, attachmentId)}/usage`, { method: "PUT", body: JSON.stringify({ version_id: versionId, enabled, selected_span_ids: selectedSpanIds, expected_revision: expectedRevision }) }); return { attachment: attachment(x.attachment), material_set: set(x.material_set) }; }
  private async jobAction(setId: string, jobId: string, action: "retry" | "cancel") { const x = await this.request(`${this.route(setId)}/jobs/${encodeURIComponent(jobId)}/${action}`, { method: "POST", body: "{}" }); const value = job(x.job); if (!value) throw new ContractError("资料任务未返回编号"); return value; }
  async retry(setId: string, jobId: string) { return this.jobAction(setId, jobId, "retry"); }
  async cancel(setId: string, jobId: string) { return this.jobAction(setId, jobId, "cancel"); }
  async remove(setId: string, attachmentId: string, expectedRevision: number) { const x = await this.request(`${this.route(setId, attachmentId)}?expected_revision=${expectedRevision}`, { method: "DELETE" }); const task = job(x.job); if (!task) throw new ContractError("移除资料未返回清理任务"); return { attachment: attachment(x.attachment), job: task }; }
  async removeDraft(setId: string, expectedRevision: number) { return set((await this.request(`${this.route(setId)}?expected_revision=${expectedRevision}`, { method: "DELETE" })).material_set); }
  async source(meetingId: string, sourceId: string) { const raw = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/sources/${encodeURIComponent(sourceId)}?allow_historical=true`); const x = record(raw.source); if (x.source_state === "deleted") throw new Error("这条来源正文已删除，仅保留历史来源标记，无法打开原文。"); const s = parseDocumentSourceEvidence(x); if (s.sourceType !== "meeting_attachment") throw new ContractError("来源不是本场附件"); return { ...s, sourceState: typeof x.source_state === "string" ? x.source_state : undefined, staleReason: typeof x.stale_reason === "string" ? x.stale_reason : undefined }; }
}
