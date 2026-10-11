import type { AskAiEvidence, DocumentSourceEvidence, TranscriptSourceEvidence } from "../domain/events";
import { ContractError } from "./schema";

export function isTranscriptEvidence(value: AskAiEvidence): value is TranscriptSourceEvidence { return value.sourceType !== "document" && value.sourceType !== "meeting_attachment"; }
export function parseDocumentSourceEvidence(raw: unknown): DocumentSourceEvidence {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ContractError("资料引用必须是对象");
  const item = raw as Record<string, unknown>;
  const pick = (snake: string, camel: string) => item[snake] ?? item[camel];
  const text = (snake: string, camel: string) => { const value = pick(snake, camel); if (typeof value !== "string" || !value.trim()) throw new ContractError(`资料引用 ${snake} 缺失`); return value; };
  const integer = (snake: string, camel: string, minimum: number) => { const value = pick(snake, camel); if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) throw new ContractError(`资料引用 ${snake} 无效`); return value; };
  const type = pick("source_type", "sourceType");
  if (type !== "document" && type !== "meeting_attachment") throw new ContractError("来源不是资料引用");
  const locator = item.locator;
  if (!locator || typeof locator !== "object" || Array.isArray(locator)) throw new ContractError("资料引用缺少原文位置");
  if (type === "meeting_attachment") return { sourceType: "meeting_attachment", sourceId: text("source_id", "sourceId"), setId: text("set_id", "setId"), meetingId: text("meeting_id", "meetingId"), attachmentId: text("attachment_id", "attachmentId"), title: text("title", "title"), versionId: text("version_id", "versionId"), version: integer("version", "version", 1), contentHash: text("content_hash", "contentHash"), locator: locator as Record<string, unknown>, quote: text("quote", "quote"), contextRevision: integer("context_revision", "contextRevision", 0), ...(item.ordinal !== undefined ? { ordinal: integer("ordinal", "ordinal", 0) } : {}) };
  return { sourceType: "document", sourceId: text("source_id", "sourceId"), documentId: text("document_id", "documentId"), projectId: text("project_id", "projectId"), title: text("title", "title"), versionId: text("version_id", "versionId"), version: integer("version", "version", 1), contentHash: text("content_hash", "contentHash"), locator: locator as Record<string, unknown>, quote: text("quote", "quote"), contextRevision: integer("context_revision", "contextRevision", 0) };
}
export function parseDocumentSources(value: unknown): DocumentSourceEvidence[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 20) throw new ContractError("资料引用列表无效");
  return value.map(parseDocumentSourceEvidence);
}

export function serializeDocumentSource(item: DocumentSourceEvidence): Record<string, unknown> {
  return { source_type: item.sourceType, source_id: item.sourceId, title: item.title, version_id: item.versionId, version: item.version, content_hash: item.contentHash, locator: item.locator, quote: item.quote, context_revision: item.contextRevision,
    ...(item.sourceType === "document" ? { document_id: item.documentId, project_id: item.projectId } : { set_id: item.setId, meeting_id: item.meetingId, attachment_id: item.attachmentId }) };
}
