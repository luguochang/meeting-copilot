import { openUrl } from "@tauri-apps/plugin-opener";
import {
  AlertTriangle,
  BookOpenText,
  Check,
  CheckCircle2,
  ExternalLink,
  Github,
  HeartHandshake,
  KeyRound,
  LoaderCircle,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { type FormEvent, type MouseEvent, useCallback, useEffect, useState } from "react";
import { fetchProviderStatus } from "../../api/client";
import {
  parseProviderProbeResult,
  reconcileProviderStatus,
  type ProviderProbeStatus,
  type ProviderProbeResult,
  type ProviderStatus,
} from "../../api/schema";
import { resolveTauriInvoke } from "../../desktop/tauri";

type ProviderApiStyle = "chat_completions" | "responses";

interface ProviderConfigResponse {
  command_status: "ok" | "error";
  configured: boolean;
  api_key_present: boolean;
  base_url: string | null;
  model: string | null;
  realtime_model: string | null;
  realtime_model_source?: string | null;
  realtime_model_explicit?: boolean;
  realtime_model_warning?: string | null;
  correction_model: string | null;
  correction_model_source?: string | null;
  correction_model_explicit?: boolean;
  correction_model_warning?: string | null;
  api_style: ProviderApiStyle | null;
  provider_label: string;
  runtime_synced: boolean;
  probe_status?: ProviderProbeStatus;
  errors: string[];
}

interface CostBreakdown {
  tokens?: number;
  total_tokens?: number;
}

interface CostStatsResponse {
  currentSession?: number | null;
  today?: number | null;
  month?: number | null;
  breakdown?: CostBreakdown[];
  currency?: string;
  costStatus?: string;
  estimated?: boolean;
  currentSessionTokens?: number | null;
  todayTokens?: number | null;
  current_session_tokens?: number | null;
  today_tokens?: number | null;
}

type ProviderPhase = "loading" | "unavailable" | "unconfigured" | "saved" | "configured" | "error";
type ProviderConnectionState = "testing" | "connected" | "slow" | "unknown" | "failed" | "untested" | "unconfigured";

const DEFAULT_BASE_URL = "https://codexai.club";
const BASE_URL_PLACEHOLDER = "https://api.example.com/v1";
const DEFAULT_MODEL = "gpt-5.5";
const SPONSOR_URL = "https://codexai.club/";
const REPOSITORY_URL = "https://github.com/luguochang/meeting-copilot";
const BLOG_URL = "https://blog.csdn.net/luguochang";

const emptyResponse: ProviderConfigResponse = {
  command_status: "ok",
  configured: false,
  api_key_present: false,
  base_url: null,
  model: null,
  realtime_model: null,
  realtime_model_source: "not_configured",
  realtime_model_explicit: false,
  realtime_model_warning: null,
  correction_model: null,
  correction_model_source: "not_configured",
  correction_model_explicit: false,
  correction_model_warning: null,
  api_style: null,
  provider_label: "openai_compatible_gateway",
  runtime_synced: false,
  errors: [],
};

const emptyProviderStatus: ProviderStatus = {
  configured: false,
  runtime_synced: false,
  probe_status: "not_run",
  model: null,
  realtime_model: null,
  operational: null,
  realtime_ready: null,
  probe_latency_ms: null,
  probe_usage: null,
  realtime_cutoff_ms: 2_500,
};

function phaseFor(response: ProviderConfigResponse, status: ProviderStatus): ProviderPhase {
  if (response.command_status !== "ok") return "error";
  if (!status.configured) return "unconfigured";
  return status.runtime_synced ? "configured" : "saved";
}

function responseError(response: ProviderConfigResponse, fallback: string): Error {
  return new Error(response.errors.filter(Boolean).join("；") || fallback);
}

function editableRealtimeModel(response: ProviderConfigResponse): string {
  if (response.realtime_model_explicit === false) return "";
  return response.realtime_model ?? response.model ?? "";
}

function editableCorrectionModel(response: ProviderConfigResponse): string {
  if (response.correction_model_explicit === false) return "";
  return response.correction_model ?? response.model ?? "";
}

function statusAfterProbe(
  base: ProviderStatus,
  probeResult: ProviderProbeResult | null,
): ProviderStatus {
  return {
    ...base,
    // A successful HTTP response without a valid readiness contract is not a
    // successful probe. Keep the runtime connected, but require a fresh test.
    probe_status: probeResult ? "succeeded" : "not_run",
    operational: probeResult?.operational ?? null,
    realtime_ready: probeResult?.realtime_ready ?? null,
    probe_latency_ms: probeResult?.probe_latency_ms ?? null,
    probe_usage: probeResult?.usage ?? null,
    realtime_cutoff_ms: probeResult?.realtime_cutoff_ms ?? base.realtime_cutoff_ms,
  };
}

function probeSuccessMessage(probeResult: ProviderProbeResult | null, suffix = ""): string {
  if (probeResult?.realtime_ready === true) return `Provider 探测通过，实时稳定性待验收${suffix}`;
  if (probeResult?.realtime_ready === false) return `Provider 已连接，但单次探测超过实时窗口${suffix}`;
  return `Provider 已连接，实时性待确认${suffix}`;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => null) as { detail?: { message?: unknown } } | null;
  if (!response.ok) {
    const detailMessage = typeof body?.detail?.message === "string" ? body.detail.message : null;
    throw new Error(detailMessage ?? `请求失败（${response.status}）`);
  }
  return body as T;
}

