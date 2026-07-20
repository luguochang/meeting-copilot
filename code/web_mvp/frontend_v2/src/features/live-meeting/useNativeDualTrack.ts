import { useCallback, useEffect, useRef, useState } from "react";
import {
  dualTrackCaptureFailure,
  dualTrackCleanup,
  dualTrackCollectEvents,
  dualTrackEventsFailure,
  dualTrackStart,
  dualTrackStatus,
  dualTrackStop,
  type DualTrackCaptureResponse,
  type DualTrackName,
} from "../../desktop/dualTrackAdapter";
import {
  EMPTY_NATIVE_CAPTURE_HEALTH,
  nativeCaptureStatusMessage,
  readNativeCaptureHealth,
  type NativeCaptureHealth,
} from "../../desktop/nativeCaptureHealth";
import type {
  BrowserMicrophoneController,
  BrowserMicrophoneState,
} from "./useBrowserMicrophone";

interface DualTrackRuntime {
  meetingId: string;
  startedAtMs: number;
  stopping: boolean;
  collecting: boolean;
  levels: Record<DualTrackName, number | null>;
  asrReady: Record<DualTrackName, boolean>;
  systemAudioHealth: NativeCaptureHealth;
}

export interface NativeDualTrackController extends BrowserMicrophoneController {
  inputSource: "dual_track";
  supportsPause: false;
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
  statusMessage: "尚未开始双轨采集",
  droppedFrames: 0,
  systemAudioHealth: { ...EMPTY_NATIVE_CAPTURE_HEALTH },
};

