import { EMPTY_KNOWLEDGE_SELECTION, type KnowledgeApi, type MeetingKnowledgeSelection } from "../../api/knowledge";
import { KnowledgeScopePicker } from "../knowledge-library/KnowledgeScopePicker";
import { MeetingAttachments } from "./MeetingAttachments";
import type { MaterialsApi, MaterialSet } from "../../api/materials";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import { MeetingProjectBrief } from "../project-workspace/MeetingProjectBrief";
import { PreparationBriefPanel } from "../project-workspace/PreparationBriefPanel";
import type { MemoryApi, MemoryPreview } from "../../api/memory";
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchProviderStatus, fetchAsrRuntimeStatus, fetchMeetingCorrectionCapability, prepareLocalAsrRefiner } from "../../api/client";
import { motionAwareScrollBehavior } from "../../app/motion";
import {
  parseProviderStatus,
  reconcileProviderStatus,
  type DesktopProviderStatusLike,
  type RealtimeProviderCircuitStatus,
  type ProviderStatus,
  type AsrRuntimeStatus,
  type MeetingCorrectionCapability,
  asrPreparationBlocker,
} from "../../api/schema";
import { resolveTauriInvoke } from "../../desktop/tauri";
import { dualTrackStatus, isDualTrackCapabilityAvailable } from "../../desktop/dualTrackAdapter";
import type { NativeCaptureHealthFields } from "../../desktop/nativeCaptureHealth";
import type { LiveAnalysisProfile, MeetingCorrectionMode, MeetingInputSource, MeetingPreparationInput } from "../../domain/events";
import { useModalDialog } from "../../components/useModalDialog";

interface MeetingPreflightDialogProps {
  materialsApi?: MaterialsApi;
  materialSet?: MaterialSet | null;
  onMaterialSetChange?(set: MaterialSet | null): void;
  memoryApi?: MemoryApi;
  projectApi?: ProjectOutcomesApi;
  open: boolean;
  busy: boolean;
  initialLiveAnalysisProfile?: LiveAnalysisProfile;
  initialCorrectionMode?: MeetingCorrectionMode;
  liveAnalysisProfileLoading?: boolean;
  liveAnalysisProfileReadKey?: number;
  liveAnalysisProfileError?: string;
  liveAnalysisProfileLocked?: boolean;
  onRetryLiveAnalysisProfile?(): void;
  onCancel(): void;
  knowledgeApi?: KnowledgeApi;
  onStart(preparation: MeetingPreparationInput, knowledgeSelection?: MeetingKnowledgeSelection): Promise<void>;
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
    runtime_synced?: boolean;
    probe_status?: "not_run" | "probing" | "succeeded" | "failed";
    provider?: string;
    model?: string;
    operational?: boolean | null;
    realtime_ready?: boolean | null;
    probe_latency_ms?: number | null;
    probe_usage?: {
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
    } | null;
    realtime_cutoff_ms?: number | null;
  };
  asr?: {
    realtime_asr_available?: boolean;
    file_asr_available?: boolean;
    realtime_providers?: string[];
  };
  cost_policy?: {
    remote_asr_default_enabled?: boolean;
    raw_audio_uploaded_by_default?: boolean;
  };
  realtime_circuit?: RealtimeProviderCircuitStatus | null;
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

interface SelectableAudioDevice {
  deviceId: string;
  label: string;
  isDefault: boolean;
}

interface WindowsAudioDeviceResponse {
  devices?: Array<{
    endpoint_id?: string;
    display_name?: string;
    is_default?: boolean;
  }>;
}

interface PreflightDevices {
  microphones: SelectableAudioDevice[];
  systemAudio: SelectableAudioDevice[];
}

interface BrowserInputProbe {
  rms: number;
  peakRms: number;
  level: number;
  durationMs: number;
}

type MicrophoneProbeStatus = "permission_denied" | "no_device" | "silent" | "receiving_audio";
type InputCheck = "idle" | "checking" | MicrophoneProbeStatus | "error";
type BrowserInputCheckStage = "idle" | "permission" | "sampling";
type SystemAudioCheck = "idle" | "checking" | "available" | "failed";
type AiConnectionState = "idle" | "connecting" | "error";

interface ProviderConfigSyncResponse {
  command_status?: string;
  runtime_synced?: boolean;
  errors?: string[];
}

const MEETING_NOTICE = "本次会议将录音并实时转写，用于生成会议建议和会后纪要。原始音频默认仅保存在本机。";
const MICROPHONE_PROBE_DURATION_MS = 2_500;
const MICROPHONE_PERMISSION_TIMEOUT_MS = 8_000;
const MICROPHONE_AUDIO_CONTEXT_TIMEOUT_MS = 1_500;
const MICROPHONE_AUDIO_CONTEXT_CLOSE_TIMEOUT_MS = 500;
const PREFLIGHT_REQUEST_TIMEOUT_MS = 8_000;
const NATIVE_CAPTURE_REQUEST_TIMEOUT_MS = 5_000;
const AUDIBLE_RMS_THRESHOLD = 0.002;

