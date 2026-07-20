import { Check, Clock3, Eye, History, LoaderCircle, Pencil, Redo2, RefreshCw, RotateCcw, Undo2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ReviewDocument, ReviewDocumentRevision } from "../../domain/events";
import { useReviewDocumentDraft } from "./useReviewDocumentDraft";

interface MarkdownDocumentEditorProps {
  meetingId: string;
  document: ReviewDocument | undefined;
  fallbackMarkdown: string;
  degraded: boolean;
  onSave(expectedRevision: number, content: unknown): Promise<ReviewDocument>;
  onLoadRevisions(): Promise<ReviewDocumentRevision[]>;
  onRegenerate(): Promise<void>;
}

const fromMarkdownContent = (content: unknown, fallback: string): string => {
  if (typeof content === "string") return content;
  if (content && typeof content === "object" && !Array.isArray(content)) {
    const markdown = (content as { markdown?: unknown }).markdown;
    if (typeof markdown === "string") return markdown;
  }
  return fallback;
};

const toMarkdownContent = (markdown: string) => ({ markdown });

function revisionMarkdown(content: unknown): string | null {
  if (typeof content === "string") return content;
  if (!content || typeof content !== "object" || Array.isArray(content)) return null;
  const markdown = (content as { markdown?: unknown }).markdown;
  return typeof markdown === "string" ? markdown : null;
}

