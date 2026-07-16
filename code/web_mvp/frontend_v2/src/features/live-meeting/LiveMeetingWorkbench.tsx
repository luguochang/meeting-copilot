import { Gauge, LoaderCircle, Mic, Pause, Play, Radio, Square } from "lucide-react";
import { useEffect, useState } from "react";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import { useMeetingProjection } from "../../app/useMeetingProjection";
import { DiagnosticsDrawer } from "../../components/DiagnosticsDrawer";
import { StatusIndicator } from "../../components/StatusIndicator";
import type { RuntimeIndicator } from "../../domain/events";
import { segmentDomId } from "./domIds";
import { NowRail } from "./NowRail";
import { TranscriptPane } from "./TranscriptPane";
import { MeetingHistory } from "../history/MeetingHistory";
import { ReviewWorkspace } from "../review/ReviewWorkspace";
import {
  type BrowserMicrophoneController,
  type BrowserMicrophoneState,
  useBrowserMicrophone,
} from "./useBrowserMicrophone";

interface LiveMeetingWorkbenchProps {
  meetingId: string | null;
  api: MeetingApi;
  transport: MeetingEventTransport;
  asrBaseUrl?: string;
  onCreateMeeting?: () => string;
  onOpenMeeting?: (meetingId: string) => void;
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

function localInputIndicator(state: BrowserMicrophoneState): RuntimeIndicator | null {
  if (state.phase === "idle") return null;
  if (state.phase === "paused") {
    return { state: "paused", label: "已暂停", level: 0, detail: null };
  }
  if (state.phase === "error") {
    return { state: "error", label: "不可用", level: 0, detail: state.error };
  }
  const active = state.phase === "recording";
  return {
    state: active ? "active" : "busy",
    label: active ? (state.inputLevel >= 0.035 ? "有声音" : "声音较弱") : "检测中",
    level: state.inputLevel,
    detail: null,
  };
}

const capturePhases = new Set(["requesting", "connecting", "starting", "recording", "paused", "stopping"]);

export function LiveMeetingWorkbench({
  meetingId,
  api,
  transport,
  asrBaseUrl = "",
  onCreateMeeting,
  onOpenMeeting,
  microphoneController,
}: LiveMeetingWorkbenchProps) {
  const { state, actions, transportKind } = useMeetingProjection(meetingId, api, transport);
  const liveMicrophone = useBrowserMicrophone({ asrBaseUrl });
  const microphone = microphoneController ?? liveMicrophone;
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [message, setMessage] = useState("");

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
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    element.focus({ preventScroll: true });
    element.classList.remove("is-evidence-target");
    window.requestAnimationFrame(() => element.classList.add("is-evidence-target"));
  };

  const startMeeting = async () => {
    const activeMeetingId = meetingId ?? onCreateMeeting?.();
    if (!activeMeetingId) {
      setMessage("无法创建会议");
      return;
    }
    try {
      await api.createMeeting(activeMeetingId);
      await microphone.start(activeMeetingId);
      setMessage("会议已开始");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "麦克风启动失败");
    }
  };

  const endMeeting = async () => {
    if (!window.confirm("结束会议并开始整理复盘？")) return;
    try {
      if (capturePhases.has(microphone.state.phase)) await microphone.end();
      await actions.endMeeting();
      setMessage("会议已结束，正在整理复盘");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "结束会议失败");
    }
  };

  if (!meetingId) {
    return (
      <main className="start-home">
        <section className="start-command">
          <span className="brand-mark" aria-hidden="true"><Radio size={21} /></span>
          <div>
            <span className="brand-name">Meeting Copilot</span>
            <h1>开始一场会议</h1>
          </div>
          <button className="start-meeting-button" type="button" onClick={() => void startMeeting()}>
            {microphone.state.phase === "requesting" ? <LoaderCircle className="spin" size={17} /> : <Mic size={17} />}
            {microphone.state.phase === "requesting" ? "正在请求权限" : "开始会议"}
          </button>
          {microphone.state.error ? <p className="unbound-error">{microphone.state.error}</p> : null}
        </section>
        <MeetingHistory api={api} onOpenMeeting={onOpenMeeting ?? (() => undefined)} />
      </main>
    );
  }

  const localRecording = localRecordingIndicator(microphone.state);
  const localInput = localInputIndicator(microphone.state);
  const meetingEnded = state.runtime.phase === "ended" || microphone.state.phase === "ended";
  const localCaptureActive = !meetingEnded && capturePhases.has(microphone.state.phase);
  const recordingIndicator = meetingEnded ? state.runtime.recording : localRecording ?? state.runtime.recording;
  const inputIndicator = meetingEnded ? state.runtime.input : localInput ?? state.runtime.input;
  const elapsedMs = meetingEnded ? state.runtime.elapsedMs : microphone.state.elapsedMs ?? state.runtime.elapsedMs;
  const showEndCommand = localCaptureActive || state.runtime.phase === "live" || state.runtime.phase === "ending";
  const canStartCapture = !localCaptureActive && !meetingEnded;
  const candidatePartial = meetingEnded ? null : microphone.state.activePartial ?? state.activePartial;
  const committedSegmentIds = new Set([
    ...state.segments.map((segment) => segment.segmentId),
    ...state.fullTranscript.map((segment) => segment.segmentId),
  ]);
  const partial = candidatePartial && !committedSegmentIds.has(candidatePartial.segmentId)
    ? candidatePartial
    : null;

  return (
    <div className="workbench-shell">
      <header className="app-header">
        <div className="meeting-identity">
          <span className="brand-mark" aria-hidden="true"><Radio size={18} /></span>
          <div>
            <span className="brand-name">Meeting Copilot</span>
            <h1>{meetingEnded ? "会议复盘" : state.title ?? "实时会议"}</h1>
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
          {canStartCapture ? (
            <button className="start-recording-button" type="button" onClick={() => void startMeeting()}>
              <Mic size={16} />
              开始录音
            </button>
          ) : null}
          {localCaptureActive && microphone.state.phase !== "stopping" ? (
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

      {meetingEnded ? (
        <ReviewWorkspace
          state={state}
          onReloadTranscript={actions.loadFullTranscript}
          onReloadAudio={actions.loadAudio}
        />
      ) : (
        <main className="meeting-grid">
          <TranscriptPane
            segments={state.segments}
            archivedTranscript={state.archivedTranscript}
            archivedSegmentCount={state.archivedSegmentCount}
            activePartial={partial}
            connection={state.connection}
          />
          <NowRail
            currentTopic={state.currentTopic}
            openQuestions={state.openQuestions}
            suggestions={state.suggestions}
            onEvidence={focusEvidence}
            onFeedback={actions.saveSuggestionFeedback}
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
        state={state}
        transportKind={transportKind}
      />
    </div>
  );
}
