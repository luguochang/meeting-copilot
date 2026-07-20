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

interface NativeMicEventsResponse {
  command_status: string;
  events?: unknown[];
  errors: string[];
}

interface NativeRuntime {
  meetingId: string;
  startedAtMs: number;
  lastConnectedAtMs: number;
  pausedAtMs: number | null;
  totalPausedMs: number;
  reconnectAttempts: number;
  reconnecting: boolean;
  stopping: boolean;
}

export interface NativeMicrophoneController extends BrowserMicrophoneController {
  probe(): Promise<boolean>;
}

const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const MAX_RECONNECT_ATTEMPTS = 3;
const STABLE_CONNECTION_RESET_MS = 30_000;

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

function asNativeEvent(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function nativeEventText(event: Record<string, unknown>): string {
  return String(event.normalized_text ?? event.text ?? "").trim();
}

function nativeEventSegmentId(event: Record<string, unknown>): string {
  return String(event.segment_id ?? "").trim();
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
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

  const collectEvents = useCallback(async () => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || runtime.stopping || !invoke) return;
    try {
      const response = await invoke<NativeMicEventsResponse>("mic_adapter_collect_events", {
        sessionId: runtime.meetingId,
      });
      if (response.command_status !== "ok") {
        throw responseError(response as NativeMicCommandResponse, "实时麦克风事件读取失败");
      }
      for (const rawEvent of response.events ?? []) {
        const event = asNativeEvent(rawEvent);
        if (!event) continue;
        const eventType = String(event.event_type ?? "");
        if (eventType === "asr_starting") {
          updateState({ asrReady: false, statusMessage: "正在准备实时识别" });
        } else if (eventType === "asr_ready") {
          const ready = event.ready === true;
          updateState({
            asrReady: ready,
            statusMessage: ready ? "实时识别已就绪" : "实时识别仍在准备",
          });
        } else if (eventType === "partial" || eventType === "final") {
          const text = nativeEventText(event);
          const segmentId = nativeEventSegmentId(event);
          if (!text || !segmentId) continue;
          updateState({
            activePartial: {
              segmentId,
              text,
              startedAtMs: typeof event.start_ms === "number" ? event.start_ms : null,
              updatedAtMs: Date.now(),
            },
            statusMessage: eventType === "final" ? "文字已确认，正在整理" : "正在实时识别",
            });
        } else if (eventType === "input_level") {
          const level = Number(event.level);
          if (Number.isFinite(level)) {
            updateState({
              inputLevel: Math.max(0, Math.min(1, level)),
              inputLevelAvailable: true,
              statusMessage: "正在收音",
            });
          }
        } else if (eventType === "error" || eventType === "provider_error") {
          const message = String(event.message ?? event.detail ?? "实时识别服务异常");
          updateState({ phase: "error", error: message, statusMessage: message });
        }
      }
      const status = await invoke<NativeMicCommandResponse>("mic_adapter_status");
      if (status.status === "stopped" && runtimeRef.current === runtime && !runtime.stopping) {
        if (runtime.reconnecting) return;
        runtime.reconnecting = true;
        if (Date.now() - runtime.lastConnectedAtMs >= STABLE_CONNECTION_RESET_MS) {
          runtime.reconnectAttempts = 0;
        }
        while (
          runtimeRef.current === runtime
          && !runtime.stopping
          && runtime.reconnectAttempts < MAX_RECONNECT_ATTEMPTS
        ) {
          runtime.reconnectAttempts += 1;
          updateState({
            phase: "connecting",
            error: null,
            statusMessage: `连接中断，正在自动恢复（${runtime.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS}）`,
          });
          await delay(runtime.reconnectAttempts * 300);
          try {
            const restarted = await invoke<NativeMicCommandResponse>("mic_adapter_start", {
              sessionId: runtime.meetingId,
            });
            if (restarted.command_status !== "ok" || !restarted.captures_audio) {
              throw responseError(restarted, "系统麦克风自动恢复失败");
            }
            runtime.lastConnectedAtMs = Date.now();
            runtime.reconnecting = false;
            updateState({
              phase: "recording",
              asrReady: true,
              error: null,
              statusMessage: "系统麦克风已自动恢复，正在实时识别",
            });
            return;
          } catch {
            // Retry within the bounded loop. The final failure is surfaced below.
          }
        }
        runtime.reconnecting = false;
        updateState({
          phase: "error",
          error: "系统麦克风连接中断，自动恢复失败",
          statusMessage: "系统麦克风连接中断，自动恢复失败",
        });
      }
    } catch (error) {
      if (runtimeRef.current !== runtime || runtime.stopping) return;
      const message = error instanceof Error ? error.message : "实时麦克风事件读取失败";
      updateState({ phase: "error", error: message, statusMessage: message });
    }
  }, [updateState]);

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
      lastConnectedAtMs: Date.now(),
      pausedAtMs: null,
      totalPausedMs: 0,
      reconnectAttempts: 0,
      reconnecting: false,
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
      runtime.lastConnectedAtMs = Date.now();
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
    void collectEvents();
    const eventTimer = window.setInterval(() => {
      void collectEvents();
    }, 300);
    const timer = window.setInterval(() => {
      const runtime = runtimeRef.current;
      if (runtime && !runtime.stopping) updateState({ elapsedMs: elapsed(runtime) });
    }, 500);
    return () => {
      window.clearInterval(eventTimer);
      window.clearInterval(timer);
    };
  }, [collectEvents, state.phase, updateState]);

  useEffect(() => () => {
    const runtime = runtimeRef.current;
    const invoke = resolveTauriInvoke();
    if (!runtime || !invoke) return;
    void invoke("mic_adapter_stop", { sessionId: runtime.meetingId });
    runtimeRef.current = null;
  }, []);

  return { state, probe, start, togglePause, end, acknowledgeCommitted };
}
