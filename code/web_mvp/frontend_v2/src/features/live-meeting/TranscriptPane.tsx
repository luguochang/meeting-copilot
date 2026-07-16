import { Check, LoaderCircle, Sparkles } from "lucide-react";
import type { ActivePartial, TranscriptSegment } from "../../domain/events";
import { segmentDomId } from "./domIds";

interface TranscriptPaneProps {
  segments: TranscriptSegment[];
  archivedTranscript: string;
  archivedSegmentCount: number;
  activePartial: ActivePartial | null;
  connection: string;
  onSeekAudio?(offsetMs: number): void;
}

function formatOffset(milliseconds: number | null): string {
  if (milliseconds === null) return "";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function TranscriptPane({
  segments,
  archivedTranscript,
  archivedSegmentCount,
  activePartial,
  connection,
  onSeekAudio,
}: TranscriptPaneProps) {
  const hasTranscript = Boolean(archivedTranscript || segments.length || activePartial);
  return (
    <section className="transcript-pane" aria-labelledby="transcript-title">
      <header className="section-heading transcript-heading">
        <div>
          <span className="eyebrow">实时记录</span>
          <h2 id="transcript-title">会议文字</h2>
        </div>
        <div className="confirmed-count" title="已写入会议记录的文字段落">
          <Check size={14} />
          {archivedSegmentCount + segments.length} 段已确认
        </div>
      </header>

      <div className="transcript-scroll" data-testid="transcript-scroll" aria-live="polite">
        {!hasTranscript ? (
          <div className="transcript-empty">
            {connection === "connecting" || connection === "reconnecting" ? <LoaderCircle className="spin" size={22} /> : <Sparkles size={22} />}
            <p>{connection === "offline" ? "会议连接暂时中断" : "等待会议文字"}</p>
            <span>{connection === "offline" ? "恢复连接后会继续追加已确认内容" : "识别后的内容会按发言顺序连续出现"}</span>
          </div>
        ) : null}

        {archivedTranscript ? (
          <div className="transcript-archive" data-testid="archived-transcript">
            <span className="archive-label">较早的 {archivedSegmentCount} 段</span>
            <p>{archivedTranscript}</p>
          </div>
        ) : null}

        <div className="transcript-segments">
          {segments.map((segment) => {
            const displayText = segment.normalizedText.trim() || segment.text;
            const corrected = segment.revision > 1;
            return (
              <article
                className="transcript-segment"
                id={segmentDomId(segment.segmentId)}
                data-segment-id={segment.segmentId}
                key={segment.segmentId}
                tabIndex={-1}
              >
                <div className="segment-meta">
                  {onSeekAudio && segment.startedAtMs !== null ? (
                    <button
                      className="segment-time-button"
                      type="button"
                      onClick={() => onSeekAudio(segment.startedAtMs ?? 0)}
                      aria-label={`在录音中定位到 ${formatOffset(segment.startedAtMs)}`}
                    >
                      {formatOffset(segment.startedAtMs)}
                    </button>
                  ) : <time>{formatOffset(segment.startedAtMs)}</time>}
                  {corrected ? <span className="correction-mark"><Sparkles size={12} />AI 已校正</span> : null}
                </div>
                <p>{displayText}</p>
              </article>
            );
          })}
        </div>

        {activePartial ? (
          <div className="active-partial" aria-label="正在识别">
            <span className="listening-pulse" aria-hidden="true" />
            <p>{activePartial.text}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}
