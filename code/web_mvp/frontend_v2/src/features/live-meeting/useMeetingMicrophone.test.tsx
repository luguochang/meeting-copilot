import { act, renderHook, waitFor } from "@testing-library/react";
import { useMeetingMicrophone } from "./useMeetingMicrophone";

interface NativeResponse {
  command_status: string;
  status: string;
  helper_present: boolean;
  captures_audio: boolean;
  errors: string[];
}

function response(overrides: Partial<NativeResponse> = {}): NativeResponse {
  return {
    command_status: "ok",
    status: "recording",
    helper_present: true,
    captures_audio: true,
    errors: [],
    ...overrides,
  };
}

describe("useMeetingMicrophone", () => {
  afterEach(() => {
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
  });

  it("uses Tauri native capture for the complete lifecycle when the packaged helper exists", async () => {
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "mic_adapter_prepare") return response({ captures_audio: false });
      if (command === "mic_adapter_stop") return response({ status: "stopped", captures_audio: false });
      if (command === "mic_adapter_pause") return response({ status: "paused", captures_audio: false });
      if (command === "mic_adapter_resume") return response({ captures_audio: false });
      return response();
    });
    const invoke = <T,>(command: string, args?: Record<string, unknown>) => (
      invokeMock(command, args) as Promise<T>
    );
    window.__TAURI__ = { core: { invoke } };
    const getUserMedia = vi.fn().mockRejectedValue(new Error("browser capture must not run"));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    const { result } = renderHook(() => useMeetingMicrophone());
    await act(async () => result.current.start("meeting_native"));

    expect(getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_prepare", undefined);
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_start", { sessionId: "meeting_native" });
    expect(result.current.state.phase).toBe("recording");
    expect(result.current.state.inputLevelAvailable).toBe(false);

    act(() => result.current.togglePause());
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("mic_adapter_pause", undefined));
    expect(result.current.state.phase).toBe("paused");

    act(() => result.current.togglePause());
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith("mic_adapter_resume", undefined));
    expect(result.current.state.phase).toBe("recording");

    await act(async () => result.current.end());
    expect(invokeMock).toHaveBeenCalledWith("mic_adapter_stop", { sessionId: "meeting_native" });
    expect(result.current.state.phase).toBe("ended");
  });

  it("keeps the browser capture fallback when no Tauri bridge is available", async () => {
    const getUserMedia = vi.fn().mockRejectedValue(new Error("browser fallback selected"));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const { result } = renderHook(() => useMeetingMicrophone());

    let capturedError: unknown;
    await act(async () => {
      try {
        await result.current.start("meeting_browser");
      } catch (error) {
        capturedError = error;
      }
    });
    expect(capturedError).toEqual(new Error("browser fallback selected"));
    expect(getUserMedia).toHaveBeenCalledOnce();
    await waitFor(() => expect(result.current.state.phase).toBe("error"));
  });
});
