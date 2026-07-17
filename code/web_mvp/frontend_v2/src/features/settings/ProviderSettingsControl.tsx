import { Check, KeyRound, LoaderCircle, Settings, Trash2, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { resolveTauriInvoke } from "../../desktop/tauri";

interface ProviderConfigResponse {
  command_status: "ok" | "error";
  configured: boolean;
  api_key_present: boolean;
  base_url: string | null;
  model: string | null;
  provider_label: string;
  runtime_synced: boolean;
  errors: string[];
}

type ProviderPhase = "loading" | "unavailable" | "unconfigured" | "configured" | "error";

const emptyResponse: ProviderConfigResponse = {
  command_status: "ok",
  configured: false,
  api_key_present: false,
  base_url: null,
  model: null,
  provider_label: "openai_compatible_gateway",
  runtime_synced: false,
  errors: [],
};

function phaseFor(response: ProviderConfigResponse): ProviderPhase {
  if (response.command_status !== "ok") return "error";
  return response.configured && response.runtime_synced ? "configured" : "unconfigured";
}

function responseError(response: ProviderConfigResponse, fallback: string): Error {
  return new Error(response.errors.filter(Boolean).join("；") || fallback);
}

export function ProviderSettingsControl() {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<ProviderPhase>("loading");
  const [config, setConfig] = useState<ProviderConfigResponse>(emptyResponse);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("gpt-5.5");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "probe" | "clear" | null>(null);
  const [probeSucceeded, setProbeSucceeded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const invoke = resolveTauriInvoke();
    if (!invoke) {
      setPhase("unavailable");
      return;
    }
    try {
      const response = await invoke<ProviderConfigResponse>("provider_config_status");
      setConfig(response);
      setBaseUrl(response.base_url ?? "");
      setModel(response.model ?? "gpt-5.5");
      setPhase(phaseFor(response));
      setProbeSucceeded(false);
      setError(response.command_status === "ok" ? null : response.errors.join("；"));
    } catch (statusError) {
      setPhase("error");
      setError(statusError instanceof Error ? statusError.message : "AI 配置状态读取失败");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const invoke = resolveTauriInvoke();
    if (!invoke || busy) return;
    setBusy("save");
    setError(null);
    setMessage(null);
    try {
      const response = await invoke<ProviderConfigResponse>("provider_config_save", {
        baseUrl: baseUrl.trim(),
        apiKey,
        model: model.trim(),
      });
      if (response.command_status !== "ok") throw responseError(response, "AI 配置保存失败");
      setConfig(response);
      setPhase(phaseFor(response));
      setProbeSucceeded(false);
      setApiKey("");
      setMessage("AI 已配置");
    } catch (saveError) {
      setPhase("error");
      setError(saveError instanceof Error ? saveError.message : "AI 配置保存失败");
    } finally {
      setBusy(null);
    }
  };

  const probe = async () => {
    if (busy || !config.configured || !config.runtime_synced) return;
    setBusy("probe");
    setProbeSucceeded(false);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch("/providers/llm/probe", {
        method: "POST",
        headers: { "X-Meeting-Copilot-Verification": "1" },
      });
      const body = await response.json().catch(() => null) as { detail?: { message?: string } } | null;
      if (!response.ok) throw new Error(body?.detail?.message ?? `连接测试失败（${response.status}）`);
      setProbeSucceeded(true);
      setMessage("AI 已连接");
    } catch (probeError) {
      setError(probeError instanceof Error ? probeError.message : "连接测试失败");
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    const invoke = resolveTauriInvoke();
    if (!invoke || busy || !window.confirm("移除已保存的 AI 中转站配置？")) return;
    setBusy("clear");
    setError(null);
    setMessage(null);
    try {
      const response = await invoke<ProviderConfigResponse>("provider_config_clear");
      if (response.command_status !== "ok") throw responseError(response, "AI 配置移除失败");
      setConfig(response);
      setPhase("unconfigured");
      setProbeSucceeded(false);
      setBaseUrl("");
      setModel("gpt-5.5");
      setApiKey("");
      setMessage("AI 配置已移除");
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "AI 配置移除失败");
    } finally {
      setBusy(null);
    }
  };

  const triggerLabel = phase === "configured"
    ? (probeSucceeded ? "AI 已连接" : "AI 已配置")
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
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    placeholder="gpt-5.5"
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
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={config.api_key_present ? "留空以继续使用已保存密钥" : "输入 API Key"}
                    autoComplete="new-password"
                    required={!config.api_key_present}
                    disabled={Boolean(busy)}
                  />
                </label>

                <div className="credential-status">
                  {config.api_key_present ? <Check size={15} /> : <KeyRound size={15} />}
                  <span>{config.api_key_present ? "密钥已保存在系统凭据库" : "密钥尚未保存"}</span>
                </div>
                {error ? <p className="inline-error" role="alert">{error}</p> : null}
                {message ? <p className="inline-success" role="status">{message}</p> : null}

                <footer className="provider-settings-actions">
                  {config.configured ? (
                    <button className="danger-text-button" type="button" onClick={() => void clear()} disabled={Boolean(busy)}>
                      <Trash2 size={15} />移除
                    </button>
                  ) : <span />}
                  <div>
                    <button className="secondary-button" type="button" onClick={() => void probe()} disabled={Boolean(busy) || !config.runtime_synced}>
                      {busy === "probe" ? <LoaderCircle className="spin" size={15} /> : null}
                      测试连接
                    </button>
                    <button className="primary-button" type="submit" disabled={Boolean(busy)}>
                      {busy === "save" ? <LoaderCircle className="spin" size={15} /> : null}
                      保存
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
