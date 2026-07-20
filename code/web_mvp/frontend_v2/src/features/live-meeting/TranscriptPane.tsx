import { Check, CircleAlert, LoaderCircle, Pencil, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ActivePartial,
  MeetingSpeaker,
  SemanticParagraph,
  TranscriptSegment,
} from "../../domain/events";
import { segmentDomId } from "./domIds";

interface TranscriptPaneProps {
  segments: TranscriptSegment[];
  semanticParagraphs?: SemanticParagraph[];
  archivedTranscript: string;
  archivedSegmentCount: number;
  activePartial: ActivePartial | null;
  connection: string;
  mergeSegments?: boolean;
  speakers?: MeetingSpeaker[];
  onRenameSpeaker?(speakerId: string, speakerLabel: string): Promise<void>;
  onSeekAudio?(offsetMs: number): void;
}

function formatOffset(milliseconds: number | null): string {
  if (milliseconds === null) return "";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

interface DisplayParagraph {
  id: string;
  text: string;
  segmentIds: string[];
  startedAtMs: number | null;
  revised: boolean;
  speakerId: string | null;
  speakerLabel: string | null;
  speakerConfidence: number | null;
  correctionStatus?: string;
  corrections: Array<{ before: string; after: string }>;
}

function joinCheckpointText(existing: string, incoming: string): string {
  const left = existing.trim();
  const right = incoming.trim();
  if (!left) return right;
  if (!right) return left;
  for (let length = Math.min(80, left.length, right.length); length > 1; length -= 1) {
    if (left.slice(-length) === right.slice(0, length)) return `${left}${right.slice(length)}`;
  }
  const needsSpace = /[A-Za-z0-9]$/.test(left) && /^[A-Za-z0-9]/.test(right);
  return `${left}${needsSpace ? " " : ""}${right}`;
}

function assembleDisplayParagraphs(segments: TranscriptSegment[]): DisplayParagraph[] {
  const ordered = [...segments].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
  const paragraphs: DisplayParagraph[] = [];
  for (const segment of ordered) {
    const text = segment.normalizedText.trim() || segment.text.trim();
    if (!text) continue;
    const previous = paragraphs[paragraphs.length - 1];
    const previousSegment = previous && ordered.find((item) => item.segmentId === previous.segmentIds.at(-1));
    const gap = previousSegment?.endedAtMs !== null && previousSegment?.endedAtMs !== undefined && segment.startedAtMs !== null
      ? segment.startedAtMs - previousSegment.endedAtMs
      : 0;
    const speakerId = segment.speakerId ?? null;
    const canJoin = Boolean(previous) && previous?.speakerId === speakerId && gap < 1_800 &&
      (segment.endedAtMs ?? segment.startedAtMs ?? 0) - (previous?.startedAtMs ?? 0) <= 60_000;
    if (previous && canJoin) {
      previous.text = joinCheckpointText(previous.text, text);
      previous.segmentIds.push(segment.segmentId);
      previous.revised = previous.revised || segment.correctionStatus === "changed" ||
        (segment.correctionStatus === undefined && segment.revision > 1);
      previous.correctionStatus = mergeCorrectionStatus(previous.correctionStatus, segment.correctionStatus);
      previous.corrections.push(...correctionDetails([segment]));
      previous.speakerConfidence = mergeSpeakerConfidence(
        previous.speakerConfidence,
        segment.speakerConfidence ?? null,
      );
      continue;
    }
    paragraphs.push({
      id: `paragraph:${segment.segmentId}`,
      text,
      segmentIds: [segment.segmentId],
      startedAtMs: segment.startedAtMs,
      speakerId,
      speakerLabel: segment.speakerLabel ?? null,
      speakerConfidence: segment.speakerConfidence ?? null,
      revised: segment.correctionStatus === "changed" ||
        (segment.correctionStatus === undefined && segment.revision > 1),
      correctionStatus: segment.correctionStatus,
      corrections: correctionDetails([segment]),
    });
  }
  return paragraphs;
}

function mergeSpeakerConfidence(current: number | null, incoming: number | null): number | null {
  if (current === null || incoming === null) return null;
  return Math.min(current, incoming);
}

function mergeCorrectionStatus(current: string | undefined, incoming: string | undefined): string | undefined {
  const priority: Record<string, number> = {
    failed_preserved_original: 5,
    processing: 4,
    pending: 3,
    changed: 2,
    no_change: 1,
  };
  if (!current) return incoming;
  if (!incoming) return current;
  return (priority[incoming] ?? 0) > (priority[current] ?? 0) ? incoming : current;
}

function correctionDetails(segments: TranscriptSegment[]): Array<{ before: string; after: string }> {
  return segments.flatMap((segment) => {
    if (segment.correctionStatus !== "changed") return [];
    const before = segment.correctionBeforeText?.trim();
    const after = segment.correctionAfterText?.trim() || segment.normalizedText.trim();
    return before && after && before !== after ? [{ before, after }] : [];
  });
}

function correctionLabel(status: string | undefined, revised: boolean): string | null {
  if (status === "processing") return "AI 分析中";
  if (status === "no_change") return "已检查，无需修改";
  if (status === "changed" || revised) return "AI 已校正";
  if (status === "failed_preserved_original") return "AI 分析失败，原文已保留";
  if (status === "pending") return "等待 AI 分析";
  return null;
}

function displaySemanticParagraphs(
  paragraphs: SemanticParagraph[],
  segments: TranscriptSegment[],
): DisplayParagraph[] {
  const segmentsById = new Map(segments.map((segment) => [segment.segmentId, segment]));
  return paragraphs.flatMap((paragraph) => {
    const text = paragraph.text.trim();
    if (!text) return [];
    const checkpoints = paragraph.checkpointIds.flatMap((checkpointId) => {
      const segment = segmentsById.get(checkpointId);
      return segment ? [segment] : [];
    });
    const revisedSpeaker = semanticParagraphSpeaker(paragraph, checkpoints);
    return [{
      id: paragraph.paragraphId,
      text,
      segmentIds: paragraph.checkpointIds,
      startedAtMs: paragraph.startMs,
      speakerId: revisedSpeaker.speakerId,
      speakerLabel: revisedSpeaker.speakerLabel,
      speakerConfidence: revisedSpeaker.speakerConfidence,
      revised: paragraph.checkpointIds.some((checkpointId) => {
        const segment = segmentsById.get(checkpointId);
        return segment?.correctionStatus === "changed" || (
          segment?.correctionStatus === undefined && (segment?.revision ?? 0) > 1
        );
      }),
      correctionStatus: paragraph.checkpointIds
        .map((checkpointId) => segmentsById.get(checkpointId)?.correctionStatus)
        .reduce(mergeCorrectionStatus, undefined),
      corrections: correctionDetails(checkpoints),
    }];
  });
}

function semanticParagraphSpeaker(
  paragraph: SemanticParagraph,
  checkpoints: TranscriptSegment[],
): Pick<DisplayParagraph, "speakerId" | "speakerLabel" | "speakerConfidence"> {
  const hasIndependentRevision = checkpoints.some(
    (segment) => (segment.speakerAttributionRevision ?? 0) > 0,
  );
  const fullyAttributed = checkpoints.length === paragraph.checkpointIds.length &&
    checkpoints.length > 0 && checkpoints.every((segment) => Boolean(segment.speakerId));
  const speakerIds = new Set(checkpoints.flatMap((segment) => segment.speakerId ? [segment.speakerId] : []));
  if (fullyAttributed && speakerIds.size === 1) {
    const latest = [...checkpoints]
      .sort((left, right) => (right.speakerAttributionRevision ?? 0) - (left.speakerAttributionRevision ?? 0))[0];
    return {
      speakerId: latest.speakerId ?? null,
      speakerLabel: latest.speakerLabel ?? null,
      speakerConfidence: checkpoints.reduce<number | null>(
        (confidence, segment) => mergeSpeakerConfidence(confidence, segment.speakerConfidence ?? null),
        checkpoints[0].speakerConfidence ?? null,
      ),
    };
  }
  if (hasIndependentRevision) {
    return { speakerId: null, speakerLabel: null, speakerConfidence: null };
  }
  return {
    speakerId: paragraph.speakerId ?? null,
    speakerLabel: paragraph.speakerLabel ?? null,
    speakerConfidence: paragraph.speakerConfidence ?? null,
  };
}

export function TranscriptPane({
  segments,
  semanticParagraphs = [],
  archivedTranscript,
  archivedSegmentCount,
  activePartial,
  connection,
  mergeSegments = true,
  speakers = [],
  onRenameSpeaker,
  onSeekAudio,
}: TranscriptPaneProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [followingLatest, setFollowingLatest] = useState(true);
  const [newParagraphCount, setNewParagraphCount] = useState(0);
  const [editingSpeaker, setEditingSpeaker] = useState<{ paragraphId: string; speakerId: string } | null>(null);
  const [speakerDraft, setSpeakerDraft] = useState("");
  const [speakerSaving, setSpeakerSaving] = useState(false);
  const [speakerError, setSpeakerError] = useState<string | null>(null);
  const previousParagraphCount = useRef(0);
  const speakerLabels = useMemo(
    () => new Map(speakers.map((speaker) => [speaker.speakerId, speaker.speakerLabel])),
    [speakers],
  );
  const paragraphs = useMemo(
    () => semanticParagraphs.length
      ? displaySemanticParagraphs(semanticParagraphs, segments)
      : mergeSegments
      ? assembleDisplayParagraphs(segments)
      : segments.map((segment) => ({
        id: `paragraph:${segment.segmentId}`,
        text: segment.normalizedText.trim() || segment.text.trim(),
        segmentIds: [segment.segmentId],
        startedAtMs: segment.startedAtMs,
        speakerId: segment.speakerId ?? null,
        speakerLabel: segment.speakerLabel ?? null,
        speakerConfidence: segment.speakerConfidence ?? null,
        revised: segment.correctionStatus === "changed" ||
          (segment.correctionStatus === undefined && segment.revision > 1),
        correctionStatus: segment.correctionStatus,
        corrections: correctionDetails([segment]),
      })),
    [mergeSegments, segments, semanticParagraphs],
  );
  const visibleActivePartial = useMemo(() => {
    if (!activePartial) return null;
    const committedSegmentIds = new Set(segments.map((segment) => segment.segmentId));
    const activeText = activePartial.text.trim();
    const coveredByDurableProjection = paragraphs.some((paragraph) =>
      paragraph.segmentIds.includes(activePartial.segmentId) ||
      (activeText.length > 0 && paragraph.text.trim() === activeText),
    );
    return committedSegmentIds.has(activePartial.segmentId) || coveredByDurableProjection
      ? null
      : activePartial;
  }, [activePartial, paragraphs, segments]);
  const hasTranscript = Boolean(archivedTranscript || paragraphs.length || visibleActivePartial);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    if (followingLatest) {
      node.scrollTop = node.scrollHeight;
      setNewParagraphCount(0);
    } else if (paragraphs.length > previousParagraphCount.current) {
      setNewParagraphCount((count) => count + paragraphs.length - previousParagraphCount.current);
    }
    previousParagraphCount.current = paragraphs.length;
  }, [followingLatest, paragraphs.length, visibleActivePartial?.updatedAtMs]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const atLatest = node.scrollHeight - node.scrollTop - node.clientHeight < 56;
    setFollowingLatest(atLatest);
    if (atLatest) setNewParagraphCount(0);
  };

  const returnToLatest = () => {
    setFollowingLatest(true);
    setNewParagraphCount(0);
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  };

  const beginSpeakerRename = (paragraph: DisplayParagraph, label: string) => {
    if (!paragraph.speakerId) return;
    setEditingSpeaker({ paragraphId: paragraph.id, speakerId: paragraph.speakerId });
    setSpeakerDraft(label);
    setSpeakerError(null);
  };

  const cancelSpeakerRename = () => {
    if (speakerSaving) return;
    setEditingSpeaker(null);
    setSpeakerDraft("");
    setSpeakerError(null);
  };

  const saveSpeakerRename = async () => {
    const label = speakerDraft.trim();
    if (!editingSpeaker || !onRenameSpeaker || !label || speakerSaving) return;
    setSpeakerSaving(true);
    setSpeakerError(null);
    try {
      await onRenameSpeaker(editingSpeaker.speakerId, label);
      setEditingSpeaker(null);
      setSpeakerDraft("");
    } catch (error) {
      const status = error && typeof error === "object" && "status" in error
        ? Number((error as { status?: unknown }).status)
        : null;
      setSpeakerError(status === 409
        ? "这个名称已用于其他说话人"
        : status === 422
          ? "名称需要是 1 到 80 个有效字符"
          : error instanceof Error && error.message
            ? error.message
            : "名称保存失败，请重试");
    } finally {
      setSpeakerSaving(false);
    }
  };

  return (
    <section className="transcript-pane" aria-labelledby="transcript-title">
      <header className="section-heading transcript-heading">
        <div>
          <span className="eyebrow">实时记录</span>
          <h2 id="transcript-title">会议文字</h2>
        </div>
        <div className="confirmed-count" title="已写入会议记录的文字段落">
          <Check size={14} />
          {archivedSegmentCount + paragraphs.length} 段已确认
        </div>
      </header>

      <div
        className="transcript-scroll"
        data-testid="transcript-scroll"
        ref={scrollRef}
        onScroll={handleScroll}
        aria-live="polite"
      >
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

        {newParagraphCount > 0 && !followingLatest ? (
          <button className="transcript-return-latest" type="button" onClick={returnToLatest} data-testid="transcript-new-content">
            有 {newParagraphCount} 段新内容，回到最新
          </button>
        ) : null}

        <div className="transcript-segments">
          {paragraphs.map((paragraph) => {
            const speakerLabel = paragraph.speakerId
              ? speakerLabels.get(paragraph.speakerId) ?? paragraph.speakerLabel
              : paragraph.speakerLabel;
            const lowSpeakerConfidence = Boolean(paragraph.speakerId) && (
              paragraph.speakerConfidence === null || paragraph.speakerConfidence < 0.7
            );
            const editingThisSpeaker = Boolean(
              editingSpeaker?.paragraphId === paragraph.id &&
              editingSpeaker.speakerId === paragraph.speakerId,
            );
            return (
              <article
                className="transcript-segment"
                id={segmentDomId(paragraph.segmentIds[0])}
                data-segment-id={paragraph.segmentIds[0]}
                key={paragraph.id}
                tabIndex={-1}
              >
                <div className="segment-meta">
                  {onSeekAudio && paragraph.startedAtMs !== null ? (
                    <button
                      className="segment-time-button"
                      type="button"
                      onClick={() => onSeekAudio(paragraph.startedAtMs ?? 0)}
                      aria-label={`在录音中定位到 ${formatOffset(paragraph.startedAtMs)}`}
                    >
                      {formatOffset(paragraph.startedAtMs)}
                    </button>
                  ) : <time>{formatOffset(paragraph.startedAtMs)}</time>}
                  {correctionLabel(paragraph.correctionStatus, paragraph.revised) ? (
                    <span
                      className={`correction-mark correction-mark--${paragraph.correctionStatus ?? "changed"}`}
                      title={paragraph.correctionStatus === "changed" && paragraph.revised ? "文字已发生真实修正，可在复盘中查看原文与最终版本" : undefined}
                    >
                      <Sparkles size={12} />{correctionLabel(paragraph.correctionStatus, paragraph.revised)}
                    </span>
                  ) : null}
                  {paragraph.corrections.length ? (
                    <details className="transcript-correction-detail">
                      <summary>查看修正对照</summary>
                      <div className="transcript-correction-diff">
                        {paragraph.corrections.map((correction, index) => (
                          <div key={`${correction.before}:${correction.after}:${index}`}>
                            <span>识别</span><del>{correction.before}</del>
                            <span>AI</span><ins>{correction.after}</ins>
                          </div>
                        ))}
                      </div>
                    </details>
                  ) : null}
                </div>
                <div className="segment-content">
                  {speakerLabel ? (
                    <div className="speaker-row">
                      {editingThisSpeaker ? (
                        <form
                          className="speaker-rename-form"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void saveSpeakerRename();
                          }}
                        >
                          <input
                            value={speakerDraft}
                            onChange={(event) => setSpeakerDraft(event.target.value)}
                            aria-label={`重命名 ${speakerLabel}`}
                            maxLength={80}
                            autoFocus
                          />
                          <button
                            className="icon-button icon-button--small"
                            type="submit"
                            aria-label={`保存 ${speakerLabel} 的名称`}
                            title="保存名称"
                            disabled={!speakerDraft.trim() || speakerSaving}
                          >
                            {speakerSaving ? <LoaderCircle className="spin" size={13} /> : <Check size={13} />}
                          </button>
                          <button
                            className="icon-button icon-button--small"
                            type="button"
                            aria-label="取消重命名"
                            title="取消"
                            onClick={cancelSpeakerRename}
                            disabled={speakerSaving}
                          >
                            <X size={13} />
                          </button>
                          {speakerError ? <span className="speaker-rename-error" role="alert">{speakerError}</span> : null}
                        </form>
                      ) : (
                        <button
                          className="speaker-label-button"
                          type="button"
                          onClick={() => beginSpeakerRename(paragraph, speakerLabel)}
                          disabled={!onRenameSpeaker || !paragraph.speakerId}
                          title={onRenameSpeaker ? `重命名 ${speakerLabel}` : undefined}
                        >
                          <span>{speakerLabel}</span>
                          {onRenameSpeaker && paragraph.speakerId ? <Pencil size={11} aria-hidden="true" /> : null}
                        </button>
                      )}
                      {lowSpeakerConfidence ? (
                        <span
                          className="speaker-confidence-hint"
                          title="说话人区分置信度较低，请结合上下文确认"
                          aria-label="说话人区分置信度较低"
                        >
                          <CircleAlert size={12} />
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  <p>{paragraph.text}</p>
                </div>
              </article>
            );
          })}
        </div>

        {visibleActivePartial ? (
          <div className="active-partial" aria-label="正在识别">
            <span className="listening-pulse" aria-hidden="true" />
            <p>{visibleActivePartial.text}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}
