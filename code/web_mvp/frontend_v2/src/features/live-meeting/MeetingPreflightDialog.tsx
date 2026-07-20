import {
  AlertTriangle,
  AudioLines,
  CheckCircle2,
  Copy,
  HardDrive,
  LoaderCircle,
  Mic,
  MonitorSpeaker,
  ShieldCheck,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchProviderStatus } from "../../api/client";
import {
  parseProviderStatus,
  reconcileProviderStatus,
  type DesktopProviderStatusLike,
  type ProviderStatus,
} from "../../api/schema";
import { resolveTauriInvoke } from "../../desktop/tauri";
import { dualTrackStatus, isDualTrackCapabilityAvailable } from "../../desktop/dualTrackAdapter";
import type { NativeCaptureHealthFields } from "../../desktop/nativeCaptureHealth";
import type { MeetingInputSource, MeetingPreparationInput } from "../../domain/events";

interface MeetingPreflightDialogProps {
  open: boolean;
  busy: boolean;
  onCancel(): void;
  onStart(preparation: MeetingPreparationInput): Promise<void>;
}

interface StoragePreflight {
  allowed: boolean;
  reason_code?: string | null;
  writable_capacity_bytes?: number;
  estimated_meeting_bytes?: number;
}

interface ProviderHealth {
  llm?: {
    configured?: boolean;
    provider?: string;
    model?: string;
  };
  asr?: {
    realtime_asr_available?: boolean;
    realtime_providers?: string[];
  };
  cost_policy?: {
    remote_asr_default_enabled?: boolean;
    raw_audio_uploaded_by_default?: boolean;
  };
}

interface NativeMicProbeResponse {
  command_status?: string;
  probe_status?: "receiving_audio" | "silent" | "permission_denied" | "no_device" | "audible" | "device_unavailable" | "error";
  sampled?: boolean;
  rms?: number;
  peak_rms?: number;
  level?: number;
  duration_ms?: number;
  helper_present?: boolean;
  errors?: string[];
}

interface NativeSystemAudioPrepareResponse extends NativeCaptureHealthFields {
  command_status?: string;
  status?: string;
  source?: string;
  helper_present?: boolean;
  fallback_source?: string | null;
  errors?: string[];
}

interface BrowserInputProbe {
  rms: number;
  peakRms: number;
  level: number;
  durationMs: number;
}

type MicrophoneProbeStatus = "permission_denied" | "no_device" | "silent" | "receiving_audio";
type InputCheck = "idle" | "checking" | MicrophoneProbeStatus | "error";
type SystemAudioCheck = "idle" | "checking" | "available" | "failed";
type AiConnectionState = "idle" | "connecting" | "error";

interface ProviderConfigSyncResponse {
  command_status?: string;
  runtime_synced?: boolean;
  errors?: string[];
}

const MEETING_NOTICE = "本次会议将录音并实时转写，用于生成会议建议和会后纪要。原始音频默认仅保存在本机。";
const MICROPHONE_PROBE_DURATION_MS = 2_500;
const AUDIBLE_RMS_THRESHOLD = 0.002;

function parseHotwords(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[,，;；\n]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || item.length > 64) return false;
      const key = item.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 50);
}

