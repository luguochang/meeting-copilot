import {
  AlertCircle,
  ArrowLeft,
  CalendarDays,
  CircleCheck,
  Clock3,
  FileAudio,
  FileText,
  Lightbulb,
  LoaderCircle,
  Mic,
  Pause,
  Play,
  Square,
  Users,
  Video,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import { motionAwareScrollBehavior } from "../../app/motion";
import { useMeetingProjection } from "../../app/useMeetingProjection";
import { BrandMark } from "../../components/BrandMark";
import { DiagnosticsDrawer } from "../../components/DiagnosticsDrawer";
import { MeetingTitleEditor } from "../../components/MeetingTitleEditor";
import { ProductNavigation, type ActiveMeetingNavigation } from "../../components/ProductNavigation";
import type {
  MeetingFactKind,
  MeetingFactStatus,
  MeetingPreparationInput,
  RuntimeIndicator,
} from "../../domain/events";
import { segmentDomId } from "./domIds";
import { AiWorkspace } from "./AiWorkspace";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";
import { captureHealthSummary, resolveMeetingSessionState, type MeetingSessionState } from "./meetingSessionState";
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
  activeCaptureMeetingId?: string | null;
  activeMeeting?: ActiveMeetingNavigation | null;
  onOpenActiveMeeting?: () => void;
  onMeetingStarted?: (meetingId: string) => void;
  onMeetingEnded?: (meetingId: string) => void;
  onMeetingSessionStateChange?: (meetingId: string, state: MeetingSessionState) => void;
}

type MeetingPanel = "transcript" | "coach";

