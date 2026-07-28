import { Check, CircleAlert, CircleHelp, ListTodo, LoaderCircle, MessageSquareText, NotebookPen, Pencil, ScanText, Search, Sparkles, UsersRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ActivePartial,
  MeetingSpeaker,
  RuntimeIndicator,
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
  aiIndicator?: RuntimeIndicator;
  mergeSegments?: boolean;
  liveMode?: boolean;
  speakers?: MeetingSpeaker[];
  onRenameSpeaker?(speakerId: string, speakerLabel: string): Promise<void>;
  onSeekAudio?(offsetMs: number): void;
  onSelectionChange?(selection: TranscriptSelection | null): void;
  onAskSelection?(selection: TranscriptSelection, action: TranscriptSelectionAction): void;
  onSaveSelection?(selection: TranscriptSelection): Promise<void> | void;
}

export interface TranscriptSelection {
  text: string;
  segmentIds: string[];
}

export type TranscriptSelectionAction = "ask" | "explain" | "extract_action_items" | "mark_pending";

interface SelectionToolbarPosition {
  left: number;
  top: number;
  placement: "above" | "below";
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
  endedAtMs: number | null;
  revised: boolean;
  speakerId: string | null;
  speakerLabel: string | null;
  speakerConfidence: number | null;
  correctionStatus?: string;
  corrections: Array<{ before: string; after: string }>;
  runs: DisplayRun[];
}

function formatRange(startMs: number | null, endMs: number | null): string {
  const start = formatOffset(startMs);
  const end = formatOffset(endMs);
  return end && end !== start ? `${start}–${end}` : start;
}

interface DisplayRun {
  key: string;
  text: string;
  segmentIds: string[];
  speakerId: string | null;
  speakerLabel: string | null;
  speakerConfidence: number | null;
  sourceTrack: TranscriptSegment["sourceTrack"];
}

