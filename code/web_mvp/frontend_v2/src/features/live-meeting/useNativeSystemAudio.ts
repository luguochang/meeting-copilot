import { useCallback, useEffect, useRef, useState } from "react";
import {
  EMPTY_NATIVE_CAPTURE_HEALTH,
  nativeCaptureRuntimeFailure,
  nativeCaptureStartupFailure,
  nativeCaptureStatusMessage,
  readNativeCaptureHealth,
  type NativeCaptureHealth,
  type NativeCaptureHealthFields,
} from "../../desktop/nativeCaptureHealth";
import { resolveTauriInvoke } from "../../desktop/tauri";
import type {
  BrowserMicrophoneController,
  BrowserMicrophoneState,
} from "./useBrowserMicrophone";

interface SystemAudioCommandResponse extends NativeCaptureHealthFields {
  command_status: string;
  status: string;
  permission_status: string;
  source: string;
  helper_present: boolean;
  fallback_source: string | null;
  errors: string[];
}

interface SystemAudioEventsResponse {
  command_status: string;
  source: string;
  events?: unknown[];
  errors: string[];
}

interface SystemAudioRuntime {
  meetingId: string;
  startedAtMs: number;
  stopping: boolean;
  collecting: boolean;
  health: NativeCaptureHealth;
}

export interface NativeSystemAudioController extends BrowserMicrophoneController {
  inputSource: "system_audio";
  supportsPause: false;
  probe(): Promise<boolean>;
}

const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const AUDIBLE_PCM_RMS_THRESHOLD = 0.0001;

const initialState: BrowserMicrophoneState = {
  phase: "idle",
  asrReady: false,
  inputLevel: 0,
  inputLevelAvailable: false,
  elapsedMs: null,
  activePartial: null,
  error: null,
  statusMessage: "尚未开始采集系统音频",
  droppedFrames: 0,
  systemAudioHealth: { ...EMPTY_NATIVE_CAPTURE_HEALTH },
};

function responseError(response: SystemAudioCommandResponse, fallback: string): Error {
  if (response.permission_status === "denied" || response.status === "permission_denied") {
    return new Error("系统音频权限被拒绝，请在系统设置的“屏幕与系统音频录制”中允许访问");
  }
  if (response.fallback_source) {
    return new Error("系统音频启动失败，已阻止自动切换到其他声音来源");
  }
  return new Error(response.errors?.filter(Boolean).join("；") || fallback);
}

