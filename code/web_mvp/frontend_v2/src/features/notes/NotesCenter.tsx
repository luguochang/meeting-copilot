import { Archive, ArchiveRestore, ArrowUpRight, FileText, Info, Link2, LoaderCircle, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MeetingApi } from "../../api/client";
import { ProductNavigation } from "../../components/ProductNavigation";
import type { MeetingHistoryItem, MeetingNote, MeetingNoteStatus } from "../../domain/events";
import { ProviderSettingsControl } from "../settings/ProviderSettingsControl";

interface NotesCenterProps {
  api: MeetingApi;
  onOpenMeetings: () => void;
  onOpenMeeting: (meetingId: string) => void;
  onOpenEvidence: (meetingId: string, segmentId: string) => void;
  onOpenCapabilities?: () => void;
}

function formatUpdatedAt(timestamp: number): string {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function meetingTitle(meetings: MeetingHistoryItem[], meetingId: string | null): string {
  if (!meetingId) return "原会议已删除";
  return meetings.find((meeting) => meeting.meetingId === meetingId)?.title || "未命名会议";
}

export function NotesCenter({
  api,
  onOpenMeetings,
  onOpenMeeting,
  onOpenEvidence,
  onOpenCapabilities,
}: NotesCenterProps) {
  const [notes, setNotes] = useState<MeetingNote[]>([]);
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [status, setStatus] = useState<Extract<MeetingNoteStatus, "active" | "archived">>("active");
  const [meetingFilter, setMeetingFilter] = useState("");
  const [query, setQuery] = useState("");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef(title);
  const bodyRef = useRef(body);
  const hydratedNoteIdRef = useRef<string | null>(null);
  titleRef.current = title;
  bodyRef.current = body;

  const selectedNote = useMemo(
    () => notes.find((note) => note.noteId === selectedNoteId) ?? null,
    [notes, selectedNoteId],
  );

  const loadNotes = useCallback(async () => {
    if (!api.listNotes) return;
    const nextNotes = await api.listNotes({
      meetingId: meetingFilter || null,
      status,
      query,
      limit: 300,
    });
    setNotes(nextNotes);
    setSelectedNoteId((current) => nextNotes.some((note) => note.noteId === current)
      ? current
      : nextNotes[0]?.noteId ?? null);
  }, [api, meetingFilter, query, status]);

  useEffect(() => {
    let cancelled = false;
    void api.listMeetings().then((history) => {
      if (!cancelled) setMeetings(history.meetings);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      void loadNotes().catch((loadError) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : "笔记加载失败");
      }).finally(() => {
        if (!cancelled) setLoading(false);
      });
    }, 160);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [loadNotes]);

  useEffect(() => {
    const changedSelection = hydratedNoteIdRef.current !== (selectedNote?.noteId ?? null);
    hydratedNoteIdRef.current = selectedNote?.noteId ?? null;
    setTitle(selectedNote?.title ?? "");
    setBody(selectedNote?.body ?? "");
    setDirty(false);
    if (changedSelection) setSaveState("idle");
  }, [selectedNote?.body, selectedNote?.noteId, selectedNote?.title]);

  useEffect(() => {
    if (!dirty || !selectedNote || !api.updateNote) return;
    const updateNote = api.updateNote;
    const noteId = selectedNote.noteId;
    const expectedVersion = selectedNote.version;
    const nextTitle = title.trim();
    const nextBody = body.trim();
    if (!nextTitle || !nextBody) return;
    const timer = window.setTimeout(() => {
      setSaveState("saving");
      void updateNote(noteId, expectedVersion, { title: nextTitle, body: nextBody })
        .then((updated) => {
          setNotes((current) => current.map((note) => note.noteId === updated.noteId ? updated : note));
          if (titleRef.current.trim() === nextTitle && bodyRef.current.trim() === nextBody) setDirty(false);
          setSaveState("saved");
        })
        .catch((saveError) => {
          setSaveState("error");
          setError(saveError instanceof Error ? saveError.message : "自动保存失败");
        });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [api, body, dirty, selectedNote, title]);

  const updateStatus = async (nextStatus: MeetingNoteStatus) => {
    if (!selectedNote || !api.updateNote) return;
    try {
      const updated = await api.updateNote(selectedNote.noteId, selectedNote.version, { status: nextStatus });
      setNotes((current) => current.filter((note) => note.noteId !== updated.noteId));
      setSelectedNoteId(null);
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "笔记状态更新失败");
    }
  };

  const deleteNote = async () => {
    if (!selectedNote || !api.deleteNote) return;
    try {
      await api.deleteNote(selectedNote.noteId, selectedNote.version);
      setNotes((current) => current.filter((note) => note.noteId !== selectedNote.noteId));
      setSelectedNoteId(null);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "笔记删除失败");
    }
  };

  return (
    <div className="product-app product-app--notes">
      <ProductNavigation active="notes" onOpenMeetings={onOpenMeetings} onOpenCapabilities={onOpenCapabilities} />
      <div className="notes-shell">
        <header className="notes-header">
          <div>
            <span className="eyebrow">会议笔记</span>
            <h1>笔记</h1>
            <p>编辑会议笔记并核对关联原文。</p>
          </div>
          <ProviderSettingsControl />
        </header>

        <main className="notes-layout">
          <aside className="notes-index" aria-label="笔记列表">
            <div className="notes-index-controls">
              <label className="notes-search">
                <Search size={15} aria-hidden="true" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索笔记" aria-label="搜索笔记" />
              </label>
              <select value={meetingFilter} onChange={(event) => setMeetingFilter(event.target.value)} aria-label="按会议筛选笔记">
                <option value="">全部会议</option>
                {meetings.map((meeting) => (
                  <option value={meeting.meetingId} key={meeting.meetingId}>{meeting.title || "未命名会议"}</option>
                ))}
              </select>
              <div className="notes-status-switch" role="group" aria-label="笔记状态">
                <button type="button" aria-pressed={status === "active"} onClick={() => setStatus("active")}>当前</button>
                <button type="button" aria-pressed={status === "archived"} onClick={() => setStatus("archived")}>已归档</button>
              </div>
            </div>

            <div className="notes-list">
              {loading ? <p className="notes-empty"><LoaderCircle className="spin" size={18} />正在加载笔记</p> : null}
              {!loading && !notes.length ? <p className="notes-empty">没有匹配的笔记</p> : null}
              {notes.map((note) => (
                <button
                  type="button"
                  className={`notes-list-item${note.noteId === selectedNoteId ? " is-selected" : ""}`}
                  onClick={() => setSelectedNoteId(note.noteId)}
                  key={note.noteId}
                >
                  <strong>{note.title}</strong>
                  <span>{note.body}</span>
                  <small>{meetingTitle(meetings, note.meetingId)} · {formatUpdatedAt(note.updatedAtMs)}</small>
                </button>
              ))}
            </div>
          </aside>

          <section className="note-editor" aria-label="笔记编辑器">
            {!selectedNote ? (
              <div className="note-editor-empty"><FileText size={24} /><p>从左侧选择一条笔记</p></div>
            ) : (
              <>
                <header className="note-editor-toolbar">
                  <button
                    type="button"
                    className="note-meeting-link"
                    onClick={() => selectedNote.meetingId && onOpenMeeting(selectedNote.meetingId)}
                    disabled={!selectedNote.meetingId}
                  >
                    {meetingTitle(meetings, selectedNote.meetingId)}<ArrowUpRight size={14} />
                  </button>
                  <span className={`note-save-state note-save-state--${saveState}`}>
                    {saveState === "saving" ? "正在保存" : saveState === "error" ? "保存失败" : saveState === "saved" ? "已保存" : "自动保存"}
                  </span>
                  <button
                    className="icon-button icon-button--small"
                    type="button"
                    onClick={() => void updateStatus(status === "archived" ? "active" : "archived")}
                    title={status === "archived" ? "恢复笔记" : "归档笔记"}
                    aria-label={status === "archived" ? "恢复笔记" : "归档笔记"}
                  >
                    {status === "archived" ? <ArchiveRestore size={16} /> : <Archive size={16} />}
                  </button>
                  <button className="icon-button icon-button--small" type="button" onClick={() => void deleteNote()} title="删除笔记" aria-label="删除笔记">
                    <Trash2 size={16} />
                  </button>
                </header>
                <input
                  className="note-title-input"
                  value={title}
                  onChange={(event) => { setTitle(event.target.value); setDirty(true); }}
                  aria-label="笔记标题"
                  maxLength={200}
                />
                <textarea
                  className="note-body-input"
                  value={body}
                  onChange={(event) => { setBody(event.target.value); setDirty(true); }}
                  aria-label="笔记正文"
                />
                <section className="note-evidence-list" aria-label="笔记依据">
                  <h2>原文依据</h2>
                  {!selectedNote.evidence.length ? <p>这条笔记没有关联原文。</p> : null}
                  {selectedNote.evidence.map((evidence) => (
                    <button
                      type="button"
                      key={`${selectedNote.noteId}:${evidence.ordinal}`}
                      onClick={() => evidence.meetingId && onOpenEvidence(evidence.meetingId, evidence.segmentId)}
                      disabled={!evidence.meetingId}
                    >
                      <span>{evidence.startMs === null ? "原文" : `${Math.floor(evidence.startMs / 60_000)}:${String(Math.floor(evidence.startMs / 1_000) % 60).padStart(2, "0")}`}</span>
                      <q>{evidence.quote}</q>
                      <ArrowUpRight size={14} />
                    </button>
                  ))}
                </section>
              </>
            )}
            {error ? <p className="inline-error note-editor-error" role="alert">{error}</p> : null}
          </section>

          <aside className="note-context-rail" aria-label="笔记信息">
            <header>
              <div><Info size={17} /><h2>笔记信息</h2></div>
            </header>
            {!selectedNote ? (
              <p className="note-context-empty">选择一条笔记后查看来源和关联原文。</p>
            ) : (
              <>
                <dl className="note-context-facts">
                  <div><dt>所属会议</dt><dd>{meetingTitle(meetings, selectedNote.meetingId)}</dd></div>
                  <div><dt>更新时间</dt><dd>{formatUpdatedAt(selectedNote.updatedAtMs)}</dd></div>
                  <div><dt>状态</dt><dd>{status === "archived" ? "已归档" : "当前笔记"}</dd></div>
                  <div><dt>关联原文</dt><dd>{selectedNote.evidence.length} 条</dd></div>
                </dl>
                <section className="note-context-evidence" aria-label="关联原文摘要">
                  <div className="note-context-section-title"><Link2 size={15} /><h3>证据与来源</h3></div>
                  {!selectedNote.evidence.length ? <p>这条笔记没有关联原文。</p> : null}
                  {selectedNote.evidence.slice(0, 4).map((evidence) => (
                    <button
                      type="button"
                      key={`rail:${selectedNote.noteId}:${evidence.ordinal}`}
                      aria-label={`从笔记信息打开第 ${evidence.ordinal + 1} 条原文证据`}
                      onClick={() => evidence.meetingId && onOpenEvidence(evidence.meetingId, evidence.segmentId)}
                      disabled={!evidence.meetingId}
                    >
                      <span>{evidence.startMs === null ? "原文" : `${Math.floor(evidence.startMs / 60_000)}:${String(Math.floor(evidence.startMs / 1_000) % 60).padStart(2, "0")}`}</span>
                      <q>{evidence.quote}</q>
                      <ArrowUpRight size={13} />
                    </button>
                  ))}
                </section>
              </>
            )}
          </aside>
        </main>
      </div>
    </div>
  );
}
