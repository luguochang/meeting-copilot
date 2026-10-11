import { useState } from "react";
import type { KnowledgeApi } from "../../api/knowledge";
import { isTranscriptEvidence } from "../../api/sourceEvidence";
import type { AskAiEvidence, DocumentSourceEvidence } from "../../domain/events";

function locatorLabel(value: Record<string, unknown>): string { return typeof value.page === "number" ? `第 ${value.page} 页` : typeof value.paragraph === "number" ? `第 ${value.paragraph} 段` : "转换文本"; }
function historicalAttachmentNotice(reason?: string): string {
  if (reason === "attachment_scope_or_version_changed") return "这一版附件的使用范围或版本已变化；当前打开的是历史原文，仅供核对，不作为当前建议的依据。";
  return "这一版附件已不用于当前建议；正在打开历史原文供核对。";
}
export function SourceEvidenceList({ evidence, api, meetingId, onTranscript, onDocument, stale = false }: {
  evidence: AskAiEvidence[]; api?: KnowledgeApi; meetingId: string; onTranscript: (segmentId: string) => void;
  onDocument?: (value: DocumentSourceEvidence, sourceMeetingId?: string) => void; stale?: boolean;
}) {
  const [error, setError] = useState<string | null>(null); const [checking, setChecking] = useState<string | null>(null);
  const open = async (value: DocumentSourceEvidence) => {
    setError(null); if (!meetingId) { setError("原会议不存在，无法验证这条历史资料引用。请在资料库核对它的版本。"); return; } if (!api || !onDocument) { setError("当前无法验证或打开资料来源，请到资料库核对这一版。"); return; }
    setChecking(value.sourceId);
    try {
      if (value.sourceType === "meeting_attachment") {
        if (!api.getMeetingAttachmentSource) throw new Error("本场附件来源接口暂不可用。");
        const source = await api.getMeetingAttachmentSource(meetingId, value.sourceId);
        if (source.meetingId !== meetingId || source.attachmentId !== value.attachmentId || source.versionId !== value.versionId || source.contentHash !== value.contentHash) throw new Error("附件来源身份不匹配，无法打开。");
        // Historical same-meeting sources remain inspectable, with an explicit
        // stale notice. They must never become current facts merely by opening.
        if (stale || source.sourceState === "stale") setError(historicalAttachmentNotice(source.staleReason));
        onDocument(source, meetingId);
        return;
      }
      const context = await api.getMeetingContext(meetingId);
      const source = await api.getMeetingSource(value.sourceId, meetingId);
      if (stale || context.revision !== value.contextRevision || source.versionId !== value.versionId || source.contentHash !== value.contentHash || source.documentId !== value.documentId) throw new Error("此引用已过期：资料版本或本场范围已变化。保留旧内容供核对，请重新提问获取有效依据。");
      onDocument(value, meetingId);
    } catch (e) { setError(`无法打开这条有效来源；它可能已过期或不在本场范围。${e instanceof Error ? e.message : "请重新核对资料。"}`); }
    finally { setChecking(null); }
  };
  if (!evidence.length) return null;
  return <section className="typed-source-list" aria-label="回答来源"><details><summary>查看全部依据 · {evidence.length} 条</summary>{evidence.map((item, index) => isTranscriptEvidence(item)
    ? <button type="button" key={`transcript:${item.segmentId}:${index}`} title={item.quote} onClick={() => onTranscript(item.segmentId)}>会议原话 {index + 1}{item.revision ? ` · 第 ${item.revision} 版` : ""}<small>{item.quote}</small></button>
    : <button type="button" key={`${item.sourceType}:${item.sourceId}:${index}`} title={item.quote} disabled={checking !== null} onClick={() => void open(item)}>{checking === item.sourceId ? "正在核对来源…" : `${item.sourceType === "meeting_attachment" ? "本场附件" : "资料"} · ${item.title} · 第 ${item.version} 版 · ${locatorLabel(item.locator)}`}<small>{item.quote}</small></button>)}</details>{error ? <p role="alert" className="inline-error">{error}</p> : null}</section>;
}