const FALLBACK_PARAGRAPH_GAP_MS = 3_500;
const FALLBACK_PARAGRAPH_MIN_DURATION_MS = 15_000;
const FALLBACK_PARAGRAPH_TARGET_MAX_DURATION_MS = 45_000;
const FALLBACK_PARAGRAPH_HARD_MAX_DURATION_MS = 60_000;
const FALLBACK_PARAGRAPH_TARGET_MAX_CHARACTERS = 220;
const FALLBACK_PARAGRAPH_HARD_MAX_CHARACTERS = 280;
const FALLBACK_PARAGRAPH_MIN_SENTENCES = 2;
const FALLBACK_PARAGRAPH_TARGET_MAX_SENTENCES = 5;
const SENTENCE_END_PATTERN = /[。！？!?；;：:]+(?:[”’"']+)?/g;
const MAX_RENDERED_PARAGRAPHS = 500;

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

function sentenceCount(text: string): number {
  return text.match(SENTENCE_END_PATTERN)?.length ?? 0;
}

function buildDisplayRuns(segments: TranscriptSegment[], fallbackText = ""): DisplayRun[] {
  const runs: DisplayRun[] = [];
  for (const segment of segments) {
    const text = segment.normalizedText.trim() || segment.text.trim();
    if (!text) continue;
    const speakerId = segment.speakerId ?? null;
    const sourceTrack = segment.sourceTrack ?? null;
    const key = sourceTrack ? `track:${sourceTrack}:${speakerId ?? "unknown"}` : `speaker:${speakerId ?? "unknown"}`;
    const previous = runs[runs.length - 1];
    if (previous?.key === key) {
      previous.text = joinCheckpointText(previous.text, text);
      previous.segmentIds.push(segment.segmentId);
      previous.speakerConfidence = mergeSpeakerConfidence(
        previous.speakerConfidence,
        segment.speakerConfidence ?? null,
      );
      continue;
    }
    runs.push({
      key,
      text,
      segmentIds: [segment.segmentId],
      speakerId,
      speakerLabel: segment.speakerLabel ?? null,
      speakerConfidence: segment.speakerConfidence ?? null,
      sourceTrack,
    });
  }
  return runs.length ? runs : [{
    key: "speaker:unknown",
    text: fallbackText,
    segmentIds: [],
    speakerId: null,
    speakerLabel: null,
    speakerConfidence: null,
    sourceTrack: null,
  }];
}

function assembleDisplayParagraphs(segments: TranscriptSegment[]): DisplayParagraph[] {
  const ordered = [...segments].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
  const paragraphs: DisplayParagraph[] = [];
  let previousSegment: TranscriptSegment | undefined;
  for (const segment of ordered) {
    const text = segment.normalizedText.trim() || segment.text.trim();
    if (!text) continue;
    const previous = paragraphs[paragraphs.length - 1];
    const gap = previousSegment?.endedAtMs !== null && previousSegment?.endedAtMs !== undefined && segment.startedAtMs !== null
      ? segment.startedAtMs - previousSegment.endedAtMs
      : 0;
    const speakerId = segment.speakerId ?? null;
    const previousDuration = previous
      ? (previous.endedAtMs ?? previous.startedAtMs ?? 0) - (previous.startedAtMs ?? 0)
      : 0;
    const previousSentenceCount = previous ? sentenceCount(previous.text) : 0;
    const previousHasMinimumCheckpoints = (previous?.segmentIds.length ?? 0) >= 2;
    const previousReadable = previousHasMinimumCheckpoints &&
      previousDuration >= FALLBACK_PARAGRAPH_MIN_DURATION_MS &&
      previousSentenceCount >= FALLBACK_PARAGRAPH_MIN_SENTENCES;
    const previousReachedTarget = previousHasMinimumCheckpoints && (
      previousDuration >= FALLBACK_PARAGRAPH_TARGET_MAX_DURATION_MS ||
      (previous?.text.length ?? 0) >= FALLBACK_PARAGRAPH_TARGET_MAX_CHARACTERS ||
      previousSentenceCount >= FALLBACK_PARAGRAPH_TARGET_MAX_SENTENCES
    );
    const previousReachedHardLimit = previousHasMinimumCheckpoints && (
      previousDuration >= FALLBACK_PARAGRAPH_HARD_MAX_DURATION_MS ||
      (previous?.text.length ?? 0) >= FALLBACK_PARAGRAPH_HARD_MAX_CHARACTERS
    );
    const sourceTrackChanged = Boolean(
      previousSegment?.sourceTrack && segment.sourceTrack &&
      previousSegment.sourceTrack !== segment.sourceTrack &&
      ["microphone", "system_audio"].includes(previousSegment.sourceTrack) &&
      ["microphone", "system_audio"].includes(segment.sourceTrack),
    );
    const canJoin = Boolean(previous) &&
      gap < FALLBACK_PARAGRAPH_GAP_MS &&
      !previousReadable &&
      !previousReachedHardLimit &&
      !(previousDuration >= FALLBACK_PARAGRAPH_MIN_DURATION_MS && previousSentenceCount > 0 && previousReachedTarget) &&
      !sourceTrackChanged;
    if (previous && canJoin) {
      previous.text = joinCheckpointText(previous.text, text);
      previous.segmentIds.push(segment.segmentId);
      previous.endedAtMs = segment.endedAtMs ?? segment.startedAtMs;
      previous.runs = buildDisplayRuns(
        ordered.filter((candidate) => previous.segmentIds.includes(candidate.segmentId)),
        previous.text,
      );
      if (previous.speakerId !== speakerId) {
        previous.speakerId = null;
        previous.speakerLabel = null;
      }
      previous.revised = previous.revised || segment.correctionStatus === "changed" ||
        (segment.correctionStatus === undefined && segment.revision > 1);
      previous.correctionStatus = mergeCorrectionStatus(previous.correctionStatus, segment.correctionStatus);
      previous.corrections.push(...correctionDetails([segment]));
      previous.speakerConfidence = mergeSpeakerConfidence(
        previous.speakerConfidence,
        segment.speakerConfidence ?? null,
      );
      previousSegment = segment;
      continue;
    }
    paragraphs.push({
      id: `paragraph:${segment.segmentId}`,
      text,
      segmentIds: [segment.segmentId],
      startedAtMs: segment.startedAtMs,
      endedAtMs: segment.endedAtMs,
      speakerId,
      speakerLabel: segment.speakerLabel ?? null,
      speakerConfidence: segment.speakerConfidence ?? null,
      revised: segment.correctionStatus === "changed" ||
        (segment.correctionStatus === undefined && segment.revision > 1),
      correctionStatus: segment.correctionStatus,
      corrections: correctionDetails([segment]),
      runs: buildDisplayRuns([segment], text),
    });
    previousSegment = segment;
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

function correctionLabel(
  status: string | undefined,
  revised: boolean,
  aiIndicator: RuntimeIndicator | undefined,
): string | null {
  if (status === "processing") return "AI 校对中";
  if (status === "no_change") return "无需校对";
  if (status === "changed" || revised) return "已校对";
  if (status === "failed_preserved_original") return "已保留原文";
  if (status === "pending") {
    return aiIndicator?.state === "paused" || aiIndicator?.state === "offline" || aiIndicator?.state === "error"
      ? "识别原文"
      : "等待校对";
  }
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
      endedAtMs: paragraph.endMs,
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
      runs: buildDisplayRuns(checkpoints, text),
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

interface ResolvedDisplayRun extends DisplayRun {
  displayLabel: string | null;
  renameSpeakerId: string | null;
  lowConfidence: boolean;
}

function resolveDisplayRuns(
  runs: DisplayRun[],
  speakerById: Map<string, MeetingSpeaker>,
  showExperimentalSpeakers: boolean,
): ResolvedDisplayRun[] {
  const resolved: ResolvedDisplayRun[] = [];
  for (const run of runs) {
    const speaker = run.speakerId ? speakerById.get(run.speakerId) : undefined;
    const userConfirmed = speaker?.labelSource === "user" || speaker?.labelLocked === true;
    const mayShowIdentity = userConfirmed || showExperimentalSpeakers;
    const sourceLabel = run.sourceTrack === "microphone"
      ? "我"
      : run.sourceTrack === "system_audio"
        ? "会议声音"
        : null;
    const displayLabel = mayShowIdentity
      ? speaker?.speakerLabel ?? run.speakerLabel
      : sourceLabel;
    const renameSpeakerId = mayShowIdentity && run.speakerId ? run.speakerId : null;
    const lowConfidence = Boolean(
      showExperimentalSpeakers && !userConfirmed && run.speakerId &&
      (run.speakerConfidence === null || run.speakerConfidence < 0.7),
    );
    const previous = resolved[resolved.length - 1];
    if (
      previous && previous.displayLabel === displayLabel &&
      previous.renameSpeakerId === renameSpeakerId && previous.lowConfidence === lowConfidence
    ) {
      previous.text = joinCheckpointText(previous.text, run.text);
      previous.segmentIds.push(...run.segmentIds);
      previous.speakerConfidence = mergeSpeakerConfidence(previous.speakerConfidence, run.speakerConfidence);
      continue;
    }
    resolved.push({ ...run, displayLabel: displayLabel ?? null, renameSpeakerId, lowConfidence });
  }
  return resolved;
}

export function TranscriptPane({
  segments,
  semanticParagraphs = [],
  archivedTranscript,
  archivedSegmentCount,
  activePartial,
  connection,
  aiIndicator,
  mergeSegments = true,
  liveMode = true,
  speakers = [],
  onRenameSpeaker,
  onSeekAudio,
  onSelectionChange,
  onAskSelection,
  onSaveSelection,
}: TranscriptPaneProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [followingLatest, setFollowingLatest] = useState(true);
  const [newParagraphCount, setNewParagraphCount] = useState(0);
  const [editingSpeaker, setEditingSpeaker] = useState<{ paragraphId: string; speakerId: string } | null>(null);
  const [speakerDraft, setSpeakerDraft] = useState("");
  const [speakerSaving, setSpeakerSaving] = useState(false);
  const [speakerError, setSpeakerError] = useState<string | null>(null);
  const [selection, setSelection] = useState<TranscriptSelection | null>(null);
  const [selectionToolbarPosition, setSelectionToolbarPosition] = useState<SelectionToolbarPosition | null>(null);
  const selectionFrameRef = useRef<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showExperimentalSpeakers, setShowExperimentalSpeakers] = useState(false);
  const previousParagraphCount = useRef(0);
  const speakerById = useMemo(
    () => new Map(speakers.map((speaker) => [speaker.speakerId, speaker])),
    [speakers],
  );
  const hasExperimentalSpeakers = speakers.some((speaker) => speaker.labelSource !== "user");
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
        endedAtMs: segment.endedAtMs,
        speakerId: segment.speakerId ?? null,
        speakerLabel: segment.speakerLabel ?? null,
        speakerConfidence: segment.speakerConfidence ?? null,
        revised: segment.correctionStatus === "changed" ||
          (segment.correctionStatus === undefined && segment.revision > 1),
        correctionStatus: segment.correctionStatus,
        corrections: correctionDetails([segment]),
        runs: buildDisplayRuns([segment], segment.normalizedText.trim() || segment.text.trim()),
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
  const normalizedSearchQuery = searchQuery.trim().toLocaleLowerCase();
  const filteredParagraphs = normalizedSearchQuery
    ? paragraphs.filter((paragraph) => paragraph.text.toLocaleLowerCase().includes(normalizedSearchQuery))
    : paragraphs;
  const foldedParagraphCount = Math.max(0, filteredParagraphs.length - MAX_RENDERED_PARAGRAPHS);
  const renderedParagraphs = foldedParagraphCount
    ? filteredParagraphs.slice(-MAX_RENDERED_PARAGRAPHS)
    : filteredParagraphs;

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

  const captureSelection = useCallback(() => {
    const root = scrollRef.current;
    const browserSelection = window.getSelection();
    if (!root || !browserSelection || browserSelection.rangeCount === 0 || browserSelection.isCollapsed) {
      setSelection(null);
      setSelectionToolbarPosition(null);
      onSelectionChange?.(null);
      return;
    }
    const range = browserSelection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) {
      setSelection(null);
      setSelectionToolbarPosition(null);
      onSelectionChange?.(null);
      return;
    }
    const text = browserSelection.toString().trim().slice(0, 4_000);
    const segmentIds = Array.from(root.querySelectorAll<HTMLElement>("[data-segment-id]"))
      .filter((element) => {
        try { return range.intersectsNode(element); } catch { return false; }
      })
      .flatMap((element) => {
        const encodedIds = element.dataset.segmentIds;
        if (!encodedIds) return [element.dataset.segmentId ?? ""];
        try {
          const parsed = JSON.parse(encodedIds);
          return Array.isArray(parsed) ? parsed.map(String) : [];
        } catch {
          return [element.dataset.segmentId ?? ""];
        }
      })
      .filter((segmentId, index, all) => Boolean(segmentId) && all.indexOf(segmentId) === index);
    const next = text && segmentIds.length ? { text, segmentIds } : null;
    setSelection(next);
    if (next) {
      const rangeRect = typeof range.getBoundingClientRect === "function"
        ? range.getBoundingClientRect()
        : null;
      const rootRect = root.getBoundingClientRect();
      const hasRangePosition = Boolean(rangeRect && (rangeRect.width > 0 || rangeRect.height > 0));
      const anchorLeft = hasRangePosition && rangeRect
        ? rangeRect.left + rangeRect.width / 2
        : rootRect.left + rootRect.width / 2;
      const placeAbove = Boolean(hasRangePosition && rangeRect && rangeRect.top >= 64);
      const toolbarHalfWidth = Math.min(260, Math.max(80, (window.innerWidth - 24) / 2));
      setSelectionToolbarPosition({
        left: Math.min(window.innerWidth - 12 - toolbarHalfWidth, Math.max(12 + toolbarHalfWidth, anchorLeft)),
        top: hasRangePosition && rangeRect
          ? placeAbove ? rangeRect.top - 8 : rangeRect.bottom + 8
          : Math.min(window.innerHeight - 12, rootRect.top + 72),
        placement: placeAbove ? "above" : "below",
      });
    } else {
      setSelectionToolbarPosition(null);
    }
    onSelectionChange?.(next);
  }, [onSelectionChange]);

  const scheduleSelectionCapture = useCallback(() => {
    if (selectionFrameRef.current !== null) window.cancelAnimationFrame(selectionFrameRef.current);
    selectionFrameRef.current = window.requestAnimationFrame(() => {
      selectionFrameRef.current = null;
      captureSelection();
    });
  }, [captureSelection]);

  useEffect(() => {
    document.addEventListener("selectionchange", scheduleSelectionCapture);
    window.addEventListener("resize", scheduleSelectionCapture);
    return () => {
      document.removeEventListener("selectionchange", scheduleSelectionCapture);
      window.removeEventListener("resize", scheduleSelectionCapture);
      if (selectionFrameRef.current !== null) window.cancelAnimationFrame(selectionFrameRef.current);
    };
  }, [scheduleSelectionCapture]);

  const returnToLatest = () => {
    setFollowingLatest(true);
    setNewParagraphCount(0);
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  };

  const beginSpeakerRename = (paragraphId: string, speakerId: string | null, label: string) => {
    if (!speakerId) return;
    setEditingSpeaker({ paragraphId, speakerId });
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
        <div className="transcript-heading-tools">
          {hasExperimentalSpeakers ? (
            <label className="speaker-experiment-toggle" title="自动说话人尚未通过真实嘈杂会议门禁">
              <input
                type="checkbox"
                checked={showExperimentalSpeakers}
                onChange={(event) => setShowExperimentalSpeakers(event.target.checked)}
              />
              <UsersRound size={14} /><span>实验说话人</span>
            </label>
          ) : null}
          <div className="confirmed-count" title="已写入会议记录的文字段落">
            <Check size={14} />
            {archivedSegmentCount + paragraphs.length} 段已确认
          </div>
          <label className="transcript-search">
            <Search size={13} aria-hidden="true" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              aria-label="搜索会议文字"
              placeholder="搜索"
            />
          </label>
        </div>
      </header>

      <div
        className="transcript-scroll"
        data-testid="transcript-scroll"
        ref={scrollRef}
        onScroll={handleScroll}
        onPointerUp={scheduleSelectionCapture}
        onKeyUp={scheduleSelectionCapture}
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

        {foldedParagraphCount ? (
          <p className="transcript-virtualized-note" role="status">
            较早的 {foldedParagraphCount} 段已折叠；搜索仍会匹配完整已加载文字。
          </p>
        ) : null}

        {normalizedSearchQuery && !filteredParagraphs.length ? (
          <p className="transcript-search-empty">没有匹配的会议文字</p>
        ) : null}

        <div className="transcript-segments">
          {renderedParagraphs.map((paragraph) => {
            const displayRuns = resolveDisplayRuns(paragraph.runs, speakerById, showExperimentalSpeakers);
            return (
              <article
                className="transcript-segment"
                id={segmentDomId(paragraph.segmentIds[0])}
                data-segment-id={paragraph.segmentIds[0]}
                data-segment-ids={JSON.stringify(paragraph.segmentIds)}
                key={paragraph.id}
                tabIndex={-1}
              >
                {paragraph.segmentIds.slice(1).map((segmentId) => (
                  <span
                    className="transcript-segment-anchor"
                    id={segmentDomId(segmentId)}
                    key={segmentId}
                    aria-hidden="true"
                  />
                ))}
                <div className="segment-meta">
                  {onSeekAudio && paragraph.startedAtMs !== null ? (
                    <button
                      className="segment-time-button"
                      type="button"
                      onClick={() => onSeekAudio(paragraph.startedAtMs ?? 0)}
                      aria-label={`在录音中定位到 ${formatOffset(paragraph.startedAtMs)}`}
                    >
                      {formatRange(paragraph.startedAtMs, paragraph.endedAtMs)}
                    </button>
                  ) : <time>{formatRange(paragraph.startedAtMs, paragraph.endedAtMs)}</time>}
                  {correctionLabel(paragraph.correctionStatus, paragraph.revised, aiIndicator) ? (
                    <span
                      className={`correction-mark correction-mark--${paragraph.correctionStatus ?? "changed"}`}
                      title={paragraph.correctionStatus === "changed" && paragraph.revised ? "文字已发生真实修正，可在复盘中查看原文与最终版本" : undefined}
                    >
                      <Sparkles size={12} />{correctionLabel(paragraph.correctionStatus, paragraph.revised, aiIndicator)}
                    </span>
                  ) : null}
                </div>
                <div className="segment-content">
                  {displayRuns.some((run) => Boolean(run.displayLabel)) ? (
                    <div className="transcript-speaker-runs">
                    {displayRuns.map((run, runIndex) => {
                      const editingThisSpeaker = Boolean(
                        editingSpeaker?.paragraphId === paragraph.id &&
                        editingSpeaker.speakerId === run.renameSpeakerId,
                      );
                      return (
                        <div className="transcript-speaker-run" key={`${paragraph.id}:${run.key}:${runIndex}`}>
                          {run.displayLabel ? (
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
                                    aria-label={`重命名 ${run.displayLabel}`}
                                    maxLength={80}
                                    autoFocus
                                  />
                                  <button
                                    className="icon-button icon-button--small"
                                    type="submit"
                                    aria-label={`保存 ${run.displayLabel} 的名称`}
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
                                  onClick={() => beginSpeakerRename(paragraph.id, run.renameSpeakerId, run.displayLabel ?? "")}
                                  disabled={!onRenameSpeaker || !run.renameSpeakerId}
                                  title={onRenameSpeaker && run.renameSpeakerId ? `重命名 ${run.displayLabel}` : undefined}
                                >
                                  <span>{run.displayLabel}</span>
                                  {onRenameSpeaker && run.renameSpeakerId ? <Pencil size={11} aria-hidden="true" /> : null}
                                </button>
                              )}
                              {run.lowConfidence ? (
                                <span
                                  className="speaker-confidence-hint"
                                  title="自动说话人置信度较低，请结合上下文确认"
                                  aria-label="自动说话人置信度较低"
                                >
                                  <CircleAlert size={12} />
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                          <p>{run.text}</p>
                        </div>
                      );
                    })}
                    </div>
                  ) : <p>{paragraph.text}</p>}
                  {paragraph.corrections.length ? (
                    <details className="transcript-correction-detail">
                      <summary>查看校对稿</summary>
                      <div className="transcript-correction-diff">
                        <span>校对稿</span>
                        <p>{paragraph.text}</p>
                      </div>
                    </details>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>

        {liveMode ? (
          <div className="active-partial-slot" data-active={Boolean(visibleActivePartial)}>
            {visibleActivePartial ? (
              <div className="active-partial" aria-label="正在识别">
                <span className="listening-pulse" aria-hidden="true" />
                <p>{visibleActivePartial.text}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {selection && selectionToolbarPosition && (onAskSelection || onSaveSelection) ? (
          <div
            className={`transcript-selection-toolbar transcript-selection-toolbar--${selectionToolbarPosition.placement}`}
            role="toolbar"
            aria-label="选中文字操作"
            style={{ left: selectionToolbarPosition.left, top: selectionToolbarPosition.top }}
            onPointerDown={(event) => event.preventDefault()}
          >
            {onSaveSelection ? (
              <button type="button" onClick={() => void onSaveSelection(selection)} title="保存到笔记" aria-label="保存到笔记">
                <NotebookPen size={15} /><span>记笔记</span>
              </button>
            ) : null}
            {onAskSelection ? (
              <>
                <button type="button" onClick={() => onAskSelection(selection, "ask")} title="询问 AI" aria-label="询问 AI">
                  <MessageSquareText size={15} /><span>问 AI</span>
                </button>
                <button type="button" onClick={() => onAskSelection(selection, "explain")} title="解释这段" aria-label="解释这段">
                  <ScanText size={15} /><span>解释</span>
                </button>
                <button type="button" onClick={() => onAskSelection(selection, "extract_action_items")} title="提炼行动项" aria-label="提炼行动项">
                  <ListTodo size={15} /><span>行动项</span>
                </button>
                <button type="button" onClick={() => onAskSelection(selection, "mark_pending")} title="标记待确认" aria-label="标记待确认">
                  <CircleHelp size={15} /><span>待确认</span>
                </button>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
