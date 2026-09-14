import { act, renderHook, waitFor } from "@testing-library/react";
import { useBrowserMicrophone } from "./useBrowserMicrophone";

class FakeTrack {
  stop = vi.fn();
}

class FakeMediaStream {
  readonly track = new FakeTrack();
  getTracks() {
    return [this.track] as unknown as MediaStreamTrack[];
  }
}

class FakeNode {
  connect = vi.fn();
  disconnect = vi.fn();
}

class FakeScriptProcessor extends FakeNode {
  onaudioprocess: ((event: AudioProcessingEvent) => void) | null = null;

  emit(samples: Float32Array) {
    this.onaudioprocess?.({
      inputBuffer: { getChannelData: () => samples },
    } as unknown as AudioProcessingEvent);
  }
}

class FakeAudioContext {
  static latest: FakeAudioContext | null = null;
  static resumeImplementation: (() => Promise<void>) | null = null;
  static workletImplementation: (() => Promise<void>) | null = null;
  readonly sampleRate = 16_000;
  readonly destination = new FakeNode() as unknown as AudioDestinationNode;
  readonly audioWorklet = {
    addModule: vi.fn(() => FakeAudioContext.workletImplementation?.()
      ?? Promise.reject(new Error("worklet unavailable"))),
  };
  readonly source = new FakeNode();
  readonly processor = new FakeScriptProcessor();
  readonly gain = Object.assign(new FakeNode(), { gain: { value: 1 } });
  resume = vi.fn(() => FakeAudioContext.resumeImplementation?.() ?? Promise.resolve());
  close = vi.fn().mockResolvedValue(undefined);
  createMediaStreamSource = vi.fn(() => this.source as unknown as MediaStreamAudioSourceNode);
  createScriptProcessor = vi.fn(() => this.processor as unknown as ScriptProcessorNode);
  createGain = vi.fn(() => this.gain as unknown as GainNode);

  constructor() {
    FakeAudioContext.latest = this;
  }
}

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static latest: FakeWebSocket | null = null;

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  bufferedAmount = 0;
  binaryType: BinaryType = "blob";
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  send = vi.fn();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeWebSocket.latest = this;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  message(payload: Record<string, unknown>) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }

  close = vi.fn(() => {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  });
}

async function flushMicrotasks(turns = 10) {
  await act(async () => {
    for (let index = 0; index < turns; index += 1) await Promise.resolve();
  });
}

async function startAndOpen(start: () => Promise<void>): Promise<FakeWebSocket> {
  let startPromise!: Promise<void>;
  act(() => {
    startPromise = start();
  });
  await flushMicrotasks();
  const socket = FakeWebSocket.latest;
  expect(socket).not.toBeNull();
  act(() => socket!.open());
  await act(async () => startPromise);
  return socket!;
}

