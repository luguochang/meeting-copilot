import { AlertTriangle, Check, KeyRound, LoaderCircle, Settings, Trash2, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { fetchProviderStatus } from "../../api/client";
import {
  reconcileProviderStatus,
  type ProviderProbeStatus,
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
  api_style: ProviderApiStyle | null;
  provider_label: string;
  runtime_synced: boolean;
  probe_status?: ProviderProbeStatus;
  errors: string[];
}

interface ProviderHealthResponse {
  llm?: {
    configured?: boolean;
    provider?: string;
    model?: string;
    api_style?: ProviderApiStyle;
  };
  asr?: {
    file_asr_available?: boolean;
  };
  remote_asr?: {
    default_enabled?: boolean;
  };
  cost_policy?: {
    remote_asr_default_enabled?: boolean;
  };
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

const emptyResponse: ProviderConfigResponse = {
  command_status: "ok",
  configured: false,
  api_key_present: false,
  base_url: null,
  model: null,
  realtime_model: null,
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
};

function phaseFor(response: ProviderConfigResponse, status: ProviderStatus): ProviderPhase {
  if (response.command_status !== "ok") return "error";
  if (!status.configured) return "unconfigured";
  return status.runtime_synced ? "configured" : "saved";
}

function responseError(response: ProviderConfigResponse, fallback: string): Error {
  return new Error(response.errors.filter(Boolean).join("；") || fallback);
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

async function verifyProviderConnection(): Promise<void> {
  const response = await fetch("/providers/llm/probe", {
    method: "POST",
    headers: { "X-Meeting-Copilot-Verification": "1" },
  });
  const body = await response.json().catch(() => null) as { detail?: { message?: string } } | null;
  if (!response.ok) throw new Error(body?.detail?.message ?? `连接测试失败（${response.status}）`);
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

export function ProviderSettingsControl() {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<ProviderPhase>("loading");
  const [config, setConfig] = useState<ProviderConfigResponse>(emptyResponse);
  const [providerStatus, setProviderStatus] = useState<ProviderStatus>(emptyProviderStatus);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("gpt-5.5");
  const [realtimeModel, setRealtimeModel] = useState("");
  const [apiStyle, setApiStyle] = useState<ProviderApiStyle>("chat_completions");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "probe" | "clear" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [providerHealth, setProviderHealth] = useState<ProviderHealthResponse | null>(null);
  const [costStats, setCostStats] = useState<CostStatsResponse | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);

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
      setBaseUrl(response.base_url ?? "");
      setModel(response.model ?? "gpt-5.5");
      setRealtimeModel(response.realtime_model ?? response.model ?? "");
      setApiStyle(response.api_style ?? "chat_completions");
      setPhase(phaseFor(response, status));
      setError(response.command_status === "ok" ? null : response.errors.join("；"));
    } catch (statusError) {
      setPhase("error");
      setError(statusError instanceof Error ? statusError.message : "AI 配置状态读取失败");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadDetails = useCallback(async () => {
    setDetailsLoading(true);
    setDetailsError(null);
    const [healthResult, costResult] = await Promise.allSettled([
      getJson<ProviderHealthResponse>("/providers/health"),
      getJson<CostStatsResponse>("/settings/cost-stats"),
    ]);
    const errors: string[] = [];
    if (healthResult.status === "fulfilled") setProviderHealth(healthResult.value);
    else errors.push(healthResult.reason instanceof Error ? healthResult.reason.message : "Provider 健康状态读取失败");
    if (costResult.status === "fulfilled") setCostStats(costResult.value);
    else errors.push(costResult.reason instanceof Error ? costResult.reason.message : "成本统计读取失败");
    setDetailsError(errors.length ? errors.join("；") : null);
    setDetailsLoading(false);
  }, []);

  useEffect(() => {
    if (open) void loadDetails();
  }, [loadDetails, open]);

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
          apiStyle,
        })
        : await requestWebProviderConfig("PUT", {
          base_url: baseUrl.trim(),
          api_key: apiKey.trim() || null,
          model: model.trim(),
          realtime_model: realtimeModel.trim() || null,
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
      await verifyProviderConnection();
      const connectedStatus: ProviderStatus = {
        configured: true,
        runtime_synced: true,
        probe_status: "succeeded",
        model: synced.model,
        realtime_model: synced.realtime_model ?? synced.model,
      };
      setConfig(synced);
      setProviderStatus(connectedStatus);
      setPhase("configured");
      setApiKey("");
      setMessage("AI 已连接");
      await loadDetails();
      setOpen(false);
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
        const syncedStatus: ProviderStatus = {
          configured: true,
          runtime_synced: true,
          probe_status: "not_run",
          model: synced.model,
          realtime_model: synced.realtime_model ?? synced.model,
        };
        setProviderStatus(syncedStatus);
        setPhase(phaseFor(synced, syncedStatus));
      }
      await verifyProviderConnection();
      setProviderStatus({
        configured: true,
        runtime_synced: true,
        probe_status: "succeeded",
        model: activeModel,
        realtime_model: config.realtime_model ?? activeModel,
      });
      setPhase("configured");
      setMessage("AI 已连接");
      await loadDetails();
    } catch (probeError) {
      setProviderStatus((current) => current.runtime_synced
        ? { ...current, probe_status: "failed" }
        : current);
      setError(probeError instanceof Error ? probeError.message : "连接测试失败");
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    const invoke = resolveTauriInvoke();
    if (busy || !window.confirm("移除已保存的 AI 中转站配置？")) return;
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
      setBaseUrl("");
      setModel("gpt-5.5");
      setRealtimeModel("");
      setApiStyle("chat_completions");
      setApiKey("");
      setMessage("AI 配置已移除");
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "AI 配置移除失败");
    } finally {
      setBusy(null);
    }
  };

  const triggerLabel = phase === "configured"
    ? (providerStatus.probe_status === "succeeded"
      ? `AI 已连接 · ${providerStatus.model ?? config.model ?? model} / ${providerStatus.realtime_model ?? config.realtime_model ?? model}`
      : providerStatus.probe_status === "failed"
        ? `AI 连接失败 · ${providerStatus.model ?? config.model ?? model} / ${providerStatus.realtime_model ?? config.realtime_model ?? model}`
      : "AI 已配置")
    : phase === "saved"
      ? "AI 待连接"
    : phase === "error"
      ? "AI 配置异常"
      : phase === "loading"
        ? "读取 AI 配置"
        : "配置 AI";

  return (
    <>
      <button
        className={`provider-settings-trigger provider-settings-trigger--${phase}`}
        type="button"
        onClick={() => setOpen(true)}
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
              <div>
                <span className="eyebrow">AI 设置</span>
                <h2 id="provider-settings-title">OpenAI 兼容中转站</h2>
              </div>
              <button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="关闭 AI 设置" title="关闭">
                <X size={18} />
              </button>
            </header>

            <section className="provider-settings-form" aria-labelledby="provider-health-title">
              <span className="eyebrow" id="provider-health-title">Provider 健康与使用边界</span>
              {detailsLoading && !providerHealth && !costStats ? <p className="rail-empty">正在读取 Provider 健康和成本...</p> : null}
              {detailsError ? <p className="inline-error" role="alert">{detailsError}</p> : null}
              {providerHealth ? (
                <dl className="diagnostics-list">
                  <div><dt>LLM Provider</dt><dd>{providerHealth.llm?.provider ?? config.provider_label}</dd></div>
                  <div><dt>通用 / 会后模型</dt><dd>{config.model ?? providerHealth.llm?.model ?? "未配置"}</dd></div>
                  <div><dt>实时模型</dt><dd>{config.realtime_model ?? config.model ?? providerHealth.llm?.model ?? "未配置"}</dd></div>
                  <div>
                    <dt>接口协议</dt>
                    <dd>
                      {(providerHealth.llm?.api_style ?? config.api_style) === "responses"
                        ? "Responses"
                        : "Chat Completions"}
                    </dd>
                  </div>
                  <div>
                    <dt>连接状态</dt>
                    <dd>
                      {providerStatus.probe_status === "succeeded"
                        ? "连接正常（刚刚测试）"
                        : providerStatus.probe_status === "failed"
                          ? "最近一次连接测试失败"
                        : providerStatus.runtime_synced
                          ? "已配置，尚未测试"
                          : "未配置"}
                    </dd>
                  </div>
                  <div>
                    <dt>本地 ASR</dt>
                    <dd>
                      默认免费
                      {providerHealth.asr?.file_asr_available === false ? "（当前不可用）" : "（本机处理）"}
                    </dd>
                  </div>
                  <div>
                    <dt>远程 ASR</dt>
                    <dd>
                      {(providerHealth.remote_asr?.default_enabled
                        ?? providerHealth.cost_policy?.remote_asr_default_enabled
                        ?? false)
                        ? "默认开启"
                        : "默认关闭"}
                    </dd>
                  </div>
                </dl>
              ) : null}
              <p className="rail-empty">原始音频默认不上传；只有稳定文字发送到 LLM。</p>
              {costStats ? (
                <>
                  <span className="eyebrow">Token 与估算费用</span>
                  <dl className="diagnostics-list">
                    <div><dt>本次</dt><dd>{costDisplay(costStats, "currentSession", "本次")}</dd></div>
                    <div><dt>今日</dt><dd>{costDisplay(costStats, "today", "今日")}</dd></div>
                    <div>
                      <dt>本月 token</dt>
                      <dd>
                        {monthlyTokenCount(costStats) === null
                          ? "暂无记录"
                          : `本月已记录 ${formatTokenCount(monthlyTokenCount(costStats) ?? 0)} token`}
                      </dd>
                    </div>
                  </dl>
                </>
              ) : null}
            </section>

            {phase === "unavailable" ? (
              <div className="provider-desktop-only" role="status">
                <Settings size={20} />
                <p>请在 Meeting Copilot 桌面客户端中配置 AI。</p>
              </div>
            ) : (
              <form className="provider-settings-form" onSubmit={(event) => void save(event)}>
                <label>
                  <span>中转站地址</span>
                  <input
                    type="url"
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                    placeholder="https://example.com"
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
                    onChange={(event) => setModel(event.target.value)}
                    placeholder="gpt-5.5"
                    autoComplete="off"
                    required
                    disabled={Boolean(busy)}
                  />
                </label>
                <label>
                  <span>实时模型（可选）</span>
                  <input
                    value={realtimeModel}
                    onChange={(event) => setRealtimeModel(event.target.value)}
                    placeholder="留空则使用通用 / 会后模型"
                    autoComplete="off"
                    disabled={Boolean(busy)}
                  />
                </label>
                <label>
                  <span>接口协议</span>
                  <select
                    value={apiStyle}
                    onChange={(event) => setApiStyle(event.target.value as ProviderApiStyle)}
                    disabled={Boolean(busy)}
                  >
                    <option value="responses">Responses（GPT-5 / Codex）</option>
                    <option value="chat_completions">Chat Completions（通用兼容）</option>
                  </select>
                </label>
                <label>
                  <span>API Key</span>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={config.api_key_present ? "留空以继续使用已保存密钥" : "输入 API Key"}
                    autoComplete="new-password"
                    required={!config.api_key_present}
                    disabled={Boolean(busy)}
                  />
                </label>

                <div className="credential-status">
                  {config.api_key_present ? <Check size={15} /> : <KeyRound size={15} />}
                  <span>
                    {config.api_key_present
                      ? (resolveTauriInvoke() ? "密钥已保存在系统凭据库" : "密钥已保存在本机数据目录")
                      : "密钥尚未保存"}
                  </span>
                </div>
                {error ? <p className="inline-error" role="alert">{error}</p> : null}
                {message ? <p className="inline-success" role="status">{message}</p> : null}
                <div className="diagnostic-alert" role="note">
                  <AlertTriangle size={16} />
                  <span>“保存并连接”和“测试连接”会产生一次真实中转站调用，并可能产生费用。</span>
                </div>

                <footer className="provider-settings-actions">
                  {config.configured ? (
                    <button className="danger-text-button" type="button" onClick={() => void clear()} disabled={Boolean(busy)}>
                      <Trash2 size={15} />移除
                    </button>
                  ) : <span />}
                  <div>
                    <button className="secondary-button" type="button" onClick={() => void probe()} disabled={Boolean(busy) || !config.configured}>
                      {busy === "probe" ? <LoaderCircle className="spin" size={15} /> : null}
                      {providerStatus.runtime_synced ? "测试连接" : "连接并测试"}
                    </button>
                    <button className="primary-button" type="submit" disabled={Boolean(busy)}>
                      {busy === "save" ? <LoaderCircle className="spin" size={15} /> : null}
                      保存并连接
                    </button>
                  </div>
                </footer>
              </form>
            )}
          </section>
        </div>
      ) : null}
    </>
  );
}