function asNativeEvent(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function eventText(event: Record<string, unknown>): string {
  return String(event.normalized_text ?? event.text ?? "").trim();
}

function trackLabel(trackName: DualTrackName): string {
  return trackName === "microphone" ? "麦克风轨道" : "系统音频轨道";
}

function stopFailure(response: DualTrackCaptureResponse): string | null {
  if (response.command_status === "ok" && response.status === "stopped") return null;
  const details = [
    ...(response.microphone?.command_status !== "ok" ? [`麦克风轨道：${response.microphone.errors?.join("；") || "停止失败"}`] : []),
    ...(response.system_audio?.command_status !== "ok" ? [`系统音频轨道：${response.system_audio.errors?.join("；") || "停止失败"}`] : []),
  ];
  return details.join("；") || "双轨停止不完整";
}

function cleanupFailure(response: DualTrackCaptureResponse): string | null {
  return response.command_status === "ok" && response.status === "cleaned"
    ? null
    : "双轨采集资源清理不完整";
}

export function useNativeDualTrack(): NativeDualTrackController {
  const [state, setState] = useState<BrowserMicrophoneState>(initialState);
  const runtimeRef = useRef<DualTrackRuntime | null>(null);

  const updateState = useCallback((patch: Partial<BrowserMicrophoneState>) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const cleanupRuntime = useCallback(async (runtime: DualTrackRuntime): Promise<string[]> => {
    const failures: string[] = [];
    try {
      const stopped = await dualTrackStop(runtime.meetingId);
      const failure = stopFailure(stopped);
      if (failure) failures.push(failure);
    } catch (error) {
      failures.push(error instanceof Error ? error.message : "双轨停止失败");
    }
    try {
      const cleaned = await dualTrackCleanup(runtime.meetingId);
      const failure = cleanupFailure(cleaned);
      if (failure) failures.push(failure);
    } catch (error) {
      failures.push(error instanceof Error ? error.message : "双轨资源清理失败");
    }
    return failures;
  }, []);

  const failRuntime = useCallback(async (runtime: DualTrackRuntime, detail: string) => {
    if (runtimeRef.current !== runtime || runtime.stopping) return;
    runtime.stopping = true;
    await cleanupRuntime(runtime);
    if (runtimeRef.current === runtime) runtimeRef.current = null;
    const message = `双轨采集不完整：${detail}`;
    updateState({
      phase: "error",
      asrReady: false,
      inputLevel: 0,
      error: message,
      statusMessage: message,
      systemAudioHealth: { ...runtime.systemAudioHealth, asrReady: false },
    });
  }, [cleanupRuntime, updateState]);

  const applyEvents = useCallback((
    runtime: DualTrackRuntime,
    trackName: DualTrackName,
    rawEvents: unknown[],
  ): string | null => {
    for (const rawEvent of rawEvents) {
      const event = asNativeEvent(rawEvent);
      if (!event) continue;
      const eventType = String(event.event_type ?? "");
      if (eventType === "asr_starting") {
        runtime.asrReady[trackName] = false;
        if (trackName === "system_audio") {
          runtime.systemAudioHealth = { ...runtime.systemAudioHealth, asrReady: false };
        }
        updateState({ asrReady: false, statusMessage: `${trackLabel(trackName)}正在准备实时识别` });
      } else if (eventType === "asr_ready") {
        runtime.asrReady[trackName] = event.ready === true;
        if (trackName === "system_audio") {
          runtime.systemAudioHealth = {
            ...runtime.systemAudioHealth,
            asrReady: runtime.asrReady[trackName],
          };
        }
        const ready = runtime.asrReady.microphone && runtime.asrReady.system_audio;
        updateState({
          asrReady: ready,
          systemAudioHealth: { ...runtime.systemAudioHealth },
          statusMessage: !runtime.systemAudioHealth.audiblePcmSeen
            ? nativeCaptureStatusMessage(runtime.systemAudioHealth)
            : ready ? "双轨实时识别已就绪" : `${trackLabel(trackName)}实时识别已就绪，等待另一轨`,
        });
      } else if (eventType === "input_level" || eventType === "pcm") {
        const rawLevel = eventType === "input_level" ? Number(event.level) : Number(event.rms) * 6;
        if (Number.isFinite(rawLevel)) {
          runtime.levels[trackName] = Math.max(0, Math.min(1, rawLevel));
          if (trackName === "system_audio" && eventType === "pcm") {
            const rms = Number(event.rms);
            runtime.systemAudioHealth = {
              ...runtime.systemAudioHealth,
              pcmSeen: true,
              audiblePcmSeen: runtime.systemAudioHealth.audiblePcmSeen
                || (Number.isFinite(rms) && rms > AUDIBLE_PCM_RMS_THRESHOLD),
            };
          }
          const levels = Object.values(runtime.levels).filter((value): value is number => value !== null);
          updateState({
            inputLevel: levels.length ? Math.max(...levels) : 0,
            inputLevelAvailable: levels.length > 0,
            systemAudioHealth: { ...runtime.systemAudioHealth },
            statusMessage: runtime.systemAudioHealth.audiblePcmSeen
              ? "麦克风和系统音频正在采集"
              : nativeCaptureStatusMessage(runtime.systemAudioHealth),
          });
        }
      } else if (eventType === "partial" || eventType === "final") {
        const text = eventText(event);
        const segmentId = String(event.segment_id ?? "").trim();
        if (!text || !segmentId) continue;
        updateState({
          activePartial: {
            segmentId,
            text,
            startedAtMs: typeof event.start_ms === "number" ? event.start_ms : null,
            updatedAtMs: Date.now(),
          },
          statusMessage: eventType === "final" ? "双轨文字已确认，正在整理" : "正在汇入双轨实时文字",
        });
      } else if (eventType === "error" || eventType === "provider_error") {
        return `${trackLabel(trackName)}失败：${String(event.message ?? event.detail ?? "实时识别异常")}`;
      }
    }
    return null;
  }, [updateState]);

  const collectEvents = useCallback(async () => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.stopping || runtime.collecting) return;
    runtime.collecting = true;
    try {
      const response = await dualTrackCollectEvents(runtime.meetingId);
      const eventFailure = dualTrackEventsFailure(response);
      if (eventFailure) {
        await failRuntime(runtime, eventFailure);
        return;
      }
      const microphoneFailure = applyEvents(runtime, "microphone", response.microphone.events ?? []);
      const systemAudioFailure = applyEvents(runtime, "system_audio", response.system_audio.events ?? []);
      if (microphoneFailure || systemAudioFailure) {
        await failRuntime(runtime, [microphoneFailure, systemAudioFailure].filter(Boolean).join("；"));
        return;
      }
      const status = await dualTrackStatus();
      const captureFailure = dualTrackCaptureFailure(status, "runtime");
      if (captureFailure) {
        await failRuntime(runtime, captureFailure);
        return;
      }
      runtime.systemAudioHealth = readNativeCaptureHealth(
        status.system_audio,
        runtime.systemAudioHealth,
      );
      runtime.asrReady.system_audio = runtime.systemAudioHealth.asrReady;
      updateState({
        asrReady: runtime.asrReady.microphone && runtime.asrReady.system_audio,
        systemAudioHealth: { ...runtime.systemAudioHealth },
        statusMessage: nativeCaptureStatusMessage(runtime.systemAudioHealth),
      });
    } catch (error) {
      if (runtimeRef.current === runtime && !runtime.stopping) {
        await failRuntime(runtime, error instanceof Error ? error.message : "双轨事件读取失败");
      }
    } finally {
      runtime.collecting = false;
    }
  }, [applyEvents, failRuntime, updateState]);

  const start = useCallback(async (meetingId: string) => {
    const normalizedMeetingId = meetingId.trim();
    if (!SESSION_ID_PATTERN.test(normalizedMeetingId)) throw new Error("会议 ID 格式无效");
    const runtime: DualTrackRuntime = {
      meetingId: normalizedMeetingId,
      startedAtMs: Date.now(),
      stopping: false,
      collecting: false,
      levels: { microphone: null, system_audio: null },
      asrReady: { microphone: false, system_audio: false },
      systemAudioHealth: { ...EMPTY_NATIVE_CAPTURE_HEALTH },
    };
    runtimeRef.current = runtime;
    updateState({ ...initialState, phase: "starting", statusMessage: "正在启动麦克风和系统音频" });
    try {
      const response = await dualTrackStart(normalizedMeetingId);
      const failure = dualTrackCaptureFailure(response);
      if (failure) {
        runtime.stopping = true;
        await cleanupRuntime(runtime);
        runtimeRef.current = null;
        const message = `双轨采集不完整：${failure}`;
        updateState({ phase: "error", error: message, statusMessage: message });
        throw new Error(message);
      }
      runtime.systemAudioHealth = readNativeCaptureHealth(response.system_audio);
      runtime.asrReady.system_audio = runtime.systemAudioHealth.asrReady;
      updateState({
        phase: "recording",
        asrReady: false,
        inputLevelAvailable: runtime.systemAudioHealth.pcmSeen,
        elapsedMs: 0,
        error: null,
        statusMessage: nativeCaptureStatusMessage(runtime.systemAudioHealth),
        systemAudioHealth: { ...runtime.systemAudioHealth },
      });
    } catch (error) {
      if (runtimeRef.current === runtime && !runtime.stopping) {
        runtime.stopping = true;
        await cleanupRuntime(runtime);
      }
      if (runtimeRef.current === runtime) runtimeRef.current = null;
      const message = error instanceof Error ? error.message : "双轨采集启动失败";
      updateState({ phase: "error", error: message, statusMessage: message });
      throw new Error(message);
    }
  }, [cleanupRuntime, updateState]);

  const togglePause = useCallback(() => undefined, []);

  const end = useCallback(async () => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.stopping) return;
    runtime.stopping = true;
    updateState({
      phase: "stopping",
      inputLevel: 0,
      elapsedMs: Date.now() - runtime.startedAtMs,
      statusMessage: "正在停止并封存两条音轨",
    });
    const failures = await cleanupRuntime(runtime);
    runtimeRef.current = null;
    if (failures.length) {
      const message = `双轨收尾不完整：${failures.join("；")}`;
      updateState({ phase: "error", error: message, statusMessage: message });
      throw new Error(message);
    }
    updateState({
      phase: "ended",
      asrReady: false,
      error: null,
      statusMessage: "两条音轨已安全封存，正在整理",
      systemAudioHealth: { ...runtime.systemAudioHealth, asrReady: false },
    });
  }, [cleanupRuntime, updateState]);

  const acknowledgeCommitted = useCallback(() => undefined, []);

  useEffect(() => {
    if (!runtimeRef.current || state.phase !== "recording") return;
    void collectEvents();
    const eventTimer = window.setInterval(() => void collectEvents(), 300);
    const elapsedTimer = window.setInterval(() => {
      const runtime = runtimeRef.current;
      if (runtime && !runtime.stopping) updateState({ elapsedMs: Date.now() - runtime.startedAtMs });
    }, 500);
    return () => {
      window.clearInterval(eventTimer);
      window.clearInterval(elapsedTimer);
    };
  }, [collectEvents, state.phase, updateState]);

  useEffect(() => () => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.stopping) return;
    runtime.stopping = true;
    void (async () => {
      try {
        await dualTrackStop(runtime.meetingId);
      } catch {
        // The desktop bridge may already be tearing down during window unload.
      }
      try {
        await dualTrackCleanup(runtime.meetingId);
      } catch {
        // Best-effort cleanup during component teardown cannot update visible state.
      }
    })();
    runtimeRef.current = null;
  }, []);

  return {
    state,
    inputSource: "dual_track",
    supportsPause: false,
    start,
    togglePause,
    end,
    acknowledgeCommitted,
  };
}
