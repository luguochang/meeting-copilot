import { Bookmark, Check, Copy, EyeOff, Flag, MessageCircleQuestion, MoreHorizontal, Quote, TimerOff } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  OpenQuestionProjection,
  Suggestion,
  SuggestionFeedback,
  TopicProjection,
} from "../../domain/events";

interface NowRailProps {
  currentTopic: TopicProjection | null;
  openQuestions: OpenQuestionProjection[];
  suggestions: Suggestion[];
  onEvidence(segmentId: string): void;
  onFeedback(suggestionId: string, feedback: SuggestionFeedback): Promise<void>;
  onMessage(message: string): void;
}

function currentSuggestion(suggestions: Suggestion[]): Suggestion | null {
  const visible = suggestions.filter(
    (item) =>
      item.status !== "rejected" &&
      item.status !== "superseded" &&
      item.feedback !== "ignored" &&
      item.feedback !== "false_positive" &&
      item.feedback !== "too_late",
  );
  return visible.sort((a, b) => b.evidenceTranscriptSeq - a.evidenceTranscriptSeq || b.updatedAtMs - a.updatedAtMs)[0] ?? null;
}

function suggestionText(suggestion: Suggestion): string {
  return suggestion.status === "committed" ? suggestion.text ?? suggestion.draftText : suggestion.draftText;
}

function questionIsOpen(question: OpenQuestionProjection): boolean {
  return question.status === "open" || question.status === "carried_over" || question.status === "unknown";
}

export function NowRail({ currentTopic, openQuestions, suggestions, onEvidence, onFeedback, onMessage }: NowRailProps) {
  const suggestion = useMemo(() => currentSuggestion(suggestions), [suggestions]);
  const questions = openQuestions.filter(questionIsOpen).slice(0, 3);
  const [menuOpen, setMenuOpen] = useState(false);
  const [saving, setSaving] = useState<SuggestionFeedback | null>(null);
  const text = suggestion ? suggestionText(suggestion) : "";

  const saveFeedback = async (feedback: SuggestionFeedback) => {
    if (!suggestion || saving) return;
    setSaving(feedback);
    setMenuOpen(false);
    try {
      await onFeedback(suggestion.suggestionId, feedback);
      const labels: Record<SuggestionFeedback, string> = {
        kept: "建议已保留",
        ignored: "建议已忽略",
        false_positive: "已标记为误报",
        too_late: "已标记为太晚",
      };
      onMessage(labels[feedback]);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "反馈保存失败");
    } finally {
      setSaving(null);
    }
  };

  const copySuggestion = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      onMessage("追问已复制");
    } catch {
      onMessage("复制失败，请检查剪贴板权限");
    }
  };

  return (
    <aside className="now-rail" aria-label="当前会议重点">
      <section className="rail-section topic-section" aria-labelledby="topic-title">
        <header className="rail-heading">
          <Flag size={15} />
          <h2 id="topic-title">当前议题</h2>
        </header>
        {currentTopic ? (
          <button
            className="topic-content evidence-content"
            type="button"
            onClick={() => currentTopic.evidenceSegmentIds[0] && onEvidence(currentTopic.evidenceSegmentIds[0])}
            disabled={!currentTopic.evidenceSegmentIds.length}
          >
            {currentTopic.text}
          </button>
        ) : (
          <p className="rail-empty">等待讨论形成清晰议题</p>
        )}
      </section>

      <section className="rail-section suggestion-section" aria-labelledby="suggestion-title">
        <header className="rail-heading">
          <MessageCircleQuestion size={16} />
          <h2 id="suggestion-title">现在最值得追问</h2>
          {suggestion?.status === "draft" || suggestion?.status === "validating" ? (
            <span className="draft-badge">生成中</span>
          ) : null}
        </header>

        {suggestion && text ? (
          <div className={`suggestion-card suggestion-card--${suggestion.status}`}>
            <blockquote>{text}</blockquote>
            <div className="suggestion-footer">
              <button
                className="evidence-link"
                type="button"
                onClick={() => onEvidence(suggestion.evidenceSegmentId)}
              >
                <Quote size={13} />查看依据
              </button>
              <div className="suggestion-actions" aria-label="建议操作">
                <button className="icon-button icon-button--small" type="button" onClick={copySuggestion} title="复制追问" aria-label="复制追问">
                  <Copy size={15} />
                </button>
                <button
                  className={`icon-button icon-button--small ${suggestion.feedback === "kept" ? "is-selected" : ""}`}
                  type="button"
                  onClick={() => void saveFeedback("kept")}
                  title="保留建议"
                  aria-label="保留建议"
                  disabled={Boolean(saving)}
                >
                  {suggestion.feedback === "kept" ? <Check size={15} /> : <Bookmark size={15} />}
                </button>
                <button className="icon-button icon-button--small" type="button" onClick={() => void saveFeedback("ignored")} title="忽略建议" aria-label="忽略建议" disabled={Boolean(saving)}>
                  <EyeOff size={15} />
                </button>
                <div className="feedback-menu-wrap">
                  <button className="icon-button icon-button--small" type="button" onClick={() => setMenuOpen((value) => !value)} title="更多反馈" aria-label="更多反馈" aria-expanded={menuOpen}>
                    <MoreHorizontal size={16} />
                  </button>
                  {menuOpen ? (
                    <div className="feedback-menu" role="menu">
                      <button type="button" role="menuitem" onClick={() => void saveFeedback("false_positive")}><Flag size={14} />误报</button>
                      <button type="button" role="menuitem" onClick={() => void saveFeedback("too_late")}><TimerOff size={14} />太晚</button>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <p className="rail-empty">暂无需要立即追问的建议</p>
        )}
      </section>

      <section className="rail-section questions-section" aria-labelledby="questions-title">
        <header className="rail-heading">
          <span className="question-mark" aria-hidden="true">?</span>
          <h2 id="questions-title">未闭环问题</h2>
          {questions.length ? <span className="count-badge">{questions.length}</span> : null}
        </header>
        {questions.length ? (
          <ol className="question-list">
            {questions.map((question) => (
              <li key={question.id}>
                <button
                  type="button"
                  onClick={() => question.evidenceSegmentIds[0] && onEvidence(question.evidenceSegmentIds[0])}
                  disabled={!question.evidenceSegmentIds.length}
                >
                  {question.text}
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="rail-empty">暂无已识别的未闭环问题</p>
        )}
      </section>
    </aside>
  );
}
