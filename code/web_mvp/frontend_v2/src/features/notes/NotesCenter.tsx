import type { KnowledgeApi } from "../../api/knowledge";
import type { DocumentSourceEvidence } from "../../domain/events";
import { SourceEvidenceList } from "../knowledge-library/SourceEvidenceList";
import { SourceLibraryImport } from "../knowledge-library/SourceLibraryImport";
import { Archive, ArchiveRestore, ArrowUpRight, FileText, Info, Link2, LoaderCircle, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MeetingApi } from "../../api/client";
import { ProductNavigation, type ActiveMeetingNavigation } from "../../components/ProductNavigation";
import type { MeetingHistoryItem, MeetingNote, MeetingNoteStatus } from "../../domain/events";
import { ProviderSettingsControl } from "../settings/ProviderSettingsControl";

interface NotesCenterProps {
  onOpenKnowledgeDocument?: (target: { documentId: string; projectId: string }) => void;
  knowledgeApi?: KnowledgeApi;
  onKnowledgeSource?: (value: DocumentSourceEvidence) => void;
  api: MeetingApi;
  onOpenMeetings: () => void;
  onOpenMeeting: (meetingId: string) => void;
  onOpenEvidence: (meetingId: string, segmentId: string) => void;
  onOpenCapabilities?: () => void;
  onOpenKnowledge?: () => void;
  activeMeeting?: ActiveMeetingNavigation | null;
  onOpenActiveMeeting?: () => void;
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
  onOpenKnowledgeDocument,
  knowledgeApi,
  onKnowledgeSource,
  api,
  onOpenMeetings,
  onOpenMeeting,
  onOpenEvidence,
  onOpenCapabilities,
  onOpenKnowledge,
  activeMeeting,
  onOpenActiveMeeting,
}: NotesCenterProps) {
  const [notes, setNotes] = useState<MeetingNote[]>([]);
  const [cursor, setCursor] = useState<string | null>(null); const [nextCursor, setNextCursor] = useState<string | null>(null); const [previous, setPrevious] = useState<(string | null)[]>([]); const [importing, setImporting] = useState(false);
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [status, setStatus] = useState<Extract<MeetingNoteStatus, "active" | "archived">>("active");
  const [meetingFilter, setMeetingFilter] = useState("");
  const [query, setQuery] = useState("");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveTick, setSaveTick] = useState(0); const saveInFlight = useRef(false); const [latestCompared, setLatestCompared] = useState<string | null>(null);
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

  const loadNotes = useCallback(async (signal?: AbortSignal) => {
    if (!api.listNotes && !api.listNotesPage) return;
    const queryInput = {
      meetingId: meetingFilter || null,
      status,
      query,
      limit: api.listNotesPage ? 50 : 300,
    };
    const page = api.listNotesPage ? await api.listNotesPage({ ...queryInput, cursor }, signal) : { items: await api.listNotes!(queryInput, signal), nextCursor: null, hasMore: false };
    if (signal?.aborted) return;
    const nextNotes = page.items; setNextCursor(page.hasMore ? page.nextCursor : null);
    setNotes(nextNotes);
    setSelectedNoteId((current) => nextNotes.some((note) => note.noteId === current)
      ? current
      : nextNotes[0]?.noteId ?? null);
  }, [api, cursor, meetingFilter, query, status]);

  useEffect(() => {
    let cancelled = false;
    void api.listMeetings().then((history) => {
      if (!cancelled) setMeetings(history.meetings);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      void loadNotes(controller.signal).catch((loadError) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : "笔记加载失败");
      }).finally(() => {
        if (!cancelled) setLoading(false);
      });
    }, 160);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [loadNotes]);

  useEffect(() => {
    const changedSelection = hydratedNoteIdRef.current !== (selectedNote?.noteId ?? null);
    if (!changedSelection && dirtyRef.current) return;
    hydratedNoteIdRef.current = selectedNote?.noteId ?? null;
    setTitle(selectedNote?.title ?? "");
    setBody(selectedNote?.body ?? "");
    setDirty(false);
    if (changedSelection) setSaveState("idle");
  }, [selectedNote?.body, selectedNote?.noteId, selectedNote?.title]);
  useEffect(() => { if (!dirty) return; const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", beforeUnload); return () => window.removeEventListener("beforeunload", beforeUnload); }, [dirty]);
  const resetPage = () => { setCursor(null); setPrevious([]); };
  const readLatest = async () => {
    if (!selectedNote || !api.getNote) return;
    try { const latest = await api.getNote(selectedNote.noteId); setNotes((rows) => rows.map((row) => row.noteId === latest.noteId ? latest : row)); setLatestCompared(`${latest.title}\n${latest.body}`); setError("已读取最新原文，保留你的编辑。比较后再明确继续保存。"); }
    catch (e) { setError(e instanceof Error ? e.message : "最新笔记读取失败。"); }
  };
  const guard = (action: (() => void) | undefined) => { if (dirty || saveState === "saving") { setError("请等待当前笔记保存成功后再切换。保存失败时，内容仍保留，可继续编辑后重试。"); return; } action?.(); };

  useEffect(() => {
    if (!dirty || !selectedNote || !api.updateNote || saveInFlight.current || saveState === "error") return;
    const updateNote = api.updateNote.bind(api);
    const noteId = selectedNote.noteId;
    const expectedVersion = selectedNote.version;
    const nextTitle = title.trim();
    const nextBody = body.trim();
    if (!nextTitle || !nextBody) return;
    const timer = window.setTimeout(() => {
      saveInFlight.current = true;
      setSaveState("saving");
      void updateNote(noteId, expectedVersion, { title: nextTitle, body: nextBody })
        .then((updated) => {
          setNotes((current) => current.map((note) => note.noteId === updated.noteId ? updated : note));
          const matches = titleRef.current.trim() === nextTitle && bodyRef.current.trim() === nextBody;
          if (matches) setDirty(false);
          setSaveState(matches ? "saved" : "idle");
        })
        .catch((saveError) => {
          setSaveState("error");
          setError(saveError instanceof Error ? saveError.message : "自动保存失败");
        }).finally(() => { saveInFlight.current = false; setSaveTick((x) => x + 1); });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [api, body, dirty, saveState, saveTick, selectedNote, title]);

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
      <ProductNavigation
        active="notes"
        onOpenMeetings={() => guard(onOpenMeetings)}
        onOpenCapabilities={() => guard(onOpenCapabilities)} onOpenKnowledge={() => guard(onOpenKnowledge)}
        activeMeeting={activeMeeting}
        onOpenActiveMeeting={() => guard(onOpenActiveMeeting)}
      />
      <div className="notes-shell">
        <header className="notes-header">
          <div>
            <span className="eyebrow">会议笔记</span>
            <h1>笔记</h1>
            <p>保存会议问答中的重要回答，随时回看并核对关联原文。</p>
          </div>
          <ProviderSettingsControl />
        </header>

        <main className="notes-layout">
          <aside className="notes-index" aria-label="笔记列表">
            <div className="notes-index-controls">
              <label className="notes-search">
                <Search size={15} aria-hidden="true" />
                <input disabled={dirty || saveState === "saving"} value={query} onChange={(event) => { setQuery(event.target.value); resetPage(); }} placeholder="搜索笔记" aria-label="搜索笔记" />
              </label>
              <select disabled={dirty || saveState === "saving"} value={meetingFilter} onChange={(event) => { setMeetingFilter(event.target.value); resetPage(); }} aria-label="按会议筛选笔记">
                <option value="">全部会议</option>
                {meetings.map((meeting) => (
                  <option value={meeting.meetingId} key={meeting.meetingId}>{meeting.title || "未命名会议"}</option>
                ))}
              </select>
              <div className="notes-status-switch" role="group" aria-label="笔记状态">
                <button type="button" disabled={dirty || saveState === "saving"} aria-pressed={status === "active"} onClick={() => { setStatus("active"); resetPage(); }}>当前</button>
                <button type="button" disabled={dirty || saveState === "saving"} aria-pressed={status === "archived"} onClick={() => { setStatus("archived"); resetPage(); }}>已归档</button>
              </div>
            </div>

            <div className="notes-list">
              {loading ? <p className="notes-empty"><LoaderCircle className="spin" size={18} />正在加载笔记</p> : null}
              {!loading && !notes.length ? <p className="notes-empty">{query || meetingFilter || status === "archived" || cursor ? "没有匹配的笔记，可调整搜索或会议筛选。" : "尚未保存会议笔记"}</p> : null}
              {notes.map((note) => (
                <button
                  type="button"
                  className={`notes-list-item${note.noteId === selectedNoteId ? " is-selected" : ""}`}
                  onClick={() => guard(() => setSelectedNoteId(note.noteId))}
                  key={note.noteId}
                >
                  <strong>{note.title}</strong>
                  <span>{note.body}</span>
                  <small>{meetingTitle(meetings, note.meetingId)} · {formatUpdatedAt(note.updatedAtMs)}</small>
                </button>
              ))}
            </div>
            {api.listNotesPage ? <div className="knowledge-pagination"><button type="button" disabled={dirty || saveState === "saving" || loading || !previous.length} onClick={() => { setCursor(previous.at(-1) ?? null); setPrevious((x) => x.slice(0, -1)); }}>上一页笔记</button><span>第 {previous.length + 1} 页</span><button type="button" disabled={dirty || saveState === "saving" || loading || !nextCursor} onClick={() => { setPrevious((x) => [...x, cursor]); setCursor(nextCursor); }}>下一页笔记</button></div> : null}
          </aside>

          <section className="note-editor" aria-label="笔记编辑器">
            {!selectedNote ? (
              notes.length ? <div className="note-editor-empty"><FileText size={24} /><p>从左侧选择一条笔记</p></div> : <div className="note-editor-empty note-editor-empty--guide">
                <FileText size={24} /><h2>把重要回答留成会议笔记</h2>
                <p>打开一场会议，在「询问 AI」中提问，再点击回答下的「存为笔记」。保存后可补充自己的记录，并回看关联原文。</p>
                <p>本场附件用于当前讨论的背景；资料库保存经你确认、供后续会议使用的资料。笔记保存后不会自动成为会议背景。</p>
                <button className="secondary-button" type="button" onClick={() => guard(onOpenMeetings)}>查看会议<ArrowUpRight size={14} /></button>
              </div>
            ) : (
              <>
                <header className="note-editor-toolbar">
                  <button
                    type="button"
                    className="note-meeting-link"
                    onClick={() => guard(() => selectedNote.meetingId && onOpenMeeting(selectedNote.meetingId))}
                    disabled={!selectedNote.meetingId}
                  >
                    {meetingTitle(meetings, selectedNote.meetingId)}<ArrowUpRight size={14} />
                  </button>
                  <span className={`note-save-state note-save-state--${saveState}`}>
                    {saveState === "saving" ? "正在保存" : saveState === "error" ? "保存失败" : saveState === "saved" ? "已保存" : "自动保存"}
                  </span>
                  {knowledgeApi?.importNote ? <button className="secondary-button note-import-button" type="button" disabled={dirty || saveState === "saving" || selectedNote.status !== "active" || selectedNote.evidenceStatus === "stale"} onClick={() => setImporting(true)}>加入项目资料库</button> : null}
                  <button
                    className="icon-button icon-button--small"
                    type="button"
                    onClick={() => void updateStatus(status === "archived" ? "active" : "archived")}
                    title={status === "archived" ? "恢复笔记" : "归档笔记"}
                    aria-label={status === "archived" ? "恢复笔记" : "归档笔记"}
                    disabled={dirty || saveState === "saving"}
                  >
                    {status === "archived" ? <ArchiveRestore size={16} /> : <Archive size={16} />}
                  </button>
                  <button className="icon-button icon-button--small" type="button" disabled={dirty || saveState === "saving"} onClick={() => void deleteNote()} title="删除笔记" aria-label="删除笔记">
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
                  {selectedNote.evidenceStatus === "stale" ? <p className="inline-error">原回答依据已变化。这份笔记保留供核对，不能把旧引用当作当前结论。</p> : null}
                  {selectedNote.sourceEvidence?.length ? <SourceEvidenceList evidence={selectedNote.sourceEvidence} api={knowledgeApi} meetingId={selectedNote.meetingId ?? ""} onTranscript={(id) => selectedNote.meetingId && onOpenEvidence(selectedNote.meetingId, id)} onDocument={onKnowledgeSource} stale={selectedNote.evidenceStatus === "stale"} /> : null}
                  {!selectedNote.evidence.length && !selectedNote.sourceEvidence?.length ? <p>这条笔记没有关联原文。</p> : null}
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
            {error ? <div className="inline-error note-editor-error" role="alert">{error}{dirty && saveState === "error" && api.getNote ? <><button type="button" onClick={() => void readLatest()}>读取最新版本，保留我的编辑</button>{latestCompared ? <><p>最新已保存内容：{latestCompared}</p><button type="button" onClick={() => { setError(null); setLatestCompared(null); setSaveState("idle"); }}>确认按我的编辑继续保存</button></> : null}</> : null}</div> : null}
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
                  {!selectedNote.evidence.length && !selectedNote.sourceEvidence?.length ? <p>这条笔记没有关联原文。</p> : null}
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
      {importing && selectedNote && knowledgeApi ? <SourceLibraryImport api={knowledgeApi} source={{ kind: "note", id: selectedNote.noteId, version: selectedNote.version }} onClose={() => setImporting(false)} onOpenDocument={onOpenKnowledgeDocument} /> : null}
    </div>
  );
}
