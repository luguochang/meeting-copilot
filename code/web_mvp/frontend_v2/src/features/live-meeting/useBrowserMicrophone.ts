import { useCallback, useEffect, useRef, useState } from "react";
import type { ActivePartial } from "../../domain/events";
import { pcmLevel, StreamingPcmFramer } from "./audioPcm";

export type MicrophonePhase =
  | "idle"
  | "requesting"
  | "connecting"
  | "starting"
  | "recording"
  | "paused"
  | "stopping"
  | "ended"
  | "error";

export interface BrowserMicrophoneState {
  phase: MicrophonePhase;
  asrReady: boolean;
  inputLevel: number;
  inputLevelAvailable?: boolean;
  elapsedMs: number | null;
  activePartial: ActivePartial | null;
  error: string | null;
  statusMessage: string;
  droppedFrames: number;
}

interface UseBrowserMicrophoneOptions {
  asrBaseUrl?: string;
  permissionTimeoutMs?: number;
  endTimeoutMs?: number;
}

export interface BrowserMicrophoneController {
  state: BrowserMicrophoneState;
  start(meetingId: string): Promise<void>;
  togglePause(): void;
  end(): Promise<void>;
  acknowledgeCommitted(segmentIds: Iterable<string>): void;
}

interface MicrophoneRuntime {
  meetingId: string;
  stream: MediaStream | null;
  context: AudioContext | null;
  source: MediaStreamAudioSourceNode | null;
  processor: AudioNode | null;
  monitor: GainNode | null;
  socket: WebSocket | null;
  framer: StreamingPcmFramer | null;
  queue: Float32Array[];
  paused: boolean;
  stopping: boolean;
  disposed: boolean;
  startedAtMs: number;
  pausedAtMs: number | null;
  totalPausedMs: number;
  stoppedAtMs: number | null;
  lastLevelAtMs: number;
  terminalPromise: Promise<void>;
  resolveTerminal: () => void;
  openPromise: Promise<void>;
  resolveOpen: () => void;
}

const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const MAX_QUEUED_FRAMES = 150;
const MAX_SOCKET_BUFFER_BYTES = 2 * 1024 * 1024;
const DEFAULT_PERMISSION_TIMEOUT_MS = 15_000;
const DEFAULT_END_TIMEOUT_MS = 12_000;

const initialState: BrowserMicrophoneState = {
  phase: "idle",
  asrReady: false,
  inputLevel: 0,
  inputLevelAvailable: true,
  elapsedMs: null,
  activePartial: null,
  error: null,
  statusMessage: "尚未开始录音",
  droppedFrames: 0,
};

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve: () => void = () => {};
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "麦克风权限被拒绝，请在浏览器和系统设置中允许访问";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "没有找到可用的麦克风";
  }
  if (error instanceof Error) return error.message;
  return "麦克风启动失败";
}

function buildWebSocketUrl(meetingId: string, baseUrl = ""): string {
  const base = baseUrl.trim()
    ? new URL(baseUrl, window.location.href)
    : new URL(window.location.href);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  base.pathname = `/live/asr/stream/ws/${encodeURIComponent(meetingId)}`;
  base.search = new URLSearchParams({ audio_source: "browser_live_mic" }).toString();
  base.hash = "";
  return base.toString();
}

function requestMicrophone(timeoutMs: number): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    return Promise.reject(new Error("当前浏览器不支持麦克风访问"));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("麦克风权限请求超时，请检查浏览器权限后重试"));
    }, timeoutMs);
    void navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16_000,
        echoCancellation: false,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    }).then((stream) => {
      if (settled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      resolve(stream);
    }, (error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      reject(error);
    });
  });
}

function elapsed(runtime: MicrophoneRuntime, nowMs = Date.now()): number {
  const end = runtime.stoppedAtMs ?? runtime.pausedAtMs ?? nowMs;
  return Math.max(0, end - runtime.startedAtMs - runtime.totalPausedMs);
}