function asNativeEvent(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function eventText(event: Record<string, unknown>): string {
  return String(event.normalized_text ?? event.text ?? "").trim();
}

export function useNativeSystemAudio(): NativeSystemAudioController {
  const [state, setState] = useState<BrowserMicrophoneState>(initialState);
  const runtimeRef = useRef<SystemAudioRuntime | null>(null);

  const updateState = useCallback((patch: Partial<BrowserMicrophoneState>) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const probe = useCallback(async () => {
    const invoke = resolveTauriInvoke();
    if (!invoke) return false;
    try {
      const response = await invoke<SystemAudioCommandResponse>("system_audio_adapter_prepare", undefined);
      return response.command_status === "ok"
        && response.source === "system_audio"
        && response.helper_present
        && response.fallback_source === null;
    } catch {
      return false;
    }
  }, []);

  const stopRuntime = useCallback(async (runtime: SystemAudioRuntime) => {
    const invoke = resolveTauriInvoke();
    if (!invoke) throw new Error("桌面系统音频采集不可用");
    return invoke<SystemAudioCommandResponse>("system_audio_adapter_stop", {
      sessionId: runtime.meetingId,
    });
  }, []);

  const failRuntime = useCallback(async (runtime: SystemAudioRuntime, detail: string) => {
    if (runtimeRef.current !== runtime || runtime.stopping) return;
    runtime.stopping = true;
    try {
      await stopRuntime(runtime);
    } catch {
      // Preserve the first actionable capture failure in the UI.
    }
    if (runtimeRef.current === runtime) runtimeRef.current = null;
    updateState({
      phase: "error",
      asrReady: false,
      inputLevel: 0,
      error: detail,
      statusMessage: detail,
      systemAudioHealth: { ...runtime.health, asrReady: false },
    });
  }, [stopRuntime, updateState]);

  const collectEvents = useCallback(async () => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || runtime.stopping || runtime.collecting || !invoke) return;
    runtime.collecting = true;
    try {
      const response = await invoke<SystemAudioEventsResponse>("system_audio_adapter_collect_events", {
        sessionId: runtime.meetingId,
      });
      if (response.command_status !== "ok" || response.source !== "system_audio") {
        throw new Error(response.errors?.filter(Boolean).join("；") || "系统音频事件读取失败");
      }

      const eventPatch: Partial<BrowserMicrophoneState> = {};
      let eventStatusMessage: string | null = null;
      for (const rawEvent of response.events ?? []) {
        const event = asNativeEvent(rawEvent);
        if (!event) continue;
        const eventType = String(event.event_type ?? "");
        if (eventType === "asr_starting") {
          runtime.health = { ...runtime.health, asrReady: false };
          eventStatusMessage = "正在准备实时识别";
        } else if (eventType === "asr_ready") {
          runtime.health = { ...runtime.health, asrReady: event.ready === true };
        } else if (eventType === "pcm") {
          const rms = Number(event.rms);
          runtime.health = {
            ...runtime.health,
            pcmSeen: true,
            audiblePcmSeen: runtime.health.audiblePcmSeen
              || (Number.isFinite(rms) && rms > AUDIBLE_PCM_RMS_THRESHOLD),
          };
          if (Number.isFinite(rms)) {
            eventPatch.inputLevel = Math.max(0, Math.min(1, rms * 6));
            eventPatch.inputLevelAvailable = true;
          }
        } else if (eventType === "partial" || eventType === "final") {
          const text = eventText(event);
          const segmentId = String(event.segment_id ?? "").trim();
          if (!text || !segmentId) continue;
          eventPatch.activePartial = {
            segmentId,
            text,
            startedAtMs: typeof event.start_ms === "number" ? event.start_ms : null,
            updatedAtMs: Date.now(),
          };
          eventStatusMessage = eventType === "final" ? "文字已确认，正在整理" : "正在实时识别";
        } else if (eventType === "error" || eventType === "provider_error") {
          await failRuntime(
            runtime,
            String(event.message ?? event.detail ?? "系统音频实时识别异常"),
          );
          return;
        }
      }

      const status = await invoke<SystemAudioCommandResponse>("system_audio_adapter_status", undefined);
      if (status.command_status !== "ok") {
        throw responseError(status, "系统音频状态读取失败");
      }
      if (status.status === "stopped") {
        await failRuntime(runtime, "系统音频采集已停止，请重新开始录音");
        return;
      }
      const healthFailure = nativeCaptureRuntimeFailure(status);
      if (healthFailure) {
        await failRuntime(runtime, healthFailure);
        return;
      }
      runtime.health = readNativeCaptureHealth(status, runtime.health);
      updateState({
        ...eventPatch,
        asrReady: runtime.health.asrReady,
        systemAudioHealth: { ...runtime.health },
        statusMessage: eventStatusMessage ?? nativeCaptureStatusMessage(runtime.health),
      });
    } catch (error) {
      if (runtimeRef.current === runtime && !runtime.stopping) {
        await failRuntime(runtime, error instanceof Error ? error.message : "系统音频事件读取失败");
      }
    } finally {
      runtime.collecting = false;
    }
  }, [failRuntime, updateState]);

  const start = useCallback(async (meetingId: string) => {
    const normalizedMeetingId = meetingId.trim();
    if (!SESSION_ID_PATTERN.test(normalizedMeetingId)) throw new Error("会议 ID 格式无效");
    const invoke = resolveTauriInvoke();
    if (!invoke) throw new Error("桌面系统音频采集不可用");

    updateState({
      ...initialState,
      phase: "requesting",
      statusMessage: "正在检查系统音频组件",
    });
    let startReturned = false;
    try {
      const prepared = await invoke<SystemAudioCommandResponse>("system_audio_adapter_prepare", undefined);
      if (prepared.command_status !== "ok"
        || prepared.source !== "system_audio"
        || !prepared.helper_present
        || prepared.fallback_source !== null) {
        throw responseError(prepared, "系统音频采集不可用");
      }

      updateState({ phase: "starting", statusMessage: "正在请求系统音频权限并验证数据链路" });
      const response = await invoke<SystemAudioCommandResponse>("system_audio_adapter_start", {
        sessionId: normalizedMeetingId,
        requestPermission: true,
      });
      startReturned = true;
      if (response.command_status !== "ok"
        || response.status !== "recording"
        || response.source !== "system_audio"
        || response.fallback_source !== null) {
        throw responseError(response, "系统音频启动失败");
      }
      const healthFailure = nativeCaptureStartupFailure(response);
      if (healthFailure) throw new Error(healthFailure);

      const health = readNativeCaptureHealth(response);
      runtimeRef.current = {
        meetingId: normalizedMeetingId,
        startedAtMs: Date.now(),
        stopping: false,
        collecting: false,
        health,
      };
      updateState({
        phase: "recording",
        asrReady: health.asrReady,
        inputLevelAvailable: health.pcmSeen,
        elapsedMs: 0,
        error: null,
        statusMessage: nativeCaptureStatusMessage(health),
        systemAudioHealth: { ...health },
      });
    } catch (error) {
      if (startReturned) {
        try {
          await invoke<SystemAudioCommandResponse>("system_audio_adapter_stop", {
            sessionId: normalizedMeetingId,
          });
        } catch {
          // The startup error is more actionable than a best-effort cleanup error.
        }
      }
      runtimeRef.current = null;
      const message = error instanceof Error ? error.message : "系统音频启动失败";
      updateState({ phase: "error", error: message, statusMessage: message });
      throw new Error(message);
    }
  }, [updateState]);

  const end = useCallback(async () => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.stopping) return;
    runtime.stopping = true;
    updateState({
      phase: "stopping",
      inputLevel: 0,
      elapsedMs: Math.max(0, Date.now() - runtime.startedAtMs),
      statusMessage: "正在保存录音并整理最终文字",
    });
    try {
      const response = await stopRuntime(runtime);
      if (response.command_status !== "ok" || response.status !== "stopped") {
        throw responseError(response, "系统音频停止失败");
      }
      runtimeRef.current = null;
      updateState({
        phase: "ended",
        asrReady: false,
        error: null,
        statusMessage: "录音已安全封存，正在整理",
        systemAudioHealth: { ...runtime.health, asrReady: false },
      });
    } catch (error) {
      runtime.stopping = false;
      const message = error instanceof Error ? error.message : "系统音频停止失败";
      updateState({ phase: "error", error: message, statusMessage: message });
      throw new Error(message);
    }
  }, [stopRuntime, updateState]);

  useEffect(() => {
    if (!runtimeRef.current || state.phase !== "recording") return;
    void collectEvents();
    const eventTimer = window.setInterval(() => void collectEvents(), 300);
    const elapsedTimer = window.setInterval(() => {
      const runtime = runtimeRef.current;
      if (runtime && !runtime.stopping) {
        updateState({ elapsedMs: Math.max(0, Date.now() - runtime.startedAtMs) });
      }
    }, 500);
    return () => {
      window.clearInterval(eventTimer);
      window.clearInterval(elapsedTimer);
    };
  }, [collectEvents, state.phase, updateState]);

  useEffect(() => () => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.stopping = true;
    void stopRuntime(runtime).catch(() => undefined);
    runtimeRef.current = null;
  }, [stopRuntime]);

  return {
    state,
    inputSource: "system_audio",
    supportsPause: false,
    probe,
    start,
    togglePause: () => undefined,
    end,
    acknowledgeCommitted: () => undefined,
  };
}
