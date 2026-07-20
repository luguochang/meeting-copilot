import { ArrowLeft, FileAudio, Gauge, LoaderCircle, Mic, Pause, Play, Square } from "lucide-react";
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
import { NowRail } from "./NowRail";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";
import { TranscriptPane } from "./TranscriptPane";
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

function localRecordingIndicator(state: BrowserMicrophoneState): RuntimeIndicator | null {
  if (state.phase === "idle") return null;
  const values: Record<Exclude<BrowserMicrophoneState["phase"], "idle">, RuntimeIndicator> = {
    requesting: { state: "busy", label: "请求权限", level: null, detail: state.statusMessage },
    connecting: { state: "busy", label: "连接中", level: null, detail: state.statusMessage },
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

const capturePhases = new Set(["requesting", "connecting", "starting", "recording", "paused", "stopping"]);
const endableCapturePhases = new Set([...capturePhases, "error"]);

export function LiveMeetingWorkbench({
  meetingId,
  api,
  transport,
  asrBaseUrl = "",
  onCreateMeeting,
  onOpenMeeting,
  onBackToMeetings,
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
  };

  if (!meetingId) {
    return (
      <div className="product-app product-app--home">
        <ProductNavigation active="meetings" onOpenMeetings={onBackToMeetings} />
        <main className="start-home">
          <section className="start-command">
            <BrandMark size="start" />
            <div>
              <span className="brand-name">Meeting Copilot</span>
              <h1>开始一场会议</h1>
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
      <div className="product-app">
        <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} />
        <div className="workbench-shell">
          <header className="app-header">
            <div className="meeting-identity">
              <BrandMark />
              <div>
                <span className="brand-name">Meeting Copilot</span>
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
  const meetingEnded = state.runtime.phase === "ended";
  const localCaptureActive = !meetingEnded && capturePhases.has(microphone.state.phase);
  const recordingIndicator = meetingEnded ? state.runtime.recording : localRecording ?? state.runtime.recording;
  const inputIndicator = meetingEnded ? state.runtime.input : localInput ?? state.runtime.input;
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

  return (
    <div className="product-app">
      <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} />
      <div className={`workbench-shell${nativeSystemAudioHealth ? " workbench-shell--native-health" : ""}`}>
      <header className="app-header">
        <div className="meeting-identity">
          <BrandMark />
          <div>
            <span className="brand-name">Meeting Copilot</span>
              <MeetingTitleEditor
                meetingId={meetingId}
                title={state.title}
                timestamp={state.updatedAtMs}
                onSave={async (title) => {
                  await api.updateMeetingTitle(meetingId, title);
                  await actions.refresh();
                }}
              />
          </div>
        </div>

        <div className="meeting-statuses" aria-label="会议运行状态">
          <StatusIndicator label="录音" indicator={recordingIndicator} />
          <StatusIndicator label="输入" indicator={inputIndicator} showLevel />
          <StatusIndicator label="AI" indicator={state.runtime.ai} />
          <time className="elapsed-time" aria-label={`会议时长 ${formatElapsed(elapsedMs)}`}>
            {formatElapsed(elapsedMs)}
          </time>
        </div>

        <div className="header-actions">
          {meetingEnded && onBackToMeetings ? (
            <button className="secondary-button" type="button" onClick={onBackToMeetings}>
              <ArrowLeft size={16} />
              返回会议列表
            </button>
          ) : null}
          <ProviderSettingsControl />
          {canStartCapture ? (
            <button className="start-recording-button" type="button" onClick={() => setPreflightOpen(true)}>
              <Mic size={16} />
              {microphone.state.phase === "error" ? "重新开始录音" : "开始录音"}
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
            className="icon-button"
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
            >
              {state.ending || microphone.state.phase === "stopping" ? <LoaderCircle className="spin" size={16} /> : <Square size={14} fill="currentColor" />}
              {state.ending || microphone.state.phase === "stopping" ? "正在结束" : "结束并整理"}
            </button>
          ) : null}
        </div>
      </header>

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
            speakers={state.speakers}
            onRenameSpeaker={actions.renameSpeaker}
          />
          <NowRail
            currentTopic={state.currentTopic}
            followUp={state.followUp}
            openQuestions={state.openQuestions}
            suggestions={state.suggestions}
            decisionCandidates={state.decisionCandidates}
            actionItems={state.actionItems}
            risks={state.risks}
            onEvidence={focusEvidence}
            onFeedback={actions.saveSuggestionFeedback}
            onFactStatus={saveFactStatus}
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
