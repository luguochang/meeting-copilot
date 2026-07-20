export interface NativeCaptureHealthFields {
  transport_ready?: boolean;
  pcm_seen?: boolean;
  audible_pcm_seen?: boolean;
  asr_ready?: boolean;
}

export interface NativeCaptureHealth {
  transportReady: boolean;
  pcmSeen: boolean;
  audiblePcmSeen: boolean;
  asrReady: boolean;
}

export const EMPTY_NATIVE_CAPTURE_HEALTH: NativeCaptureHealth = {
  transportReady: false,
  pcmSeen: false,
  audiblePcmSeen: false,
  asrReady: false,
};

export function readNativeCaptureHealth(
  source: NativeCaptureHealthFields,
  previous: NativeCaptureHealth = EMPTY_NATIVE_CAPTURE_HEALTH,
): NativeCaptureHealth {
  return {
    transportReady: typeof source.transport_ready === "boolean"
      ? source.transport_ready
      : previous.transportReady,
    pcmSeen: source.pcm_seen === true || previous.pcmSeen,
    audiblePcmSeen: source.audible_pcm_seen === true || previous.audiblePcmSeen,
    asrReady: typeof source.asr_ready === "boolean"
      ? source.asr_ready
      : previous.asrReady,
  };
}

export function nativeCaptureStartupFailure(
  source: NativeCaptureHealthFields,
  label = "系统音频",
): string | null {
  if (source.transport_ready !== true) {
    return source.transport_ready === false
      ? `${label}传输未就绪，已阻止开始会议`
      : `${label}未返回传输就绪状态，已阻止开始会议`;
  }
  if (source.pcm_seen !== true) {
    return source.pcm_seen === false
      ? `${label}未收到 PCM 数据，已阻止开始会议`
      : `${label}未返回 PCM 接收状态，已阻止开始会议`;
  }
  return null;
}

export function nativeCaptureRuntimeFailure(
  source: NativeCaptureHealthFields,
  label = "系统音频",
): string | null {
  if (source.transport_ready === false) return `${label}传输已中断`;
  if (source.pcm_seen === false) return `${label}PCM 数据流已中断`;
  return null;
}

export function nativeCaptureStatusMessage(
  health: NativeCaptureHealth,
  label = "系统音频",
): string {
  if (!health.transportReady) return `${label}传输尚未就绪`;
  if (!health.pcmSeen) return `${label}已连接，正在等待 PCM 数据`;
  if (!health.audiblePcmSeen) return "已连接但当前无系统声音";
  if (!health.asrReady) return `${label}已连接，正在准备实时识别`;
  return `${label}和实时识别已就绪`;
}
