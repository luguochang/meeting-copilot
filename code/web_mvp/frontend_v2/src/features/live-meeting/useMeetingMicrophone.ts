import { useCallback, useRef, useState } from "react";
import {
  type BrowserMicrophoneController,
  type MeetingCaptureStartOptions,
  useBrowserMicrophone,
} from "./useBrowserMicrophone";
import { useNativeMicrophone } from "./useNativeMicrophone";
import { useNativeSystemAudio } from "./useNativeSystemAudio";
import { useNativeDualTrack } from "./useNativeDualTrack";

interface UseMeetingMicrophoneOptions {
  asrBaseUrl?: string;
}

type CaptureMode = "browser" | "native" | "system_audio" | "dual_track";

export function useMeetingMicrophone(
  options: UseMeetingMicrophoneOptions = {},
): BrowserMicrophoneController {
  const browser = useBrowserMicrophone({ asrBaseUrl: options.asrBaseUrl });
  const native = useNativeMicrophone();
  const systemAudio = useNativeSystemAudio();
  const dualTrack = useNativeDualTrack();
  const [mode, setMode] = useState<CaptureMode>("browser");
  const modeRef = useRef<CaptureMode>("browser");

  const start = useCallback(async (
    meetingId: string,
    startOptions: MeetingCaptureStartOptions = {},
  ) => {
    if (startOptions.inputSource === "dual_track") {
      modeRef.current = "dual_track";
      setMode("dual_track");
      await dualTrack.start(meetingId, startOptions);
      return;
    }
    if (startOptions.inputSource === "system_audio") {
      modeRef.current = "system_audio";
      setMode("system_audio");
      await systemAudio.start(meetingId, startOptions);
      return;
    }
    const nextMode: CaptureMode = await native.probe() ? "native" : "browser";
    modeRef.current = nextMode;
    setMode(nextMode);
    await (nextMode === "native" ? native : browser).start(meetingId, startOptions);
  }, [browser, dualTrack, native, systemAudio]);

  const togglePause = useCallback(() => {
    if (modeRef.current === "system_audio" || modeRef.current === "dual_track") return;
    (modeRef.current === "native" ? native : browser).togglePause();
  }, [browser, native]);

  const end = useCallback(async () => {
    const controller = modeRef.current === "dual_track"
      ? dualTrack
      : modeRef.current === "system_audio" ? systemAudio
      : modeRef.current === "native" ? native : browser;
    await controller.end();
  }, [browser, dualTrack, native, systemAudio]);

  const acknowledgeCommitted = useCallback((segmentIds: Iterable<string>) => {
    const controller = modeRef.current === "dual_track"
      ? dualTrack
      : modeRef.current === "system_audio" ? systemAudio
      : modeRef.current === "native" ? native : browser;
    controller.acknowledgeCommitted(segmentIds);
  }, [browser, dualTrack, native, systemAudio]);

  const active = mode === "dual_track"
    ? dualTrack
    : mode === "system_audio" ? systemAudio : mode === "native" ? native : browser;
  return {
    state: active.state,
    inputSource: active.inputSource ?? "microphone",
    supportsPause: active.supportsPause !== false,
    start,
    togglePause,
    end,
    acknowledgeCommitted,
  };
}