const CORRECTION_MODE_LABELS: Record<MeetingCorrectionMode, string> = {
  local_and_llm: "本地精修＋大模型校对", llm_only: "仅大模型校对",
};
function correctionFallbackReason(reason: string | null): string {
  if (reason === "asr_capacity_unavailable" || reason === "asr_resource_guard_triggered" || reason === "worker_out_of_memory") return "可用内存未达到本地精修要求";
  if (reason === "asr_resource_probe_failed") return "暂时无法确认本机可用内存";
  if (reason === "asr_cold_start_busy") return "另一个本地识别模型正在加载";
  if (reason?.includes("policy") || reason?.includes("disabled")) return "当前服务未启用本地精修";
  return "本地精修组件暂不可用";
}
function memoryAmount(bytes: number | null): string {
  return bytes === null ? "未取得数值" : `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

type MeetingPresetId = NonNullable<MeetingPreparationInput["presetId"]>;
type MeetingOutputFormat = NonNullable<MeetingPreparationInput["outputFormat"]>;
type SuggestionPolicy = NonNullable<MeetingPreparationInput["proactiveSuggestionPolicy"]>;

const PRESET_DEFAULTS: Record<MeetingPresetId, {
  goal: string;
  focusPoints: string;
  outputFormat: MeetingOutputFormat;
  coachSummary: string;
}> = {
  general: { goal: "", focusPoints: "", outputFormat: "standard", coachSummary: "低频检查问题回应、承诺条件、目标偏离、前后矛盾和表达清晰度。" },
  decision: { goal: "形成可执行且有依据的决策", focusPoints: "决策结论、备选方案、决策依据、反对意见", outputFormat: "decision_log", coachSummary: "在决策落定前检查备选方案、依据、反对意见、负责人和成功标准。" },
  project: { goal: "同步项目进度并明确下一步", focusPoints: "进展、阻塞、负责人、截止时间", outputFormat: "action_plan", coachSummary: "关注阻塞、依赖、负责人、截止时间和验收条件是否闭环。" },
  interview: { goal: "完整记录访谈洞察和待验证假设", focusPoints: "用户原话、痛点、需求、待验证假设", outputFormat: "brief", coachSummary: "用中立追问补齐具体行为、场景、频率、影响和替代方案。" },
  brainstorm: { goal: "发散方案并收敛可验证的下一步", focusPoints: "新想法、约束、争议、实验方案", outputFormat: "standard", coachSummary: "不过早收敛，在合适时机把想法转成假设、最小实验和成功信号。" },
};

function parseFocusPoints(value: string): string[] {
  return value.split(/[,，、;；\n]+/).map((item) => item.trim()).filter(Boolean).slice(0, 12);
}

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

function operationWithTimeout<T>(
  operation: () => PromiseLike<T>,
  timeoutMs: number,
  timeoutError: () => Error,
): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const settle = (handler: () => void) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      handler();
    };
    const timer = window.setTimeout(() => settle(() => reject(timeoutError())), timeoutMs);
    let request: PromiseLike<T>;
    try {
      request = operation();
    } catch (error) {
      settle(() => reject(error));
      return;
    }
    Promise.resolve(request).then(
      (value) => settle(() => resolve(value)),
      (error) => settle(() => reject(error)),
    );
  });
}

async function sampleBrowserInputLevel(
  stream: MediaStream,
  onFrame?: (rms: number, level: number) => void,
  signal?: AbortSignal,
): Promise<BrowserInputProbe> {
  const AudioContextCtor = window.AudioContext
    ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) throw new Error("当前环境无法读取麦克风电平");
  let context: AudioContext | null = null;
  try {
    context = new AudioContextCtor();
    if (context.state === "suspended") {
      await operationWithTimeout(
        () => context!.resume(),
        MICROPHONE_AUDIO_CONTEXT_TIMEOUT_MS,
        () => {
          const timeout = new Error("麦克风音频上下文启动超时，请检查浏览器页面权限后重试");
          timeout.name = "AudioContextTimeoutError";
          return timeout;
        },
      );
    }
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
      if (signal?.aborted) throw new DOMException("麦克风检查已取消", "AbortError");
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
  } catch (error) {
    // Keep bounded startup failures actionable; generic analyser failures use
    // the compact probe error shown by the dialog.
    if (error instanceof Error && error.name === "AudioContextTimeoutError") throw error;
    throw new Error("无法读取麦克风输入电平");
  } finally {
    if (context) {
      await operationWithTimeout(
        () => context!.close(),
        MICROPHONE_AUDIO_CONTEXT_CLOSE_TIMEOUT_MS,
        () => new Error("麦克风音频上下文关闭超时"),
      ).catch(() => undefined);
    }
  }
}

/**
 * Browser permission prompts can remain pending while a Mac is locked or the
 * browser has lost its capture permission. Bound the request and stop a late
 * stream so a failed preflight cannot leave the microphone open invisibly.
 */
function requestBrowserMicrophone(
  constraints: MediaStreamConstraints,
  signal?: AbortSignal,
): Promise<MediaStream> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let request: Promise<MediaStream>;
    const stopLateStream = (stream: MediaStream) => {
      stream.getTracks().forEach((track) => track.stop());
    };
    const settle = (handler: () => void) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", abortRequest);
      handler();
    };
    const abortRequest = () => settle(() => reject(new DOMException("麦克风检查已取消", "AbortError")));
    const timer = window.setTimeout(() => {
      settle(() => reject(new DOMException(
        "麦克风权限请求超时，请解锁设备或在浏览器设置中允许访问后重试",
        "TimeoutError",
      )));
    }, MICROPHONE_PERMISSION_TIMEOUT_MS);
    if (signal?.aborted) {
      abortRequest();
      return;
    }
    signal?.addEventListener("abort", abortRequest, { once: true });
    try {
      request = navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
      settle(() => reject(error));
      return;
    }
    request.then(
      (stream) => {
        if (settled) {
          stopLateStream(stream);
          return;
        }
        settle(() => resolve(stream));
      },
      (error) => settle(() => reject(error)),
    );
  });
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
  if (name === "TimeoutError") {
    return {
      status: "error",
      message: "麦克风权限请求超时，请解锁设备或在浏览器设置中允许访问后重试",
    };
  }
  if (name === "AudioContextTimeoutError") {
    return {
      status: "error",
      message: error instanceof Error
        ? error.message
        : "麦克风音频上下文启动超时，请检查浏览器页面权限后重试",
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
    return parseProviderStatus({
      ...health.llm,
      realtime_circuit: health.realtime_circuit,
    });
  } catch {
    const configured = health.llm?.configured === true;
    const probeFailed = health.llm?.probe_status === "failed";
    const rawCutoff = health.llm?.realtime_cutoff_ms;
    const realtimeCutoffMs = typeof rawCutoff === "number"
      && Number.isFinite(rawCutoff)
      && Number.isInteger(rawCutoff)
      && rawCutoff > 0
      ? rawCutoff
      : 2_500;
    return {
      configured,
      runtime_synced: health.llm?.runtime_synced === true,
      // A malformed success payload is not evidence of a successful probe.
      // Preserve an explicit failure, but force all other parse failures to
      // the unknown/pending state.
      probe_status: probeFailed ? "failed" : "not_run",
      model: health.llm?.model ?? null,
      realtime_model: health.llm?.model ?? null,
      operational: probeFailed ? false : null,
      realtime_ready: probeFailed ? false : null,
      probe_latency_ms: null,
      probe_usage: null,
      realtime_cutoff_ms: realtimeCutoffMs,
      realtime_circuit: health.realtime_circuit ?? null,
    };
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok || !body) {
    if (response.status === 404) {
      throw new Error("当前页面连接的会议服务版本不匹配，请打开正在运行的本地会议服务后重试");
    }
    if (response.status >= 500) {
      throw new Error(`会议服务暂时不可用（${response.status}），请确认本地服务仍在运行后重试`);
    }
    throw new Error(`预检请求失败（${response.status}）`);
  }
  return body as T;
}

function preflightErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "TypeError" || /failed to fetch|networkerror|load failed/i.test(error.message)) {
      return "无法连接本地会议服务，请确认当前页面地址对应的会议服务已启动后重试";
    }
    return error.message;
  }
  return "会前检查失败";
}

function windowsAudioDevices(response: WindowsAudioDeviceResponse | null): SelectableAudioDevice[] {
  if (!Array.isArray(response?.devices)) return [];
  return response.devices.flatMap((device) => {
    const deviceId = String(device.endpoint_id ?? "").trim();
    if (!deviceId) return [];
    return [{
      deviceId,
      label: String(device.display_name ?? "").trim() || "Windows audio device",
      isDefault: device.is_default === true,
    }];
  });
}

export function MeetingPreflightDialog({
  materialsApi, materialSet, onMaterialSetChange,
  memoryApi,
  projectApi,
  knowledgeApi,
  open,
  busy,
  initialLiveAnalysisProfile = "participation",
  initialCorrectionMode = "local_and_llm",
  liveAnalysisProfileLoading = false,
  liveAnalysisProfileReadKey = 0,
  liveAnalysisProfileError = "",
  liveAnalysisProfileLocked = false,
  onRetryLiveAnalysisProfile,
  onCancel,
  onStart,
}: MeetingPreflightDialogProps) {
  const [knowledgeSelection, setKnowledgeSelection] = useState<MeetingKnowledgeSelection>(EMPTY_KNOWLEDGE_SELECTION);
  const [storage, setStorage] = useState<StoragePreflight | null>(null);
  const [providers, setProviders] = useState<ProviderHealth | null>(null);
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null);
  const [asrRuntime, setAsrRuntime] = useState<AsrRuntimeStatus | null>(null);
  const [correctionMode, setCorrectionMode] = useState<MeetingCorrectionMode>(initialCorrectionMode);
  const [correctionCapability, setCorrectionCapability] = useState<MeetingCorrectionCapability | null>(null);
  const [correctionChecking, setCorrectionChecking] = useState(false);
  const [asrPreparing, setAsrPreparing] = useState(false);
  const [asrChecking, setAsrChecking] = useState(false);
  const [asrPreparationError, setAsrPreparationError] = useState<string | null>(null);
  const asrObservation = useRef<{ generation: number; controller: AbortController | null }>({ generation: 0, controller: null });
  const [devices, setDevices] = useState<SelectableAudioDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [systemAudioDevices, setSystemAudioDevices] = useState<SelectableAudioDevice[]>([]);
  const [systemAudioDeviceId, setSystemAudioDeviceId] = useState("");
  const [inputSource, setInputSource] = useState<MeetingInputSource>("microphone");
  const [dualTrackAvailable, setDualTrackAvailable] = useState(false);
  const [systemAudioCheck, setSystemAudioCheck] = useState<SystemAudioCheck>("idle");
  const [hotwordsText, setHotwordsText] = useState("");
  const [title, setTitle] = useState("");
  const [presetId, setPresetId] = useState<MeetingPresetId>("general");
  const [liveAnalysisProfile, setLiveAnalysisProfile] = useState<LiveAnalysisProfile>(initialLiveAnalysisProfile);
  const [meetingGoal, setMeetingGoal] = useState("");
  const [participantRole, setParticipantRole] = useState("");
  const [focusPointsText, setFocusPointsText] = useState("");
  const [outputFormat, setOutputFormat] = useState<MeetingOutputFormat>("standard");
  const [suggestionPolicy, setSuggestionPolicy] = useState<SuggestionPolicy>("low_frequency");
  const [policyTouched, setPolicyTouched] = useState(false);
  const [memoryPreview, setMemoryPreview] = useState<MemoryPreview | null>(null);
  const [memoryPreviewError, setMemoryPreviewError] = useState<string | null>(null);
  const [memoryPreviewLoading, setMemoryPreviewLoading] = useState(false);
  const [materialRecoveryPending, setMaterialRecoveryPending] = useState(false);
  const [noticeAcknowledged, setNoticeAcknowledged] = useState(false);
  const [inputCheck, setInputCheck] = useState<InputCheck>("idle");
  const [browserInputCheckStage, setBrowserInputCheckStage] = useState<BrowserInputCheckStage>("idle");
  const [inputLevel, setInputLevel] = useState(0);
  const [inputRms, setInputRms] = useState(0);
  const [inputLevelAvailable, setInputLevelAvailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [aiConnectionState, setAiConnectionState] = useState<AiConnectionState>("idle");
  const [aiConnectionError, setAiConnectionError] = useState<string | null>(null);
  const inputCheckRuntimeRef = useRef<{ generation: number; controller: AbortController | null }>({
    generation: 0,
    controller: null,
  });
  const nativeDesktop = Boolean(resolveTauriInvoke());
  useEffect(() => { if (open) setPolicyTouched(false); }, [open]);
  useEffect(() => {
    if (open && !liveAnalysisProfileLoading && !liveAnalysisProfileError) setLiveAnalysisProfile(initialLiveAnalysisProfile);
  }, [initialLiveAnalysisProfile, liveAnalysisProfileError, liveAnalysisProfileLoading, liveAnalysisProfileReadKey, open]);
  useEffect(() => {
    if (open && !liveAnalysisProfileLoading && !liveAnalysisProfileError) setCorrectionMode(initialCorrectionMode);
  }, [initialCorrectionMode, liveAnalysisProfileError, liveAnalysisProfileLoading, liveAnalysisProfileReadKey, open]);
  useEffect(() => {
    if (!open || liveAnalysisProfileLoading || liveAnalysisProfileError) return;
    const controller = new AbortController();
    setCorrectionCapability(null);
    setCorrectionChecking(true);
    void fetchMeetingCorrectionCapability(correctionMode, controller.signal).then((actual) => {
      if (!controller.signal.aborted) setCorrectionCapability(actual?.requestedMode === correctionMode ? actual : null);
    }).finally(() => { if (!controller.signal.aborted) setCorrectionChecking(false); });
    return () => controller.abort();
  }, [correctionMode, liveAnalysisProfileError, liveAnalysisProfileLoading, open]);
  useEffect(() => {
    if (!open || !memoryApi || policyTouched) { setMemoryPreviewLoading(false); return; }
    const controller = new AbortController(); setMemoryPreview(null); setMemoryPreviewError(null); setMemoryPreviewLoading(true);
    void operationWithTimeout(() => memoryApi.preview(knowledgeSelection.projectId, presetId, controller.signal), 6_000, () => new Error("默认偏好读取超时，可手动选择本场设置。")).then((preview) => {
      if (controller.signal.aborted) return;
      setMemoryPreview(preview); setSuggestionPolicy(preview.preferences.suggestion_policy ?? "low_frequency");
    }).catch((e) => { if (!controller.signal.aborted) setMemoryPreviewError(e instanceof Error ? e.message : "默认偏好读取失败，可手动选择本场设置。"); }).finally(() => { if (!controller.signal.aborted) setMemoryPreviewLoading(false); });
    return () => controller.abort();
  }, [knowledgeSelection.projectId, memoryApi, open, policyTouched, presetId]);

  const cancelInputCheck = useCallback(() => {
    inputCheckRuntimeRef.current.generation += 1;
    inputCheckRuntimeRef.current.controller?.abort();
    inputCheckRuntimeRef.current.controller = null;
  }, []);
  const closeDialog = useCallback(() => {
    cancelInputCheck();
    asrObservation.current.generation += 1;
    asrObservation.current.controller?.abort();
    asrObservation.current.controller = null;
    onCancel();
  }, [cancelInputCheck, onCancel]);
  const dialogRef = useModalDialog(open, closeDialog, busy);

  const refreshPreflight = useCallback(async (isCancelled: () => boolean = () => false) => {
    const invoke = resolveTauriInvoke();
    const [storageResult, providerResult, runtimeStatus, desktopStatus, deviceResult, dualTrackResult, asrResult] = await Promise.all([
      operationWithTimeout(
        () => fetch("/v2/storage/preflight").then((response) => responseJson<StoragePreflight>(response)),
        PREFLIGHT_REQUEST_TIMEOUT_MS,
        () => new Error("本地存储检查超时，请确认会议服务正在运行后重试"),
      ),
      operationWithTimeout(
        () => fetch("/providers/health").then((response) => responseJson<ProviderHealth>(response)),
        PREFLIGHT_REQUEST_TIMEOUT_MS,
        () => new Error("AI 服务检查超时，请确认本机服务正在运行后重试"),
      ),
      operationWithTimeout(
        () => fetchProviderStatus(),
        PREFLIGHT_REQUEST_TIMEOUT_MS,
        () => new Error("AI 状态检查超时"),
      ).catch(() => null),
      invoke
        ? operationWithTimeout(
          () => invoke<DesktopProviderStatusLike>("provider_config_status"),
          NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
          () => new Error("桌面 AI 配置检查超时"),
        ).then((value) => (
          typeof value.configured === "boolean" ? value : null
        )).catch(() => null)
        : Promise.resolve(null),
      invoke
        ? Promise.all([
          operationWithTimeout(
            () => invoke<WindowsAudioDeviceResponse>("windows_audio_devices", { flow: "microphone" }),
            NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
            () => new Error("麦克风设备枚举超时"),
          ).catch(() => null),
          operationWithTimeout(
            () => invoke<WindowsAudioDeviceResponse>("windows_audio_devices", { flow: "render_loopback" }),
            NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
            () => new Error("系统音频设备枚举超时"),
          ).catch(() => null),
        ]).then(([microphones, systemAudio]): PreflightDevices => ({
          microphones: windowsAudioDevices(microphones),
          systemAudio: windowsAudioDevices(systemAudio),
        }))
        : operationWithTimeout(
          () => navigator.mediaDevices?.enumerateDevices?.() ?? Promise.resolve([] as MediaDeviceInfo[]),
          PREFLIGHT_REQUEST_TIMEOUT_MS,
          () => new Error("麦克风设备检查超时，请确认浏览器页面仍处于前台后重试"),
        )
          .then((browserDevices): PreflightDevices => ({
            microphones: browserDevices
              .filter((device) => device.kind === "audioinput")
              .map((device) => ({
                deviceId: device.deviceId,
                label: device.label,
                isDefault: device.deviceId === "default",
              })),
            systemAudio: [],
          })),
      invoke
        ? operationWithTimeout(
          () => dualTrackStatus(),
          NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
          () => new Error("双轨能力检查超时"),
        ).catch(() => null)
        : Promise.resolve(null),
      fetchAsrRuntimeStatus(),
    ]);
    if (isCancelled()) return null;
    setStorage(storageResult);
    setProviders(providerResult);
    setAsrRuntime(asrResult);
    const status = reconcileProviderStatus(
      desktopStatus,
      runtimeStatus ?? providerStatusFromHealth(providerResult),
    );
    setProviderStatus(status);
    const microphones = deviceResult.microphones;
    setDevices(microphones);
    setDeviceId((current) => current || microphones.find((device) => device.isDefault)?.deviceId
      || microphones[0]?.deviceId || "");
    setSystemAudioDevices(deviceResult.systemAudio);
    setSystemAudioDeviceId((current) => current
      || deviceResult.systemAudio.find((device) => device.isDefault)?.deviceId
      || deviceResult.systemAudio[0]?.deviceId
      || "");
    setDualTrackAvailable(Boolean(dualTrackResult && isDualTrackCapabilityAvailable(dualTrackResult)));
    return status;
  }, []);

  useEffect(() => {
    if (!open) return;
    const observation = asrObservation.current;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMessage(null);
    setInputSource("microphone");
    setDualTrackAvailable(false);
    setSystemAudioCheck("idle");
    setInputCheck("idle");
    setBrowserInputCheckStage("idle");
    setProviderStatus(null);
    setAsrRuntime(null);
    setAsrPreparing(false);
    setAsrChecking(false);
    setAsrPreparationError(null);
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
    setAiConnectionState("idle");
    setAiConnectionError(null);
    void refreshPreflight(() => cancelled).catch((preflightError: unknown) => {
      if (!cancelled) {
        setError(preflightErrorMessage(preflightError));
      }
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
      cancelInputCheck();
      observation.generation += 1;
      observation.controller?.abort();
      observation.controller = null;
    };
  }, [cancelInputCheck, open, refreshPreflight]);

  const observeAsr = async (prepare: boolean) => {
    if (busy || asrObservation.current.controller) return;
    const generation = ++asrObservation.current.generation;
    const controller = new AbortController();
    asrObservation.current.controller = controller;
    setAsrPreparing(prepare);
    setAsrChecking(!prepare);
    if (prepare) setAsrPreparationError(null);
    try {
      let prepareError: string | null = null;
      if (prepare) {
        try { await prepareLocalAsrRefiner(controller.signal); }
        catch (error) { prepareError = error instanceof Error ? error.message : "离线识别准备失败，请重新检查识别状态。"; }
      }
      if (controller.signal.aborted) return;
      const [actual, capability] = await Promise.all([
        fetchAsrRuntimeStatus(controller.signal), fetchMeetingCorrectionCapability(correctionMode, controller.signal),
      ]);
      if (!controller.signal.aborted && generation === asrObservation.current.generation) {
        setAsrRuntime(actual);
        setCorrectionCapability(capability?.requestedMode === correctionMode ? capability : null);
        setAsrPreparationError(prepareError);
      }
    } catch (error) {
      if (!controller.signal.aborted && generation === asrObservation.current.generation) {
        setAsrPreparationError(error instanceof Error ? error.message : "离线识别准备失败，请重新准备。");
      }
    } finally {
      if (generation === asrObservation.current.generation) {
        asrObservation.current.controller = null;
        setAsrPreparing(false);
        setAsrChecking(false);
      }
    }
  };

  const selectedDevice = useMemo(
    () => devices.find((device) => device.deviceId === deviceId) ?? null,
    [deviceId, devices],
  );
  const selectedSystemAudioDevice = useMemo(
    () => systemAudioDevices.find((device) => device.deviceId === systemAudioDeviceId) ?? null,
    [systemAudioDeviceId, systemAudioDevices],
  );

  if (!open) return null;

  const selectMicrophone = () => {
    if (busy || systemAudioCheck === "checking") return;
    cancelInputCheck();
    setInputSource("microphone");
    setInputCheck("idle");
    setError(null);
    setMessage(null);
    setBrowserInputCheckStage("idle");
  };

  const selectMicrophoneDevice = (nextDeviceId: string) => {
    cancelInputCheck();
    setDeviceId(nextDeviceId);
    setInputCheck("idle");
    setBrowserInputCheckStage("idle");
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
    setError(null);
    setMessage(null);
  };

  const selectSystemAudio = async () => {
    if (busy || systemAudioCheck === "checking") return;
    const invoke = resolveTauriInvoke();
    if (!invoke) return;
    cancelInputCheck();
    setInputCheck("idle");
    setBrowserInputCheckStage("idle");
    setSystemAudioCheck("checking");
    setError(null);
    setMessage(null);
    try {
      const response = await operationWithTimeout(
        () => invoke<NativeSystemAudioPrepareResponse>("system_audio_adapter_prepare", undefined),
        NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
        () => new Error("系统音频采集检查超时，请确认桌面权限后重试"),
      );
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
    cancelInputCheck();
    setInputSource("dual_track");
    setError(null);
    setMessage(null);
    setInputCheck("idle");
    setBrowserInputCheckStage("idle");
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
  };

  const checkInput = async () => {
    if (inputSource !== "microphone" || inputCheck === "checking") return;
    cancelInputCheck();
    const generation = inputCheckRuntimeRef.current.generation;
    const controller = new AbortController();
    inputCheckRuntimeRef.current.controller = controller;
    const isCurrent = () => inputCheckRuntimeRef.current.generation === generation
      && inputCheckRuntimeRef.current.controller === controller
      && !controller.signal.aborted;
    setInputCheck("checking");
    setBrowserInputCheckStage(nativeDesktop ? "idle" : "permission");
    setError(null);
    setMessage(null);
    setInputLevel(0);
    setInputRms(0);
    setInputLevelAvailable(false);
    let failureStatus: InputCheck = "error";
    try {
      const invoke = resolveTauriInvoke();
      if (invoke) {
        const response = await operationWithTimeout(
          () => invoke<NativeMicProbeResponse>(
            "mic_adapter_probe",
            deviceId ? { deviceId } : undefined,
          ),
          NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
          () => new Error("麦克风设备检查超时，请确认桌面权限后重试"),
        );
        if (!isCurrent()) return;
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
          setBrowserInputCheckStage("idle");
          setError("未检测到声音，请检查麦克风是否静音");
          return;
        }
        if (probeStatus !== "receiving_audio") throw new Error(nativeProbeError(response));
      } else {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境不支持麦克风访问");
        let stream: MediaStream | null = null;
        try {
          stream = await requestBrowserMicrophone({
            audio: deviceId ? { deviceId: { exact: deviceId } } : true,
            video: false,
          }, controller.signal);
          if (!isCurrent()) return;
          // Keep permission waiting separate from the analyser window. This
          // is the point where the browser has returned a MediaStream and the
          // user can see that authorization succeeded.
          setBrowserInputCheckStage("sampling");
          const activeTrack = stream.getAudioTracks().find((track) => track.readyState === "live");
          if (!activeTrack) throw new DOMException("没有可用的麦克风设备", "NotFoundError");
          const probe = await sampleBrowserInputLevel(stream, (rms, level) => {
            if (!isCurrent()) return;
            setInputRms(rms);
            setInputLevel(level);
            setInputLevelAvailable(true);
          }, controller.signal);
          if (!isCurrent()) return;
          setInputRms(probe.rms);
          setInputLevel(probe.level);
          setInputLevelAvailable(true);
          if (probe.peakRms < AUDIBLE_RMS_THRESHOLD) {
            setInputCheck("silent");
            setBrowserInputCheckStage("idle");
            setError("未检测到声音，请检查麦克风是否静音");
            return;
          }
        } finally {
          stream?.getTracks().forEach((track) => track.stop());
        }
      }
      if (!isCurrent()) return;
      setInputCheck("receiving_audio");
      setBrowserInputCheckStage("idle");
      setMessage("正常收到声音，麦克风可用");
    } catch (inputError) {
      if (!isCurrent()) return;
      if (resolveTauriInvoke()) {
        setInputCheck(failureStatus);
        setBrowserInputCheckStage("idle");
        setError(inputError instanceof Error ? inputError.message : "麦克风检查失败");
      } else {
        const failure = browserMicrophoneError(inputError);
        setInputCheck(failure.status);
        setBrowserInputCheckStage("idle");
        setError(failure.message);
      }
    } finally {
      if (inputCheckRuntimeRef.current.controller === controller) {
        inputCheckRuntimeRef.current.controller = null;
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
      const response = await operationWithTimeout(
        () => invoke<ProviderConfigSyncResponse>("provider_config_sync"),
        NATIVE_CAPTURE_REQUEST_TIMEOUT_MS,
        () => new Error("AI 配置连接超时，请重试"),
      );
      if (response.command_status !== "ok" || response.runtime_synced !== true) {
        throw new Error(response.errors?.filter(Boolean).join("；") || "AI 配置连接失败");
      }
      const refreshedProviders = await refreshPreflight();
      if (refreshedProviders?.runtime_synced !== true) {
        throw new Error("AI 已同步，但后端尚未确认配置，请重试");
      }
      setAiConnectionState("idle");
      setMessage(refreshedProviders.probe_status === "succeeded"
        && refreshedProviders.operational === true
        && refreshedProviders.realtime_ready === true
        ? "AI 已连接，Provider 单次探测通过；实时稳定性待验收"
        : refreshedProviders.probe_status === "succeeded"
          && refreshedProviders.operational === true
          && refreshedProviders.realtime_ready === false
          ? "AI 已连接，但单次探测超过实时窗口"
          : refreshedProviders.probe_status === "succeeded"
            ? "AI 已连接，实时性待确认"
            : "AI 运行时已同步");
    } catch (connectionError) {
      setAiConnectionState("error");
      setAiConnectionError(connectionError instanceof Error ? connectionError.message : "AI 配置连接失败");
    }
  };

  const submit = async () => {
    if (!noticeAcknowledged || storage?.allowed !== true || !localAsrReady || !sourceReady || busy || asrPreparing || asrChecking || correctionChecking || !correctionCapability || asrPreparationBlocker(asrRuntime, correctionCapability) || memoryPreviewLoading || materialRecoveryPending || liveAnalysisProfileLoading || liveAnalysisProfileError) return;
    cancelInputCheck();
    setInputCheck("idle");
    setBrowserInputCheckStage("idle");
    setError(null);
    try {
      const preparation: MeetingPreparationInput = {
        ...(title.trim() ? { title: title.trim() } : {}),
        hotwords: parseHotwords(hotwordsText),
        inputSource,
        inputDeviceId: inputSource === "microphone"
          ? deviceId || null
          : inputSource === "system_audio" ? systemAudioDeviceId || null : null,
        inputDeviceName: inputSource === "dual_track"
          ? "麦克风 + 系统音频"
          : inputSource === "system_audio" ? selectedSystemAudioDevice?.label || "系统音频"
          : selectedDevice?.label || (nativeDesktop ? "系统默认麦克风" : null),
        noticeAcknowledged: true,
        liveAnalysisProfile,
        correctionMode,
        ...(presetId !== "general" ? { presetId } : {}),
        ...(meetingGoal.trim() ? { meetingGoal: meetingGoal.trim() } : {}),
        ...(participantRole.trim() ? { participantRole: participantRole.trim() } : {}),
        ...(focusPointsText.trim() ? { focusPoints: parseFocusPoints(focusPointsText) } : {}),
        ...(outputFormat !== "standard" ? { outputFormat } : {}),
        ...(suggestionPolicy !== "low_frequency" ? { proactiveSuggestionPolicy: suggestionPolicy } : {}),
      };
      if (knowledgeApi) await onStart(preparation, knowledgeSelection);
      else await onStart(preparation);
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "声音采集启动失败");
    }
  };

  const localAsrReady = providers?.asr?.realtime_asr_available === true;
  const llmReady = providerStatus?.runtime_synced === true;
  const realtimeCircuitUnavailable = providerStatus?.realtime_circuit?.state === "open"
    || providerStatus?.realtime_circuit?.state === "half_open";
  const llmOperational = llmReady
    && !realtimeCircuitUnavailable
    && providerStatus?.probe_status === "succeeded"
    && providerStatus.operational === true;
  const llmRealtimeReady = llmOperational && providerStatus?.realtime_ready === true;
  const llmRealtimeSlow = llmOperational && providerStatus?.realtime_ready === false;
  const llmStatusText = realtimeCircuitUnavailable
    ? `${providerStatus?.model ?? "AI"} 实时通道连续失败，需重新测试连接；会议仍可录音和转写`
    : llmRealtimeReady
      ? `AI 已连接 · ${providerStatus?.model ?? "默认模型"}`
      : llmRealtimeSlow
      ? `AI 可连接，但实时教练延迟过高${providerStatus.probe_latency_ms !== null
        ? `（${providerStatus.probe_latency_ms}ms > ${providerStatus.realtime_cutoff_ms}ms）`
        : ""}，会议仍可录音和转写`
      : providerStatus?.probe_status === "failed" || providerStatus?.operational === false
        ? `${providerStatus.model ?? "AI"} 连接测试失败，会议仍可录音和转写`
        : llmReady
          ? `${providerStatus?.model ?? "AI"} 已连接，实时性待确认，会议仍可录音和转写`
          : providerStatus?.configured
            ? `${providerStatus.model ?? "AI"} 已保存，AI 待连接`
            : "AI 未配置，会议仍可录音和转写";
  const microphoneReady = inputSource !== "microphone" || inputCheck === "receiving_audio";
  const sourceReady = microphoneReady
    && (inputSource !== "system_audio" || systemAudioCheck === "available")
    && (inputSource !== "dual_track" || dualTrackAvailable);
  const startBlockers = [
    ...(liveAnalysisProfileLoading ? [{ id: "preflight-live-analysis-profile", label: "等待本场帮助方式读取" }] : []),
    ...(liveAnalysisProfileError ? [{ id: "preflight-live-analysis-profile", label: "重新读取本场帮助方式" }] : []),
    ...(materialRecoveryPending ? [{ id: "preflight-runtime-status", label: "等待会前资料恢复" }] : []),
    ...(memoryPreviewLoading ? [{ id: "preflight-runtime-status", label: "等待默认偏好读取，或手动设置主动建议" }] : []),
    ...(loading ? [{ id: "preflight-runtime-status", label: "等待本地服务检查" }] : []),
    ...(storage?.allowed !== true && !loading ? [{ id: "preflight-runtime-status", label: "确认本地存储可写" }] : []),
    ...(!localAsrReady && !loading ? [{ id: "preflight-runtime-status", label: "恢复本地实时识别" }] : []),
    ...(asrPreparing || asrChecking ? [{ id: "preflight-asr-preparation", label: "等待识别准备检查" }] : []),
    ...(correctionChecking ? [{ id: "preflight-asr-preparation", label: "等待校对模式和资源检查" }] : []),
    ...(!correctionChecking && !correctionCapability && !loading ? [{ id: "preflight-asr-preparation", label: "重新检查校对模式和资源" }] : []),
    ...(asrPreparationBlocker(asrRuntime, correctionCapability) ? [{ id: "preflight-asr-preparation", label: "核对本地识别准备" }] : []),
    ...(!microphoneReady ? [{
      id: "preflight-microphone",
      label: inputCheck === "permission_denied"
        ? "允许麦克风权限并重新检查"
        : inputCheck === "silent"
          ? "让麦克风检测到声音"
          : inputCheck === "checking"
            ? "等待麦克风检查完成"
            : "检查麦克风",
    }] : []),
    ...(inputSource === "system_audio" && systemAudioCheck !== "available"
      ? [{ id: "preflight-audio-source", label: "完成系统音频检查" }]
      : []),
    ...(inputSource === "dual_track" && !dualTrackAvailable
      ? [{ id: "preflight-audio-source", label: "确认双轨采集可用" }]
      : []),
    ...(!noticeAcknowledged ? [{ id: "preflight-consent", label: "确认已告知参会者" }] : []),
  ];

  const focusBlocker = (id: string) => {
    const target = document.getElementById(id);
    target?.scrollIntoView({ block: "center", behavior: motionAwareScrollBehavior() });
    target?.focus({ preventScroll: true });
  };

  return (
    <div className="drawer-layer meeting-preflight-layer" role="presentation">
      <button className="drawer-scrim" aria-label="关闭会前检查" onClick={closeDialog} disabled={busy} />
      <section ref={dialogRef} tabIndex={-1} className="meeting-preflight-dialog" role="dialog" aria-modal="true" aria-labelledby="meeting-preflight-title">
        <header className="drawer-header">
          <h2 id="meeting-preflight-title">准备开始会议</h2>
          <button className="icon-button" type="button" onClick={closeDialog} disabled={busy} aria-label="关闭会前检查" title="关闭">
            <X size={18} />
          </button>
        </header>

        <div className="meeting-preflight-body">
          {loading ? (
            <p className="preflight-loading" role="status"><LoaderCircle className="spin" size={17} />正在检查本地服务</p>
          ) : (
            <div id="preflight-runtime-status" tabIndex={-1} className="preflight-status-list" aria-label="运行条件">
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
              <div className={llmRealtimeReady ? "preflight-status preflight-status--ready" : "preflight-status preflight-status--warning"}>
                {llmRealtimeReady ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
                <span>{llmStatusText}</span>
              </div>
            </div>
          )}

          {!loading && <div id="preflight-asr-preparation" tabIndex={-1} className="preflight-system-audio-summary" aria-label="识别准备">
            <Mic size={18} />
            <div className="preflight-field-group">
            <strong>本场文字校对</strong>
            <div className="preflight-source-segmented preflight-correction-modes" role="radiogroup" aria-label="本场文字校对模式">
              {(["local_and_llm", "llm_only"] as const).map((mode) => <button key={mode} type="button" role="radio" aria-checked={correctionMode === mode} className={correctionMode === mode ? "is-selected" : ""} disabled={busy || asrPreparing || asrChecking || liveAnalysisProfileLoading || liveAnalysisProfileLocked} onClick={() => { setAsrPreparationError(null); setCorrectionMode(mode); }}>{CORRECTION_MODE_LABELS[mode]}</button>)}
            </div>
            {liveAnalysisProfileLocked && <small>本场已有录音或会议文字，沿用已保存的校对模式。</small>}
            {correctionChecking ? <p role="status">正在检查校对模式和本机资源…</p>
              : !correctionCapability ? <p role="alert">未取得校对模式和资源检查结果，请重新检查识别状态。</p>
              : <div className="preflight-correction-result">
                <p><strong>{correctionCapability.effectiveMode === "local_and_llm" && !correctionCapability.localRefinement.available ? "当前选择：" : "本场将使用："}{CORRECTION_MODE_LABELS[correctionCapability.effectiveMode]}</strong></p>
                {correctionCapability.requestedMode !== correctionCapability.effectiveMode && <p>{correctionFallbackReason(correctionCapability.fallbackReason)}；本场跳过本地精修，在线转写照常保存。</p>}
                {correctionCapability.effectiveMode === "local_and_llm" && !correctionCapability.localRefinement.available && <p role="alert">{correctionFallbackReason(correctionCapability.localRefinement.reason)}；请重新检查，或选择仅大模型校对。</p>}
                {correctionCapability.effectiveMode === "local_and_llm" && correctionCapability.localRefinement.recoveryRequired && <p>上次本地精修准备未完成，请点击重新准备。</p>}
                {correctionMode === "llm_only" && <p>保留本地在线转写，不加载离线精修模型。</p>}
                <p>{!correctionCapability.llmCorrection.enabled ? correctionCapability.effectiveMode === "llm_only" ? "大模型校对已关闭：本场仅保存在线识别原文。" : "大模型校对已关闭：保留本地识别文字，不进行大模型校对。"
                  : !correctionCapability.llmCorrection.configured ? correctionCapability.effectiveMode === "llm_only" ? "大模型校对缺少配置：本场仅保存在线识别原文。请在设置中配置校对模型。" : "大模型校对缺少配置：可使用本地识别，不会自动进行大模型校对。请在设置中配置校对模型。"
                  : `大模型校对已启用${providerStatus?.correction_model ? ` · ${providerStatus.correction_model}` : ""}：原文先保存，再在后台校对。`}</p>
                <div className="preflight-memory-readout" aria-label="本机内存与本地精修门槛">
                  <p>可用提交内存 {memoryAmount(correctionCapability.memory.commitAvailableBytes)} / 离线精修门槛 {memoryAmount(correctionCapability.memory.requiredCommitBytes)}</p>
                  <p>可用物理内存 {memoryAmount(correctionCapability.memory.physicalAvailableBytes)} / 离线精修门槛 {memoryAmount(correctionCapability.memory.requiredPhysicalBytes)}{correctionCapability.memory.totalPhysicalBytes !== null ? `（总内存 ${memoryAmount(correctionCapability.memory.totalPhysicalBytes)}）` : ""}</p>
                  {!correctionCapability.memory.checked && <small>{correctionMode === "llm_only" ? "此模式不依赖离线精修内存检查。" : "资源检查未完成，请重新检查，或选择仅大模型校对。"}</small>}
                  <small>这里是离线精修的保守启动余量，不是最低机器总内存。提交额度与空闲物理内存不同；开始时会复查，资源不足时改用仅大模型校对。</small>
                </div>
              </div>}
            {asrPreparing || asrChecking ? <p>正在核对识别准备状态…</p>
              : !asrRuntime || asrRuntime.residentEnabled === null ? <p>识别准备状态未知；将沿用当前识别方式。</p>
              : asrRuntime.residentEnabled === false ? <p>当前识别方式无需提前准备</p>
              : <>
                <p>{asrRuntime.onlineReady === true ? "实时识别进程已就绪" : asrRuntime.onlineReady === false ? "实时识别进程尚未就绪" : "实时识别进程准备状态未知"}</p>
                {correctionCapability?.effectiveMode !== "llm_only" && <p>{asrRuntime.offlinePolicy === "online_only" ? "离线精修未启用"
                  : asrRuntime.offlinePolicy === "on_demand" ? "离线精修将在首个正式段按需加载"
                  : asrRuntime.offlinePolicy === "prewarm" ? asrRuntime.offlineReady === true ? "离线精修进程已就绪" : asrRuntime.offlineReady === false ? "离线精修进程尚未就绪" : "离线精修进程准备状态未知"
                  : "离线精修方式待确认"}</p>}
              </>}
            <p>此操作只准备本地识别，不会启动录音。</p>
            {correctionCapability?.effectiveMode === "local_and_llm" && correctionCapability.localRefinement.available && asrRuntime?.residentEnabled === true && asrRuntime.offlinePolicy === "prewarm" && <button type="button" className="button secondary-button" disabled={busy || asrPreparing || asrChecking || correctionChecking} onClick={() => void observeAsr(true)}>
              {asrPreparing ? "正在准备离线识别…" : asrPreparationError || correctionCapability.localRefinement.recoveryRequired ? "重新准备" : "准备离线识别"}
            </button>}
            <button type="button" className="button secondary-button" disabled={busy || asrPreparing || asrChecking || correctionChecking} onClick={() => void observeAsr(false)}>{asrChecking ? "正在检查识别状态…" : "重新检查识别状态"}</button>
            {asrPreparationError && <p role="alert">{asrPreparationError}</p>}
            </div>
          </div>}

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
            <div id="preflight-audio-source" tabIndex={-1} className="preflight-source-field">
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
            <div id="preflight-microphone" tabIndex={-1} className="preflight-field-group">
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
                  <select
                    value={deviceId}
                    onChange={(event) => selectMicrophoneDevice(event.target.value)}
                    disabled={busy}
                  >
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
              {inputCheck === "checking" ? (
                <p className="preflight-help" role="status">
                  {nativeDesktop
                    ? "正在读取麦克风输入，请对着所选设备说一句话。"
                    : browserInputCheckStage === "permission"
                      ? "等待麦克风权限：请在浏览器提示或地址栏左侧的麦克风图标中选择“允许”。没有弹窗时，请检查浏览器和系统的麦克风权限。8 秒内未返回会自动结束，可直接重试。"
                      : "麦克风权限已通过，正在采样输入音量，请对着麦克风说一句话。"}
                </p>
              ) : null}
              <div
                className="preflight-input-meter"
                data-probe-status={inputCheck}
                aria-label={inputLevelAvailable ? `输入音量 ${(inputRms * 100).toFixed(1)}%` : "检查后显示输入音量"}
              >
                <span>输入音量</span>
                <span className="preflight-input-meter-track"><span style={{ transform: `scaleX(${inputLevel})` }} /></span>
                <small>{inputLevelAvailable
                  ? `${(inputRms * 100).toFixed(1)}%`
                  : inputCheck !== "checking"
                    ? "尚未检查"
                    : browserInputCheckStage === "permission"
                      ? "等待授权"
                      : "采样中"}</small>
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
                {inputSource === "system_audio" && systemAudioDevices.length ? (
                  <label>
                    <span className="sr-only">系统音频设备</span>
                    <select
                      value={systemAudioDeviceId}
                      onChange={(event) => setSystemAudioDeviceId(event.target.value)}
                      disabled={busy}
                    >
                      {systemAudioDevices.map((device, index) => (
                        <option key={device.deviceId || `system-audio-${index}`} value={device.deviceId}>
                          {device.label || `系统音频 ${index + 1}`}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <small>
                  {systemAudioDevices.length
                    ? "Windows 将从所选播放设备的 WASAPI loopback 采集声音。"
                    : "开始会议时 macOS 会请求“屏幕与系统音频录制”权限。"}
                </small>
              </div>
            </div>
          )}

          <div className="meeting-notice-row">
            <ShieldCheck size={18} />
            <p>{MEETING_NOTICE}</p>
            <button className="icon-button icon-button--small" type="button" onClick={() => void copyNotice()} aria-label="复制会议告知文案" title="复制会议告知文案">
              <Copy size={15} />
            </button>
          </div>
          <label id="preflight-consent" tabIndex={-1} className="preflight-consent">
            <input
              type="checkbox"
              checked={noticeAcknowledged}
              onChange={(event) => setNoticeAcknowledged(event.target.checked)}
              disabled={busy}
            />
            <span>我已告知参会者并确认可以录音</span>
          </label>

          <section id="preflight-live-analysis-profile" tabIndex={-1} className="preflight-source-field" aria-label="本场主要帮助设置">
            <span className="preflight-source-label">本场主要帮助 <small>可选，已提供默认方式</small></span>
            <div className="preflight-source-segmented" role="radiogroup" aria-label="本场主要帮助">
              <button className={liveAnalysisProfile === "participation" ? "is-selected" : ""} type="button" role="radio" aria-checked={liveAnalysisProfile === "participation"} disabled={busy || liveAnalysisProfileLoading || Boolean(liveAnalysisProfileError) || liveAnalysisProfileLocked} onClick={() => setLiveAnalysisProfile("participation")}>帮助我参与讨论</button>
              <button className={liveAnalysisProfile === "legacy" ? "is-selected" : ""} type="button" role="radio" aria-checked={liveAnalysisProfile === "legacy"} disabled={busy || liveAnalysisProfileLoading || Boolean(liveAnalysisProfileError) || liveAnalysisProfileLocked} onClick={() => setLiveAnalysisProfile("legacy")}>优先回答听到的问题</button>
            </div>
            <small>{liveAnalysisProfile === "participation" ? "围绕当前讨论提供追问、方案比较和可说出口的回应。无需先填写主题或上传资料。" : "优先回答会议中听到的问题。仍可用主动贡献入口提出追问、比较方案或组织回应。"}</small>
            {liveAnalysisProfileLoading ? <p role="status">正在读取已有会议的帮助方式，读取后才可继续录音。</p> : null}
            {liveAnalysisProfileError ? <div><p className="inline-error" role="alert">{liveAnalysisProfileError}</p>{onRetryLiveAnalysisProfile ? <button className="secondary-button" type="button" disabled={busy} onClick={onRetryLiveAnalysisProfile}>重新读取本场帮助方式</button> : null}</div> : null}
            {liveAnalysisProfileLocked ? <small>继续录音会沿用本场已保存的方式；已有录音或文字后不能切换，其他会前设置仍可调整。</small> : null}
          </section>

          {materialsApi ? <MeetingAttachments api={materialsApi} meetingId={materialSet?.meeting_id ?? null} materialSet={materialSet} onSetChange={onMaterialSetChange} onRecoveryPending={setMaterialRecoveryPending} /> : null}
          <details className="preflight-optional-settings"><summary>项目资料与会前准备（可选）</summary>
          {knowledgeApi ? <KnowledgeScopePicker api={knowledgeApi} value={knowledgeSelection} onChange={setKnowledgeSelection} disabled={busy} /> : null}
          {knowledgeApi && projectApi ? <MeetingProjectBrief api={projectApi} selection={knowledgeSelection} open={open} /> : null}
          {knowledgeApi && projectApi?.preparation ? <PreparationBriefPanel api={projectApi.preparation} projectApi={projectApi} input={{ selection: knowledgeSelection, goal: meetingGoal, role: participantRole, focus: parseFocusPoints(focusPointsText).slice(0, 8), scene: presetId }} open={open} /> : null}
          </details>
          <details className="preflight-optional-settings">
            <summary>
              <span>会议与教练设置</span>
              <small>可选</small>
            </summary>
            <div className="preflight-optional-settings__body">
              <label className="preflight-title-field">
                <span>会议名称</span>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="例如：支付服务上线评审"
                  maxLength={200}
                  disabled={busy}
                />
              </label>

              <div className="preflight-ai-context">
                <label>
                  <span>教练技能包</span>
                  <select
                    value={presetId}
                    onChange={(event) => {
                      const next = event.target.value as MeetingPresetId;
                      const defaults = PRESET_DEFAULTS[next];
                      setPresetId(next);
                      setMeetingGoal(defaults.goal);
                      setFocusPointsText(defaults.focusPoints);
                      setOutputFormat(defaults.outputFormat);
                    }}
                    disabled={busy}
                  >
                    <option value="general">通用对话教练</option>
                    <option value="decision">决策准备度教练</option>
                    <option value="project">项目执行教练</option>
                    <option value="interview">用户访谈教练</option>
                    <option value="brainstorm">头脑风暴教练</option>
                  </select>
                  <small>{PRESET_DEFAULTS[presetId].coachSummary}</small>
                </label>
                <label>
                  <span>我的角色</span>
                  <input
                    value={participantRole}
                    onChange={(event) => setParticipantRole(event.target.value)}
                    placeholder="例如：主持人、产品负责人"
                    maxLength={200}
                    disabled={busy}
                  />
                </label>
                <label className="preflight-ai-context__wide">
                  <span>会议目标</span>
                  <input
                    value={meetingGoal}
                    onChange={(event) => setMeetingGoal(event.target.value)}
                    placeholder="本场会议需要达成什么结果"
                    maxLength={2_000}
                    disabled={busy}
                  />
                </label>
                <label className="preflight-ai-context__wide">
                  <span>重点关注</span>
                  <input
                    value={focusPointsText}
                    onChange={(event) => setFocusPointsText(event.target.value)}
                    placeholder="逗号分隔，例如：决策、风险、负责人"
                    maxLength={1_200}
                    disabled={busy}
                  />
                </label>
                <label>
                  <span>整理格式</span>
                  <select value={outputFormat} onChange={(event) => setOutputFormat(event.target.value as MeetingOutputFormat)} disabled={busy}>
                    <option value="standard">标准纪要</option>
                    <option value="decision_log">决策记录</option>
                    <option value="action_plan">行动计划</option>
                    <option value="brief">简报</option>
                  </select>
                </label>
                <label>
                  <span>主动建议</span>
                  <select value={suggestionPolicy} onChange={(event) => { setPolicyTouched(true); setSuggestionPolicy(event.target.value as SuggestionPolicy); }} disabled={busy}>
                    <option value="low_frequency">低频高信号</option>
                    <option value="standard">标准频率</option>
                    <option value="off">关闭</option>
                  </select>
                  {memoryApi ? <small>{policyTouched ? "本场已手动设置，切换项目或技能包会保留这个选择。" : memoryPreview?.sources.length ? `已读取${memoryPreview.sources.some((x) => x.scope === "project") ? "项目" : "全局"}已确认偏好，本场可直接调整。` : "当前采用本场默认设置。"}</small> : null}
                  {memoryPreviewError ? <small role="status">默认偏好未加载：{memoryPreviewError} 可以手动选择后开始。</small> : null}
                </label>
              </div>

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
            </div>
          </details>

          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {inputSource === "microphone"
            && inputCheck === "error"
            && error?.includes("麦克风权限请求超时") ? (
            <p className="preflight-help" role="note">
              当前页面可能无法弹出系统授权（应用内预览尤其常见）。请改用本机 Chrome/Safari 或完整桌面端重试；若“文件转写”已就绪，也可以先用“导入录音”验证转写和 Pi 链路。
            </p>
          ) : null}
          {aiConnectionError ? <p className="inline-error" role="alert">{aiConnectionError}</p> : null}
          {message ? <p className="inline-success" role="status">{message}</p> : null}
        </div>

        <footer className="meeting-preflight-actions">
          {startBlockers.length ? (
            <div className="preflight-start-blockers" role="status" aria-label="开始会议前还需完成">
              <strong>还需完成：</strong>
              {startBlockers.map((blocker) => (
                <button
                  key={`${blocker.id}:${blocker.label}`}
                  type="button"
                  aria-label={`定位缺项：${blocker.label}`}
                  onClick={() => focusBlocker(blocker.id)}
                >
                  {blocker.label}
                </button>
              ))}
            </div>
          ) : <p className="preflight-ready-to-start"><CheckCircle2 size={15} />已满足开始条件</p>}
          <div className="meeting-preflight-action-row">
            <button className="secondary-button" type="button" onClick={closeDialog} disabled={busy}>取消</button>
            <button
              className="primary-button"
              type="button"
              onClick={() => void submit()}
              disabled={busy || startBlockers.length > 0}
            >
              {busy
                ? <LoaderCircle className="spin" size={16} />
                : inputSource === "dual_track"
                  ? <AudioLines size={16} />
                  : inputSource === "system_audio" ? <MonitorSpeaker size={16} /> : <Mic size={16} />}
              {busy ? "正在启动" : "开始会议"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
