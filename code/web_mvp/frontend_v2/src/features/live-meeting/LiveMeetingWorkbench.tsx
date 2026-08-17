import {
  AlertCircle,
  ArrowLeft,
  CalendarDays,
  CircleCheck,
  Clock3,
  FileAudio,
  FileText,
  Gauge,
  LoaderCircle,
  Mic,
  Pause,
  Play,
  Square,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import { motionAwareScrollBehavior } from "../../app/motion";
import { useMeetingProjection } from "../../app/useMeetingProjection";
import { BrandMark } from "../../components/BrandMark";
import { DiagnosticsDrawer } from "../../components/DiagnosticsDrawer";
import { MeetingTitleEditor } from "../../components/MeetingTitleEditor";
import { ProductNavigation } from "../../components/ProductNavigation";
import { StatusIndicator } from "../../components/StatusIndicator";
import type {
  MeetingFactKind,
  MeetingFactStatus,
  MeetingPreparationInput,
  RuntimeIndicator,
} from "../../domain/events";
import { segmentDomId } from "./domIds";
import { AiWorkspace } from "./AiWorkspace";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";
import { TranscriptPane, type TranscriptSelection, type TranscriptSelectionAction } from "./TranscriptPane";
import { MeetingHistory } from "../history/MeetingHistory";
import { ImportRecordingDialog } from "../history/ImportRecordingDialog";
import { ReviewWorkspace } from "../review/ReviewWorkspace";
import { ProviderSettingsControl } from "../settings/ProviderSettingsControl";
import {
  type BrowserMicrophoneController,
  type BrowserMicrophoneState,
} from "./useBrowserMicrophone";
import { useMeetingMicrophone } from "./useMeetingMicrophone";

interface LiveMeetingWorkbenchProps {
  meetingId: string | null;
  api: MeetingApi;
  transport: MeetingEventTransport;
  asrBaseUrl?: string;
  onCreateMeeting?: () => string;
  onOpenMeeting?: (meetingId: string) => void;
  onBackToMeetings?: () => void;
  onOpenNotes?: () => void;
  onOpenCapabilities?: () => void;
  initialEvidenceSegmentId?: string | null;
  onEvidenceFocused?: () => void;
  microphoneController?: BrowserMicrophoneController;
}

function formatElapsed(milliseconds: number | null): string {
  if (milliseconds === null) return "--:--";
  const seconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function formatMeetingDate(timestamp: number | null | undefined): string {
  if (!timestamp) return "时间待同步";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

interface TranscriptBackfillDiagnostic {
  status: "running" | "completed" | "failed";
  startMs: number;
  endMs: number;
}

function transcriptBackfillDiagnostic(value: unknown): TranscriptBackfillDiagnostic | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const status = source.status;
  if (status !== "running" && status !== "completed" && status !== "failed") return null;
  const startMs = typeof source.start_ms === "number" ? source.start_ms : source.startMs;
  const endMs = typeof source.end_ms === "number" ? source.end_ms : source.endMs;
  if (typeof startMs !== "number" || typeof endMs !== "number") return null;
  return {
    status,
    startMs: Math.max(0, startMs),
    endMs: Math.max(0, endMs),
  };
}

function localRecordingIndicator(state: BrowserMicrophoneState): RuntimeIndicator | null {
  if (state.phase === "idle") return null;
  const values: Record<Exclude<BrowserMicrophoneState["phase"], "idle">, RuntimeIndicator> = {
    requesting: { state: "busy", label: "请求权限", level: null, detail: state.statusMessage },
    connecting: { state: "busy", label: "连接中", level: null, detail: state.statusMessage },
    reconnecting: { state: "busy", label: "正在恢复", level: null, detail: state.statusMessage },
    starting: { state: "busy", label: "准备中", level: null, detail: state.statusMessage },
    recording: { state: "active", label: "录音中", level: null, detail: state.statusMessage },
    paused: { state: "paused", label: "已暂停", level: null, detail: state.statusMessage },
    stopping: { state: "busy", label: "保存中", level: null, detail: state.statusMessage },
    ended: { state: "idle", label: "已保存", level: null, detail: state.statusMessage },
    error: { state: "error", label: "录音异常", level: null, detail: state.error },
  };
  return values[state.phase];
}

function localInputIndicator(
  state: BrowserMicrophoneState,
  inputSource: BrowserMicrophoneController["inputSource"],
): RuntimeIndicator | null {
  if (state.phase === "idle") return null;
  if (state.phase === "paused") {
    return { state: "paused", label: "已暂停", level: 0, detail: null };
  }
  if (state.phase === "error") {
    return { state: "error", label: "不可用", level: 0, detail: state.error };
  }
  const active = state.phase === "recording";
  const nativeHealth = state.systemAudioHealth;
  if (active && (inputSource === "system_audio" || inputSource === "dual_track") && nativeHealth) {
    if (!nativeHealth.transportReady) {
      return { state: "error", label: "传输未就绪", level: 0, detail: "系统音频传输未就绪" };
    }
    if (!nativeHealth.pcmSeen) {
      return { state: "error", label: "无 PCM", level: 0, detail: "系统音频未收到 PCM 数据" };
    }
    if (!nativeHealth.audiblePcmSeen) {
      return { state: "paused", label: "当前无声音", level: 0, detail: "已连接但当前无系统声音" };
    }
  }
  if (active && state.inputLevelAvailable === false) {
    return {
      state: "active",
      label: "已连接",
      level: null,
      detail: inputSource === "dual_track"
        ? "麦克风 + 系统音频"
        : inputSource === "system_audio" ? "系统音频" : "系统麦克风",
    };
  }
  return {
    state: active ? "active" : "busy",
    label: active ? (state.inputLevel >= 0.035 ? "有声音" : "声音较弱") : "检测中",
    level: state.inputLevel,
    detail: null,
  };
}

const capturePhases = new Set(["requesting", "connecting", "reconnecting", "starting", "recording", "paused", "stopping"]);
const endableCapturePhases = new Set([...capturePhases, "error"]);

export function LiveMeetingWorkbench({
  meetingId,
  api,
  transport,
  asrBaseUrl = "",
  onCreateMeeting,
  onOpenMeeting,
  onBackToMeetings,
  onOpenNotes,
  onOpenCapabilities,
  initialEvidenceSegmentId,
  onEvidenceFocused,
  microphoneController,
}: LiveMeetingWorkbenchProps) {
  const { state, actions, transportKind } = useMeetingProjection(meetingId, api, transport);
  const liveMicrophone = useMeetingMicrophone({ asrBaseUrl });
  const microphone = microphoneController ?? liveMicrophone;
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [transcriptSelection, setTranscriptSelection] = useState<TranscriptSelection | null>(null);
  const [askSelectionNonce, setAskSelectionNonce] = useState(0);
  const [askSelectionAction, setAskSelectionAction] = useState<TranscriptSelectionAction>("ask");

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 3_000);
    return () => window.clearTimeout(timer);
  }, [message]);

  useEffect(() => {
    microphone.acknowledgeCommitted(state.segments.map((segment) => segment.segmentId));
  }, [microphone, state.segments]);

  const focusEvidence = (segmentId: string) => {
    const element = document.getElementById(segmentDomId(segmentId));
    if (!element) {
      setMessage("对应文字暂未加载");
      return;
    }
    element.scrollIntoView({ behavior: motionAwareScrollBehavior(), block: "center" });
    element.focus({ preventScroll: true });
    element.classList.remove("is-evidence-target");
    window.requestAnimationFrame(() => element.classList.add("is-evidence-target"));
  };

  useEffect(() => {
    if (!initialEvidenceSegmentId) return;
    const frame = window.requestAnimationFrame(() => {
      const element = document.getElementById(segmentDomId(initialEvidenceSegmentId));
      if (!element) return;
      focusEvidence(initialEvidenceSegmentId);
      onEvidenceFocused?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [initialEvidenceSegmentId, onEvidenceFocused, state.segments.length, state.semanticParagraphs?.length]);

  const startMeeting = async (preparation: MeetingPreparationInput) => {
    const activeMeetingId = meetingId ?? onCreateMeeting?.();
    const createdFromList = meetingId === null && Boolean(activeMeetingId);
    if (!activeMeetingId) {
      setMessage("无法创建会议");
      return;
    }
    let meetingCreated = false;
    setStarting(true);
    try {
      await api.createMeeting(activeMeetingId, preparation.title ?? null, preparation.inputSource);
      meetingCreated = true;
      await api.saveMeetingPreparation(activeMeetingId, preparation);
      if (createdFromList) onOpenMeeting?.(activeMeetingId);
      await microphone.start(activeMeetingId, {
        inputDeviceId: preparation.inputDeviceId,
        inputSource: preparation.inputSource,
      });
      setPreflightOpen(false);
      setMessage("会议已开始");
    } catch (error) {
      const captureError = error instanceof Error ? error.message : "声音采集启动失败";
      if (createdFromList && meetingCreated) {
        try {
          await api.deleteMeeting(activeMeetingId);
        } catch (rollbackError) {
          const rollbackMessage = rollbackError instanceof Error ? rollbackError.message : "会议回滚失败";
          const combinedMessage = `${captureError}；新会议清理失败：${rollbackMessage}`;
          onBackToMeetings?.();
          throw new Error(combinedMessage);
        }
      }
      if (createdFromList) onBackToMeetings?.();
      throw new Error(captureError);
    } finally {
      setStarting(false);
    }
  };

  const endMeeting = async () => {
    try {
      if (endableCapturePhases.has(microphone.state.phase)) await microphone.end();
      await actions.endMeeting();
      setMessage("会议已结束，正在整理复盘");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "结束会议失败");
    }
  };

  const saveFactStatus = async (
    factType: MeetingFactKind,
    factId: string,
    status: Extract<MeetingFactStatus, "confirmed" | "dismissed">,
  ) => {
    if (!meetingId) return;
    await api.saveFactStatus(meetingId, factType, factId, status);
    await actions.refresh();
  };

  const updateFact = async (
    _factType: MeetingFactKind,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
  ) => {
    if (!meetingId || !api.updateFact) return;
    await api.updateFact(meetingId, factId, changes, expectedVersion);
    await actions.refresh();
  };

  const mergeFacts = async (
    _factType: MeetingFactKind,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
  ) => {
    if (!meetingId || !api.mergeFacts) return;
    await api.mergeFacts(meetingId, targetFactId, sourceFactId, expectedTargetVersion, expectedSourceVersion);
    await actions.refresh();
  };

  if (!meetingId) {
    return (
      <div className="product-app product-app--home">
        <ProductNavigation active="meetings" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} />
        <main className="start-home">
          <section className="start-command">
            <div className="start-command-copy">
              <span className="section-kicker">会议工作台</span>
              <h1>会议记录</h1>
              <p>管理本机会议、录音、文字与 AI 整理结果。</p>
            </div>
            <div className="start-command-actions">
              <ProviderSettingsControl />
              <button
                className="secondary-button"
                type="button"
                onClick={() => setImportDialogOpen(true)}
                disabled={microphone.state.phase === "requesting"}
              >
                <FileAudio size={17} />
                导入录音
              </button>
              <button
                className="start-meeting-button"
                type="button"
                onClick={() => setPreflightOpen(true)}
              >
                {microphone.state.phase === "requesting" ? <LoaderCircle className="spin" size={17} /> : <Mic size={17} />}
                {microphone.state.phase === "requesting" ? "正在请求权限" : "开始会议"}
              </button>
            </div>
            {microphone.state.error ? <p className="unbound-error">{microphone.state.error}</p> : null}
            {message ? (
              <p
                className="start-command-message"
                role="alert"
                aria-live="polite"
              >
                {message}
              </p>
            ) : null}
          </section>
          <MeetingHistory api={api} onOpenMeeting={onOpenMeeting ?? (() => undefined)} />
        </main>
        <ImportRecordingDialog
          open={importDialogOpen}
          onClose={() => setImportDialogOpen(false)}
          onImport={async (file, title) => {
            return api.importRecording(file, title);
          }}
          onReadImportJob={async (importMeetingId) => (await api.getSnapshot(importMeetingId)).importJob ?? null}
          onRetryImport={(importMeetingId) => api.retryImportJob(importMeetingId)}
          onOpenMeeting={(importedMeetingId) => {
            setImportDialogOpen(false);
            onOpenMeeting?.(importedMeetingId);
          }}
        />
        <MeetingPreflightDialog
          open={preflightOpen}
          busy={starting}
          onCancel={() => setPreflightOpen(false)}
          onStart={startMeeting}
        />
      </div>
    );
  }

  const normalizedMeetingId = meetingId.trim();
  const snapshotLoading = state.meetingId !== normalizedMeetingId || state.lastSyncedAtMs === null;
  if (snapshotLoading) {
    return (
      <div className="product-app product-app--live">
        <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} />
        <div className="workbench-shell">
          <header className="app-header">
            <div className="meeting-identity">
              {onBackToMeetings ? (
                <button
                  className="icon-button meeting-back-button"
                  type="button"
                  onClick={onBackToMeetings}
                  title="返回会议列表"
                  aria-label="返回会议列表"
                >
                  <ArrowLeft size={18} />
                </button>
              ) : null}
              <BrandMark />
              <div>
                <span className="brand-name">言迹 Talktrace</span>
                <h1>会议状态加载中</h1>
              </div>
            </div>
            <div className="header-actions">
              <ProviderSettingsControl />
            </div>
          </header>
          <main className="meeting-loading-state" role="status" aria-live="polite">
            <LoaderCircle className="spin" size={22} />
            <span>正在加载会议状态</span>
          </main>
        </div>
        <MeetingPreflightDialog
          open={preflightOpen}
          busy={starting}
          onCancel={() => setPreflightOpen(false)}
          onStart={startMeeting}
        />
      </div>
    );
  }

  const localRecording = localRecordingIndicator(microphone.state);
  const localInput = localInputIndicator(microphone.state, microphone.inputSource);
  const transcriptBackfill = transcriptBackfillDiagnostic(
    state.diagnostics.transcript_backfill ?? state.diagnostics.transcriptBackfill,
  );
  const transcriptBackfillRange = transcriptBackfill
    ? `${formatElapsed(transcriptBackfill.startMs)}–${formatElapsed(transcriptBackfill.endMs)}`
    : null;
  const backfillInputIndicator: RuntimeIndicator | null = transcriptBackfill?.status === "running"
    ? {
        state: "busy",
        label: "正在补齐",
        level: null,
        detail: `正在补齐 ${transcriptBackfillRange} 的会议文字`,
      }
    : null;
  const meetingEnded = state.runtime.phase === "ended";
  const localCaptureActive = !meetingEnded && capturePhases.has(microphone.state.phase);
  const recordingIndicator = meetingEnded ? state.runtime.recording : localRecording ?? state.runtime.recording;
  const inputIndicator = meetingEnded
    ? state.runtime.input
    : backfillInputIndicator ?? localInput ?? state.runtime.input;
  const elapsedMs = meetingEnded ? state.runtime.elapsedMs : microphone.state.elapsedMs ?? state.runtime.elapsedMs;
  const showEndCommand = !meetingEnded;
  const canStartCapture = !localCaptureActive && !meetingEnded;
  const candidatePartial = meetingEnded ? null : microphone.state.activePartial ?? state.activePartial;
  const committedSegmentIds = new Set([
    ...state.segments.map((segment) => segment.segmentId),
    ...state.fullTranscript.map((segment) => segment.segmentId),
  ]);
  const partial = candidatePartial && !committedSegmentIds.has(candidatePartial.segmentId)
    ? candidatePartial
    : null;
  const nativeSystemAudioHealth = !meetingEnded
    && (microphone.inputSource === "system_audio" || microphone.inputSource === "dual_track")
    ? microphone.state.systemAudioHealth ?? null
    : null;
  const aiCapabilities = state.runtime.ai.capabilities ?? {};
  const asrIndicator: RuntimeIndicator = transcriptBackfill?.status === "running"
    ? { state: "busy", label: "补齐中", level: null, detail: "正在补齐中断处会议文字" }
    : meetingEnded
      ? { state: "idle", label: "已完成", level: null, detail: null }
      : state.connection === "offline"
        ? { state: "error", label: "已断开", level: null, detail: "实时识别连接已中断，录音仍会保存" }
        : state.connection === "connecting" || state.connection === "reconnecting"
          ? { state: "busy", label: "连接中", level: null, detail: null }
          : { state: "active", label: "实时识别", level: null, detail: null };
  const refinementPending = state.segments.some((segment) =>
    ["pending", "processing"].includes(segment.correctionStatus ?? ""));
  const refinementIndicator: RuntimeIndicator = aiCapabilities.transcript ?? {
    state: refinementPending ? "busy" : "idle",
    label: refinementPending ? "处理中" : "已稳定",
    level: null,
    detail: null,
  };
  const userNamedSpeakerCount = state.speakers.filter((speaker) =>
    speaker.labelSource === "user" || speaker.labelLocked).length;
  const automaticSpeakerCount = state.speakers.length - userNamedSpeakerCount;
  const speakerIndicator: RuntimeIndicator = userNamedSpeakerCount
    ? { state: "active", label: `${userNamedSpeakerCount} 个已命名`, level: null, detail: "只默认显示用户确认的说话人名称" }
    : automaticSpeakerCount
      ? { state: "paused", label: "实验关闭", level: null, detail: "自动说话人未通过真实嘈杂会议门禁，默认不展示" }
      : { state: "idle", label: "未启用", level: null, detail: "正文按自然段显示，不强行归属说话人" };
  const llmIndicator: RuntimeIndicator = aiCapabilities.provider ?? {
    state: state.runtime.ai.state,
    label: state.runtime.ai.label,
    level: null,
    detail: state.runtime.ai.detail,
  };
  const taskCapabilities = {
    realtime_suggestions: aiCapabilities.proactive_suggestions ?? aiCapabilities.intelligence ?? state.runtime.ai,
    minutes: state.reviewJobs.minutes
      ? {
          state: state.reviewJobs.minutes.status === "failed" ? "error" as const : ["pending", "running", "retry_wait"].includes(state.reviewJobs.minutes.status) ? "busy" as const : "idle" as const,
          label: state.reviewJobs.minutes.status === "failed" ? "纪要失败" : ["pending", "running", "retry_wait"].includes(state.reviewJobs.minutes.status) ? "纪要处理中" : "纪要已完成",
          level: null,
          detail: state.reviewJobs.minutes.errorMessage ?? null,
        }
      : { state: "idle" as const, label: meetingEnded ? "纪要待开始" : "会中待命", level: null, detail: null },
    index: state.reviewJobs.index
      ? {
          state: state.reviewJobs.index.status === "failed" ? "error" as const : ["pending", "running", "retry_wait"].includes(state.reviewJobs.index.status) ? "busy" as const : "idle" as const,
          label: state.reviewJobs.index.status === "failed" ? "索引失败" : ["pending", "running", "retry_wait"].includes(state.reviewJobs.index.status) ? "索引处理中" : "索引已完成",
          level: null,
          detail: state.reviewJobs.index.errorMessage ?? null,
        }
      : { state: "idle" as const, label: "索引待开始", level: null, detail: null },
  };
  const taskStates = Object.values(taskCapabilities).map((indicator) => indicator.state);
  const taskIndicator: RuntimeIndicator = {
    state: taskStates.includes("error") ? "error" : taskStates.includes("busy") ? "busy" : "idle",
    label: taskStates.includes("error") ? "部分失败" : taskStates.includes("busy") ? "处理中" : "已就绪",
    level: null,
    detail: null,
    capabilities: taskCapabilities,
  };

  return (
    <div className={`product-app ${meetingEnded ? "product-app--review" : "product-app--live"}`}>
      <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} />
      <div className={`workbench-shell${nativeSystemAudioHealth || transcriptBackfill ? " workbench-shell--status-band" : ""}`}>
      <header className="app-header">
        <div className="meeting-identity">
          {onBackToMeetings ? (
            <button
              className="icon-button meeting-back-button"
              type="button"
              onClick={onBackToMeetings}
              title="返回会议列表"
              aria-label="返回会议列表"
            >
              <ArrowLeft size={18} />
            </button>
          ) : null}
          <BrandMark />
          <div>
            <span className="brand-name">言迹 Talktrace</span>
              <MeetingTitleEditor
                meetingId={meetingId}
                title={state.title}
                timestamp={state.updatedAtMs}
                onSave={async (title) => {
                  await api.updateMeetingTitle(meetingId, title);
                  await actions.refresh();
                }}
              />
              <div className="meeting-header-meta" aria-label="会议基本信息">
                <span><CalendarDays size={12} />{formatMeetingDate(state.updatedAtMs)}</span>
                <span><Clock3 size={12} />{formatElapsed(elapsedMs)}</span>
                <span><FileText size={12} />{state.archivedSegmentCount + state.segments.length} 段文字</span>
                <span><Users size={12} />{state.speakers.length || 1} 个说话人</span>
              </div>
          </div>
        </div>

        <div className="meeting-statuses" aria-label="会议运行状态">
          <StatusIndicator label="录音" indicator={recordingIndicator} />
          <StatusIndicator label="声音" indicator={inputIndicator} showLevel />
          <StatusIndicator label="ASR" indicator={asrIndicator} />
          <StatusIndicator label="精修" indicator={refinementIndicator} />
          <StatusIndicator label="说话人" indicator={speakerIndicator} />
          <StatusIndicator label="LLM" indicator={llmIndicator} />
          <StatusIndicator label="任务" indicator={taskIndicator} />
          <time className="elapsed-time" aria-label={`会议时长 ${formatElapsed(elapsedMs)}`}>
            {formatElapsed(elapsedMs)}
          </time>
        </div>

        <div className="header-actions">
          <ProviderSettingsControl />
          {canStartCapture ? (
            <button
              className="start-recording-button"
              type="button"
              aria-label={microphone.state.phase === "error" ? "继续录音" : "开始录音"}
              title={microphone.state.phase === "error" ? "继续录音" : "开始录音"}
              onClick={() => setPreflightOpen(true)}
            >
              <Mic size={16} />
              <span className="meeting-command-label">{microphone.state.phase === "error" ? "继续录音" : "开始录音"}</span>
            </button>
          ) : null}
          {localCaptureActive
          && microphone.state.phase !== "stopping"
          && microphone.supportsPause !== false ? (
            <button
              className="icon-button"
              type="button"
              onClick={microphone.togglePause}
              title={microphone.state.phase === "paused" ? "继续录音" : "暂停录音"}
              aria-label={microphone.state.phase === "paused" ? "继续录音" : "暂停录音"}
            >
              {microphone.state.phase === "paused" ? <Play size={18} fill="currentColor" /> : <Pause size={18} fill="currentColor" />}
            </button>
          ) : null}
          <button
            className="icon-button runtime-diagnostics-button"
            type="button"
            onClick={() => setDiagnosticsOpen(true)}
            title="运行诊断"
            aria-label="打开运行诊断"
          >
            <Gauge size={18} />
          </button>
          {showEndCommand ? (
            <button
              className="end-meeting-button"
              type="button"
              onClick={() => void endMeeting()}
              disabled={state.ending || microphone.state.phase === "stopping"}
              title="结束并整理"
              aria-label="结束并整理"
            >
              {state.ending || microphone.state.phase === "stopping" ? <LoaderCircle className="spin" size={16} /> : <Square size={14} fill="currentColor" />}
              <span className="meeting-command-label">{state.ending || microphone.state.phase === "stopping" ? "正在结束" : "结束并整理"}</span>
            </button>
          ) : null}
        </div>
      </header>

      {nativeSystemAudioHealth || transcriptBackfill ? (
        <div className="meeting-status-bands">
        {transcriptBackfill ? (
          <div
            className={`transcript-backfill-status transcript-backfill-status--${transcriptBackfill.status}`}
            role={transcriptBackfill.status === "failed" ? "alert" : "status"}
            aria-live="polite"
          >
            {transcriptBackfill.status === "running" ? (
              <LoaderCircle className="spin" size={15} aria-hidden="true" />
            ) : transcriptBackfill.status === "completed" ? (
              <CircleCheck size={15} aria-hidden="true" />
            ) : (
              <AlertCircle size={15} aria-hidden="true" />
            )}
            <strong>
              {transcriptBackfill.status === "running"
                ? "正在补齐中断处文字"
                : transcriptBackfill.status === "completed"
                  ? "中断处文字已补齐"
                  : "中断处文字暂未补齐"}
            </strong>
            <span>
              {transcriptBackfillRange}
              {transcriptBackfill.status === "running"
                ? " · 录音仍在继续保存"
                : transcriptBackfill.status === "completed"
                  ? " · 已合并到同一会议正文"
                  : " · 录音已保存，这一小段文字可能缺失"}
            </span>
          </div>
        ) : null}
        {nativeSystemAudioHealth ? (
          <div className="native-capture-health" role="status" aria-live="polite" aria-label="系统音频分层健康状态">
          <span data-ready={nativeSystemAudioHealth.transportReady}>
            <small>传输</small>
            <strong>{nativeSystemAudioHealth.transportReady ? "已连接" : "未就绪"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.pcmSeen}>
            <small>PCM</small>
            <strong>{nativeSystemAudioHealth.pcmSeen ? "已接收" : "未收到"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.audiblePcmSeen}>
            <small>声音</small>
            <strong>{nativeSystemAudioHealth.audiblePcmSeen ? "已检测" : "当前静音"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.asrReady}>
            <small>识别</small>
            <strong>{nativeSystemAudioHealth.asrReady ? "已就绪" : "准备中"}</strong>
          </span>
          {!nativeSystemAudioHealth.audiblePcmSeen
            && nativeSystemAudioHealth.transportReady
            && nativeSystemAudioHealth.pcmSeen ? (
              <strong className="native-capture-health__message">已连接但当前无系统声音</strong>
            ) : null}
          </div>
        ) : null}
        </div>
      ) : null}

      {meetingEnded ? (
        <ReviewWorkspace
          state={state}
          onReloadTranscript={actions.loadFullTranscript}
          onReloadAudio={actions.loadAudio}
          onExport={(format) => api.exportMeeting(meetingId, format)}
          onSaveDocument={(kind, expectedRevision, content) =>
            api.saveReviewDocument(meetingId, kind, expectedRevision, content)}
          onLoadDocumentRevisions={(kind) => api.getDocumentRevisions(meetingId, kind)}
          onRegenerateDocument={(kind) => api.regenerateDocument(meetingId, kind)}
          onRetryReviewJob={(kind) => api.retryReviewJob(meetingId, kind)}
          onRenameSpeaker={actions.renameSpeaker}
          onRefresh={actions.refresh}
        />
      ) : (
        <main className="meeting-grid">
          <TranscriptPane
            segments={state.segments}
            semanticParagraphs={state.semanticParagraphs}
            archivedTranscript={state.archivedTranscript}
            archivedSegmentCount={state.archivedSegmentCount}
            activePartial={partial}
            connection={state.connection}
            aiIndicator={state.runtime.ai}
            speakers={state.speakers}
            onRenameSpeaker={actions.renameSpeaker}
            onSelectionChange={setTranscriptSelection}
            onAskSelection={(selection, action) => {
              setTranscriptSelection(selection);
              setAskSelectionAction(action);
              setAskSelectionNonce((current) => current + 1);
            }}
            onSaveSelection={async (selection) => {
              if (!api.createNote) return;
              const selectedSegments = state.segments.filter((segment) => selection.segmentIds.includes(segment.segmentId));
              await api.createNote(meetingId, {
                body: selection.text,
                sourceKind: "selection",
                evidence: selectedSegments.map((segment) => ({
                  segmentId: segment.segmentId,
                  transcriptSeq: segment.transcriptSeq,
                  startMs: segment.startedAtMs,
                  endMs: segment.endedAtMs,
                  quote: segment.normalizedText.trim() || segment.text.trim(),
                })),
              });
              setMessage("已保存到笔记");
            }}
          />
          <AiWorkspace
            meetingId={meetingId}
            api={api}
            selection={transcriptSelection}
            askSelectionNonce={askSelectionNonce}
            selectionAction={askSelectionAction}
            currentTopic={state.currentTopic}
            followUp={state.followUp}
            coachHistory={state.coachHistory}
            recentContextHistory={state.recentContextHistory}
            coachRuntime={taskCapabilities.realtime_suggestions}
            openQuestions={state.openQuestions}
            suggestions={state.suggestions}
            decisionCandidates={state.decisionCandidates}
            actionItems={state.actionItems}
            risks={state.risks}
            onEvidence={focusEvidence}
            onFeedback={actions.saveSuggestionFeedback}
            onFactStatus={saveFactStatus}
            onFactEdit={api.updateFact ? updateFact : undefined}
            onFactMerge={api.mergeFacts ? mergeFacts : undefined}
            onMessage={setMessage}
          />
        </main>
      )}

      <div className="sr-live" role="status" aria-live="polite">{message}</div>
      {state.endError || message ? (
        <div className={`toast ${state.endError ? "toast--error" : ""}`} aria-hidden="true">
          {state.endError ?? message}
        </div>
      ) : null}

      <DiagnosticsDrawer
        open={diagnosticsOpen}
        onClose={() => setDiagnosticsOpen(false)}
        onRefresh={() => void actions.refresh()}
        onExport={() => api.exportDiagnosticBundle()}
        state={state}
        transportKind={transportKind}
      />
      <MeetingPreflightDialog
        open={preflightOpen}
        busy={starting}
        onCancel={() => setPreflightOpen(false)}
        onStart={startMeeting}
      />
      </div>
    </div>
  );
}