describe("useBrowserMicrophone", () => {
  const stream = new FakeMediaStream();

  beforeEach(() => {
    vi.clearAllMocks();
    FakeAudioContext.latest = null;
    FakeAudioContext.resumeImplementation = null;
    FakeAudioContext.workletImplementation = null;
    FakeWebSocket.latest = null;
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    Object.defineProperty(window, "AudioWorkletNode", { configurable: true, value: undefined });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("streams real Float32 microphone frames and projects one live partial", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ asrBaseUrl: "http://127.0.0.1:8765" }));

    const socket = await startAndOpen(() => result.current.start("rec_test"));
    expect(socket.url).toBe("ws://127.0.0.1:8765/live/asr/stream/ws/rec_test?audio_source=browser_live_mic&capture_epoch=1&transport_handshake=1");

    act(() => {
      socket.message({ event_type: "asr_starting" });
      socket.message({ event_type: "asr_ready", ready: true });
      FakeAudioContext.latest!.processor.emit(new Float32Array(9_600).fill(0.2));
      socket.message({
        event_type: "partial",
        segment_id: "segment-1",
        normalized_text: "正在讨论发布计划",
      });
    });

    await waitFor(() => expect(result.current.state.phase).toBe("recording"));
    expect(socket.send).toHaveBeenCalledWith(expect.any(ArrayBuffer));
    expect(result.current.state.activePartial?.text).toBe("正在讨论发布计划");

    act(() => socket.message({
      event_type: "final",
      segment_id: "segment-1",
      normalized_text: "正在讨论发布计划。",
    }));
    expect(result.current.state.activePartial?.text).toBe("正在讨论发布计划。");

    act(() => result.current.acknowledgeCommitted(["segment-1"]));
    expect(result.current.state.activePartial).toBeNull();
  });

  it("accepts the explicit transport handshake before the browser open callback", async () => {
    const { result } = renderHook(() => useBrowserMicrophone());
    let startPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start("rec_transport_handshake");
    });
    await flushMicrotasks();
    const socket = FakeWebSocket.latest!;
    expect(socket).not.toBeNull();

    act(() => socket.message({ event_type: "asr_transport_ready", ready: true }));
    await act(async () => startPromise);

    expect(result.current.state.error).toBeNull();
    expect(result.current.state.phase).toBe("starting");
  });

  it("rejects a remote ASR target before requesting microphone permission or opening a socket", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ asrBaseUrl: "https://api.example.test" }));

    await expect(act(async () => result.current.start("rec_private"))).rejects.toThrow(
      "浏览器麦克风只能连接本机会议服务",
    );

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(FakeWebSocket.latest).toBeNull();
  });

  it("times out a browser permission request, stops a late stream, and can retry", async () => {
    vi.useFakeTimers();
    let resolveLateStream!: (value: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockReturnValueOnce(
      new Promise<MediaStream>((resolve) => {
        resolveLateStream = resolve;
      }),
    );
    const { result } = renderHook(() => useBrowserMicrophone({ permissionTimeoutMs: 100 }));
    let startPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start("rec_permission_timeout");
    });
    const rejection = expect(startPromise).rejects.toThrow("麦克风权限请求超时");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    await rejection;
    expect(result.current.state.phase).toBe("error");

    const lateTrack = new FakeTrack();
    await act(async () => {
      resolveLateStream({ getTracks: () => [lateTrack] } as unknown as MediaStream);
      await Promise.resolve();
    });
    expect(lateTrack.stop).toHaveBeenCalledOnce();

    const retrySocket = await startAndOpen(() => result.current.start("rec_permission_retry"));
    expect(retrySocket.url).toContain("/rec_permission_retry?");
    expect(result.current.state.phase).toBe("starting");
    expect(result.current.state.error).toBeNull();
  });

  it("times out a suspended AudioContext instead of hanging microphone startup", async () => {
    vi.useFakeTimers();
    FakeAudioContext.resumeImplementation = () => new Promise<void>(() => {});
    Object.defineProperty(FakeAudioContext.prototype, "state", {
      configurable: true,
      get: () => "suspended",
    });
    const { result } = renderHook(() => useBrowserMicrophone({ audioContextTimeoutMs: 100 }));
    let startPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start("rec_audio_context_timeout");
    });
    const rejection = expect(startPromise).rejects.toThrow("麦克风音频上下文启动超时");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    await rejection;
    expect(result.current.state.phase).toBe("error");
    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest?.close).toHaveBeenCalled();
  });

  it("falls back to ScriptProcessor when the AudioWorklet module load stays pending", async () => {
    vi.useFakeTimers();
    FakeAudioContext.workletImplementation = () => new Promise<void>(() => {});
    vi.stubGlobal("AudioWorkletNode", class FakeAudioWorkletNode {});
    const { result } = renderHook(() => useBrowserMicrophone({
      audioWorkletLoadTimeoutMs: 100,
      socketOpenTimeoutMs: 1_000,
    }));
    let startPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start("rec_worklet_timeout");
    });
    await flushMicrotasks();
    expect(result.current.state.statusMessage).toBe("麦克风权限已通过，正在初始化音频采集");
    expect(FakeAudioContext.latest?.audioWorklet.addModule).toHaveBeenCalledOnce();
    expect(FakeWebSocket.latest).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    await flushMicrotasks();
    expect(FakeAudioContext.latest?.createScriptProcessor).toHaveBeenCalledOnce();
    const socket = FakeWebSocket.latest!;
    expect(socket).not.toBeNull();
    act(() => socket.open());
    await act(async () => startPromise);

    expect(result.current.state.phase).toBe("starting");
    expect(stream.track.stop).not.toHaveBeenCalled();
  });

  it("fails and releases microphone resources when the first WebSocket never opens", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useBrowserMicrophone({ socketOpenTimeoutMs: 100 }));
    let startPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start("rec_socket_timeout");
    });
    const rejection = expect(startPromise).rejects.toThrow("实时识别服务连接超时");
    await flushMicrotasks();
    const socket = FakeWebSocket.latest!;
    expect(socket).not.toBeNull();
    expect(socket.readyState).toBe(FakeWebSocket.CONNECTING);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    await rejection;

    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toContain("实时识别服务连接超时");
    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest?.close).toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it("reconnects when a WebSocket opens but never reports ASR ready", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useBrowserMicrophone({
      asrReadyTimeoutMs: 100,
      socketOpenTimeoutMs: 100,
    }));
    const firstSocket = await startAndOpen(() => result.current.start("rec_asr_ready_timeout"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    expect(firstSocket.close).toHaveBeenCalledWith(4001, "asr_ready_timeout");
    expect(result.current.state.phase).toBe("reconnecting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    const resumedSocket = FakeWebSocket.latest!;
    expect(resumedSocket).not.toBe(firstSocket);
    expect(resumedSocket.url).toContain("capture_epoch=2");
    expect(resumedSocket.readyState).toBe(FakeWebSocket.CONNECTING);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(101);
    });
    expect(resumedSocket.close).toHaveBeenCalledWith(4002, "reconnect_open_timeout");
    expect(result.current.state.phase).toBe("reconnecting");
  });

  it("flushes the pending tail on pause and ends with END without a second pause flush", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ endTimeoutMs: 500 }));
    const socket = await startAndOpen(() => result.current.start("rec_stop"));

    act(() => {
      FakeAudioContext.latest!.processor.emit(new Float32Array(800).fill(0.2));
      result.current.togglePause();
    });
    expect(result.current.state.phase).toBe("paused");
    expect(socket.send.mock.calls.map(([payload]) => payload)).toEqual([
      expect.any(ArrayBuffer),
      "FLUSH",
    ]);
    act(() => result.current.togglePause());
    expect(result.current.state.phase).toBe("recording");

    let ending!: Promise<void>;
    act(() => {
      ending = result.current.end();
    });
    await waitFor(() => expect(socket.send).toHaveBeenCalledWith("END"));
    act(() => socket.message({ event_type: "end_of_stream" }));
    await act(async () => ending);

    expect(socket.send.mock.calls.filter(([payload]) => payload === "FLUSH")).toHaveLength(1);
    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest!.close).toHaveBeenCalled();
    expect(result.current.state.phase).toBe("ended");
  });

  it("keeps microphone capture alive and reconnects the same meeting after a socket interruption", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useBrowserMicrophone({ socketOpenTimeoutMs: 100 }));
    const firstSocket = await startAndOpen(() => result.current.start("rec_resume"));
    act(() => vi.advanceTimersByTime(101));
    expect(result.current.state.phase).toBe("starting");
    act(() => firstSocket.close());

    expect(result.current.state.phase).toBe("reconnecting");
    expect(result.current.state.statusMessage).toContain("自动恢复");
    expect(stream.track.stop).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(500));
    const resumedSocket = FakeWebSocket.latest!;
    expect(resumedSocket).not.toBe(firstSocket);
    expect(firstSocket.url).toContain("capture_epoch=1");
    expect(resumedSocket.url).toContain("capture_epoch=2");
    act(() => {
      resumedSocket.open();
      resumedSocket.message({ event_type: "asr_ready", ready: true });
    });
    expect(result.current.state.phase).toBe("recording");
    expect(result.current.state.error).toBeNull();
  });

  it("stops tracks and the AudioContext when the page unmounts", async () => {
    const { result, unmount } = renderHook(() => useBrowserMicrophone());
    await startAndOpen(() => result.current.start("rec_unmount"));

    unmount();

    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest!.close).toHaveBeenCalled();
  });
});
