import type { MeetingCaptureState, MeetingRuntime, RuntimeIndicator } from "../../domain/events";
import type { MicrophonePhase } from "./useBrowserMicrophone";

export type MeetingSessionState =
  | "idle"
  | "preparing"
  | "capturing"
  | "paused"
  | "reconnecting"
  | "capturing_elsewhere"
  | "recoverable"
  | "ending"
  | "ended"
  | "error";

export interface MeetingSessionStateInput {
  meetingPhase: MeetingRuntime["phase"];
  capturePhase: MicrophonePhase;
  captureBelongsToMeeting: boolean;
  backendCaptureState?: MeetingCaptureState;
}

export function resolveMeetingSessionState({
  meetingPhase,
  capturePhase,
  captureBelongsToMeeting,
  backendCaptureState,
}: MeetingSessionStateInput): MeetingSessionState {
  if (meetingPhase === "ended") return "ended";
  if (meetingPhase === "ending" || capturePhase === "stopping") return "ending";
  if (!captureBelongsToMeeting) return "recoverable";
  if (capturePhase === "requesting" || capturePhase === "connecting" || capturePhase === "starting") {
    return "preparing";
  }
  if (capturePhase === "recording") return "capturing";
  if (capturePhase === "paused") return "paused";
  if (capturePhase === "reconnecting") return "reconnecting";
  if (capturePhase === "error") return "error";
  if (backendCaptureState === "active") return "capturing_elsewhere";
  if (backendCaptureState === "inactive") return "idle";
  if (backendCaptureState === "recoverable") return "recoverable";
  if (meetingPhase === "live") return "recoverable";
  return "idle";
}

export interface CaptureHealthSummary {
  state: RuntimeIndicator["state"];
  label: string;
  aiLabel: string;
}

export function captureHealthSummary(
  sessionState: MeetingSessionState,
  input: RuntimeIndicator,
  asr: RuntimeIndicator,
  provider: RuntimeIndicator,
): CaptureHealthSummary {
  const aiLabel = provider.state === "error" || provider.state === "offline"
    ? "AI 不可用"
    : provider.state === "busy" || provider.state === "paused"
      ? "AI 较慢"
      : "AI 可用";

  if (sessionState === "ended") return { state: "idle", label: "会议已结束", aiLabel: "录音和文字已保存" };
  if (sessionState === "ending") return { state: "busy", label: "正在保存会议", aiLabel };
  if (sessionState === "recoverable") return { state: "paused", label: "录音待恢复", aiLabel };
  if (sessionState === "capturing_elsewhere") return { state: "active", label: "另一窗口录音中", aiLabel };
  if (sessionState === "error") return { state: "error", label: "采集异常", aiLabel };
  if (sessionState === "reconnecting") return { state: "busy", label: "正在恢复采集", aiLabel };
  if (sessionState === "paused") return { state: "paused", label: "录音已暂停", aiLabel };
  if (sessionState === "preparing") return { state: "busy", label: "正在准备采集", aiLabel };
  if (sessionState === "idle") return { state: "idle", label: "尚未开始录音", aiLabel };
  if (input.state === "error" || input.state === "offline" || asr.state === "error" || asr.state === "offline") {
    return { state: "error", label: "采集异常", aiLabel };
  }
  if (input.state === "paused") return { state: "paused", label: "采集正常 · 当前静音", aiLabel };
  if (asr.state === "busy") return { state: "busy", label: "录音正常 · 识别准备中", aiLabel };
  return { state: "active", label: "采集正常", aiLabel };
}
