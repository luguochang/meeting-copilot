import {
  AlertCircle,
  Check,
  CircleEllipsis,
  Download,
  FileAudio,
  FileJson,
  FileText,
  FileType2,
  Eye,
  History,
  ListChecks,
  LoaderCircle,
  Mic2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type {
  MeetingViewState,
  OpenQuestionProjection,
  ReviewDocument,
  ReviewDocumentKind,
  ReviewDocumentRevision,
  ReviewJob,
  ReviewJobKind,
  TranscriptSegment,
} from "../../domain/events";
import { HttpMeetingApi, type MeetingExportFormat } from "../../api/client";
import type {
  AudioTrackId,
  MeetingAudioDerivedAsset,
  MeetingAudioTrackState,
  MeetingAudioWithTracks,
} from "../../api/schema";
import { MarkdownDocumentEditor } from "./MarkdownDocumentEditor";
import { useReviewDocumentDraft } from "./useReviewDocumentDraft";
import { TranscriptPane } from "../live-meeting/TranscriptPane";
import { segmentDomId } from "../live-meeting/domIds";

type ReviewTab = "review" | "actions" | "transcript" | "audio";

interface ReviewWorkspaceProps {
  state: MeetingViewState;
  onReloadTranscript(): void;
  onReloadAudio(): void;
  onExport(format: MeetingExportFormat): Promise<void>;
  onSaveDocument?(kind: ReviewDocumentKind, expectedRevision: number, content: unknown): Promise<ReviewDocument>;
  onLoadDocumentRevisions?(kind: ReviewDocumentKind): Promise<ReviewDocumentRevision[]>;
  onRegenerateDocument?(kind: ReviewDocumentKind): Promise<void>;
  onRetryReviewJob?(kind: ReviewJobKind): Promise<void>;
  onRenameSpeaker?(speakerId: string, speakerLabel: string): Promise<void>;
  onCreateMixedAudio?(signal?: AbortSignal): Promise<MeetingAudioDerivedAsset>;
  onRefresh?(): Promise<void> | void;
}

const tabs: Array<{ id: ReviewTab; label: string; icon: typeof Sparkles }> = [
  { id: "review", label: "复盘", icon: Sparkles },
  { id: "actions", label: "决策与待办", icon: ListChecks },
  { id: "transcript", label: "会议文字", icon: FileText },
  { id: "audio", label: "录音", icon: FileAudio },
];

const jobNames: Record<ReviewJobKind, string> = {
  minutes: "会议纪要",
  approach: "分析建议",
  index: "内容索引",
};

const documentNames: Record<ReviewDocumentKind, string> = {
  minutes: "会议复盘",
  decisions: "决策",
  action_items: "行动项",
  risks: "风险",
  transcript: "完整文字",
};

function approachCardLabel(cardType: string): string {
  if (cardType === "approach.alternative") return "备选方案";
  if (cardType === "approach.risk") return "风险提示";
  if (cardType === "approach.consideration") return "考虑事项";
  return "分析建议";
}

function audioTrackLabel(trackId: AudioTrackId): string {
  return trackId === "microphone" ? "我的麦克风" : "会议声音";
}

function audioTrackStatusLabel(status: string): string {
  if (status === "ready") return "已保存";
  if (status === "active") return "正在录制";
  if (status === "sealed" || status === "exporting") return "正在整理";
  if (status === "interrupted") return "录制中断";
  if (status === "failed") return "录制失败";
  if (status === "missing") return "没有录到";
  return "暂不可用";
}

function audioErrorMessage(errorClass: string | null): string {
  if (errorClass === "screen_capture_permission_denied") return "未获得会议声音权限";
  if (errorClass === "microphone_permission_denied") return "未获得麦克风权限";
  if (errorClass === "content_unavailable") return "没有可录制的会议声音";
  if (errorClass === "device_disconnected") return "录音设备已断开";
  if (errorClass === "capture_interrupted") return "录音过程中断";
  return "录音没有完成，系统未提供更具体原因";
}

const PROVIDER_NOT_CONFIGURED_ERROR = "ProviderRuntimeNotConfiguredDeferred";

function providerNotConfigured(job: ReviewJob | undefined): boolean {
  return job?.errorClass === PROVIDER_NOT_CONFIGURED_ERROR;
}

function formatAudioDuration(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.round(durationMs / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

const AUDIO_METADATA_TIMEOUT_MS = 3_500;

interface MeetingAudioPlayerProps {
  audioRef: RefObject<HTMLAudioElement>;
  sourceUrl: string;
  onLoadedMetadata(): void;
}

function MeetingAudioPlayer({ audioRef, sourceUrl, onLoadedMetadata }: MeetingAudioPlayerProps) {
  const [resolvedSource, setResolvedSource] = useState(sourceUrl);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [retryGeneration, setRetryGeneration] = useState(0);
  const metadataLoadedRef = useRef(false);
  const fallbackRef = useRef<() => void>(() => undefined);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let fallbackStarted = false;
    let blobUrl: string | null = null;

    metadataLoadedRef.current = false;
    setResolvedSource(sourceUrl);
    setStatus("loading");

    const loadBlobFallback = async () => {
      if (fallbackStarted || disposed) return;
      fallbackStarted = true;
      try {
        const response = await fetch(sourceUrl, {
          headers: { Accept: "audio/wav" },
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`录音读取失败（${response.status}）`);
        const blob = await response.blob();
        if (blob.size < 44) throw new Error("录音文件不完整");
        if (disposed) return;
        blobUrl = URL.createObjectURL(blob);
        setResolvedSource(blobUrl);
      } catch {
        if (!disposed && !controller.signal.aborted) {
          setStatus("error");
        }
      }
    };

    fallbackRef.current = () => {
      void loadBlobFallback();
    };
    timeoutRef.current = window.setTimeout(() => {
      if (!metadataLoadedRef.current) fallbackRef.current();
    }, AUDIO_METADATA_TIMEOUT_MS);

    return () => {
      disposed = true;
      controller.abort();
      fallbackRef.current = () => undefined;
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [retryGeneration, sourceUrl]);

  const handleLoadedMetadata = () => {
    const duration = audioRef.current?.duration ?? 0;
    if (!Number.isFinite(duration) || duration <= 0) {
      fallbackRef.current();
      return;
    }
    metadataLoadedRef.current = true;
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    setStatus("ready");
    onLoadedMetadata();
  };

  const handleError = () => {
    if (resolvedSource === sourceUrl) {
      fallbackRef.current();
      return;
    }
    setStatus("error");
  };

  return (
    <>
      <audio
        ref={audioRef}
        controls
        preload="metadata"
        src={resolvedSource}
        onLoadedMetadata={handleLoadedMetadata}
        onError={handleError}
      >
        当前环境不支持音频播放。
      </audio>
      {status === "loading" ? <p className="rail-empty" role="status">正在准备录音回放...</p> : null}
      {status === "error" ? (
        <div className="inline-error" role="alert">
          <span>录音已保存，但播放器加载失败。</span>
          <button className="secondary-button" type="button" onClick={() => setRetryGeneration((value) => value + 1)}>
            重试播放
          </button>
        </div>
      ) : null}
    </>
  );
}

function jobState(job: ReviewJob | undefined, qualityPaused: boolean): { text: string; tone: string } {
  if (!job) return { text: "等待开始", tone: "muted" };
  if (job.status === "succeeded") {
    return job.output?.degraded === true
      ? { text: "部分完成", tone: "warning" }
      : { text: "已完成", tone: "success" };
  }
  if (job.status === "failed" || job.status === "cancelled") {
    if (qualityPaused && (job.kind === "minutes" || job.kind === "approach")) {
      return { text: "识别质量不足，已暂停", tone: "warning" };
    }
    return { text: "生成失败", tone: "error" };
  }
  if (job.status === "retry_wait") {
    return providerNotConfigured(job)
      ? { text: "等待配置 AI", tone: "warning" }
      : { text: "正在重试", tone: "working" };
  }
  if (job.status === "running") return { text: "正在生成", tone: "working" };
  return { text: "等待处理", tone: "working" };
}

function reviewJobError(job: ReviewJob): string {
  if (job.errorMessage) return job.errorMessage;
  const error = (job.errorClass ?? "").toLocaleLowerCase();
  if (error === PROVIDER_NOT_CONFIGURED_ERROR.toLocaleLowerCase()) {
    return "AI 尚未配置；会议文字和录音已保存，配置 AI 后可继续生成。";
  }
  if (/auth|credential|api.?key/.test(error)) return "Provider 未连接，请检查 AI 设置后重试。";
  if (/timeout/.test(error)) return "Provider 响应超时，可以单独重试此产物。";
  if (/rate|limit|429/.test(error)) return "Provider 请求受限，请稍后重试此产物。";
  if (/schema|validation|json/.test(error)) return "AI 返回结构不完整，可以重新生成此产物。";
  if (/local|service|connection/.test(error)) return "本地会议服务暂不可用，请恢复后重试。";
  return "此产物生成失败，文字、录音和其他会议结果不受影响。";
}

function displayText(segment: TranscriptSegment): string {
  return segment.normalizedText.trim() || segment.text.trim();
}

interface MinutesActionItem {
  item: string;
  owner: string | null;
  deadline: string | null;
}

interface MinutesActions {
  decisions: string[];
  actionItems: MinutesActionItem[];
  risks: string[];
  openQuestions: string[];
}

interface EditableDecision {
  id: string;
  text: string;
  status: string;
  evidenceSegmentId: string | null;
}

interface EditableActionItem extends EditableDecision {
  owner: string | null;
  deadline: string | null;
}

interface EditableRisk extends EditableDecision {
  mitigation: string | null;
}

interface EditableTranscriptSegment {
  segmentId: string;
  text: string;
  startedAtMs: number | null;
  endedAtMs: number | null;
  speakerId: string | null;
  speakerLabel: string | null;
  speakerConfidence: number | null;
}

function contentItems(content: unknown, key: string): unknown[] | null {
  if (Array.isArray(content)) return content;
  if (!content || typeof content !== "object") return null;
  const source = content as Record<string, unknown>;
  const value = source[key] ?? source.items ?? source.segments;
  return Array.isArray(value) ? value : null;
}

function editableDecisions(content: unknown, fallback: EditableDecision[]): EditableDecision[] {
  const values = contentItems(content, "decisions");
  if (!values) return fallback;
  return values.flatMap((value, index) => {
    if (typeof value === "string" && value.trim()) {
      return [{ id: `decision-user-${index}`, text: value.trim(), status: "confirmed", evidenceSegmentId: null }];
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const item = value as Record<string, unknown>;
    const text = typeof item.text === "string" ? item.text.trim() : "";
    if (!text) return [];
    return [{
      id: typeof item.id === "string" ? item.id : `decision-user-${index}`,
      text,
      status: typeof item.status === "string" ? item.status : "confirmed",
      evidenceSegmentId: typeof item.evidence_segment_id === "string"
        ? item.evidence_segment_id
        : typeof item.evidenceSegmentId === "string" ? item.evidenceSegmentId : null,
    }];
  });
}

function editableActions(content: unknown, fallback: EditableActionItem[]): EditableActionItem[] {
  const values = contentItems(content, "action_items");
  if (!values) return fallback;
  return values.flatMap((value, index) => {
    if (typeof value === "string" && value.trim()) {
      return [{ id: `action-user-${index}`, text: value.trim(), status: "open", evidenceSegmentId: null, owner: null, deadline: null }];
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const item = value as Record<string, unknown>;
    const text = typeof item.text === "string" ? item.text.trim() : typeof item.item === "string" ? item.item.trim() : "";
    if (!text) return [];
    return [{
      id: typeof item.id === "string" ? item.id : `action-user-${index}`,
      text,
      status: typeof item.status === "string" ? item.status : "open",
      evidenceSegmentId: typeof item.evidence_segment_id === "string"
        ? item.evidence_segment_id
        : typeof item.evidenceSegmentId === "string" ? item.evidenceSegmentId : null,
      owner: typeof item.owner === "string" && item.owner.trim() ? item.owner.trim() : null,
      deadline: typeof item.deadline === "string" && item.deadline.trim() ? item.deadline.trim() : null,
    }];
  });
}

function editableRisks(content: unknown, fallback: EditableRisk[]): EditableRisk[] {
  const values = contentItems(content, "risks");
  if (!values) return fallback;
  return values.flatMap((value, index) => {
    if (typeof value === "string" && value.trim()) {
      return [{ id: `risk-user-${index}`, text: value.trim(), status: "open", evidenceSegmentId: null, mitigation: null }];
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const item = value as Record<string, unknown>;
    const text = typeof item.text === "string" ? item.text.trim() : "";
    if (!text) return [];
    return [{
      id: typeof item.id === "string" ? item.id : `risk-user-${index}`,
      text,
      status: typeof item.status === "string" ? item.status : "open",
      evidenceSegmentId: typeof item.evidence_segment_id === "string"
        ? item.evidence_segment_id
        : typeof item.evidenceSegmentId === "string" ? item.evidenceSegmentId : null,
      mitigation: typeof item.mitigation === "string" && item.mitigation.trim() ? item.mitigation.trim() : null,
    }];
  });
}

function editableTranscript(content: unknown, fallback: EditableTranscriptSegment[]): EditableTranscriptSegment[] {
  const values = contentItems(content, "transcript");
  if (!values) return fallback;
  return values.flatMap((value, index) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const item = value as Record<string, unknown>;
    const text = typeof item.text === "string" ? item.text : "";
    const segmentId = typeof item.segment_id === "string"
      ? item.segment_id
      : typeof item.segmentId === "string" ? item.segmentId : `segment-user-${index}`;
    const fallbackSegment = fallback.find((segment) => segment.segmentId === segmentId);
    return [{
      segmentId,
      text,
      startedAtMs: typeof item.started_at_ms === "number" ? item.started_at_ms : typeof item.startedAtMs === "number" ? item.startedAtMs : null,
      endedAtMs: typeof item.ended_at_ms === "number" ? item.ended_at_ms : typeof item.endedAtMs === "number" ? item.endedAtMs : null,
      speakerId: typeof item.speaker_id === "string"
        ? item.speaker_id
        : typeof item.speakerId === "string" ? item.speakerId : fallbackSegment?.speakerId ?? null,
      speakerLabel: typeof item.speaker_label === "string"
        ? item.speaker_label
        : typeof item.speakerLabel === "string" ? item.speakerLabel : fallbackSegment?.speakerLabel ?? null,
      speakerConfidence: typeof item.speaker_confidence === "number"
        ? item.speaker_confidence
        : typeof item.speakerConfidence === "number" ? item.speakerConfidence : fallbackSegment?.speakerConfidence ?? null,
    }];
  });
}

const decisionsContent = (items: EditableDecision[]) => ({ decisions: items });
const actionsContent = (items: EditableActionItem[]) => ({ action_items: items });
const risksContent = (items: EditableRisk[]) => ({ risks: items });
const transcriptContent = (items: EditableTranscriptSegment[]) => ({
  segments: items.map((item) => ({
    segment_id: item.segmentId,
    text: item.text,
    started_at_ms: item.startedAtMs,
    ended_at_ms: item.endedAtMs,
    speaker_id: item.speakerId,
    speaker_label: item.speakerLabel,
    speaker_confidence: item.speakerConfidence,
  })),
});

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => {
      const key = normalizedTextKey(item);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function actionItems(value: unknown): MinutesActionItem[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap((entry) => {
    let parsed: MinutesActionItem | null = null;
    if (typeof entry === "string" && entry.trim()) {
      parsed = { item: entry.trim(), owner: null, deadline: null };
    }
    if (!parsed && entry && typeof entry === "object" && !Array.isArray(entry)) {
      const raw = entry as Record<string, unknown>;
      const item = typeof raw.item === "string" ? raw.item.trim() : "";
      if (item) {
        parsed = {
          item,
          owner: typeof raw.owner === "string" && raw.owner.trim() ? raw.owner.trim() : null,
          deadline: typeof raw.deadline === "string" && raw.deadline.trim() ? raw.deadline.trim() : null,
        };
      }
    }
    if (!parsed) return [];
    const key = [parsed.item, parsed.owner, parsed.deadline].map((item) => normalizedTextKey(item ?? "")).join("|");
    if (seen.has(key)) return [];
    seen.add(key);
    return [parsed];
  });
}

function normalizedTextKey(value: string): string {
  return value
    .trim()
    .replace(/\s+/g, " ")
    .replace(/[。.!！?？]+$/g, "")
    .toLocaleLowerCase();
}

function markdownList(markdown: string, acceptedHeadings: string[]): string[] {
  const accepted = new Set(acceptedHeadings);
  let active = false;
  let fenced = false;
  const items: string[] = [];
  for (const rawLine of markdown.split("\n")) {
    if (/^\s{0,3}(```|~~~)/.test(rawLine)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    const headingMatch = rawLine.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
    if (headingMatch) {
      active = accepted.has(headingMatch[1].trim());
      continue;
    }
    if (active && rawLine.startsWith("- ")) items.push(rawLine.slice(2).trim());
  }
  return strings(items);
}

function legacyMarkdownActionItem(value: string): MinutesActionItem {
  const metadata = value.match(/^(.*) \(owner: (.*), deadline: (.*)\)$/);
  if (!metadata) return { item: value, owner: null, deadline: null };
  return {
    item: metadata[1].trim(),
    owner: metadata[2].trim() || null,
    deadline: metadata[3].trim() || null,
  };
}

function minutesActions(markdown: string, structured: Record<string, unknown> | null): MinutesActions {
  if (structured !== null) {
    return {
      decisions: strings(structured.decisions),
      actionItems: actionItems(structured.action_items ?? structured.actionItems),
      risks: strings(structured.risks),
      openQuestions: strings(structured.open_questions ?? structured.openQuestions),
    };
  }
  return {
    decisions: markdownList(markdown, ["已确认决策", "已确认重点", "决策"]),
    actionItems: markdownList(markdown, ["行动项", "待办"]).map(legacyMarkdownActionItem),
    risks: markdownList(markdown, ["风险"]),
    openQuestions: markdownList(markdown, ["未闭环问题", "待确认问题"]),
  };
}

interface ReviewQuestion {
  text: string;
  evidenceSegmentId: string | null;
}

function mergeOpenQuestions(
  minuteQuestions: string[],
  pendingQuestions: OpenQuestionProjection[],
): ReviewQuestion[] {
  const merged = new Map<string, ReviewQuestion>();
  for (const text of minuteQuestions) {
    const key = normalizedTextKey(text);
    if (key) merged.set(key, { text, evidenceSegmentId: null });
  }
  for (const question of pendingQuestions) {
    const key = normalizedTextKey(question.text);
    if (!key) continue;
    const current = merged.get(key);
    merged.set(key, {
      text: current?.text ?? question.text,
      evidenceSegmentId: current?.evidenceSegmentId ?? question.evidenceSegmentIds[0] ?? null,
    });
  }
  return [...merged.values()];
}

interface StructuredRevisionHistoryProps {
  kind: ReviewDocumentKind;
  revisions: ReviewDocumentRevision[];
  loading: boolean;
  error: string | null;
  visibleRevision: number | null;
  onSelect(revision: number | null): void;
  onClose(): void;
}

function revisionContentJson(content: unknown): string {
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

function StructuredRevisionHistory({
  kind,
  revisions,
  loading,
  error,
  visibleRevision,
  onSelect,
  onClose,
}: StructuredRevisionHistoryProps) {
  const label = documentNames[kind];
  return (
    <section className="review-revision-history" aria-label={`${label}版本历史`}>
      <div>
        <strong>{label}版本历史</strong>
        <button className="icon-button icon-button--small" type="button" onClick={onClose} aria-label={`关闭${label}版本历史`}>
          <X size={14} />
        </button>
      </div>
      {loading ? <p role="status"><LoaderCircle className="spin" size={14} />正在读取版本</p> : null}
      {!loading && !error && revisions.length === 0 ? <p>尚无历史版本</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
      {revisions.map((revision) => {
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
                onClick={() => onSelect(expanded ? null : revision.revision)}
              >
                <Eye size={14} />
              </button>
            </span>
            {expanded ? (
              <pre
                aria-label={`${label}版本 ${revision.revision} 内容`}
                style={{ gridColumn: "1 / -1", overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
              >
                {revisionContentJson(revision.contentJson)}
              </pre>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}

export function ReviewWorkspace({
  state,
  onReloadTranscript,
  onReloadAudio,
  onExport,
  onSaveDocument,
  onLoadDocumentRevisions,
  onRegenerateDocument,
  onRetryReviewJob,
  onRenameSpeaker,
  onCreateMixedAudio,
  onRefresh,
}: ReviewWorkspaceProps) {
  const saveDocument = useMemo(
    () => onSaveDocument ?? (async () => {
      throw new Error("当前页面未连接文档保存接口");
    }),
    [onSaveDocument],
  );
  const loadDocumentRevisions = onLoadDocumentRevisions ?? (async () => []);
  const regenerateDocument = onRegenerateDocument ?? (async () => {
    throw new Error("当前页面未连接文档重生成接口");
  });
  const retryReviewJobRequest = onRetryReviewJob ?? (async () => {
    throw new Error("当前页面未连接会后任务重试接口");
  });
  const refreshAction = onRefresh ?? (() => undefined);
  const [activeTab, setActiveTab] = useState<ReviewTab>("review");
  const [pendingEvidence, setPendingEvidence] = useState<string | null>(null);
  const [pendingAudioOffsetMs, setPendingAudioOffsetMs] = useState<number | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exporting, setExporting] = useState<MeetingExportFormat | null>(null);
  const [exportNotice, setExportNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [factsEditing, setFactsEditing] = useState(false);
  const [transcriptEditing, setTranscriptEditing] = useState(false);
  const [retryingJobs, setRetryingJobs] = useState<Partial<Record<ReviewJobKind, boolean>>>({});
  const [jobErrors, setJobErrors] = useState<Partial<Record<ReviewJobKind, string>>>({});
  const [revisionHistoryKind, setRevisionHistoryKind] = useState<ReviewDocumentKind | null>(null);
  const [documentRevisions, setDocumentRevisions] = useState<ReviewDocumentRevision[]>([]);
  const [documentRevisionsLoading, setDocumentRevisionsLoading] = useState(false);
  const [documentRevisionsError, setDocumentRevisionsError] = useState<string | null>(null);
  const [visibleDocumentRevision, setVisibleDocumentRevision] = useState<number | null>(null);
  const [selectedAudioKey, setSelectedAudioKey] = useState<string | null>(null);
  const [createdMixedAsset, setCreatedMixedAsset] = useState<MeetingAudioDerivedAsset | null>(null);
  const [creatingMixed, setCreatingMixed] = useState(false);
  const [mixedError, setMixedError] = useState<string | null>(null);
  const [mixedNotice, setMixedNotice] = useState<string | null>(null);
  const revisionRequestGenerationRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement>(null);
  const createMixedAudio = useMemo(
    () => onCreateMixedAudio ?? ((signal?: AbortSignal) => new HttpMeetingApi().createMixedAudio(state.meetingId, signal)),
    [onCreateMixedAudio, state.meetingId],
  );
  const audioDetail = state.audioDetail as MeetingAudioWithTracks | null;
  const transcript = state.fullTranscript.length ? state.fullTranscript : state.segments;
  const keptSuggestions = state.suggestions.filter(
    (suggestion) => suggestion.status === "committed" && suggestion.feedback === "kept",
  );
  const pendingQuestions = state.openQuestions.filter((question) =>
    ["open", "carried_over", "unknown"].includes(question.status),
  );
  const minutesActionData = minutesActions(state.minutes?.markdown ?? "", state.minutes?.structured ?? null);
  const qualityPaused = state.diagnostics.formal_derivation_status === "suppressed_by_asr_semantic_quality"
    || (Array.isArray(state.diagnostics.degradation_reasons)
      && state.diagnostics.degradation_reasons.includes("asr_semantic_quality_blocked"));
  const reviewQuestions = mergeOpenQuestions(minutesActionData.openQuestions, pendingQuestions);
  const minutesJobFailed = ["failed", "cancelled"].includes(state.reviewJobs.minutes?.status ?? "");
  const decisionFallback = useMemo<EditableDecision[]>(() => {
    const realtime = state.decisionCandidates
      .filter((item) => item.status !== "dismissed")
      .map((item) => ({
        id: item.id,
        text: item.text,
        status: item.status,
        evidenceSegmentId: item.evidenceSegmentIds[0] ?? null,
      }));
    return realtime.length
      ? realtime
      : minutesActionData.decisions.map((text, index) => ({
          id: `decision-minutes-${index}`,
          text,
          status: "confirmed",
          evidenceSegmentId: null,
        }));
  }, [minutesActionData.decisions, state.decisionCandidates]);
  const actionFallback = useMemo<EditableActionItem[]>(() => {
    const realtime = state.actionItems
      .filter((item) => item.status !== "dismissed")
      .map((item) => ({
        id: item.id,
        text: item.text,
        status: item.status,
        evidenceSegmentId: item.evidenceSegmentIds[0] ?? null,
        owner: item.owner,
        deadline: item.deadline,
      }));
    return realtime.length
      ? realtime
      : minutesActionData.actionItems.map((item, index) => ({
          id: `action-minutes-${index}`,
          text: item.item,
          status: "open",
          evidenceSegmentId: null,
          owner: item.owner,
          deadline: item.deadline,
        }));
  }, [minutesActionData.actionItems, state.actionItems]);
  const riskFallback = useMemo<EditableRisk[]>(() => {
    const realtime = state.risks
      .filter((item) => item.status !== "dismissed")
      .map((item) => ({
        id: item.id,
        text: item.text,
        status: item.status,
        evidenceSegmentId: item.evidenceSegmentIds[0] ?? null,
        mitigation: item.mitigation,
      }));
    return realtime.length
      ? realtime
      : minutesActionData.risks.map((text, index) => ({
          id: `risk-minutes-${index}`,
          text,
          status: "open",
          evidenceSegmentId: null,
          mitigation: null,
        }));
  }, [minutesActionData.risks, state.risks]);
  const transcriptFallback = useMemo<EditableTranscriptSegment[]>(() => transcript.map((segment) => ({
    segmentId: segment.segmentId,
    text: displayText(segment),
    startedAtMs: segment.startedAtMs,
    endedAtMs: segment.endedAtMs,
    speakerId: segment.speakerId ?? null,
    speakerLabel: segment.speakerLabel ?? null,
    speakerConfidence: segment.speakerConfidence ?? null,
  })), [transcript]);

  const saveDecisions = useCallback(
    (revision: number, content: unknown) => saveDocument("decisions", revision, content),
    [saveDocument],
  );
  const saveActions = useCallback(
    (revision: number, content: unknown) => saveDocument("action_items", revision, content),
    [saveDocument],
  );
  const saveRisks = useCallback(
    (revision: number, content: unknown) => saveDocument("risks", revision, content),
    [saveDocument],
  );
  const saveTranscript = useCallback(
    (revision: number, content: unknown) => saveDocument("transcript", revision, content),
    [saveDocument],
  );
  const decisionsEditor = useReviewDocumentDraft({
    meetingId: state.meetingId,
    kind: "decisions",
    document: state.documents?.decisions,
    fallback: decisionFallback,
    enabled: factsEditing,
    fromContent: editableDecisions,
    toContent: decisionsContent,
    onSave: saveDecisions,
  });
  const actionsEditor = useReviewDocumentDraft({
    meetingId: state.meetingId,
    kind: "action_items",
    document: state.documents?.action_items,
    fallback: actionFallback,
    enabled: factsEditing,
    fromContent: editableActions,
    toContent: actionsContent,
    onSave: saveActions,
  });
  const risksEditor = useReviewDocumentDraft({
    meetingId: state.meetingId,
    kind: "risks",
    document: state.documents?.risks,
    fallback: riskFallback,
    enabled: factsEditing,
    fromContent: editableRisks,
    toContent: risksContent,
    onSave: saveRisks,
  });
  const transcriptEditor = useReviewDocumentDraft({
    meetingId: state.meetingId,
    kind: "transcript",
    document: state.documents?.transcript,
    fallback: transcriptFallback,
    enabled: transcriptEditing,
    fromContent: editableTranscript,
    toContent: transcriptContent,
    onSave: saveTranscript,
  });
  const transcriptOverrides = useMemo(
    () => new Map(transcriptEditor.draft.map((item) => [item.segmentId, item])),
    [transcriptEditor.draft],
  );
  const displayTranscript = useMemo(() => transcript.map((segment) => {
    const override = transcriptOverrides.get(segment.segmentId);
    return {
      ...segment,
      normalizedText: override?.text ?? displayText(segment),
      speakerId: override?.speakerId ?? segment.speakerId ?? null,
      speakerLabel: override?.speakerLabel ?? segment.speakerLabel ?? null,
      speakerConfidence: override?.speakerConfidence ?? segment.speakerConfidence ?? null,
    };
  }), [transcript, transcriptOverrides]);
  const reviewBasedOnOldTranscript = Boolean(
    state.documents?.transcript?.source === "user_final" &&
    (state.documents.minutes?.updatedAtMs ?? state.minutes?.updatedAtMs ?? 0) < (state.documents.transcript.updatedAtMs ?? 0),
  );
  const hasDecisionContent = decisionsEditor.draft.length > 0 || actionsEditor.draft.length > 0 || keptSuggestions.length > 0;
  const hasRiskContent = risksEditor.draft.length > 0 || reviewQuestions.length > 0;
  const actionUnavailable = !hasDecisionContent && !state.minutes
    ? qualityPaused
      ? { className: "inline-warning", text: "识别语义质量不足，决策与行动项提取已暂停。" }
      : minutesJobFailed
        ? { className: "inline-error", text: "会议纪要生成失败，未能提取决策与行动项。" }
        : { className: "review-empty", text: "会议纪要正在生成，完成后显示决策与行动项。" }
    : null;
  const riskUnavailable = !hasRiskContent && !state.minutes
    ? qualityPaused
      ? { className: "inline-warning", text: "识别语义质量不足，风险与待确认问题提取已暂停。" }
      : minutesJobFailed
        ? { className: "inline-error", text: "会议纪要生成失败，未能提取风险与待确认问题。" }
        : { className: "review-empty", text: "会议纪要正在生成，完成后显示风险与待确认问题。" }
    : null;

  const audioTrackRows = useMemo(() => {
    const tracks = new Map<AudioTrackId, MeetingAudioTrackState>();
    for (const track of audioDetail?.trackStates ?? []) {
      const current = tracks.get(track.trackId);
      if (!current || track.epoch >= current.epoch) tracks.set(track.trackId, track);
    }
    return (["microphone", "system_audio"] as const).map((trackId) => ({
      trackId,
      track: tracks.get(trackId) ?? null,
    }));
  }, [audioDetail?.trackStates]);
  const readyAudioTracks = audioTrackRows
    .map(({ track }) => track)
    .filter((track): track is MeetingAudioTrackState => Boolean(track?.status === "ready" && track.playbackUrl));
  const serverMixedAsset = (audioDetail?.derivedAssets ?? []).find(
    (asset) => asset.kind === "mixed" && asset.status === "ready" && asset.playbackUrl,
  ) ?? null;
  const mixedAsset = createdMixedAsset ?? serverMixedAsset;
  const mixedReady = Boolean(mixedAsset?.status === "ready" && mixedAsset.playbackUrl);
  const bothTracksReady = audioTrackRows.every(
    ({ track }) => track?.status === "ready" && Boolean(track.playbackUrl),
  );
  const audioHasTrackData = audioTrackRows.some(({ track }) => track !== null);
  const partialAudioFailure = audioHasTrackData && (
    audioDetail?.overallStatus === "partial_failure"
    || (readyAudioTracks.length > 0 && audioTrackRows.some(({ track }) => (
      track === null || track.status === "failed" || track.status === "interrupted"
    )))
  );
  const completeAudioFailure = audioHasTrackData && (
    audioDetail?.overallStatus === "failed" && readyAudioTracks.length === 0
  );
  const selectedAudio = useMemo(() => {
    const selectedTrack = readyAudioTracks.find((track) => `track:${track.trackId}:${track.epoch}` === selectedAudioKey);
    if (selectedTrack) {
      return {
        key: `track:${selectedTrack.trackId}:${selectedTrack.epoch}`,
        label: audioTrackLabel(selectedTrack.trackId),
        sourceUrl: selectedTrack.playbackUrl,
      };
    }
    if (selectedAudioKey?.startsWith("mixed:") && mixedReady && mixedAsset?.playbackUrl) {
      return { key: selectedAudioKey, label: "混合回放", sourceUrl: mixedAsset.playbackUrl };
    }
    const defaultTrack = readyAudioTracks.find((track) => track.trackId === "microphone") ?? readyAudioTracks[0];
    if (defaultTrack) {
      return {
        key: `track:${defaultTrack.trackId}:${defaultTrack.epoch}`,
        label: audioTrackLabel(defaultTrack.trackId),
        sourceUrl: defaultTrack.playbackUrl,
      };
    }
    return audioDetail?.playbackUrl
      ? { key: "legacy", label: "会议录音", sourceUrl: audioDetail.playbackUrl }
      : null;
  }, [audioDetail?.playbackUrl, mixedAsset?.playbackUrl, mixedReady, readyAudioTracks, selectedAudioKey]);

  useEffect(() => {
    if (state.meetingId) {
      setSelectedAudioKey(null);
      setCreatedMixedAsset(null);
      setMixedError(null);
      setMixedNotice(null);
    }
  }, [state.meetingId]);

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
      blockedByQuality: qualityPaused && (kind === "minutes" || kind === "approach"),
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

  const retryReviewJob = async (kind: ReviewJobKind) => {
    if (retryingJobs[kind]) return;
    setRetryingJobs((current) => ({ ...current, [kind]: true }));
    setJobErrors((current) => ({ ...current, [kind]: undefined }));
    try {
      await retryReviewJobRequest(kind);
      await refreshAction();
    } catch (error) {
      setJobErrors((current) => ({
        ...current,
        [kind]: error instanceof Error ? error.message : `${jobNames[kind]}重试失败`,
      }));
    } finally {
      setRetryingJobs((current) => ({ ...current, [kind]: false }));
    }
  };

  const createMixedReplay = async () => {
    if (!bothTracksReady || creatingMixed) return;
    setCreatingMixed(true);
    setMixedError(null);
    setMixedNotice(null);
    try {
      const asset = await createMixedAudio();
      setCreatedMixedAsset(asset);
      setMixedNotice("混合回放已生成，原来的两条录音仍然保留。");
    } catch (error) {
      setMixedError(error instanceof Error ? error.message : "混合回放生成失败，请稍后重试。");
    } finally {
      setCreatingMixed(false);
    }
  };

  const showDocumentRevisions = async (kind: ReviewDocumentKind) => {
    const generation = revisionRequestGenerationRef.current + 1;
    revisionRequestGenerationRef.current = generation;
    setRevisionHistoryKind(kind);
    setDocumentRevisions([]);
    setVisibleDocumentRevision(null);
    setDocumentRevisionsError(null);
    setDocumentRevisionsLoading(true);
    try {
      const revisions = await loadDocumentRevisions(kind);
      if (revisionRequestGenerationRef.current === generation) setDocumentRevisions(revisions);
    } catch (error) {
      if (revisionRequestGenerationRef.current === generation) {
        setDocumentRevisionsError(error instanceof Error ? error.message : `${documentNames[kind]}版本历史加载失败`);
      }
    } finally {
      if (revisionRequestGenerationRef.current === generation) setDocumentRevisionsLoading(false);
    }
  };

  const closeDocumentRevisions = () => {
    revisionRequestGenerationRef.current += 1;
    setRevisionHistoryKind(null);
    setDocumentRevisionsLoading(false);
    setVisibleDocumentRevision(null);
  };

  const toggleFactsEditing = async () => {
    if (!factsEditing) {
      setFactsEditing(true);
      return;
    }
    const editors = [decisionsEditor, actionsEditor, risksEditor];
    if (editors.some((editor) => editor.saveState === "saving")) return;
    const results = await Promise.all(editors.map((editor) =>
      editor.saveState === "unsaved" || editor.saveState === "error" ? editor.saveNow() : Promise.resolve(true),
    ));
    if (results.every(Boolean)) setFactsEditing(false);
  };

  const toggleTranscriptEditing = async () => {
    if (!transcriptEditing) {
      setTranscriptEditing(true);
      return;
    }
    if (transcriptEditor.saveState === "saving") return;
    const saved = transcriptEditor.saveState === "unsaved" || transcriptEditor.saveState === "error"
      ? await transcriptEditor.saveNow()
      : true;
    if (saved) setTranscriptEditing(false);
  };

  const exportMeeting = async (format: MeetingExportFormat) => {
    if (exporting) return;
    setExporting(format);
    setExportMenuOpen(false);
    setExportNotice(null);
    try {
      await onExport(format);
      setExportNotice({
        text: `已导出 ${format === "json" ? "JSON" : format === "docx" ? "Word 文档" : "Markdown"}`,
        error: false,
      });
    } catch (error) {
      setExportNotice({
        text: error instanceof Error ? error.message : "会议导出失败",
        error: true,
      });
    } finally {
      setExporting(null);
    }
  };

  useEffect(() => {
    if (activeTab !== "audio" || pendingAudioOffsetMs === null || !audioRef.current) return;
    audioRef.current.currentTime = pendingAudioOffsetMs / 1_000;
    setPendingAudioOffsetMs(null);
  }, [activeTab, pendingAudioOffsetMs]);

  const revisionHistory = (kind: ReviewDocumentKind) => revisionHistoryKind === kind ? (
    <StructuredRevisionHistory
      kind={kind}
      revisions={documentRevisions}
      loading={documentRevisionsLoading}
      error={documentRevisionsError}
      visibleRevision={visibleDocumentRevision}
      onSelect={setVisibleDocumentRevision}
      onClose={closeDocumentRevisions}
    />
  ) : null;

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
        {reviewJobEntries.map(({ kind, job, state: status, blockedByQuality }) => (
          <div
            key={kind}
            className={`review-progress-item review-progress-item--${status.tone}`}
            title={job && (providerNotConfigured(job) || ["failed", "cancelled"].includes(job.status)) ? reviewJobError(job) : undefined}
          >
            {status.tone === "success" ? <Check size={16} /> : status.tone === "error" ? <AlertCircle size={16} /> : <CircleEllipsis size={16} />}
            <span>
              {jobNames[kind]}：{status.text}
              {job?.attempts && !blockedByQuality ? `（${job.attempts}${job.maxAttempts ? `/${job.maxAttempts}` : ""} 次）` : ""}
            </span>
            {job && ["failed", "cancelled"].includes(job.status) && !blockedByQuality ? (
              <button
                className="review-job-retry"
                type="button"
                onClick={() => void retryReviewJob(kind)}
                disabled={retryingJobs[kind]}
                aria-label={`重试${jobNames[kind]}任务`}
                title={reviewJobError(job)}
              >
                {retryingJobs[kind] ? <LoaderCircle className="spin" size={13} /> : <RotateCcw size={13} />}
              </button>
            ) : null}
          </div>
        ))}
      </section>
      {reviewJobEntries.some(({ job }) => providerNotConfigured(job)) ? (
        <p className="inline-warning" role="status">
          AI 尚未配置，会议文字和录音已保存；配置 AI 后会自动继续生成会后产物。
        </p>
      ) : null}
      {reviewJobEntries.map(({ kind, job, blockedByQuality }) =>
        job && ["failed", "cancelled"].includes(job.status) && !blockedByQuality ? (
          <p key={`${kind}-failure-detail`} className="inline-error">
            {jobNames[kind]}：{reviewJobError(job)} 文字、录音和其他会议结果已保留。
          </p>
        ) : null,
      )}
      {Object.entries(jobErrors).map(([kind, error]) => error ? (
        <div key={kind} className="toast toast--error" role="alert">{error}</div>
      ) : null)}

      <div className="review-tab-row">
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
        <div className="review-export-wrap">
          <button
            className="icon-button"
            type="button"
            aria-label="导出会议"
            title="导出会议"
            aria-haspopup="menu"
            aria-expanded={exportMenuOpen}
            onClick={() => setExportMenuOpen((open) => !open)}
            disabled={Boolean(exporting)}
          >
            {exporting ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />}
          </button>
          {exportMenuOpen ? (
            <div className="review-export-menu" role="menu" aria-label="导出格式">
              <button type="button" role="menuitem" onClick={() => void exportMeeting("markdown")}>
                <FileText size={16} />Markdown
              </button>
              <button type="button" role="menuitem" onClick={() => void exportMeeting("docx")}>
                <FileType2 size={16} />Word 文档
              </button>
              <button type="button" role="menuitem" onClick={() => void exportMeeting("json")}>
                <FileJson size={16} />JSON
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {exportNotice ? (
        <div className={`toast ${exportNotice.error ? "toast--error" : ""}`} role="status">
          {exportNotice.text}
        </div>
      ) : null}

      <section
        className={`review-tab-panel${activeTab === "transcript" ? " review-tab-panel--transcript" : ""}`}
        role="tabpanel"
      >
        {activeTab === "review" ? (
          <div className="review-summary-layout">
            <section className="review-document" aria-labelledby="minutes-heading">
              <MarkdownDocumentEditor
                meetingId={state.meetingId}
                document={state.documents?.minutes}
                fallbackMarkdown={state.minutes?.markdown ?? ""}
                degraded={state.minutes?.status === "degraded"}
                onSave={(revision, content) => saveDocument("minutes", revision, content)}
                onLoadRevisions={() => loadDocumentRevisions("minutes")}
                onRegenerate={() => regenerateDocument("minutes")}
              />
              {reviewBasedOnOldTranscript ? (
                <div className="inline-warning stale-review-warning">
                  <span>完整文字已由用户修改，当前复盘基于旧版本。</span>
                  <button className="secondary-button compact-button" type="button" onClick={() => void regenerateDocument("minutes")}>
                    <RefreshCw size={13} />基于最新文字重新整理
                  </button>
                </div>
              ) : null}
              {!state.minutes && !state.documents?.minutes && qualityPaused ? (
                <p className="inline-warning">识别语义质量不足，正式会议纪要已暂停；会议文字和录音仍已保存。</p>
              ) : !state.minutes && !state.documents?.minutes && transcript.length === 0 ? (
                <p className="inline-warning">本次没有形成可确认的会议文字，录音已保存；补充有效音频后可重新整理。</p>
              ) : !state.minutes && !state.documents?.minutes && providerNotConfigured(state.reviewJobs.minutes) ? (
                <p className="inline-warning">AI 尚未配置，会议文字和录音已保存；配置 AI 后可重新生成会议纪要。</p>
              ) : !state.minutes && !state.documents?.minutes && state.reviewJobs.minutes?.status === "failed" ? (
                <p className="inline-error">{reviewJobError(state.reviewJobs.minutes)} 会议文字和录音仍已保存。</p>
              ) : !state.minutes && !state.documents?.minutes ? (
                <p className="review-empty">会议纪要正在生成，完成后会自动出现在这里。</p>
              ) : null}
            </section>

            <aside className="review-considerations" aria-labelledby="considerations-heading">
              <div className="review-section-heading">
                <div>
                  <span className="section-kicker">AI 补充视角</span>
                  <h2 id="considerations-heading">分析建议</h2>
                </div>
              </div>
              {state.approach.cards.length ? (
                <div className="consideration-list">
                  {state.approach.cards.map((card, index) => (
                    <article className="consideration-item" key={card.cardId ?? `${card.cardType}-${index}`}>
                      <span className="state-label">{approachCardLabel(card.cardType)}</span>
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
                <p className="inline-warning">识别语义质量不足，分析建议生成已暂停。</p>
              ) : transcript.length === 0 ? (
                <p className="inline-warning">本次没有形成可确认的会议文字，暂时没有可生成的分析建议。</p>
              ) : providerNotConfigured(state.reviewJobs.approach) ? (
                <p className="inline-warning">AI 尚未配置，会议文字和录音已保存；配置 AI 后可重新生成分析建议。</p>
              ) : state.reviewJobs.approach?.status === "failed" ? (
                <p className="inline-error">{reviewJobError(state.reviewJobs.approach)}</p>
              ) : (
                <p className="review-empty">分析建议正在整理。</p>
              )}
            </aside>
          </div>
        ) : null}

        {activeTab === "actions" ? (
          <div className="actions-layout">
            <section>
              <div className="facts-heading">
                <div><span className="section-kicker">会议结果</span><h2>决策与待办</h2></div>
                <div className="document-heading-actions">
                  <button className="icon-button icon-button--small" type="button" onClick={() => void showDocumentRevisions("decisions")} aria-label="查看决策版本历史" title="决策版本历史">
                    <History size={15} />
                  </button>
                  <button className="icon-button icon-button--small" type="button" onClick={() => void showDocumentRevisions("action_items")} aria-label="查看行动项版本历史" title="行动项版本历史">
                    <History size={15} />
                  </button>
                  <button
                    className="secondary-button compact-button"
                    type="button"
                    onClick={() => void toggleFactsEditing()}
                    disabled={[decisionsEditor.saveState, actionsEditor.saveState, risksEditor.saveState].includes("saving")}
                  >
                    {factsEditing ? <X size={14} /> : <Pencil size={14} />}
                    {factsEditing ? "完成编辑" : "编辑最终稿"}
                  </button>
                </div>
              </div>
              {factsEditing ? (
                <p className="document-save-summary" role="status">
                  {[decisionsEditor.saveState, actionsEditor.saveState, risksEditor.saveState].includes("error")
                    ? "部分内容保存失败，本地草稿已保留"
                    : [decisionsEditor.saveState, actionsEditor.saveState, risksEditor.saveState].includes("saving")
                      ? "正在自动保存用户最终稿"
                      : [decisionsEditor.saveState, actionsEditor.saveState, risksEditor.saveState].includes("unsaved")
                        ? "等待自动保存"
                        : "用户最终稿已保存"}
                </p>
              ) : null}
              {factsEditing && decisionsEditor.recoveredLocalDraft ? <p className="inline-warning">已恢复上次未保存的决策草稿。</p> : null}
              {factsEditing && actionsEditor.recoveredLocalDraft ? <p className="inline-warning">已恢复上次未保存的行动项草稿。</p> : null}
              {factsEditing && risksEditor.recoveredLocalDraft ? <p className="inline-warning">已恢复上次未保存的风险草稿。</p> : null}
              {decisionsEditor.draft.length || factsEditing ? (
                <>
                  <h3 className="action-group-title">已确认决策</h3>
                  <div className="action-list">
                    {decisionsEditor.draft.map((decision, index) => (
                      <article key={decision.id} className={`action-item${factsEditing ? " action-item--editing" : ""}`}>
                        {factsEditing ? (
                          <>
                            <textarea
                              value={decision.text}
                              onChange={(event) => decisionsEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item))}
                              aria-label={`编辑决策 ${index + 1}`}
                            />
                            <div className="fact-edit-meta">
                              <select
                                value={decision.status}
                                onChange={(event) => decisionsEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status: event.target.value } : item))}
                                aria-label={`决策 ${index + 1} 状态`}
                              >
                                <option value="candidate">待确认</option>
                                <option value="confirmed">已确认</option>
                                <option value="dismissed">已放弃</option>
                              </select>
                              <button className="icon-button icon-button--small" type="button" onClick={() => decisionsEditor.setDraft((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label={`删除决策 ${index + 1}`} title="删除"><Trash2 size={14} /></button>
                            </div>
                          </>
                        ) : (
                          <>
                            <p>{decision.text}</p>
                            {decision.evidenceSegmentId ? <button type="button" onClick={() => showEvidence(decision.evidenceSegmentId!)}>查看依据</button> : null}
                          </>
                        )}
                      </article>
                    ))}
                  </div>
                  {factsEditing ? (
                    <button className="secondary-button compact-button fact-add-button" type="button" onClick={() => decisionsEditor.setDraft((items) => [...items, { id: `decision-user-${Date.now()}`, text: "", status: "confirmed", evidenceSegmentId: null }])}>
                      <Plus size={14} />添加决策
                    </button>
                  ) : null}
                </>
              ) : null}
              {actionsEditor.draft.length || factsEditing ? (
                <>
                  <h3 className="action-group-title">行动项</h3>
                  <div className="action-list">
                    {actionsEditor.draft.map((action, index) => (
                      <article key={action.id} className={`action-item${factsEditing ? " action-item--editing" : ""}`}>
                        {factsEditing ? (
                          <>
                            <textarea value={action.text} onChange={(event) => actionsEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item))} aria-label={`编辑行动项 ${index + 1}`} />
                            <div className="fact-edit-fields">
                              <input value={action.owner ?? ""} onChange={(event) => actionsEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, owner: event.target.value || null } : item))} placeholder="负责人" aria-label={`行动项 ${index + 1} 负责人`} />
                              <input value={action.deadline ?? ""} onChange={(event) => actionsEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, deadline: event.target.value || null } : item))} placeholder="截止时间" aria-label={`行动项 ${index + 1} 截止时间`} />
                              <button className="icon-button icon-button--small" type="button" onClick={() => actionsEditor.setDraft((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label={`删除行动项 ${index + 1}`} title="删除"><Trash2 size={14} /></button>
                            </div>
                          </>
                        ) : (
                          <>
                            <p>{action.text}</p>
                            {action.owner || action.deadline ? <span>{[action.owner ? `负责人：${action.owner}` : null, action.deadline ? `截止：${action.deadline}` : null].filter(Boolean).join(" · ")}</span> : null}
                            {action.evidenceSegmentId ? <button type="button" onClick={() => showEvidence(action.evidenceSegmentId!)}>查看依据</button> : null}
                          </>
                        )}
                      </article>
                    ))}
                  </div>
                  {factsEditing ? (
                    <button className="secondary-button compact-button fact-add-button" type="button" onClick={() => actionsEditor.setDraft((items) => [...items, { id: `action-user-${Date.now()}`, text: "", status: "open", evidenceSegmentId: null, owner: null, deadline: null }])}>
                      <Plus size={14} />添加行动项
                    </button>
                  ) : null}
                </>
              ) : null}
              {keptSuggestions.length ? (
                <>
                  <h3 className="action-group-title">保留的会中建议</h3>
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
                </>
              ) : null}
              {actionUnavailable ? <p className={actionUnavailable.className}>{actionUnavailable.text}</p> : null}
              {!factsEditing && !hasDecisionContent && state.minutes ? (
                <p className="review-empty">本次会议尚未形成明确决策或行动项。</p>
              ) : null}
              {decisionsEditor.error ? <p className="inline-error">决策保存失败：{decisionsEditor.error}<button type="button" onClick={() => void decisionsEditor.saveNow()}>重试</button></p> : null}
              {actionsEditor.error ? <p className="inline-error">行动项保存失败：{actionsEditor.error}<button type="button" onClick={() => void actionsEditor.saveNow()}>重试</button></p> : null}
              {revisionHistory("decisions")}
              {revisionHistory("action_items")}
            </section>
            <section>
              <div className="facts-heading">
                <div><span className="section-kicker">继续确认</span><h2>风险与未闭环</h2></div>
                <button className="icon-button icon-button--small" type="button" onClick={() => void showDocumentRevisions("risks")} aria-label="查看风险版本历史" title="风险版本历史">
                  <History size={15} />
                </button>
              </div>
              {risksEditor.draft.length || factsEditing ? (
                <>
                  <h3 className="action-group-title">风险</h3>
                  <div className="action-list">
                    {risksEditor.draft.map((risk, index) => (
                      <article key={risk.id} className={`action-item${factsEditing ? " action-item--editing" : ""}`}>
                        {factsEditing ? (
                          <>
                            <textarea value={risk.text} onChange={(event) => risksEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item))} aria-label={`编辑风险 ${index + 1}`} />
                            <div className="fact-edit-fields">
                              <input value={risk.mitigation ?? ""} onChange={(event) => risksEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, mitigation: event.target.value || null } : item))} placeholder="应对建议" aria-label={`风险 ${index + 1} 应对建议`} />
                              <button className="icon-button icon-button--small" type="button" onClick={() => risksEditor.setDraft((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label={`删除风险 ${index + 1}`} title="删除"><Trash2 size={14} /></button>
                            </div>
                          </>
                        ) : (
                          <>
                            <p>{risk.text}</p>
                            {risk.mitigation ? <span>应对：{risk.mitigation}</span> : null}
                            {risk.evidenceSegmentId ? <button type="button" onClick={() => showEvidence(risk.evidenceSegmentId!)}>查看依据</button> : null}
                          </>
                        )}
                      </article>
                    ))}
                  </div>
                  {factsEditing ? (
                    <button className="secondary-button compact-button fact-add-button" type="button" onClick={() => risksEditor.setDraft((items) => [...items, { id: `risk-user-${Date.now()}`, text: "", status: "open", evidenceSegmentId: null, mitigation: null }])}>
                      <Plus size={14} />添加风险
                    </button>
                  ) : null}
                </>
              ) : null}
              {reviewQuestions.length ? (
                <>
                  <h3 className="action-group-title">待确认问题</h3>
                <div className="action-list">
                  {reviewQuestions.map((question) => (
                    <article key={normalizedTextKey(question.text)} className="action-item">
                      <p>{question.text}</p>
                      {question.evidenceSegmentId ? (
                        <button type="button" onClick={() => showEvidence(question.evidenceSegmentId!)}>
                          查看上下文
                        </button>
                      ) : null}
                    </article>
                  ))}
                </div>
                </>
              ) : null}
              {riskUnavailable ? <p className={riskUnavailable.className}>{riskUnavailable.text}</p> : null}
              {!factsEditing && !hasRiskContent && state.minutes ? (
                <p className="review-empty">没有未闭环风险或待确认问题。</p>
              ) : null}
              {risksEditor.error ? <p className="inline-error">风险保存失败：{risksEditor.error}<button type="button" onClick={() => void risksEditor.saveNow()}>重试</button></p> : null}
              {revisionHistory("risks")}
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
              <div className="document-heading-actions">
                <span className={`document-source document-source--${transcriptEditor.isUserFinal ? "user_final" : state.documents?.transcript?.source ?? "ai_generated"}`}>
                  {transcriptEditor.isUserFinal ? "用户最终稿" : "AI 修正版"}
                </span>
                <button className="icon-button" type="button" onClick={() => void showDocumentRevisions("transcript")} title="完整文字版本历史" aria-label="查看完整文字版本历史">
                  <History size={17} />
                </button>
                <button className="icon-button" type="button" onClick={onReloadTranscript} title="重新加载文字" aria-label="重新加载文字">
                  <RefreshCw size={17} />
                </button>
                <button className="secondary-button compact-button" type="button" onClick={() => void toggleTranscriptEditing()} disabled={transcriptEditor.saveState === "saving"}>
                  {transcriptEditing ? <X size={14} /> : <Pencil size={14} />}
                  {transcriptEditing ? "完成编辑" : "编辑最终文字"}
                </button>
              </div>
            </div>
            {state.fullTranscriptError ? <p className="inline-error">{state.fullTranscriptError}</p> : null}
            {transcriptEditing ? (
              <div className="transcript-document-editor">
                <p className="document-save-summary" role="status">
                  {transcriptEditor.saveState === "error"
                    ? "保存失败，本地草稿已保留"
                    : transcriptEditor.saveState === "saving"
                      ? "正在自动保存用户最终文字"
                      : transcriptEditor.saveState === "unsaved" ? "等待自动保存" : "用户最终文字已保存"}
                </p>
                {transcriptEditor.recoveredLocalDraft ? <p className="inline-warning">已恢复上次未保存的文字草稿。</p> : null}
                {transcriptEditor.draft.map((segment, index) => (
                  <label key={segment.segmentId} className="transcript-edit-segment">
                    <span>
                      {segment.speakerLabel ? `${segment.speakerLabel} · ` : ""}
                      {index + 1} · {segment.startedAtMs === null ? "时间未知" : `${Math.floor(segment.startedAtMs / 60_000)}:${String(Math.floor(segment.startedAtMs / 1_000) % 60).padStart(2, "0")}`}
                    </span>
                    <textarea
                      value={segment.text}
                      onChange={(event) => transcriptEditor.setDraft((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item))}
                      aria-label={`编辑会议文字第 ${index + 1} 段`}
                    />
                  </label>
                ))}
                {transcriptEditor.error ? (
                  <p className="inline-error">{transcriptEditor.error}<button type="button" onClick={() => void transcriptEditor.saveNow()}>重试保存</button></p>
                ) : null}
              </div>
            ) : (
              <TranscriptPane
                segments={displayTranscript}
                archivedTranscript=""
                archivedSegmentCount={0}
                activePartial={null}
                connection={state.connection}
                mergeSegments={false}
                speakers={state.speakers}
                onRenameSpeaker={onRenameSpeaker}
                onSeekAudio={seekAudio}
              />
            )}
            {revisionHistory("transcript")}
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
            {audioHasTrackData ? (
              <>
                {partialAudioFailure ? (
                  <div className="audio-review-notice audio-review-notice--warning" role="status">
                    <AlertCircle size={17} aria-hidden="true" />
                    <div>
                      <strong>本次录音不完整</strong>
                      <p>可播放已保存的轨道，但这不是完整会议回放。</p>
                    </div>
                  </div>
                ) : null}
                {completeAudioFailure ? (
                  <div className="audio-review-notice audio-review-notice--error" role="alert">
                    <AlertCircle size={17} aria-hidden="true" />
                    <div>
                      <strong>本次会议没有可用录音</strong>
                      <p>会议文字和复盘仍然保留，你可以继续查看其他内容。</p>
                    </div>
                  </div>
                ) : null}
                <div className="audio-track-list" aria-label="会议录音轨道">
                  {audioTrackRows.map(({ trackId, track }) => {
                    const ready = track?.status === "ready" && Boolean(track.playbackUrl);
                    const key = track ? `track:${track.trackId}:${track.epoch}` : null;
                    const selected = key !== null && selectedAudio?.key === key;
                    return (
                      <div key={trackId} className={`audio-track-row ${selected ? "is-selected" : ""}`}>
                        <div className="audio-track-icon" aria-hidden="true">
                          {trackId === "microphone" ? <Mic2 size={18} /> : <FileAudio size={18} />}
                        </div>
                        <div className="audio-track-copy">
                          <div className="audio-track-title">
                            <strong>{audioTrackLabel(trackId)}</strong>
                            <span className={`audio-track-status audio-track-status--${track?.status ?? "missing"}`}>
                              {audioTrackStatusLabel(track?.status ?? "missing")}
                            </span>
                          </div>
                          <div className="audio-track-meta">
                            <span>{formatAudioDuration(track?.durationMs ?? 0)}</span>
                            {track?.errorClass ? <span>{audioErrorMessage(track.errorClass)}</span> : null}
                            {!track ? <span>没有发现这条录音</span> : null}
                          </div>
                        </div>
                        {ready && key ? (
                          <button
                            className="secondary-button compact-button audio-track-play"
                            type="button"
                            title={`播放${audioTrackLabel(trackId)}`}
                            aria-label={`播放${audioTrackLabel(trackId)}`}
                            onClick={() => setSelectedAudioKey(key)}
                          >
                            <Play size={14} />
                            播放
                          </button>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {bothTracksReady && !mixedReady ? (
                  <button
                    className="secondary-button audio-mixed-action"
                    type="button"
                    onClick={() => void createMixedReplay()}
                    disabled={creatingMixed}
                    title="只在两条录音都保存后生成一份新的混合回放"
                  >
                    {creatingMixed ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}
                    {creatingMixed ? "正在生成混合回放" : "生成混合回放"}
                  </button>
                ) : null}
                {mixedError ? <p className="inline-error" role="alert">{mixedError}</p> : null}
                {mixedNotice ? <p className="inline-success" role="status">{mixedNotice}</p> : null}
                {mixedReady && mixedAsset?.playbackUrl ? (
                  <div className={`audio-mixed-row ${selectedAudio?.key === `mixed:${mixedAsset.assetId}` ? "is-selected" : ""}`}>
                    <div>
                      <strong>混合回放</strong>
                      <span>新生成的本地文件，不会覆盖上面两条录音</span>
                    </div>
                    <button
                      className="secondary-button compact-button"
                      type="button"
                      title="播放混合回放"
                      aria-label="播放混合回放"
                      onClick={() => setSelectedAudioKey(`mixed:${mixedAsset.assetId}`)}
                    >
                      <Play size={14} />
                      播放
                    </button>
                  </div>
                ) : null}
                {selectedAudio?.sourceUrl ? (
                  <div className="audio-current-replay">
                    <div className="audio-current-replay-heading">
                      <span>当前回放</span>
                      <strong>{selectedAudio.label}</strong>
                    </div>
                    <MeetingAudioPlayer
                      key={selectedAudio.key}
                      audioRef={audioRef}
                      sourceUrl={selectedAudio.sourceUrl}
                      onLoadedMetadata={() => {
                        if (pendingAudioOffsetMs === null || !audioRef.current) return;
                        audioRef.current.currentTime = pendingAudioOffsetMs / 1_000;
                        setPendingAudioOffsetMs(null);
                      }}
                    />
                  </div>
                ) : state.audioLoadState === "error" ? null : (
                  <p className="review-empty">录音还没有可播放的文件。</p>
                )}
                <dl className="audio-facts">
                  <div><dt>会议时长</dt><dd>{formatAudioDuration(audioDetail?.durationMs ?? 0)}</dd></div>
                  <div><dt>录音分片</dt><dd>{audioDetail?.chunkCount ?? 0} 个</dd></div>
                  <div><dt>已保存轨道</dt><dd>{readyAudioTracks.length} / 2</dd></div>
                </dl>
              </>
            ) : state.audioDetail?.assembled && state.audioDetail.playbackUrl ? (
              <>
                <MeetingAudioPlayer
                  audioRef={audioRef}
                  sourceUrl={state.audioDetail.playbackUrl}
                  onLoadedMetadata={() => {
                    if (pendingAudioOffsetMs === null || !audioRef.current) return;
                    audioRef.current.currentTime = pendingAudioOffsetMs / 1_000;
                    setPendingAudioOffsetMs(null);
                  }}
                />
                <dl className="audio-facts">
                  <div><dt>时长</dt><dd>{formatAudioDuration(state.audioDetail.durationMs)}</dd></div>
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
        {transcript.map(displayText).length ? "会议复盘已加载" : "没有可复盘的会议文字"}
      </span>
    </main>
  );
}