function panelFromHistory(meetingId: string | null): MeetingPanel {
  if (!meetingId) return "transcript";
  const historyState = window.history.state as { meetingPanel?: unknown; meetingPanelMeetingId?: unknown } | null;
  return historyState?.meetingPanelMeetingId === meetingId && historyState.meetingPanel === "coach"
    ? "coach"
    : "transcript";
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

const endableCapturePhases = new Set(["requesting", "connecting", "reconnecting", "starting", "recording", "paused", "stopping", "error"]);

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
  activeCaptureMeetingId = null,
  activeMeeting,
  onOpenActiveMeeting,
  onMeetingStarted,
  onMeetingEnded,
  onMeetingSessionStateChange,
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
  const [preparationRefreshKey, setPreparationRefreshKey] = useState(0);
  const [mobileMeetingView, setMobileMeetingView] = useState<MeetingPanel>(() => panelFromHistory(meetingId));
  const [evidenceReturnAvailable, setEvidenceReturnAvailable] = useState(false);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 3_000);
    return () => window.clearTimeout(timer);
  }, [message]);

  useEffect(() => {
    if (activeCaptureMeetingId && activeCaptureMeetingId !== meetingId) return;
    microphone.acknowledgeCommitted(state.segments.map((segment) => segment.segmentId));
  }, [activeCaptureMeetingId, meetingId, microphone, state.segments]);

  useEffect(() => {
    const restoreMeetingPanel = () => {
      const panel = panelFromHistory(meetingId);
      setEvidenceReturnAvailable(false);
      setMobileMeetingView(panel);
      window.requestAnimationFrame(() => {
        document.getElementById(`meeting-mobile-panel-${panel}`)?.focus({ preventScroll: true });
      });
    };
    setEvidenceReturnAvailable(false);
    setMobileMeetingView(panelFromHistory(meetingId));
    window.addEventListener("popstate", restoreMeetingPanel);
    return () => window.removeEventListener("popstate", restoreMeetingPanel);
  }, [meetingId]);

  const focusEvidence = (segmentId: string) => {
    setEvidenceReturnAvailable(true);
    setMobileMeetingView("transcript");
    window.requestAnimationFrame(() => {
      const element = document.getElementById(segmentDomId(segmentId));
      if (!element) {
        setMessage("对应文字暂未加载");
        return;
      }
      element.scrollIntoView({ behavior: motionAwareScrollBehavior(), block: "center" });
      element.focus({ preventScroll: true });
      element.classList.remove("is-evidence-target");
      window.requestAnimationFrame(() => element.classList.add("is-evidence-target"));
    });
  };

  const focusMeetingPanel = (view: MeetingPanel, pushHistory = false) => {
    if (view === "coach") setEvidenceReturnAvailable(false);
    if (pushHistory && meetingId && panelFromHistory(meetingId) !== view) {
      window.history.pushState({
        ...(window.history.state ?? {}),
        meetingPanel: view,
        meetingPanelMeetingId: meetingId,
      }, "", window.location.href);
    }
    setMobileMeetingView(view);
    window.requestAnimationFrame(() => {
      document.getElementById(`meeting-mobile-panel-${view}`)?.focus({ preventScroll: true });
    });
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

  useEffect(() => {
    if (!meetingId || state.meetingId !== meetingId.trim() || state.lastSyncedAtMs === null) return;
    onMeetingSessionStateChange?.(meetingId, resolveMeetingSessionState({
      meetingPhase: state.runtime.phase,
      capturePhase: microphone.state.phase,
      captureBelongsToMeeting: !activeCaptureMeetingId || activeCaptureMeetingId === meetingId,
      backendCaptureState: state.runtime.capture?.state,
    }));
  }, [
    activeCaptureMeetingId,
    meetingId,
    microphone.state.phase,
    onMeetingSessionStateChange,
    state.lastSyncedAtMs,
    state.meetingId,
    state.runtime.capture?.state,
    state.runtime.phase,
  ]);

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
      setPreparationRefreshKey((current) => current + 1);
      if (createdFromList) onOpenMeeting?.(activeMeetingId);
      await microphone.start(activeMeetingId, {
        inputDeviceId: preparation.inputDeviceId,
        inputSource: preparation.inputSource,
      });
      onMeetingStarted?.(activeMeetingId);
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
      if (meetingId) onMeetingEnded?.(meetingId);
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
    const activeMeetingAction = activeMeeting?.state === "external"
      ? "查看录音中的会议"
      : activeMeeting?.state === "recoverable"
        ? "恢复未结束会议"
        : activeMeeting?.state === "paused"
          ? "返回已暂停会议"
          : "返回正在会议";
    return (
      <div className="product-app product-app--home">
        <ProductNavigation active="meetings" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
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
              {activeMeeting ? (
                <button
                  className="start-meeting-button"
                  type="button"
                  onClick={onOpenActiveMeeting}
                  disabled={!onOpenActiveMeeting}
                >
                  <Video size={17} />
                  {activeMeetingAction}
                </button>
              ) : (
                <button
                  className="start-meeting-button"
                  type="button"
                  onClick={() => setPreflightOpen(true)}
                >
                  {microphone.state.phase === "requesting" ? <LoaderCircle className="spin" size={17} /> : <Mic size={17} />}
                  {microphone.state.phase === "requesting" ? "正在请求权限" : "开始会议"}
                </button>
              )}
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
          <MeetingHistory api={api} onOpenMeeting={onOpenMeeting ?? (() => undefined)} activeMeetingId={activeCaptureMeetingId} />
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
        <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
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
  const captureBelongsToMeeting = !activeCaptureMeetingId || activeCaptureMeetingId === meetingId;
  const otherMeetingCapturing = Boolean(activeCaptureMeetingId && activeCaptureMeetingId !== meetingId);
  const sessionState = resolveMeetingSessionState({
    meetingPhase: state.runtime.phase,
    capturePhase: microphone.state.phase,
    captureBelongsToMeeting,
    backendCaptureState: state.runtime.capture?.state,
  });
  const remoteCaptureActive = sessionState === "capturing_elsewhere";
  const localCaptureActive = ["preparing", "capturing", "paused", "reconnecting", "ending"].includes(sessionState);
  const inputIndicator = meetingEnded
    ? state.runtime.input
    : backfillInputIndicator ?? (captureBelongsToMeeting ? localInput : null) ?? state.runtime.input;
  const elapsedMs = meetingEnded || !captureBelongsToMeeting
    ? state.runtime.elapsedMs
    : microphone.state.elapsedMs ?? state.runtime.elapsedMs;
  const showEndCommand = localCaptureActive && sessionState !== "ending";
  const canStartCapture = ["idle", "recoverable", "error"].includes(sessionState) && !otherMeetingCapturing;
  const candidatePartial = meetingEnded || !captureBelongsToMeeting ? null : microphone.state.activePartial ?? state.activePartial;
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
  const captureHealth = captureHealthSummary(sessionState, inputIndicator, asrIndicator, llmIndicator);

  return (
    <div className={`product-app ${meetingEnded ? "product-app--review" : "product-app--live"}`}>
      {!meetingEnded ? (
        <nav className="meeting-skip-links" aria-label="跳过导航">
          <a
            href="#meeting-mobile-panel-transcript"
            onClick={(event) => {
              event.preventDefault();
              focusMeetingPanel("transcript", true);
            }}
          >
            跳到会议文字
          </a>
          <a
            href="#meeting-mobile-panel-coach"
            onClick={(event) => {
              event.preventDefault();
              focusMeetingPanel("coach", true);
            }}
          >
            跳到实时教练
          </a>
        </nav>
      ) : null}
      <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
      <div className={`workbench-shell${nativeSystemAudioHealth || transcriptBackfill || canStartCapture || otherMeetingCapturing || remoteCaptureActive ? " workbench-shell--status-band" : ""}`}>
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
          <button
            className="capture-health-summary"
            type="button"
            onClick={() => setDiagnosticsOpen(true)}
            aria-label={`采集健康：${captureHealth.label}；${captureHealth.aiLabel}。打开运行诊断`}
            title="查看录音、声音、识别、精修、说话人、模型和任务详情"
          >
            <span className={`status-dot status-dot--${captureHealth.state}`} aria-hidden="true" />
            <span>
              <strong>{captureHealth.label}</strong>
              <small>{captureHealth.aiLabel}</small>
            </span>
          </button>
          <time className="elapsed-time" aria-label={`会议时长 ${formatElapsed(elapsedMs)}`}>
            {formatElapsed(elapsedMs)}
          </time>
        </div>

        <div className="header-actions">
          <ProviderSettingsControl />
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

      {nativeSystemAudioHealth || transcriptBackfill || canStartCapture || otherMeetingCapturing || remoteCaptureActive ? (
        <div className="meeting-status-bands">
        {canStartCapture ? (
          <div className="capture-inactive-status" role="alert" aria-live="polite">
            <AlertCircle size={17} aria-hidden="true" />
            <div>
              <strong>
                {sessionState === "error" ? "录音已中断" : sessionState === "recoverable" ? "会议录音待恢复" : "当前没有录音输入"}
              </strong>
              <span>
                {sessionState === "recoverable"
                  ? "浏览器无法自动恢复上次的声音权限；已保存的文字和会议上下文不会丢失。"
                  : "现在说话不会进入会议文字，Pi 教练也不会收到新内容。"}
              </span>
            </div>
            <button
              className="start-recording-button capture-inactive-status__action"
              type="button"
              onClick={() => setPreflightOpen(true)}
            >
              <Mic size={15} />
              {sessionState === "error" ? "立即恢复录音" : sessionState === "recoverable" ? "恢复录音" : "立即开始录音"}
            </button>
            <button
              className="secondary-button capture-inactive-status__action"
              type="button"
              onClick={() => void endMeeting()}
              disabled={state.ending}
            >
              <Square size={14} fill="currentColor" />
              {state.ending ? "正在结束" : "直接结束并整理"}
            </button>
          </div>
        ) : null}
        {otherMeetingCapturing ? (
          <div className="capture-inactive-status" role="status" aria-live="polite">
            <AlertCircle size={17} aria-hidden="true" />
            <div>
              <strong>另一场会议正在录音</strong>
              <span>当前页面只读展示；返回正在进行的会议后再暂停或结束录音。</span>
            </div>
            <button className="secondary-button capture-inactive-status__action" type="button" onClick={onOpenActiveMeeting}>
              <Play size={15} />
              返回正在会议
            </button>
          </div>
        ) : null}
        {remoteCaptureActive && !otherMeetingCapturing ? (
          <div className="capture-inactive-status" role="status" aria-live="polite">
            <CircleCheck size={17} aria-hidden="true" />
            <div>
              <strong>另一窗口正在录音</strong>
              <span>服务端仍收到新鲜采集心跳；当前页面保持只读，避免重复占用麦克风或覆盖录音。</span>
            </div>
          </div>
        ) : null}
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
          onRetryTranscriptCorrection={() => api.retryTranscriptCorrection(meetingId)}
          onRenameSpeaker={actions.renameSpeaker}
          onRefresh={actions.refresh}
        />
      ) : (
        <main className="meeting-grid">
          <div className="meeting-mobile-view-switch" role="group" aria-label="会中视图">
            <button
              type="button"
              aria-pressed={mobileMeetingView === "transcript"}
              aria-controls="meeting-mobile-panel-transcript"
              onClick={() => focusMeetingPanel("transcript", true)}
            >
              <FileText size={15} />会议文字
            </button>
            <button
              type="button"
              aria-pressed={mobileMeetingView === "coach"}
              aria-controls="meeting-mobile-panel-coach"
              onClick={() => focusMeetingPanel("coach", true)}
            >
              <Lightbulb size={15} />实时教练
            </button>
          </div>
          <section
            id="meeting-mobile-panel-transcript"
            className={`meeting-view-panel meeting-view-panel--transcript${mobileMeetingView === "transcript" ? " is-selected" : ""}`}
            aria-label="会议文字视图"
            tabIndex={-1}
          >
            {evidenceReturnAvailable ? (
              <button
                className="evidence-return-control"
                type="button"
                onClick={() => focusMeetingPanel("coach")}
              >
                <ArrowLeft size={15} />
                返回实时教练
              </button>
            ) : null}
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
                setEvidenceReturnAvailable(false);
                focusMeetingPanel("coach", true);
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
          </section>
          <section
            id="meeting-mobile-panel-coach"
            className={`meeting-view-panel meeting-view-panel--coach${mobileMeetingView === "coach" ? " is-selected" : ""}`}
            aria-label="实时教练视图"
            tabIndex={-1}
          >
            <AiWorkspace
              meetingId={meetingId}
              api={api}
              selection={transcriptSelection}
              askSelectionNonce={askSelectionNonce}
              selectionAction={askSelectionAction}
              preparationRefreshKey={preparationRefreshKey}
              currentTopic={state.currentTopic}
              followUp={state.followUp}
              semanticFollowUp={state.semanticFollowUp}
              coachDecision={state.coachDecision}
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
          </section>
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
