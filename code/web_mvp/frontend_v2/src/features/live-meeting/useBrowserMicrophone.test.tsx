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
  readonly sampleRate = 16_000;
  readonly destination = new FakeNode() as unknown as AudioDestinationNode;
  readonly audioWorklet = { addModule: vi.fn().mockRejectedValue(new Error("worklet unavailable")) };
  readonly source = new FakeNode();
  readonly processor = new FakeScriptProcessor();
  readonly gain = Object.assign(new FakeNode(), { gain: { value: 1 } });
  resume = vi.fn().mockResolvedValue(undefined);
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

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
}

describe("useBrowserMicrophone", () => {
  const stream = new FakeMediaStream();

  beforeEach(() => {
    FakeAudioContext.latest = null;
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
    vi.unstubAllGlobals();
  });

  it("streams real Float32 microphone frames and projects one live partial", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ asrBaseUrl: "http://127.0.0.1:8765" }));

    await act(async () => result.current.start("rec_test"));
    const socket = FakeWebSocket.latest!;
    expect(socket.url).toBe("ws://127.0.0.1:8765/live/asr/stream/ws/rec_test?audio_source=browser_live_mic");

    act(() => socket.open());
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

  it("rejects a remote ASR target before requesting microphone permission or opening a socket", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ asrBaseUrl: "https://api.example.test" }));

    await expect(act(async () => result.current.start("rec_private"))).rejects.toThrow(
      "浏览器麦克风只能连接本机会议服务",
    );

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(FakeWebSocket.latest).toBeNull();
  });

  it("pauses capture and ends with END before releasing browser audio resources", async () => {
    const { result } = renderHook(() => useBrowserMicrophone({ endTimeoutMs: 500 }));
    await act(async () => result.current.start("rec_stop"));
    const socket = FakeWebSocket.latest!;
    act(() => socket.open());

    act(() => result.current.togglePause());
    expect(result.current.state.phase).toBe("paused");
    act(() => result.current.togglePause());
    expect(result.current.state.phase).toBe("recording");

    let ending!: Promise<void>;
    act(() => {
      ending = result.current.end();
    });
    await waitFor(() => expect(socket.send).toHaveBeenCalledWith("END"));
    act(() => socket.message({ event_type: "end_of_stream" }));
    await act(async () => ending);

    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest!.close).toHaveBeenCalled();
    expect(result.current.state.phase).toBe("ended");
  });

  it("stops tracks and the AudioContext when the page unmounts", async () => {
    const { result, unmount } = renderHook(() => useBrowserMicrophone());
    await act(async () => result.current.start("rec_unmount"));

    unmount();

    expect(stream.track.stop).toHaveBeenCalled();
    expect(FakeAudioContext.latest!.close).toHaveBeenCalled();
  });
});