function revisionJson(content: unknown): string {
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

function saveLabel(state: ReturnType<typeof useReviewDocumentDraft<string>>["saveState"], savedAtMs: number | null): string {
  if (state === "unsaved") return "等待自动保存";
  if (state === "saving") return "正在保存";
  if (state === "error") return "保存失败，草稿已保留";
  if (!savedAtMs) return "已保存";
  return `已保存 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(savedAtMs)}`;
}

export function MarkdownDocumentEditor({
  meetingId,
  document,
  fallbackMarkdown,
  degraded,
  onSave,
  onLoadRevisions,
  onRegenerate,
}: MarkdownDocumentEditorProps) {
  const [editing, setEditing] = useState(false);
  const [revisionsOpen, setRevisionsOpen] = useState(false);
  const [revisions, setRevisions] = useState<ReviewDocumentRevision[]>([]);
  const [revisionsLoading, setRevisionsLoading] = useState(false);
  const [visibleRevision, setVisibleRevision] = useState<number | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef(-1);
  const editor = useReviewDocumentDraft({
    meetingId,
    kind: "minutes",
    document,
    fallback: fallbackMarkdown,
    enabled: editing,
    fromContent: fromMarkdownContent,
    toContent: toMarkdownContent,
    onSave,
  });

  useEffect(() => {
    if (!editing) return;
    historyRef.current = [editor.draft];
    historyIndexRef.current = 0;
    // Seed undo history only when editing starts; draft changes are recorded by updateDraft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const updateDraft = (next: string) => {
    const history = historyRef.current.slice(0, historyIndexRef.current + 1);
    if (history[history.length - 1] !== next) history.push(next);
    historyRef.current = history.slice(-100);
    historyIndexRef.current = historyRef.current.length - 1;
    editor.setDraft(next);
  };

  const moveHistory = (direction: -1 | 1) => {
    const nextIndex = historyIndexRef.current + direction;
    if (nextIndex < 0 || nextIndex >= historyRef.current.length) return;
    historyIndexRef.current = nextIndex;
    editor.setDraft(historyRef.current[nextIndex]);
  };

  const loadRevisions = useCallback(async () => {
    setRevisionsOpen(true);
    setRevisionsLoading(true);
    setVisibleRevision(null);
    setCommandError(null);
    try {
      setRevisions(await onLoadRevisions());
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : "版本历史加载失败");
    } finally {
      setRevisionsLoading(false);
    }
  }, [onLoadRevisions]);

  const regenerate = async () => {
    setRegenerating(true);
    setCommandError(null);
    try {
      await onRegenerate();
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : "AI 初稿重新生成失败");
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <>
      <div className="review-section-heading">
        <div>
          <span className="section-kicker">会议结果</span>
          <h2 id="minutes-heading">会议复盘</h2>
        </div>
        <div className="document-heading-actions">
          <span className={`document-source document-source--${editor.isUserFinal ? "user_final" : document?.source ?? "ai_generated"}`}>
            {editor.isUserFinal ? "用户最终稿" : "AI 初稿"}
          </span>
          {degraded ? <span className="state-label state-label--warning">结果不完整</span> : null}
          <button className="icon-button icon-button--small" type="button" onClick={() => void loadRevisions()} aria-label="查看复盘版本历史" title="版本历史">
            <History size={15} />
          </button>
          <button className="icon-button icon-button--small" type="button" onClick={() => void regenerate()} disabled={regenerating} aria-label="重新生成会议纪要" title="生成新 AI 初稿，不覆盖用户最终稿">
            {regenerating ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}
          </button>
          <button className="secondary-button compact-button" type="button" onClick={() => setEditing((value) => !value)}>
            {editing ? <X size={14} /> : <Pencil size={14} />}
            {editing ? "退出编辑" : "编辑"}
          </button>
        </div>
      </div>

      {editing ? (
        <div className="document-editor">
          <div className="document-editor-toolbar">
            <button className="icon-button icon-button--small" type="button" onClick={() => moveHistory(-1)} aria-label="撤销" title="撤销"><Undo2 size={15} /></button>
            <button className="icon-button icon-button--small" type="button" onClick={() => moveHistory(1)} aria-label="重做" title="重做"><Redo2 size={15} /></button>
            <span className={`document-save-state document-save-state--${editor.saveState}`} role="status">
              {editor.saveState === "saving" ? <LoaderCircle className="spin" size={13} /> : editor.saveState === "saved" ? <Check size={13} /> : <Clock3 size={13} />}
              {saveLabel(editor.saveState, editor.savedAtMs)}
            </span>
            {editor.saveState === "error" ? (
              <button className="secondary-button compact-button" type="button" onClick={() => void editor.saveNow()}>
                <RefreshCw size={13} />重试保存
              </button>
            ) : null}
          </div>
          {editor.recoveredLocalDraft ? <p className="inline-warning">已恢复上次未保存的本地草稿。</p> : null}
          <label>
            <span className="sr-only">编辑会议复盘</span>
            <textarea value={editor.draft} onChange={(event) => updateDraft(event.target.value)} aria-label="编辑会议复盘" />
          </label>
          {editor.error ? <p className="inline-error">{editor.error}</p> : null}
        </div>
      ) : editor.draft ? (
        <div className="minutes-markdown">
          <ReactMarkdown
            skipHtml
            disallowedElements={["img"]}
            components={{
              h1: ({ children }) => <h3>{children}</h3>,
              h2: ({ children }) => <h3>{children}</h3>,
              h3: ({ children }) => <h4>{children}</h4>,
            }}
          >
            {editor.draft}
          </ReactMarkdown>
        </div>
      ) : null}

      {revisionsOpen ? (
        <section className="review-revision-history" aria-label="复盘版本历史">
          <div><strong>版本历史</strong><button className="icon-button icon-button--small" type="button" onClick={() => setRevisionsOpen(false)} aria-label="关闭版本历史"><X size={14} /></button></div>
          {revisionsLoading ? <p role="status"><LoaderCircle className="spin" size={14} />正在读取版本</p> : null}
          {!revisionsLoading && revisions.length === 0 ? <p>尚无历史版本</p> : null}
          {revisions.map((revision) => {
            const markdown = revisionMarkdown(revision.contentJson);
            const expanded = visibleRevision === revision.revision;
            return (
              <article key={`${revision.revision}-${revision.createdAtMs}`}>
                <strong>版本 {revision.revision}</strong>
                <span>{revision.source === "user_final" ? "用户最终稿" : revision.source === "ai_generated" ? "AI 初稿" : revision.author}</span>
                <span className="document-heading-actions">
                  <time>{revision.createdAtMs ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(revision.createdAtMs) : "时间未知"}</time>
                  <button
                    className="icon-button icon-button--small"
                    type="button"
                    aria-label={`${expanded ? "收起" : "查看"}版本 ${revision.revision} 内容`}
                    aria-expanded={expanded}
                    title={expanded ? "收起版本内容" : "查看版本内容"}
                    onClick={() => setVisibleRevision(expanded ? null : revision.revision)}
                  >
                    <Eye size={14} />
                  </button>
                </span>
                {expanded ? (
                  <div className="minutes-markdown" style={{ gridColumn: "1 / -1" }}>
                    {markdown === null ? (
                      <pre style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{revisionJson(revision.contentJson)}</pre>
                    ) : (
                      <ReactMarkdown
                        skipHtml
                        disallowedElements={["img"]}
                        components={{
                          h1: ({ children }) => <h3>{children}</h3>,
                          h2: ({ children }) => <h3>{children}</h3>,
                          h3: ({ children }) => <h4>{children}</h4>,
                        }}
                      >
                        {markdown}
                      </ReactMarkdown>
                    )}
                  </div>
                ) : null}
              </article>
            );
          })}
        </section>
      ) : null}
      {commandError ? <p className="inline-error">{commandError}</p> : null}
    </>
  );
}
