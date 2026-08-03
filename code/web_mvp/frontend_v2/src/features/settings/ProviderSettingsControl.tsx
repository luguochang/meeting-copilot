import { openUrl } from "@tauri-apps/plugin-opener";
import {
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
      setRealtimeModel(response.realtime_model ?? response.model ?? "");
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
      setMessage("连接正常，配置已保存");
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
      setMessage("连接正常");
      await loadUsage();
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

  const triggerLabel = phase === "configured"
    ? (providerStatus.probe_status === "succeeded"
      ? `AI 已连接 · ${providerStatus.model ?? config.model ?? model}`
      : providerStatus.probe_status === "failed"
        ? `AI 连接失败 · ${providerStatus.model ?? config.model ?? model}`
        : "AI 已配置")
    : phase === "saved"
      ? "AI 待连接"
      : phase === "error"
        ? "AI 配置异常"
        : phase === "loading"
          ? "读取 AI 配置"
          : "配置 AI";

  const connectionState = busy === "save" || busy === "probe"
    ? "testing"
    : providerStatus.probe_status === "succeeded"
      ? "connected"
      : providerStatus.probe_status === "failed" || phase === "error"
        ? "failed"
        : config.configured
          ? "untested"
          : "unconfigured";

  const connectionLabel = connectionState === "testing"
    ? "正在测试连接"
    : connectionState === "connected"
      ? "连接正常"
      : connectionState === "failed"
        ? "连接失败"
        : connectionState === "untested"
          ? "待测试"
          : "尚未配置";

  const markConfigChanged = () => {
    setDirty(true);
    setMessage(null);
    setError(null);
    setProviderStatus((current) => current.probe_status === "succeeded"
      ? { ...current, probe_status: "not_run" }
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
                      : <Settings size={18} />}
                </span>
                <div>
                  <strong>{connectionLabel}</strong>
                  <span>
                    {config.configured
                      ? `${config.model ?? model}${dirty ? " · 配置有修改" : ""}`
                      : "未配置，不影响本地转写"}
                  </span>
                </div>
                {config.configured ? (
                  <button
                    className={`provider-test-button provider-test-button--${connectionState}`}
                    type="button"
                    onClick={() => void probe()}
                    disabled={Boolean(busy) || dirty}
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
                    </div>
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
                  <button className="primary-button" type="submit" form="provider-config-panel" disabled={Boolean(busy)}>
                    {busy === "save" ? <LoaderCircle className="spin" size={15} /> : null}
                    {busy === "save" ? "正在测试" : "保存并测试"}
                  </button>
                </div>
              </footer>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
