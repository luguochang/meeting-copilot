import {
  AlertCircle,
  Check,
  CircleEllipsis,
  FileAudio,
  FileText,
  ListChecks,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type {
  MeetingViewState,
  ReviewJob,
  ReviewJobKind,
  TranscriptSegment,
} from "../../domain/events";
import { TranscriptPane } from "../live-meeting/TranscriptPane";
import { segmentDomId } from "../live-meeting/domIds";

type ReviewTab = "review" | "actions" | "transcript" | "audio";

interface ReviewWorkspaceProps {
  state: MeetingViewState;
  onReloadTranscript(): void;
  onReloadAudio(): void;
}

const tabs: Array<{ id: ReviewTab; label: string; icon: typeof Sparkles }> = [
  { id: "review", label: "复盘", icon: Sparkles },
  { id: "actions", label: "决策与待办", icon: ListChecks },
  { id: "transcript", label: "会议文字", icon: FileText },
  { id: "audio", label: "录音", icon: FileAudio },
];

const jobNames: Record<ReviewJobKind, string> = {
  minutes: "会议纪要",
  approach: "方案与风险",
  index: "内容索引",
};

function jobState(job: ReviewJob | undefined, qualityPaused: boolean): { text: string; tone: string } {
  if (!job) return { text: "等待开始", tone: "muted" };
  if (job.status === "succeeded") return { text: "已完成", tone: "success" };
  if (job.status === "failed" || job.status === "cancelled") {
    if (qualityPaused && (job.kind === "minutes" || job.kind === "approach")) {
      return { text: "识别质量不足，已暂停", tone: "warning" };
    }
    return { text: "生成失败", tone: "error" };
  }
  if (job.status === "retry_wait") return { text: "正在重试", tone: "working" };
  if (job.status === "running") return { text: "正在生成", tone: "working" };
  return { text: "等待处理", tone: "working" };
}

function displayText(segment: TranscriptSegment): string {
  return segment.normalizedText.trim() || segment.text.trim();
}

export function ReviewWorkspace({ state, onReloadTranscript, onReloadAudio }: ReviewWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<ReviewTab>("review");
  const [pendingEvidence, setPendingEvidence] = useState<string | null>(null);
  const [pendingAudioOffsetMs, setPendingAudioOffsetMs] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcript = state.fullTranscript.length ? state.fullTranscript : state.segments;
  const keptSuggestions = state.suggestions.filter(
    (suggestion) => suggestion.status === "committed" && suggestion.feedback === "kept",
  );
  const pendingQuestions = state.openQuestions.filter((question) =>
    ["open", "carried_over", "unknown"].includes(question.status),
  );
  const qualityPaused = state.diagnostics.formal_derivation_status === "suppressed_by_asr_semantic_quality"
    || (Array.isArray(state.diagnostics.degradation_reasons)
      && state.diagnostics.degradation_reasons.includes("asr_semantic_quality_blocked"));

  useEffect(() => {
    if (activeTab !== "transcript" || !pendingEvidence) return;
    const frame = window.requestAnimationFrame(() => {
      const element = document.getElementById(segmentDomId(pendingEvidence));
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
      element?.focus({ preventScroll: true });
      element?.classList.add("is-evidence-target");
      setPendingEvidence(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeTab, pendingEvidence]);

  const reviewJobEntries = useMemo(
    () => (["minutes", "approach", "index"] as const).map((kind) => ({
      kind,
      job: state.reviewJobs[kind],
      state: jobState(state.reviewJobs[kind], qualityPaused),
    })),
    [qualityPaused, state.reviewJobs],
  );

  const showEvidence = (segmentId: string) => {
    setPendingEvidence(segmentId);
    setActiveTab("transcript");
  };

  const seekAudio = (offsetMs: number) => {
    setPendingAudioOffsetMs(Math.max(0, offsetMs));
    setActiveTab("audio");
  };

  useEffect(() => {
    if (activeTab !== "audio" || pendingAudioOffsetMs === null || !audioRef.current) return;
    audioRef.current.currentTime = pendingAudioOffsetMs / 1_000;
    setPendingAudioOffsetMs(null);
  }, [activeTab, pendingAudioOffsetMs]);

  return (
    <main className="review-workspace">
      <section className="review-progress" aria-label="会议整理进度">
        <div className="review-progress-item review-progress-item--success">
          <Check size={16} />
          <span>文字已确认</span>
        </div>
        <div className={`review-progress-item ${state.audioDetail?.assembled ? "review-progress-item--success" : "review-progress-item--working"}`}>
          {state.audioDetail?.assembled ? <Check size={16} /> : <CircleEllipsis size={16} />}
          <span>{state.audioDetail?.assembled ? "录音已保存" : "录音整理中"}</span>
        </div>
        {reviewJobEntries.map(({ kind, state: status }) => (
          <div key={kind} className={`review-progress-item review-progress-item--${status.tone}`}>
            {status.tone === "success" ? <Check size={16} /> : status.tone === "error" ? <AlertCircle size={16} /> : <CircleEllipsis size={16} />}
            <span>{jobNames[kind]}：{status.text}</span>
          </div>
        ))}
      </section>

      <div className="review-tabs" role="tablist" aria-label="会后内容">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={activeTab === tab.id ? "is-active" : ""}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={16} />
              {tab.label}
            </button>
          );
        })}
      </div>

      <section
        className={`review-tab-panel${activeTab === "transcript" ? " review-tab-panel--transcript" : ""}`}
        role="tabpanel"
      >
        {activeTab === "review" ? (
          <div className="review-summary-layout">
            <section className="review-document" aria-labelledby="minutes-heading">
              <div className="review-section-heading">
                <div>
                  <span className="section-kicker">会议结果</span>
                  <h2 id="minutes-heading">会议复盘</h2>
                </div>
                {state.minutes?.status === "degraded" ? <span className="state-label state-label--warning">结果不完整</span> : null}
              </div>
              {state.minutes ? (
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
                    {state.minutes.markdown}
                  </ReactMarkdown>
                </div>
              ) : qualityPaused ? (
                <p className="inline-warning">识别语义质量不足，正式会议纪要已暂停；会议文字和录音仍已保存。</p>
              ) : state.reviewJobs.minutes?.status === "failed" ? (
                <p className="inline-error">会议纪要生成失败，会议文字和录音仍已保存。</p>
              ) : (
                <p className="review-empty">会议纪要正在生成，完成后会自动出现在这里。</p>
              )}
            </section>

            <aside className="review-considerations" aria-labelledby="considerations-heading">
              <div className="review-section-heading">
                <div>
                  <span className="section-kicker">继续确认</span>
                  <h2 id="considerations-heading">方案与风险</h2>
                </div>
              </div>
              {state.approach.cards.length ? (
                <div className="consideration-list">
                  {state.approach.cards.map((card, index) => (
                    <article className="consideration-item" key={card.cardId ?? `${card.cardType}-${index}`}>
                      <p>{card.suggestionText}</p>
                      {card.triggerReason ? <span>{card.triggerReason}</span> : null}
                      {card.evidenceSegmentIds[0] ? (
                        <button type="button" onClick={() => showEvidence(card.evidenceSegmentIds[0])}>
                          查看原话
                        </button>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : qualityPaused ? (
                <p className="inline-warning">识别语义质量不足，方案与风险推断已暂停。</p>
              ) : state.reviewJobs.approach?.status === "failed" ? (
                <p className="inline-error">方案整理失败，未生成可用内容。</p>
              ) : (
                <p className="review-empty">方案与风险正在整理。</p>
              )}
            </aside>
          </div>
        ) : null}

        {activeTab === "actions" ? (
          <div className="actions-layout">
            <section>
              <span className="section-kicker">已保留</span>
              <h2>决策与建议</h2>
              {keptSuggestions.length ? (
                <div className="action-list">
                  {keptSuggestions.map((suggestion) => (
                    <article key={suggestion.suggestionId} className="action-item">
                      <p>{suggestion.text ?? suggestion.draftText}</p>
                      <button type="button" onClick={() => showEvidence(suggestion.evidenceSegmentId)}>
                        查看依据
                      </button>
                    </article>
                  ))}
                </div>
              ) : <p className="review-empty">本次会议没有保留的 AI 建议。</p>}
            </section>
            <section>
              <span className="section-kicker">未闭环</span>
              <h2>待确认问题</h2>
              {pendingQuestions.length ? (
                <div className="action-list">
                  {pendingQuestions.map((question) => (
                    <article key={question.id} className="action-item">
                      <p>{question.text}</p>
                      {question.evidenceSegmentIds[0] ? (
                        <button type="button" onClick={() => showEvidence(question.evidenceSegmentIds[0])}>
                          查看上下文
                        </button>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : <p className="review-empty">没有未闭环问题。</p>}
            </section>
          </div>
        ) : null}

        {activeTab === "transcript" ? (
          <div className="review-transcript">
            <div className="review-section-heading">
              <div>
                <span className="section-kicker">{transcript.length} 段已确认</span>
                <h2>完整会议文字</h2>
              </div>
              <button className="icon-button" type="button" onClick={onReloadTranscript} title="重新加载文字" aria-label="重新加载文字">
                <RefreshCw size={17} />
              </button>
            </div>
            {state.fullTranscriptError ? <p className="inline-error">{state.fullTranscriptError}</p> : null}
            <TranscriptPane
              segments={transcript}
              archivedTranscript=""
              archivedSegmentCount={0}
              activePartial={null}
              connection={state.connection}
              onSeekAudio={seekAudio}
            />
          </div>
        ) : null}

        {activeTab === "audio" ? (
          <div className="audio-review">
            <div className="review-section-heading">
              <div>
                <span className="section-kicker">本地保存</span>
                <h2>会议录音</h2>
              </div>
              <button className="icon-button" type="button" onClick={onReloadAudio} title="重新加载录音" aria-label="重新加载录音">
                <RefreshCw size={17} />
              </button>
            </div>
            {state.audioError ? <p className="inline-error">{state.audioError}</p> : null}
            {state.audioDetail?.assembled && state.audioDetail.playbackUrl ? (
              <>
                <audio
                  ref={audioRef}
                  controls
                  preload="metadata"
                  src={state.audioDetail.playbackUrl}
                  onLoadedMetadata={() => {
                    if (pendingAudioOffsetMs === null || !audioRef.current) return;
                    audioRef.current.currentTime = pendingAudioOffsetMs / 1_000;
                    setPendingAudioOffsetMs(null);
                  }}
                >
                  当前环境不支持音频播放。
                </audio>
                <dl className="audio-facts">
                  <div><dt>时长</dt><dd>{Math.round(state.audioDetail.durationMs / 1_000)} 秒</dd></div>
                  <div><dt>录音分片</dt><dd>{state.audioDetail.chunkCount} 个</dd></div>
                  <div><dt>音轨</dt><dd>{state.audioDetail.tracks.join("、") || "麦克风"}</dd></div>
                </dl>
              </>
            ) : state.audioLoadState === "error" ? null : (
              <p className="review-empty">录音正在安全组装，会议文字不受影响。</p>
            )}
          </div>
        ) : null}
      </section>

      <span className="sr-live" aria-live="polite">
        {transcript.map(displayText).length ? "会议复盘已加载" : "会议复盘正在加载"}
      </span>
    </main>
  );
}
