import {
  EMPTY_NATIVE_CAPTURE_HEALTH,
  nativeCaptureRuntimeFailure,
  nativeCaptureStartupFailure,
  nativeCaptureStatusMessage,
  readNativeCaptureHealth,
} from "./nativeCaptureHealth";

describe("native capture layered health", () => {
  it("keeps transport, PCM, audible PCM and ASR as independent fields", () => {
    expect(readNativeCaptureHealth({
      transport_ready: true,
      pcm_seen: true,
      audible_pcm_seen: false,
      asr_ready: false,
    })).toEqual({
      transportReady: true,
      pcmSeen: true,
      audiblePcmSeen: false,
      asrReady: false,
    });
    expect(nativeCaptureStatusMessage({
      transportReady: true,
      pcmSeen: true,
      audiblePcmSeen: false,
      asrReady: false,
    })).toBe("已连接但当前无系统声音");
  });

  it("never infers layered readiness from a legacy aggregate field", () => {
    expect(readNativeCaptureHealth({ captures_audio: true } as never)).toEqual(EMPTY_NATIVE_CAPTURE_HEALTH);
    expect(nativeCaptureStartupFailure({})).toBe("系统音频未返回传输就绪状态，已阻止开始会议");
  });

  it("distinguishes startup gates from a runtime transport interruption", () => {
    expect(nativeCaptureStartupFailure({ transport_ready: false, pcm_seen: false }))
      .toBe("系统音频传输未就绪，已阻止开始会议");
    expect(nativeCaptureStartupFailure({ transport_ready: true, pcm_seen: false }))
      .toBe("系统音频未收到 PCM 数据，已阻止开始会议");
    expect(nativeCaptureRuntimeFailure({ transport_ready: false, pcm_seen: true }))
      .toBe("系统音频传输已中断");
  });

  it("preserves cumulative PCM evidence while ASR remains a current state", () => {
    const previous = {
      transportReady: true,
      pcmSeen: true,
      audiblePcmSeen: true,
      asrReady: true,
    };
    expect(readNativeCaptureHealth({
      transport_ready: true,
      pcm_seen: false,
      audible_pcm_seen: false,
      asr_ready: false,
    }, previous)).toEqual({
      transportReady: true,
      pcmSeen: true,
      audiblePcmSeen: true,
      asrReady: false,
    });
  });
});
