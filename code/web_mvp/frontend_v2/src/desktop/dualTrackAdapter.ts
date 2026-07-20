import { resolveTauriInvoke } from "./tauri";
import {
  nativeCaptureRuntimeFailure,
  nativeCaptureStartupFailure,
  type NativeCaptureHealthFields,
} from "./nativeCaptureHealth";

export type DualTrackName = "microphone" | "system_audio";

export interface DualTrackCaptureTrackResponse extends NativeCaptureHealthFields {
  command_status: string;
  status: string;
  helper_present: boolean;
  captures_audio?: boolean;
  permission_status?: string;
  fallback_source?: string | null;
  errors?: string[];
}

export interface DualTrackCaptureResponse {
  command_status: string;
  status: string;
  requested_mode: string;
  active_mode: string;
  session_id?: string | null;
  active_track_count: number;
  microphone: DualTrackCaptureTrackResponse;
  system_audio: DualTrackCaptureTrackResponse;
}

export interface DualTrackEventsTrackResponse {
  command_status: string;
  events?: unknown[];
  errors?: string[];
}

export interface DualTrackEventsResponse {
  command_status: string;
  requested_mode: string;
  session_id?: string | null;
  microphone: DualTrackEventsTrackResponse;
  system_audio: DualTrackEventsTrackResponse;
}

function invokeDualTrack<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const invoke = resolveTauriInvoke();
  if (!invoke) return Promise.reject(new Error("桌面双轨采集不可用"));
  return invoke<T>(command, args);
}

export function dualTrackStatus(): Promise<DualTrackCaptureResponse> {
  return invokeDualTrack("dual_track_adapter_status", undefined);
}

export function dualTrackStart(sessionId: string): Promise<DualTrackCaptureResponse> {
  return invokeDualTrack("dual_track_adapter_start", {
    sessionId,
    requestSystemAudioPermission: true,
  });
}

export function dualTrackCollectEvents(sessionId: string): Promise<DualTrackEventsResponse> {
  return invokeDualTrack("dual_track_adapter_collect_events", { sessionId });
}

export function dualTrackStop(sessionId: string): Promise<DualTrackCaptureResponse> {
  return invokeDualTrack("dual_track_adapter_stop", { sessionId });
}

export function dualTrackCleanup(sessionId: string): Promise<DualTrackCaptureResponse> {
  return invokeDualTrack("dual_track_adapter_cleanup", { sessionId });
}

export function isDualTrackCapabilityAvailable(response: DualTrackCaptureResponse): boolean {
  return response.command_status === "ok"
    && response.requested_mode === "dual_track"
    && response.microphone?.command_status === "ok"
    && response.microphone?.helper_present === true
    && response.system_audio?.command_status === "ok"
    && response.system_audio?.helper_present === true
    && response.system_audio?.fallback_source !== undefined
    && response.system_audio.fallback_source === null;
}

function trackFailure(
  trackName: DualTrackName,
  response: DualTrackCaptureTrackResponse | DualTrackEventsTrackResponse | undefined,
): string | null {
  const label = trackName === "microphone" ? "麦克风轨道" : "系统音频轨道";
  if (!response) return `${label}状态缺失`;
  const capture = response as DualTrackCaptureTrackResponse;
  if (trackName === "system_audio"
    && (capture.permission_status === "denied" || capture.status === "permission_denied")) {
    return `${label}权限被拒绝，请在系统设置的“屏幕与系统音频录制”中允许访问`;
  }
  const detail = response.errors?.filter(Boolean).join("；");
  if (detail) return `${label}失败：${detail}`;
  if (trackName === "system_audio") {
    const healthFailure = nativeCaptureStartupFailure(capture, label);
    if (healthFailure) return healthFailure;
  }
  return `${label}失败`;
}

export function dualTrackCaptureFailure(
  response: DualTrackCaptureResponse,
  stage: "startup" | "runtime" = "startup",
): string | null {
  const microphoneReady = response.microphone?.command_status === "ok"
    && response.microphone.status === "recording"
    && response.microphone.captures_audio === true;
  const systemAudioBaseReady = response.system_audio?.command_status === "ok"
    && response.system_audio.status === "recording"
    && response.system_audio.fallback_source === null;
  const systemAudioHealthFailure = systemAudioBaseReady
    ? stage === "runtime"
      ? nativeCaptureRuntimeFailure(response.system_audio ?? {}, "系统音频轨道")
        ?? (response.system_audio?.transport_ready !== true
          ? "系统音频轨道传输状态缺失"
          : response.system_audio?.pcm_seen !== true ? "系统音频轨道 PCM 接收状态缺失" : null)
      : nativeCaptureStartupFailure(response.system_audio ?? {}, "系统音频轨道")
    : null;
  const systemAudioReady = systemAudioBaseReady && systemAudioHealthFailure === null;
  if (response.command_status === "ok"
    && response.status === "recording"
    && response.requested_mode === "dual_track"
    && response.active_mode === "dual_track"
    && response.active_track_count === 2
    && microphoneReady
    && systemAudioReady) {
    return null;
  }
  const failures = [
    ...(!microphoneReady ? [trackFailure("microphone", response.microphone)] : []),
    ...(!systemAudioReady
      ? [systemAudioHealthFailure ?? trackFailure("system_audio", response.system_audio)]
      : []),
  ].filter((value): value is string => Boolean(value));
  return failures.join("；") || "双轨采集未同时保持两条轨道";
}

export function dualTrackEventsFailure(response: DualTrackEventsResponse): string | null {
  if (response.command_status === "ok"
    && response.requested_mode === "dual_track"
    && response.microphone?.command_status === "ok"
    && response.system_audio?.command_status === "ok") {
    return null;
  }
  const failures = [
    ...(response.microphone?.command_status !== "ok" ? [trackFailure("microphone", response.microphone)] : []),
    ...(response.system_audio?.command_status !== "ok" ? [trackFailure("system_audio", response.system_audio)] : []),
  ].filter((value): value is string => Boolean(value));
  return failures.join("；") || "双轨事件读取不完整";
}