async function requestWebProviderConfig(
  method: "GET" | "PUT" | "DELETE",
  payload?: Record<string, unknown>,
): Promise<ProviderConfigResponse> {
  const response = await fetch("/providers/config", {
    method,
    headers: {
      Accept: "application/json",
      ...(payload ? { "Content-Type": "application/json" } : {}),
    },
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
  const body = await response.json().catch(() => null) as ProviderConfigResponse & {
    detail?: { message?: unknown };
  } | null;
  if (!response.ok) {
    const detailMessage = typeof body?.detail?.message === "string" ? body.detail.message : null;
    throw new Error(detailMessage ?? `请求失败（${response.status}）`);
  }
  return body as ProviderConfigResponse;
}

function formatTokenCount(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

async function verifyProviderConnection(): Promise<ProviderProbeResult | null> {
  const response = await fetch("/providers/llm/probe", {
    method: "POST",
    headers: { "X-Meeting-Copilot-Verification": "1" },
  });
  const body = await response.json().catch(() => null) as {
    detail?: { message?: unknown };
  } | null;
  if (!response.ok) {
    const detailMessage = typeof body?.detail?.message === "string" ? body.detail.message : null;
    throw new Error(detailMessage ?? `连接测试失败（${response.status}）`);
  }
  try {
    return parseProviderProbeResult(body);
  } catch {
    // Older sidecars returned only {ok:true}. The connection result remains
    // useful, but readiness must stay unknown until a full probe is available.
    return null;
  }
}

function formatCost(value: number, currency: string): string {
  if (currency === "CNY") return `¥${value.toFixed(2)}`;
  return `${value.toFixed(4)} ${currency}`;
}

function monthlyTokenCount(stats: CostStatsResponse | null): number | null {
  if (!stats?.breakdown?.length) return null;
  return stats.breakdown.reduce((total, item) => total + (item.tokens ?? item.total_tokens ?? 0), 0);
}

function periodTokenCount(stats: CostStatsResponse | null, period: "currentSession" | "today"): number | null {
  if (!stats) return null;
  const value = period === "currentSession"
    ? stats.currentSessionTokens ?? stats.current_session_tokens
    : stats.todayTokens ?? stats.today_tokens;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function costDisplay(
  stats: CostStatsResponse | null,
  period: "currentSession" | "today",
  label: string,
): string {
  if (!stats) return "读取中...";
  const value = stats[period];
  if (
    typeof value === "number"
    && Number.isFinite(value)
    && (stats.costStatus === "estimated" || stats.estimated === true)
  ) {
    return `${label}估算费用 ${formatCost(value, stats.currency ?? "CNY")}`;
  }
  const tokens = periodTokenCount(stats, period);
  if (tokens !== null) return `${label}已记录 ${formatTokenCount(tokens)} token`;
  if (stats.costStatus === "unavailable") return "已记录 token，未配置单价，无法估算";
  return `${label}暂无记录`;
}

function hasUsageData(stats: CostStatsResponse | null): boolean {
  if (!stats) return false;
  return [
    stats.currentSession,
    stats.today,
    periodTokenCount(stats, "currentSession"),
    periodTokenCount(stats, "today"),
    monthlyTokenCount(stats),
  ].some((value) => typeof value === "number" && Number.isFinite(value) && value > 0);
}

export function ProviderSettingsControl() {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<ProviderPhase>("loading");
  const [config, setConfig] = useState<ProviderConfigResponse>(emptyResponse);
  const [providerStatus, setProviderStatus] = useState<ProviderStatus>(emptyProviderStatus);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [realtimeModel, setRealtimeModel] = useState("");
  const [correctionModel, setCorrectionModel] = useState("");
  const [apiStyle, setApiStyle] = useState<ProviderApiStyle>("chat_completions");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "probe" | "clear" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [costStats, setCostStats] = useState<CostStatsResponse | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [dirty, setDirty] = useState(false);

  const refresh = useCallback(async () => {
    const invoke = resolveTauriInvoke();
    try {
      const [response, runtimeStatus] = invoke
        ? await Promise.all([
          invoke<ProviderConfigResponse>("provider_config_status"),
          fetchProviderStatus().catch(() => null),
        ])
        : await Promise.all([
          requestWebProviderConfig("GET"),
          fetchProviderStatus().catch(() => null),
        ]);
      const status = reconcileProviderStatus(response, runtimeStatus);
      setConfig(response);
      setProviderStatus(status);
      setBaseUrl(response.base_url ?? DEFAULT_BASE_URL);
      setModel(response.model ?? DEFAULT_MODEL);
      setRealtimeModel(editableRealtimeModel(response));
      setCorrectionModel(editableCorrectionModel(response));
      setApiStyle(response.api_style ?? "chat_completions");
      setPhase(phaseFor(response, status));
      setError(response.command_status === "ok" ? null : response.errors.join("；"));
      setDirty(false);
    } catch (statusError) {
      setPhase("error");
      setError(statusError instanceof Error ? statusError.message : "AI 配置状态读取失败");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadUsage = useCallback(async () => {
    try {
      setCostStats(await getJson<CostStatsResponse>("/settings/cost-stats"));
    } catch {
      setCostStats(null);
    }
  }, []);

  useEffect(() => {
    if (open) void loadUsage();
  }, [loadUsage, open]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const invoke = resolveTauriInvoke();
    if (busy) return;
    setBusy("save");
    setError(null);
    setMessage(null);
    try {
      const response = invoke
        ? await invoke<ProviderConfigResponse>("provider_config_save", {
          baseUrl: baseUrl.trim(),
          apiKey,
          model: model.trim(),
          realtimeModel: realtimeModel.trim() || null,
          correctionModel: correctionModel.trim() || null,
          apiStyle,
        })
        : await requestWebProviderConfig("PUT", {
          base_url: baseUrl.trim(),
          api_key: apiKey.trim() || null,
          model: model.trim(),
          realtime_model: realtimeModel.trim() || null,
          correction_model: correctionModel.trim() || null,
          api_style: apiStyle,
        });
      if (response.command_status !== "ok") throw responseError(response, "AI 配置保存失败");
      const synced = response.runtime_synced
        ? response
        : invoke
          ? await invoke<ProviderConfigResponse>("provider_config_sync")
          : await requestWebProviderConfig("GET");
      if (synced.command_status !== "ok" || !synced.runtime_synced) {
        throw responseError(synced, "AI 配置连接失败");
      }
      const savedStatus: ProviderStatus = {
        ...providerStatus,
        configured: true,
        runtime_synced: true,
        probe_status: "not_run",
        model: synced.model,
        realtime_model: synced.realtime_model ?? synced.model,
        operational: null,
        realtime_ready: null,
        probe_latency_ms: null,
        probe_usage: null,
        realtime_cutoff_ms: providerStatus.realtime_cutoff_ms || 2_500,
        // A new runtime identity invalidates stale circuit state. The next
        // explicit probe fetches the authoritative circuit snapshot.
        realtime_circuit: null,
      };
      setConfig(synced);
      setProviderStatus(savedStatus);
      setPhase("configured");
      setApiKey("");
      setMessage("AI 配置已保存，请点击“测试连接”验证 Provider");
      setConfirmingClear(false);
      setDirty(false);
      await loadUsage();
    } catch (saveError) {
      setPhase("error");
      setError(saveError instanceof Error ? saveError.message : "AI 配置保存失败");
    } finally {
      setBusy(null);
    }
  };

  const probe = async () => {
    if (busy || !config.configured) return;
    setBusy("probe");
    setError(null);
    setMessage(null);
    let activeModel = config.model;
    let probeBaseStatus: ProviderStatus = {
      ...providerStatus,
      configured: true,
      runtime_synced: true,
      probe_status: "not_run",
      model: config.model,
      realtime_model: config.realtime_model ?? config.model,
      operational: null,
      realtime_ready: null,
      probe_latency_ms: null,
      probe_usage: null,
      realtime_cutoff_ms: providerStatus.realtime_cutoff_ms || 2_500,
    };
    try {
      if (!providerStatus.runtime_synced) {
        const invoke = resolveTauriInvoke();
        const synced = invoke
          ? await invoke<ProviderConfigResponse>("provider_config_sync")
          : await requestWebProviderConfig("GET");
        if (synced.command_status !== "ok" || !synced.runtime_synced) {
          throw responseError(synced, "AI 配置连接失败");
        }
        setConfig(synced);
        activeModel = synced.model;
        probeBaseStatus = {
          ...providerStatus,
          configured: true,
          runtime_synced: true,
          probe_status: "not_run",
          model: synced.model,
          realtime_model: synced.realtime_model ?? synced.model,
          operational: null,
          realtime_ready: null,
          probe_latency_ms: null,
          probe_usage: null,
          realtime_cutoff_ms: 2_500,
        };
        setProviderStatus(probeBaseStatus);
        setPhase(phaseFor(synced, probeBaseStatus));
      }
      const probeResult = await verifyProviderConnection();
      const probedStatus = statusAfterProbe({
        ...probeBaseStatus,
        model: activeModel,
      }, probeResult);
      setProviderStatus(probedStatus);
      setPhase("configured");
      setMessage(probeSuccessMessage(probeResult));
      await loadUsage();
    } catch (probeError) {
      setPhase("error");
      setProviderStatus((current) => current.runtime_synced
        ? {
          ...current,
          probe_status: "failed",
          operational: false,
          realtime_ready: false,
          probe_latency_ms: null,
          probe_usage: null,
        }
        : current);
      setError(probeError instanceof Error ? probeError.message : "连接测试失败");
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    const invoke = resolveTauriInvoke();
    if (busy) return;
    setBusy("clear");
    setError(null);
    setMessage(null);
    try {
      const response = invoke
        ? await invoke<ProviderConfigResponse>("provider_config_clear")
        : await requestWebProviderConfig("DELETE");
      if (response.command_status !== "ok") throw responseError(response, "AI 配置移除失败");
      setConfig(response);
      setProviderStatus(emptyProviderStatus);
      setPhase("unconfigured");
      setBaseUrl(DEFAULT_BASE_URL);
      setModel(DEFAULT_MODEL);
      setRealtimeModel("");
      setCorrectionModel("");
      setApiStyle("chat_completions");
      setApiKey("");
      setMessage("AI 配置已移除");
      setConfirmingClear(false);
      setDirty(false);
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "AI 配置移除失败");
    } finally {
      setBusy(null);
    }
  };

  const probeSucceeded = providerStatus.probe_status === "succeeded";
  const realtimeReady = probeSucceeded
    && providerStatus.operational === true
    && providerStatus.realtime_ready === true;
  const realtimeSlow = probeSucceeded
    && providerStatus.operational === true
    && providerStatus.realtime_ready === false;
  const realtimeCircuitUnavailable = providerStatus.realtime_circuit?.state === "open"
    || providerStatus.realtime_circuit?.state === "half_open";
  const connectionState: ProviderConnectionState = busy === "save" || busy === "probe"
    ? "testing"
    : realtimeCircuitUnavailable
      || providerStatus.probe_status === "failed"
      || providerStatus.operational === false
      || phase === "error"
      ? "failed"
      : realtimeReady
        ? "connected"
        : realtimeSlow
          ? "slow"
          : providerStatus.probe_status === "succeeded"
            ? "unknown"
            : config.configured
              ? "untested"
              : "unconfigured";

  const triggerLabel = phase === "configured"
    ? (connectionState === "connected"
      ? `AI 已连接 · ${providerStatus.model ?? config.model ?? model}`
      : connectionState === "slow"
        ? "AI 已连接 · 实时延迟较高"
        : connectionState === "unknown"
          ? "AI 已连接 · 实时性待确认"
          : connectionState === "failed"
            ? realtimeCircuitUnavailable
              ? `AI 实时通道异常 · ${providerStatus.model ?? config.model ?? model}`
              : `AI 连接失败 · ${providerStatus.model ?? config.model ?? model}`
            : "AI 已配置")
    : phase === "saved"
      ? "AI 待连接"
      : phase === "error"
        ? "AI 配置异常"
        : phase === "loading"
          ? "读取 AI 配置"
          : "配置 AI";

  const connectionLabel = connectionState === "testing"
    ? "正在测试连接"
    : connectionState === "connected"
      ? "Provider 探测通过"
      : connectionState === "slow"
        ? "已连接，但实时响应过慢"
        : connectionState === "unknown"
          ? "已连接，实时性待确认"
          : connectionState === "failed"
            ? realtimeCircuitUnavailable
              ? "实时通道暂不可用"
              : "连接失败"
            : connectionState === "untested"
              ? "已保存，待测试"
              : "尚未配置";

  const realtimeReadinessLabel = connectionState === "slow"
    ? `单次探测超过实时窗口${providerStatus.probe_latency_ms !== null
      ? ` · 探测 ${providerStatus.probe_latency_ms}ms > 实时窗口 ${providerStatus.realtime_cutoff_ms}ms`
      : ""}`
    : connectionState === "connected"
      ? `Provider 探测通过 · 实时稳定性待验收 · 探测 ${providerStatus.probe_latency_ms ?? 0}ms`
      : connectionState === "unknown"
        ? "实时性待确认 · 请完成一次完整连接测试"
        : connectionState === "untested"
          ? "配置已保存 · 尚未发起连接测试"
        : realtimeCircuitUnavailable
          ? "连续实时请求失败 · 请完成连接测试后恢复"
        : null;

  const markConfigChanged = () => {
    setDirty(true);
    setMessage(null);
    setError(null);
    setProviderStatus((current) => current.probe_status === "succeeded"
      ? {
        ...current,
        probe_status: "not_run",
        operational: null,
        realtime_ready: null,
        probe_latency_ms: null,
        probe_usage: null,
      }
      : current);
  };

  const openExternalLink = async (event: MouseEvent<HTMLAnchorElement>, url: string) => {
    if (!resolveTauriInvoke()) return;
    event.preventDefault();
    try {
      await openUrl(url);
    } catch {
      setError(`无法打开链接，请在浏览器访问 ${url}`);
    }
  };

  return (
    <>
      <button
        className={`provider-settings-trigger provider-settings-trigger--${phase}`}
        type="button"
        onClick={() => {
          setConfirmingClear(false);
          setOpen(true);
        }}
        aria-label="打开 AI 设置"
        title={triggerLabel}
      >
        {phase === "loading" ? <LoaderCircle className="spin" size={16} /> : <Settings size={16} />}
        <span>{triggerLabel}</span>
      </button>

      {open ? (
        <div className="drawer-layer" role="presentation">
          <button className="drawer-scrim" aria-label="关闭 AI 设置" onClick={() => setOpen(false)} />
          <section className="provider-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="provider-settings-title">
            <header className="drawer-header">
              <h2 id="provider-settings-title">AI 设置</h2>
              <button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="关闭 AI 设置" title="关闭">
                <X size={18} />
              </button>
            </header>

            <div className="provider-settings-body">
              <section className={`provider-connection provider-connection--${connectionState}`} aria-label="AI 连接状态">
                <span className="provider-connection-icon" aria-hidden="true">
                  {connectionState === "testing"
                    ? <LoaderCircle className="spin" size={18} />
                    : connectionState === "connected"
                      ? <CheckCircle2 size={18} />
                      : connectionState === "slow" || connectionState === "unknown" || connectionState === "failed"
                        ? <AlertTriangle size={18} />
                        : <Settings size={18} />}
                </span>
                <div>
                  <strong>{connectionLabel}</strong>
                  <span>
                    {config.configured
                      ? `${config.model ?? model}${dirty ? " · 配置有修改" : ""}`
                      : "未配置，不影响本地转写"}
                  </span>
                  {realtimeReadinessLabel ? (
                    <span className={providerStatus.realtime_ready === true ? "provider-realtime-ready" : "provider-realtime-warning"} role="status">
                      {realtimeReadinessLabel}
                    </span>
                  ) : null}
                </div>
                {config.configured ? (
                  <button
                    className={`provider-test-button provider-test-button--${connectionState}`}
                    type="button"
                    onClick={() => void probe()}
                    disabled={Boolean(busy) || dirty}
                    aria-label="在状态区测试连接"
                  >
                    {connectionState === "connected" ? <Check size={15} /> : null}
                    {busy === "probe" ? "测试中" : dirty ? "先保存修改" : "测试连接"}
                  </button>
                ) : null}
              </section>

              <section className="provider-sponsor-card" aria-label="AI 赞助商 codexai.club">
                <span className="provider-sponsor-icon" aria-hidden="true">
                  <HeartHandshake size={18} />
                </span>
                <div>
                  <span>AI 赞助商</span>
                  <strong>codexai.club</strong>
                </div>
                <a
                  href={SPONSOR_URL}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(event) => void openExternalLink(event, SPONSOR_URL)}
                  aria-label="访问 AI 赞助商 codexai.club"
                >
                  访问服务 <ExternalLink size={13} />
                </a>
              </section>

              {phase === "unavailable" ? (
                <div className="provider-desktop-only" role="status">
                  <Settings size={20} />
                  <p>请在桌面客户端中配置 AI。</p>
                </div>
              ) : (
                <form className="provider-settings-form" id="provider-config-panel" onSubmit={(event) => void save(event)}>
                  <div className="provider-form-heading">
                    <strong>OpenAI-compatible 服务</strong>
                    <span>可选</span>
                  </div>
                  <label>
                    <span>服务地址（Base URL）</span>
                    <input
                      type="url"
                      value={baseUrl}
                      onChange={(event) => {
                        setBaseUrl(event.target.value);
                        markConfigChanged();
                      }}
                      placeholder={BASE_URL_PLACEHOLDER}
                      autoComplete="url"
                      required
                      disabled={Boolean(busy)}
                    />
                  </label>
                  <label>
                    <span>模型</span>
                    <input
                      aria-label="模型"
                      value={model}
                      onChange={(event) => {
                        setModel(event.target.value);
                        markConfigChanged();
                      }}
                      placeholder={DEFAULT_MODEL}
                      autoComplete="off"
                      required
                      disabled={Boolean(busy)}
                    />
                  </label>

                  <label>
                    <span>API Key</span>
                    <input
                      type="password"
                      value={apiKey}
                      onChange={(event) => {
                        setApiKey(event.target.value);
                        markConfigChanged();
                      }}
                      placeholder={config.api_key_present ? "留空以继续使用已保存密钥" : "输入 API Key"}
                      autoComplete="new-password"
                      required={!config.api_key_present}
                      disabled={Boolean(busy)}
                    />
                  </label>
                  <div className="credential-status">
                    {config.api_key_present ? <Check size={15} /> : <KeyRound size={15} />}
                    <span>{config.api_key_present ? "API Key 已安全保存" : "API Key 尚未保存"}</span>
                  </div>

                  <details className="provider-advanced">
                    <summary>高级设置</summary>
                    <div className="provider-advanced-fields">
                      <label>
                        <span>接口协议</span>
                        <select
                          value={apiStyle}
                          onChange={(event) => {
                            setApiStyle(event.target.value as ProviderApiStyle);
                            markConfigChanged();
                          }}
                          disabled={Boolean(busy)}
                        >
                          <option value="chat_completions">Chat Completions</option>
                          <option value="responses">Responses</option>
                        </select>
                      </label>
                      <label>
                        <span>实时模型（可选）</span>
                        <input
                          value={realtimeModel}
                          onChange={(event) => {
                            setRealtimeModel(event.target.value);
                            markConfigChanged();
                          }}
                          placeholder="留空则使用上方模型"
                          autoComplete="off"
                          disabled={Boolean(busy)}
                        />
                      </label>
                      <label>
                        <span>转写校正模型（可选）</span>
                        <input
                          value={correctionModel}
                          onChange={(event) => {
                            setCorrectionModel(event.target.value);
                            markConfigChanged();
                          }}
                          placeholder="留空则使用上方模型"
                          autoComplete="off"
                          disabled={Boolean(busy)}
                        />
                      </label>
                    </div>
                    {config.realtime_model_warning === "realtime_model_inherits_general_model"
                      && !realtimeModel.trim() ? (
                        <p className="inline-warning" role="status">
                          实时教练当前继承通用模型 {config.model ?? model}。建议单独配置已通过延迟门禁的实时模型。
                        </p>
                    ) : null}
                    {config.correction_model_warning === "correction_model_inherits_general_model"
                      && !correctionModel.trim() ? (
                      <p className="inline-warning" role="status">
                        转写校正当前继承通用模型 {config.model ?? model}。可单独配置更快或更稳定的校正模型。
                      </p>
                    ) : null}
                  </details>

                  <p className="provider-settings-note">AI 仅接收会议文字，不上传录音。测试连接会发送一次最小请求，可能产生少量费用。</p>
                  {error ? <p className="inline-error" role="alert">{error}</p> : null}
                  {message ? <p className="inline-success" role="status">{message}</p> : null}

                  {hasUsageData(costStats) ? (
                    <div className="provider-usage-summary" aria-label="AI 用量">
                      <strong>用量</strong>
                      {periodTokenCount(costStats, "currentSession") !== null || typeof costStats?.currentSession === "number"
                        ? <span>{costDisplay(costStats, "currentSession", "本次")}</span>
                        : null}
                      {periodTokenCount(costStats, "today") !== null || typeof costStats?.today === "number"
                        ? <span>{costDisplay(costStats, "today", "今日")}</span>
                        : null}
                      {monthlyTokenCount(costStats) ? <span>本月 {formatTokenCount(monthlyTokenCount(costStats) ?? 0)} token</span> : null}
                    </div>
                  ) : null}

                </form>
              )}

              <nav className="provider-project-links" aria-label="项目链接">
                <span>项目链接</span>
                <div>
                  <a
                    href={REPOSITORY_URL}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => void openExternalLink(event, REPOSITORY_URL)}
                  >
                    <Github size={14} /> GitHub 仓库 <ExternalLink size={12} />
                  </a>
                  <a
                    href={BLOG_URL}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => void openExternalLink(event, BLOG_URL)}
                  >
                    <BookOpenText size={14} /> CSDN 博客 <ExternalLink size={12} />
                  </a>
                </div>
              </nav>
            </div>

            {phase !== "unavailable" ? (
              <footer className="provider-settings-actions">
                {confirmingClear ? (
                  <div className="provider-clear-confirm" role="alert">
                    <span>确定移除已保存的 AI 配置？</span>
                    <button type="button" onClick={() => setConfirmingClear(false)} disabled={Boolean(busy)}>取消</button>
                    <button className="is-danger" type="button" onClick={() => void clear()} disabled={Boolean(busy)}>
                      {busy === "clear" ? <LoaderCircle className="spin" size={14} /> : null}
                      确认移除
                    </button>
                  </div>
                ) : null}
                <div className="provider-settings-action-row">
                  {config.configured ? (
                    <button className="danger-text-button" type="button" onClick={() => setConfirmingClear(true)} disabled={Boolean(busy) || confirmingClear}>
                      <Trash2 size={15} />移除配置
                    </button>
                  ) : <span />}
                  <div className="provider-settings-primary-actions">
                    <button
                      className={`secondary-button provider-test-button provider-test-button--${connectionState}`}
                      type="button"
                      onClick={() => void probe()}
                      disabled={Boolean(busy) || dirty || !config.configured}
                    >
                      {busy === "probe" ? <LoaderCircle className="spin" size={15} /> : null}
                      {busy === "probe" ? "测试中" : dirty ? "先保存修改" : "测试连接"}
                    </button>
                    <button className="primary-button" type="submit" form="provider-config-panel" disabled={Boolean(busy)}>
                      {busy === "save" ? <LoaderCircle className="spin" size={15} /> : null}
                      {busy === "save" ? "正在保存" : "保存配置"}
                    </button>
                  </div>
                </div>
              </footer>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