export function useBrowserMicrophone(
  options: UseBrowserMicrophoneOptions = {},
): BrowserMicrophoneController {
  const [state, setState] = useState<BrowserMicrophoneState>(initialState);
  const runtimeRef = useRef<MicrophoneRuntime | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const updateState = useCallback((patch: Partial<BrowserMicrophoneState>) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const closeAudio = useCallback((runtime: MicrophoneRuntime) => {
    try { runtime.processor?.disconnect(); } catch { /* already detached */ }
    try { runtime.monitor?.disconnect(); } catch { /* already detached */ }
    try { runtime.source?.disconnect(); } catch { /* already detached */ }
    runtime.processor = null;
    runtime.monitor = null;
    runtime.source = null;
    runtime.stream?.getTracks().forEach((track) => track.stop());
    runtime.stream = null;
    if (runtime.context) void runtime.context.close().catch(() => undefined);
    runtime.context = null;
  }, []);

  const dispose = useCallback((runtime: MicrophoneRuntime, closeSocket = true) => {
    if (runtime.disposed) return;
    runtime.disposed = true;
    closeAudio(runtime);
    if (closeSocket && runtime.socket && runtime.socket.readyState < WebSocket.CLOSING) {
      try { runtime.socket.close(1000, "client_cleanup"); } catch { /* already closed */ }
    }
    runtime.resolveOpen();
    runtime.resolveTerminal();
  }, [closeAudio]);

  const flushQueue = useCallback((runtime: MicrophoneRuntime, force = false) => {
    const socket = runtime.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    while (runtime.queue.length && (force || socket.bufferedAmount < MAX_SOCKET_BUFFER_BYTES)) {
      const frame = runtime.queue.shift();
      if (!frame) break;
      socket.send(frame.slice().buffer);
    }
  }, []);

  const queueOrSend = useCallback((runtime: MicrophoneRuntime, frame: Float32Array) => {
    const socket = runtime.socket;
    if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < MAX_SOCKET_BUFFER_BYTES) {
      flushQueue(runtime);
      socket.send(frame.slice().buffer);
      return;
    }
    if (runtime.queue.length >= MAX_QUEUED_FRAMES) {
      runtime.queue.shift();
      setState((current) => ({
        ...current,
        droppedFrames: current.droppedFrames + 1,
        statusMessage: "连接中断时间过长，部分最早音频未能发送",
      }));
    }
    runtime.queue.push(new Float32Array(frame));
  }, [flushQueue]);

  const consumeAudio = useCallback((runtime: MicrophoneRuntime, samples: Float32Array) => {
    if (runtime.disposed || runtime.paused || runtime.stopping || !runtime.framer) return;
    const nowMs = Date.now();
    if (nowMs - runtime.lastLevelAtMs >= 100) {
      runtime.lastLevelAtMs = nowMs;
      updateState({ inputLevel: pcmLevel(samples), elapsedMs: elapsed(runtime, nowMs) });
    }
    for (const frame of runtime.framer.push(samples)) queueOrSend(runtime, frame);
  }, [queueOrSend, updateState]);

  const start = useCallback(async (meetingId: string) => {
    const normalizedMeetingId = meetingId.trim();
    if (!SESSION_ID_PATTERN.test(normalizedMeetingId)) throw new Error("会议 ID 格式无效");
    if (runtimeRef.current && !runtimeRef.current.disposed) dispose(runtimeRef.current);

    updateState({
      ...initialState,
      phase: "requesting",
      statusMessage: "正在请求麦克风权限",
    });

    const terminal = deferred();
    const opened = deferred();
    const runtime: MicrophoneRuntime = {
      meetingId: normalizedMeetingId,
      stream: null,
      context: null,
      source: null,
      processor: null,
      monitor: null,
      socket: null,
      framer: null,
      queue: [],
      paused: false,
      stopping: false,
      disposed: false,
      startedAtMs: Date.now(),
      pausedAtMs: null,
      totalPausedMs: 0,
      stoppedAtMs: null,
      lastLevelAtMs: 0,
      terminalPromise: terminal.promise,
      resolveTerminal: terminal.resolve,
      openPromise: opened.promise,
      resolveOpen: opened.resolve,
    };
    runtimeRef.current = runtime;

    try {
      runtime.stream = await requestMicrophone(
        optionsRef.current.permissionTimeoutMs ?? DEFAULT_PERMISSION_TIMEOUT_MS,
      );
      if (runtimeRef.current !== runtime || runtime.disposed) {
        runtime.stream.getTracks().forEach((track) => track.stop());
        return;
      }

      const AudioContextConstructor = window.AudioContext;
      if (!AudioContextConstructor) throw new Error("当前浏览器不支持 AudioContext");
      runtime.context = new AudioContextConstructor({ latencyHint: "interactive" });
      await runtime.context.resume();
      runtime.framer = new StreamingPcmFramer(runtime.context.sampleRate);
      runtime.source = runtime.context.createMediaStreamSource(runtime.stream);
      runtime.monitor = runtime.context.createGain();
      runtime.monitor.gain.value = 0;

      let processor: AudioNode | null = null;
      if (runtime.context.audioWorklet && typeof AudioWorkletNode !== "undefined") {
        try {
          await runtime.context.audioWorklet.addModule(
            new URL("./audio-capture-worklet.js", import.meta.url).href,
          );
          const worklet = new AudioWorkletNode(runtime.context, "meeting-copilot-audio-capture", {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            outputChannelCount: [1],
          });
          worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
            consumeAudio(runtime, new Float32Array(event.data));
          };
          processor = worklet;
        } catch {
          processor = null;
        }
      }
      if (!processor) {
        const scriptProcessor = runtime.context.createScriptProcessor(4_096, 1, 1);
        scriptProcessor.onaudioprocess = (event) => {
          consumeAudio(runtime, new Float32Array(event.inputBuffer.getChannelData(0)));
        };
        processor = scriptProcessor;
      }
      runtime.processor = processor;
      runtime.source.connect(processor);
      processor.connect(runtime.monitor);
      runtime.monitor.connect(runtime.context.destination);

      const socket = new WebSocket(buildWebSocketUrl(
        normalizedMeetingId,
        optionsRef.current.asrBaseUrl,
      ));
      runtime.socket = socket;
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        if (runtime.disposed) return;
        runtime.resolveOpen();
        flushQueue(runtime);
        updateState({
          phase: runtime.paused ? "paused" : "starting",
          statusMessage: "麦克风已连接，正在准备实时识别",
        });
      };
      socket.onmessage = (message) => {
        if (runtime.disposed || typeof message.data !== "string") return;
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(message.data) as Record<string, unknown>;
        } catch {
          return;
        }
        const eventType = String(event.event_type ?? "");
        if (eventType === "asr_starting") {
          updateState({ asrReady: false, phase: "starting", statusMessage: "正在准备实时识别" });
          return;
        }
        if (eventType === "asr_ready") {
          const ready = event.ready === true;
          updateState({
            asrReady: ready,
            phase: runtime.paused ? "paused" : ready ? "recording" : "starting",
            statusMessage: ready ? "实时识别已就绪" : "实时识别仍在准备",
          });
          return;
        }
        if (eventType === "partial" || eventType === "final") {
          const text = String(event.normalized_text ?? event.text ?? "").trim();
          const segmentId = String(event.segment_id ?? "").trim();
          if (text && segmentId) {
            updateState({
              activePartial: {
                segmentId,
                text,
                startedAtMs: typeof event.start_ms === "number" ? event.start_ms : null,
                updatedAtMs: Date.now(),
              },
              statusMessage: eventType === "final" ? "文字已确认，正在同步" : "正在实时识别",
            });
          }
          return;
        }
        if (eventType === "end_of_stream") {
          runtime.resolveTerminal();
          return;
        }
        if (eventType === "error" || eventType === "provider_error") {
          const messageText = String(event.message ?? event.detail ?? "实时识别服务异常");
          updateState({ phase: "error", error: messageText, statusMessage: messageText });
        }
      };
      socket.onerror = () => {
        if (!runtime.stopping && !runtime.disposed) {
          updateState({ statusMessage: "录音连接异常，正在等待连接关闭" });
        }
      };
      socket.onclose = () => {
        runtime.resolveOpen();
        runtime.resolveTerminal();
        if (runtime.stopping || runtime.disposed) return;
        closeAudio(runtime);
        updateState({
          phase: "error",
          asrReady: false,
          inputLevel: 0,
          error: "录音连接已中断，请重新开始",
          statusMessage: "录音连接已中断",
        });
      };

      updateState({ phase: "connecting", elapsedMs: 0, statusMessage: "正在连接录音服务" });
    } catch (error) {
      dispose(runtime);
      if (runtimeRef.current === runtime) runtimeRef.current = null;
      const message = errorMessage(error);
      updateState({ phase: "error", error: message, statusMessage: message, inputLevel: 0 });
      throw error;
    }
  }, [closeAudio, consumeAudio, dispose, flushQueue, updateState]);

  const togglePause = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.disposed || runtime.stopping) return;
    const nowMs = Date.now();
    if (runtime.paused) {
      if (runtime.pausedAtMs !== null) runtime.totalPausedMs += nowMs - runtime.pausedAtMs;
      runtime.pausedAtMs = null;
      runtime.paused = false;
      updateState({ phase: runtime.socket?.readyState === WebSocket.OPEN ? "recording" : "connecting", statusMessage: "已继续录音" });
      return;
    }
    runtime.paused = true;
    runtime.pausedAtMs = nowMs;
    updateState({ phase: "paused", inputLevel: 0, elapsedMs: elapsed(runtime, nowMs), statusMessage: "录音已暂停" });
  }, [updateState]);

  const end = useCallback(async () => {
    const runtime = runtimeRef.current;
    if (!runtime || runtime.disposed || runtime.stopping) return;
    runtime.stopping = true;
    runtime.paused = true;
    runtime.stoppedAtMs = Date.now();
    updateState({
      phase: "stopping",
      inputLevel: 0,
      elapsedMs: elapsed(runtime),
      statusMessage: "正在保存录音并整理最终文字",
    });

    if (runtime.framer) {
      for (const frame of runtime.framer.flush()) queueOrSend(runtime, frame);
    }
    closeAudio(runtime);

    if (runtime.socket?.readyState === WebSocket.CONNECTING) {
      await Promise.race([
        runtime.openPromise,
        new Promise<void>((resolve) => window.setTimeout(resolve, 2_000)),
      ]);
    }
    if (runtime.socket?.readyState === WebSocket.OPEN) {
      flushQueue(runtime, true);
      runtime.socket.send("END");
      await Promise.race([
        runtime.terminalPromise,
        new Promise<void>((resolve) => window.setTimeout(
          resolve,
          optionsRef.current.endTimeoutMs ?? DEFAULT_END_TIMEOUT_MS,
        )),
      ]);
    }

    if (runtime.socket && runtime.socket.readyState < WebSocket.CLOSING) {
      try { runtime.socket.close(1000, "meeting_ended"); } catch { /* already closed */ }
    }
    runtime.disposed = true;
    runtime.resolveTerminal();
    if (runtimeRef.current === runtime) runtimeRef.current = null;
    updateState({
      phase: "ended",
      asrReady: false,
      inputLevel: 0,
      error: null,
      statusMessage: "录音已安全封存，正在整理",
    });
  }, [closeAudio, flushQueue, queueOrSend, updateState]);

  const acknowledgeCommitted = useCallback((segmentIds: Iterable<string>) => {
    const committed = new Set(segmentIds);
    setState((current) => current.activePartial && committed.has(current.activePartial.segmentId)
      ? { ...current, activePartial: null }
      : current);
  }, []);

  useEffect(() => {
    if (!runtimeRef.current || !["recording", "paused", "starting", "connecting"].includes(state.phase)) return;
    const timer = window.setInterval(() => {
      const runtime = runtimeRef.current;
      if (runtime && !runtime.disposed) updateState({ elapsedMs: elapsed(runtime) });
    }, 500);
    return () => window.clearInterval(timer);
  }, [state.phase, updateState]);

  useEffect(() => {
    const cleanup = () => {
      const runtime = runtimeRef.current;
      if (!runtime) return;
      dispose(runtime);
      runtimeRef.current = null;
    };
    window.addEventListener("beforeunload", cleanup);
    return () => {
      window.removeEventListener("beforeunload", cleanup);
      cleanup();
    };
  }, [dispose]);

  return { state, start, togglePause, end, acknowledgeCommitted };
}
