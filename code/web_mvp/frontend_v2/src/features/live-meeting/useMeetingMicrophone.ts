import { useCallback, useRef, useState } from "react";
import {
  type BrowserMicrophoneController,
  useBrowserMicrophone,
} from "./useBrowserMicrophone";
import { useNativeMicrophone } from "./useNativeMicrophone";

interface UseMeetingMicrophoneOptions {
  asrBaseUrl?: string;
}

type CaptureMode = "browser" | "native";

export function useMeetingMicrophone(
  options: UseMeetingMicrophoneOptions = {},
): BrowserMicrophoneController {
  const browser = useBrowserMicrophone({ asrBaseUrl: options.asrBaseUrl });
  const native = useNativeMicrophone();
  const [mode, setMode] = useState<CaptureMode>("browser");
  const modeRef = useRef<CaptureMode>("browser");

  const start = useCallback(async (meetingId: string) => {
    const nextMode: CaptureMode = await native.probe() ? "native" : "browser";
    modeRef.current = nextMode;
    setMode(nextMode);
    await (nextMode === "native" ? native : browser).start(meetingId);
  }, [browser, native]);

  const togglePause = useCallback(() => {
    (modeRef.current === "native" ? native : browser).togglePause();
  }, [browser, native]);

  const end = useCallback(async () => {
    await (modeRef.current === "native" ? native : browser).end();
  }, [browser, native]);

  const acknowledgeCommitted = useCallback((segmentIds: Iterable<string>) => {
    (modeRef.current === "native" ? native : browser).acknowledgeCommitted(segmentIds);
  }, [browser, native]);

  const active = mode === "native" ? native : browser;
  return { state: active.state, start, togglePause, end, acknowledgeCommitted };
}
