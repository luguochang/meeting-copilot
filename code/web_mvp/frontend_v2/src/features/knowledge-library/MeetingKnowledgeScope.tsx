import { useEffect, useState } from "react";
import type { KnowledgeApi, MeetingKnowledgeContext, MeetingKnowledgeSelection } from "../../api/knowledge";
import { KnowledgeScopePicker } from "./KnowledgeScopePicker";

export function MeetingKnowledgeScope({ api, meetingId, onSaved }: { api: KnowledgeApi; meetingId: string; onSaved?: () => void }) {
  const [saved, setSaved] = useState<MeetingKnowledgeContext | null>(null); const [draft, setDraft] = useState<MeetingKnowledgeSelection | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  useEffect(() => { const controller = new AbortController(); setSaved(null); setDraft(null); void api.getMeetingContext(meetingId, controller.signal).then((value) => { if (!controller.signal.aborted) { setSaved(value); setDraft(value); } }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "会议资料范围读取失败"); }); return () => controller.abort(); }, [api, meetingId, refresh]);
  const dirty = saved && draft && (saved.projectId !== draft.projectId || saved.folderId !== draft.folderId || JSON.stringify(saved.documentIds) !== JSON.stringify(draft.documentIds));
  const save = async () => { if (!draft || !saved || busy) return; setBusy(true); setError(null); try { const value = await api.saveMeetingContext(meetingId, draft, saved.revision); setSaved(value); setDraft(value); setNotice("资料范围已保存，后续回答使用这一范围。已有引用会重新检查有效性。"); onSaved?.(); } catch (e) { setError(e instanceof Error ? e.message : "资料范围保存失败"); setNotice(null); } finally { setBusy(false); } };
  return <section className="meeting-knowledge-scope" aria-label="会议资料设置">{draft ? <KnowledgeScopePicker api={api} value={draft} onChange={(value) => { setDraft(value); setNotice(null); }} disabled={busy} /> : !error ? <p role="status">正在读取会议资料范围…</p> : null}
    {error ? <p role="alert" className="inline-error">{error}<button type="button" disabled={busy} onClick={() => { setError(null); setNotice(null); setRefresh((x) => x + 1); }}>重新读取已保存范围</button></p> : null}
    {notice ? <p role="status">{notice}</p> : dirty ? <p role="status">范围已修改，尚未保存。</p> : saved ? <p>当前范围已保存。</p> : null}
    <button type="button" className="primary-button" onClick={() => void save()} disabled={!saved || !draft || busy || !dirty}>{busy ? "正在保存资料范围" : "保存资料范围"}</button>
  </section>;
}
