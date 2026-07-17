import { useCallback, useEffect, useRef, useState } from "react";
import { resolveTauriInvoke } from "../../desktop/tauri";
import type {
  BrowserMicrophoneController,
  BrowserMicrophoneState,
} from "./useBrowserMicrophone";

interface NativeMicCommandResponse {
  command_status: string;
  status: string;
  helper_present: boolean;
  captures_audio: boolean;
  errors: string[];
}

interface NativeRuntime {
  meetingId: string;
  startedAtMs: number;
  pausedAtMs: number | null;
  totalPausedMs: number;
  stopping: boolean;
}

export interface NativeMicrophoneController extends BrowserMicrophoneController {
  probe(): Promise<boolean>;
}

const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

const initialState: BrowserMicrophoneState = {
  phase: "idle",
  asrReady: false,
  inputLevel: 0,
  inputLevelAvailable: false,
  elapsedMs: null,
  activePartial: null,
  error: null,
  statusMessage: "尚未开始录音",
  droppedFrames: 0,
};

function responseError(response: NativeMicCommandResponse, fallback: string): Error {
  return new Error(response.errors.filter(Boolean).join("；") || fallback);
}

function elapsed(runtime: NativeRuntime, nowMs = Date.now()): number {
  const end = runtime.pausedAtMs ?? nowMs;
  return Math.max(0, end - runtime.startedAtMs - runtime.totalPausedMs);
}

export function useNativeMicrophone(): NativeMicrophoneController {
  const [state, setState] = useState<BrowserMicrophoneState>(initialState);
  const runtimeRef = useRef<NativeRuntime | null>(null);

  const updateState = useCallback((patch: Partial<BrowserMicrophoneState>) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const probe = useCallback(async () => {
    const invoke = resolveTauriInvoke();
    if (!invoke) return false;
    try {
      const response = await invoke<NativeMicCommandResponse>("mic_adapter_prepare");
      return response.command_status === "ok" && response.helper_present;
    } catch {
      return false;
    }
  }, []);

  const start = useCallback(async (meetingId: string) => {
    const normalizedMeetingId = meetingId.trim();
    if (!SESSION_ID_PATTERN.test(normalizedMeetingId)) throw new Error("会议 ID 格式无效");
    const invoke = resolveTauriInvoke();
    if (!invoke) throw new Error("桌面原生麦克风不可用");

    updateState({
      ...initialState,
      phase: "requesting",
      statusMessage: "正在启动系统麦克风",
    });
    const runtime: NativeRuntime = {
      meetingId: normalizedMeetingId,
      startedAtMs: Date.now(),
      pausedAtMs: null,
      totalPausedMs: 0,
      stopping: false,
    };
    runtimeRef.current = runtime;
    try {
      const response = await invoke<NativeMicCommandResponse>("mic_adapter_start", {
        sessionId: normalizedMeetingId,
      });
      if (response.command_status !== "ok" || !response.captures_audio) {
        throw responseError(response, "系统麦克风启动失败");
      }
      updateState({
        phase: "recording",
        asrReady: true,
        elapsedMs: 0,
        error: null,
        statusMessage: "系统麦克风已连接，正在实时识别",
      });
    } catch (error) {
      runtimeRef.current = null;
      const message = error instanceof Error ? error.message : "系统麦克风启动失败";
      updateState({ phase: "error", error: message, statusMessage: message });
      throw error;
    }
  }, [updateState]);

  const togglePause = useCallback(() => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || runtime.stopping || !invoke) return;
    const nowMs = Date.now();
    const resuming = runtime.pausedAtMs !== null;
    if (resuming) {
      runtime.totalPausedMs += nowMs - (runtime.pausedAtMs ?? nowMs);
      runtime.pausedAtMs = null;
      updateState({ phase: "recording", statusMessage: "已继续录音" });
    } else {
      runtime.pausedAtMs = nowMs;
      updateState({
        phase: "paused",
        inputLevel: 0,
        elapsedMs: elapsed(runtime, nowMs),
        statusMessage: "录音已暂停",
      });
    }
    const command = resuming ? "mic_adapter_resume" : "mic_adapter_pause";
    void invoke<NativeMicCommandResponse>(command).then((response) => {
      if (response.command_status === "ok") return;
      throw responseError(response, resuming ? "继续录音失败" : "暂停录音失败");
    }).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : "麦克风状态切换失败";
      updateState({ phase: "error", error: message, statusMessage: message });
    });
  }, [updateState]);

  const end = useCallback(async () => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || runtime.stopping || !invoke) return;
    runtime.stopping = true;
    updateState({
      phase: "stopping",
      inputLevel: 0,
      elapsedMs: elapsed(runtime),
      statusMessage: "正在保存录音并整理最终文字",
    });
    try {
      const response = await invoke<NativeMicCommandResponse>("mic_adapter_stop", {
        sessionId: runtime.meetingId,
      });
      if (response.command_status !== "ok") {
        throw responseError(response, "系统麦克风停止失败");
      }
      runtimeRef.current = null;
      updateState({
        phase: "ended",
        asrReady: false,
        error: null,
        statusMessage: "录音已安全封存，正在整理",
      });
    } catch (error) {
      runtime.stopping = false;
      const message = error instanceof Error ? error.message : "系统麦克风停止失败";
      updateState({ phase: "error", error: message, statusMessage: message });
      throw error;
    }
  }, [updateState]);

  const acknowledgeCommitted = useCallback(() => undefined, []);

  useEffect(() => {
    if (!runtimeRef.current || !["recording", "paused"].includes(state.phase)) return;
    const timer = window.setInterval(() => {
      const runtime = runtimeRef.current;
      if (runtime && !runtime.stopping) updateState({ elapsedMs: elapsed(runtime) });
    }, 500);
    return () => window.clearInterval(timer);
  }, [state.phase, updateState]);

  useEffect(() => () => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || !invoke) return;
    void invoke("mic_adapter_stop", { sessionId: runtime.meetingId });
    runtimeRef.current = null;
  }, []);

  return { state, probe, start, togglePause, end, acknowledgeCommitted };
}