function formatBytes(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "容量未知";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(value / 1024 ** 2)} MB`;
}

async function sampleBrowserInputLevel(
  stream: MediaStream,
  onFrame?: (rms: number, level: number) => void,
): Promise<BrowserInputProbe> {
  const AudioContextCtor = window.AudioContext
    ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) throw new Error("当前环境无法读取麦克风电平");
  let context: AudioContext | null = null;
  try {
    context = new AudioContextCtor();
    if (context.state === "suspended") await context.resume();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1_024;
    const source = context.createMediaStreamSource(stream);
    source.connect(analyser);
    const values = new Float32Array(analyser.fftSize);
    let sumSquares = 0;
    let sampleCount = 0;
    let peakRms = 0;
    const startedAt = Date.now();
    const deadline = startedAt + MICROPHONE_PROBE_DURATION_MS;
    while (Date.now() < deadline) {
      analyser.getFloatTimeDomainData(values);
      let frameSquares = 0;
      for (const value of values) frameSquares += value * value;
      const frameRms = Math.sqrt(frameSquares / values.length);
      sumSquares += frameSquares;
      sampleCount += values.length;
      peakRms = Math.max(peakRms, frameRms);
      onFrame?.(frameRms, Math.min(1, frameRms * 6));
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    if (sampleCount === 0) throw new Error("麦克风没有返回可采样的音频");
    return {
      rms: Math.sqrt(sumSquares / sampleCount),
      peakRms,
      level: Math.min(1, peakRms * 6),
      durationMs: Date.now() - startedAt,
    };
  } catch {
    throw new Error("无法读取麦克风输入电平");
  } finally {
    await context?.close().catch(() => undefined);
  }
}

function browserMicrophoneError(error: unknown): { status: MicrophoneProbeStatus | "error"; message: string } {
  const name = error instanceof DOMException
    ? error.name
    : typeof error === "object" && error && "name" in error
      ? String(error.name)
      : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return {
      status: "permission_denied",
      message: "麦克风权限被拒绝，请在系统或浏览器设置中允许访问",
    };
  }
  if (["NotFoundError", "DevicesNotFoundError", "NotReadableError", "TrackStartError"].includes(name)) {
    return { status: "no_device", message: "没有可用的麦克风设备" };
  }
  return { status: "error", message: error instanceof Error ? error.message : "麦克风检查失败" };
}

function normalizeNativeProbeStatus(response: NativeMicProbeResponse): MicrophoneProbeStatus | "error" {
  if (response.probe_status === "audible") return "receiving_audio";
  if (response.probe_status === "device_unavailable") return "no_device";
  if (["receiving_audio", "silent", "permission_denied", "no_device"].includes(String(response.probe_status))) {
    return response.probe_status as MicrophoneProbeStatus;
  }
  return "error";
}

function nativeProbeError(response: NativeMicProbeResponse): string {
  const status = normalizeNativeProbeStatus(response);
  if (status === "permission_denied") {
    return "麦克风权限被拒绝，请在系统设置中允许访问";
  }
  if (status === "no_device") return "没有可用的麦克风设备";
  return response.errors?.filter(Boolean).join("；") || "麦克风检查失败";
}

function providerStatusFromHealth(health: ProviderHealth): ProviderStatus {
  try {
    return parseProviderStatus(health.llm);
  } catch {
    const configured = health.llm?.configured === true;
    return {
      configured,
      runtime_synced: configured,
      probe_status: "not_run",
      model: health.llm?.model ?? null,
    };
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok || !body) throw new Error(`预检请求失败（${response.status}）`);
  return body as T;
}

export function MeetingPreflightDialog({
  open,
  busy,
  onCancel,
  onStart,
}: MeetingPreflightDialogProps) {
  const [storage, setStorage] = useState<StoragePreflight | null>(null);
  const [providers, setProviders] = useState<ProviderHealth | null>(null);
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [inputSource, setInputSource] = useState<MeetingInputSource>("microphone");
  const [dualTrackAvailable, setDualTrackAvailable] = useState(false);
  const [systemAudioCheck, setSystemAudioCheck] = useState<SystemAudioCheck>("idle");
  const [hotwordsText, setHotwordsText] = useState("");
  const [title, setTitle] = useState("");
  const [noticeAcknowledged, setNoticeAcknowledged] = useState(false);
  const [inputCheck, setInputCheck] = useState<InputCheck>("idle");
  const [inputLevel, setInputLevel] = useState(0);
  const [inputRms, setInputRms] = useState(0);
  const [inputLevelAvailable, setInputLevelAvailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [aiConnectionState, setAiConnectionState] = useState<AiConnectionState>("idle");
  const [aiConnectionError, setAiConnectionError] = useState<string | null>(null);
  const nativeDesktop = Boolean(resolveTauriInvoke());

  const refreshPreflight = useCallback(async (isCancelled: () => boolean = () => false) => {
    const invoke = resolveTauriInvoke();
    const [storageResult, providerResult, runtimeStatus, desktopStatus, deviceResult, dualTrackResult] = await Promise.all([
      fetch("/v2/storage/preflight").then((response) => responseJson<StoragePreflight>(response)),
      fetch("/providers/health").then((response) => responseJson<ProviderHealth>(response)),
      fetchProviderStatus().catch(() => null),
      invoke
        ? invoke<DesktopProviderStatusLike>("provider_config_status").then((value) => (
          typeof value.configured === "boolean" ? value : null
        )).catch(() => null)
        : Promise.resolve(null),
      nativeDesktop
        ? Promise.resolve([] as MediaDeviceInfo[])
        : navigator.mediaDevices?.enumerateDevices?.() ?? Promise.resolve([] as MediaDeviceInfo[]),
      invoke
        ? dualTrackStatus().catch(() => null)
        : Promise.resolve(null),
    ]);
    if (isCancelled()) return null;
    setStorage(storageResult);
    setProviders(providerResult);
    const status = reconcileProviderStatus(
      desktopStatus,
      runtimeStatus ?? providerStatusFromHealth(providerResult),
    );
    setProviderStatus(status);
    const microphones = deviceResult.filter((device) => device.kind === "audioinput");
    setDevices(microphones);
    setDeviceId((current) => current || microphones[0]?.deviceId || "");
    setDualTrackAvailable(Boolean(dualTrackResult && isDualTrackCapabilityAvailable(dualTrackResult)));
    return status;
  }, [nativeDesktop]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMessage(null);
    setInputSource("microphone");
    setDualTrackAvailable(false);
    setSystemAudioCheck("idle");
    setInputCheck("idle");
    setProviderStatus(null);
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
    setAiConnectionState("idle");
    setAiConnectionError(null);
    void refreshPreflight(() => cancelled).catch((preflightError: unknown) => {
      if (!cancelled) {
        setError(preflightError instanceof Error ? preflightError.message : "会前检查失败");
      }
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, refreshPreflight]);

  const selectedDevice = useMemo(
    () => devices.find((device) => device.deviceId === deviceId) ?? null,
    [deviceId, devices],
  );

  if (!open) return null;

  const selectMicrophone = () => {
    if (busy || systemAudioCheck === "checking") return;
    setInputSource("microphone");
    setError(null);
    setMessage(null);
  };

  const selectSystemAudio = async () => {
    if (busy || systemAudioCheck === "checking") return;
    const invoke = resolveTauriInvoke();
    if (!invoke) return;
    setSystemAudioCheck("checking");
    setError(null);
    setMessage(null);
    try {
      const response = await invoke<NativeSystemAudioPrepareResponse>("system_audio_adapter_prepare", undefined);
      if (response.command_status !== "ok"
        || response.source !== "system_audio"
        || response.helper_present !== true
        || response.fallback_source !== null) {
        throw new Error(response.errors?.filter(Boolean).join("；") || "系统音频采集不可用");
      }
      setInputSource("system_audio");
      setSystemAudioCheck("available");
      setInputCheck("idle");
      setInputLevel(0);
      setInputRms(0);
      setInputLevelAvailable(false);
    } catch (selectionError) {
      setInputSource("microphone");
      setSystemAudioCheck("failed");
      const detail = selectionError instanceof Error ? selectionError.message : "完整桌面客户端未安装";
      setError(`系统音频采集不可用：${detail}`);
    }
  };

  const selectDualTrack = () => {
    if (busy || !dualTrackAvailable || systemAudioCheck === "checking") return;
    setInputSource("dual_track");
    setError(null);
    setMessage(null);
    setInputCheck("idle");
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
  };

  const checkInput = async () => {
    if (inputSource !== "microphone" || inputCheck === "checking") return;
    setInputCheck("checking");
    setError(null);
    setMessage(null);
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
    let failureStatus: InputCheck = "error";
    try {
      const invoke = resolveTauriInvoke();
      if (invoke) {
        const response = await invoke<NativeMicProbeResponse>("mic_adapter_probe");
        const probeStatus = normalizeNativeProbeStatus(response);
        if (probeStatus === "permission_denied" || probeStatus === "no_device") {
          failureStatus = probeStatus;
          throw new Error(nativeProbeError(response));
        }
        if (response.command_status !== "ok"
          || response.sampled !== true
          || typeof response.rms !== "number"
          || typeof response.level !== "number"
          || typeof response.duration_ms !== "number"
          || !Number.isFinite(response.rms)
          || !Number.isFinite(response.level)
          || response.duration_ms < 2_000
          || response.duration_ms > 3_000) {
          throw new Error(nativeProbeError(response));
        }
        setInputRms(Math.max(0, Math.min(1, response.rms)));
        setInputLevel(Math.max(0, Math.min(1, response.level)));
        setInputLevelAvailable(true);
        if (probeStatus === "silent") {
          setInputCheck("silent");
          setError("未检测到声音，请检查麦克风是否静音");
          return;
        }
        if (probeStatus !== "receiving_audio") throw new Error(nativeProbeError(response));
      } else {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境不支持麦克风访问");
        let stream: MediaStream | null = null;
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: deviceId ? { deviceId: { exact: deviceId } } : true,
            video: false,
          });
          const activeTrack = stream.getAudioTracks().find((track) => track.readyState === "live");
          if (!activeTrack) throw new DOMException("没有可用的麦克风设备", "NotFoundError");
          const probe = await sampleBrowserInputLevel(stream, (rms, level) => {
            setInputRms(rms);
            setInputLevel(level);
            setInputLevelAvailable(true);
          });
          setInputRms(probe.rms);
          setInputLevel(probe.level);
          setInputLevelAvailable(true);
          if (probe.peakRms < AUDIBLE_RMS_THRESHOLD) {
            setInputCheck("silent");
            setError("未检测到声音，请检查麦克风是否静音");
            return;
          }
        } finally {
          stream?.getTracks().forEach((track) => track.stop());
        }
      }
      setInputCheck("receiving_audio");
      setMessage("正常收到声音，麦克风可用");
    } catch (inputError) {
      if (resolveTauriInvoke()) {
        setInputCheck(failureStatus);
        setError(inputError instanceof Error ? inputError.message : "麦克风检查失败");
      } else {
        const failure = browserMicrophoneError(inputError);
        setInputCheck(failure.status);
        setError(failure.message);
      }
    }
  };

  const copyNotice = async () => {
    try {
      await navigator.clipboard.writeText(MEETING_NOTICE);
      setMessage("会议告知文案已复制");
    } catch {
      setError("无法复制会议告知文案");
    }
  };

  const connectAi = async () => {
    if (aiConnectionState === "connecting" || busy) return;
    const invoke = resolveTauriInvoke();
    if (!invoke) return;
    setAiConnectionState("connecting");
    setAiConnectionError(null);
    setError(null);
    setMessage(null);
    try {
      const response = await invoke<ProviderConfigSyncResponse>("provider_config_sync");
      if (response.command_status !== "ok" || response.runtime_synced !== true) {
        throw new Error(response.errors?.filter(Boolean).join("；") || "AI 配置连接失败");
      }
      const refreshedProviders = await refreshPreflight();
      if (refreshedProviders?.runtime_synced !== true) {
        throw new Error("AI 已同步，但后端尚未确认配置，请重试");
      }
      setAiConnectionState("idle");
      setMessage(refreshedProviders.probe_status === "succeeded" ? "AI 已连接" : "AI 运行时已同步");
    } catch (connectionError) {
      setAiConnectionState("error");
      setAiConnectionError(connectionError instanceof Error ? connectionError.message : "AI 配置连接失败");
    }
  };

  const submit = async () => {
    if (!noticeAcknowledged || storage?.allowed !== true || busy) return;
    setError(null);
    try {
      await onStart({
        ...(title.trim() ? { title: title.trim() } : {}),
        hotwords: parseHotwords(hotwordsText),
        inputSource,
        inputDeviceId: inputSource !== "microphone" || nativeDesktop ? null : deviceId || null,
        inputDeviceName: inputSource === "dual_track"
          ? "麦克风 + 系统音频"
          : inputSource === "system_audio" ? "系统音频"
          : nativeDesktop ? "系统默认麦克风" : selectedDevice?.label || null,
        noticeAcknowledged: true,
      });
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "声音采集启动失败");
    }
  };

  const localAsrReady = providers?.asr?.realtime_asr_available === true;
  const llmReady = providerStatus?.runtime_synced === true;
  const llmConnected = llmReady && providerStatus?.probe_status === "succeeded";
  const llmStatusText = llmConnected
    ? `AI 已连接 · ${providerStatus?.model ?? "默认模型"}`
    : providerStatus?.probe_status === "failed"
      ? `${providerStatus.model ?? "AI"} 连接测试失败，会议仍可录音和转写`
      : llmReady
        ? `${providerStatus?.model ?? "AI"} 已同步，连接尚未测试`
        : providerStatus?.configured
          ? `${providerStatus.model ?? "AI"} 已保存，AI 待连接`
          : "AI 未配置，会议仍可录音和转写";

  return (
    <div className="drawer-layer meeting-preflight-layer" role="presentation">
      <button className="drawer-scrim" aria-label="关闭会前检查" onClick={onCancel} disabled={busy} />
      <section className="meeting-preflight-dialog" role="dialog" aria-modal="true" aria-labelledby="meeting-preflight-title">
        <header className="drawer-header">
          <div>
            <span className="eyebrow">会前检查</span>
            <h2 id="meeting-preflight-title">准备开始会议</h2>
          </div>
          <button className="icon-button" type="button" onClick={onCancel} disabled={busy} aria-label="关闭会前检查" title="关闭">
            <X size={18} />
          </button>
        </header>

        <div className="meeting-preflight-body">
          {loading ? (
            <p className="preflight-loading" role="status"><LoaderCircle className="spin" size={17} />正在检查本地服务</p>
          ) : (
            <div className="preflight-status-list" aria-label="运行条件">
              <div className={storage?.allowed ? "preflight-status preflight-status--ready" : "preflight-status preflight-status--error"}>
                <HardDrive size={17} />
                <span>
                  {storage?.allowed
                    ? `本地可写 ${formatBytes(storage.writable_capacity_bytes)} · 本场预计 ${formatBytes(storage.estimated_meeting_bytes)}`
                    : "本地空间不足或不可用"}
                </span>
              </div>
              <div className={localAsrReady ? "preflight-status preflight-status--ready" : "preflight-status preflight-status--error"}>
                {localAsrReady ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
                <span>{localAsrReady ? "本地中文实时识别可用" : "本地实时识别不可用"}</span>
              </div>
              <div className={llmConnected ? "preflight-status preflight-status--ready" : "preflight-status preflight-status--warning"}>
                {llmConnected ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
                <span>{llmStatusText}</span>
              </div>
            </div>
          )}

          {nativeDesktop && !loading && !llmReady ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => void connectAi()}
              disabled={busy || aiConnectionState === "connecting"}
            >
              {aiConnectionState === "connecting" ? <LoaderCircle className="spin" size={15} /> : null}
              {aiConnectionState === "connecting"
                ? "正在连接 AI"
                : aiConnectionState === "error"
                  ? "重试连接 AI"
                  : "连接 AI"}
            </button>
          ) : null}

          {nativeDesktop ? (
            <div className="preflight-source-field">
              <span className="preflight-source-label">会议声音来源</span>
              <div
                className={`preflight-source-segmented${dualTrackAvailable ? " has-dual-track" : ""}`}
                role="radiogroup"
                aria-label="会议声音来源"
              >
                <button
                  className={inputSource === "microphone" ? "is-selected" : ""}
                  type="button"
                  role="radio"
                  aria-checked={inputSource === "microphone"}
                  onClick={selectMicrophone}
                  disabled={busy || systemAudioCheck === "checking"}
                >
                  <Mic size={16} />
                  麦克风
                </button>
                <button
                  className={inputSource === "system_audio" ? "is-selected" : ""}
                  type="button"
                  role="radio"
                  aria-checked={inputSource === "system_audio"}
                  onClick={() => void selectSystemAudio()}
                  disabled={busy || systemAudioCheck === "checking"}
                >
                  {systemAudioCheck === "checking"
                    ? <LoaderCircle className="spin" size={16} />
                    : <MonitorSpeaker size={16} />}
                  系统音频
                </button>
                {dualTrackAvailable ? (
                  <button
                    className={inputSource === "dual_track" ? "is-selected" : ""}
                    type="button"
                    role="radio"
                    aria-checked={inputSource === "dual_track"}
                    onClick={selectDualTrack}
                    disabled={busy || systemAudioCheck === "checking"}
                  >
                    <AudioLines size={16} />
                    双轨
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}

          {inputSource === "microphone" ? (
            <div className="preflight-field-group">
              <div className="preflight-field-heading">
                <div><Mic size={17} /><strong>麦克风</strong></div>
                <button className="secondary-button" type="button" onClick={() => void checkInput()} disabled={busy || inputCheck === "checking"}>
                  {inputCheck === "checking" ? <LoaderCircle className="spin" size={15} /> : null}
                  {inputCheck === "receiving_audio" ? "重新检查" : "检查麦克风"}
                </button>
              </div>
              {devices.length ? (
                <label>
                  <span className="sr-only">输入设备</span>
                  <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={busy}>
                    {devices.map((device, index) => (
                      <option key={device.deviceId || `microphone-${index}`} value={device.deviceId}>
                        {device.label || `麦克风 ${index + 1}`}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p className="preflight-help">使用系统默认麦克风，点击检查时会申请权限。</p>
              )}
              <div
                className="preflight-input-meter"
                data-probe-status={inputCheck}
                aria-label={inputLevelAvailable ? `RMS 输入电平 ${(inputRms * 100).toFixed(1)}%` : "检查后显示 RMS 输入电平"}
              >
                <span>RMS 电平</span>
                <span className="preflight-input-meter-track"><span style={{ transform: `scaleX(${inputLevel})` }} /></span>
                <small>{inputLevelAvailable ? `${(inputRms * 100).toFixed(1)}%` : inputCheck === "checking" ? "采样中" : "尚未检查"}</small>
              </div>
            </div>
          ) : (
            <div className="preflight-system-audio-summary">
              {inputSource === "dual_track" ? <AudioLines size={18} /> : <MonitorSpeaker size={18} />}
              <div>
                <strong>{inputSource === "dual_track" ? "双轨" : "系统音频"}</strong>
                <p>
                  {inputSource === "dual_track"
                    ? "同时采集麦克风和系统音频；任一轨失败都会中止本次采集。"
                    : "将采集本机播放的会议声音，不会同时启动麦克风。"}
                </p>
                <small>开始会议时 macOS 会请求“屏幕与系统音频录制”权限。</small>
                <div className="preflight-native-health" aria-label="系统音频启动检查项">
                  <span><small>传输</small><strong>开始时验证</strong></span>
                  <span><small>PCM</small><strong>开始时验证</strong></span>
                  <span><small>声音</small><strong>启动后检测</strong></span>
                  <span><small>识别</small><strong>独立就绪</strong></span>
                </div>
              </div>
            </div>
          )}

          <label className="preflight-title-field">
            <span>会议名称 <small>可选</small></span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：支付服务上线评审"
              maxLength={200}
              disabled={busy}
            />
          </label>

          <label className="preflight-hotwords-field">
            <span>本次会议技术词</span>
            <textarea
              value={hotwordsText}
              onChange={(event) => setHotwordsText(event.target.value)}
              placeholder="例如：checkout-service、P99、订单中台"
              rows={3}
              maxLength={2_500}
              disabled={busy}
            />
            <small>逗号或换行分隔，仅用于本次会议的本地识别。</small>
          </label>

          <div className="meeting-notice-row">
            <ShieldCheck size={18} />
            <p>{MEETING_NOTICE}</p>
            <button className="icon-button icon-button--small" type="button" onClick={() => void copyNotice()} aria-label="复制会议告知文案" title="复制会议告知文案">
              <Copy size={15} />
            </button>
          </div>
          <label className="preflight-consent">
            <input
              type="checkbox"
              checked={noticeAcknowledged}
              onChange={(event) => setNoticeAcknowledged(event.target.checked)}
              disabled={busy}
            />
            <span>我已告知参会者并确认可以录音</span>
          </label>

          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {aiConnectionError ? <p className="inline-error" role="alert">{aiConnectionError}</p> : null}
          {message ? <p className="inline-success" role="status">{message}</p> : null}
        </div>

        <footer className="meeting-preflight-actions">
          <button className="secondary-button" type="button" onClick={onCancel} disabled={busy}>取消</button>
          <button
            className="primary-button"
            type="button"
            onClick={() => void submit()}
            disabled={busy
              || loading
              || !noticeAcknowledged
              || storage?.allowed !== true
              || !localAsrReady
              || (inputSource === "system_audio" && systemAudioCheck !== "available")
              || (inputSource === "dual_track" && !dualTrackAvailable)}
          >
            {busy
              ? <LoaderCircle className="spin" size={16} />
              : inputSource === "dual_track"
                ? <AudioLines size={16} />
                : inputSource === "system_audio" ? <MonitorSpeaker size={16} /> : <Mic size={16} />}
            {busy ? "正在启动" : "开始会议"}
          </button>
        </footer>
      </section>
    </div>
  );
}
