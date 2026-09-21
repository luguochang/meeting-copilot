import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TauriInvoke } from "../../desktop/tauri";
import { ProviderSettingsControl } from "./ProviderSettingsControl";

const unconfigured = {
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

const configured = {
  ...unconfigured,
  configured: true,
  api_key_present: true,
  base_url: "https://relay.example",
  model: "gpt-test",
  realtime_model: "gpt-realtime-test",
  realtime_model_source: "runtime_realtime_model",
  realtime_model_explicit: true,
  realtime_model_warning: null,
  correction_model: "gpt-test",
  correction_model_source: "general_model_fallback",
  correction_model_explicit: false,
  correction_model_warning: "correction_model_inherits_general_model",
  api_style: "chat_completions",
  runtime_synced: true,
};

const realtimeProbe = {
  ok: true,
  operational: true,
  realtime_ready: true,
  probe_latency_ms: 120,
  realtime_cutoff_ms: 2_500,
  usage: {
    prompt_tokens: 7,
    completion_tokens: 2,
    total_tokens: 9,
  },
};

const slowProbe = {
  ...realtimeProbe,
  realtime_ready: false,
  probe_latency_ms: 3_000,
};

const saved = { ...configured, runtime_synced: false };

const inheritedRealtimeModel = {
  ...configured,
  realtime_model: "gpt-test",
  realtime_model_source: "general_model_fallback",
  realtime_model_explicit: false,
  realtime_model_warning: "realtime_model_inherits_general_model",
};

const connectedProviderStatus = {
  configured: true,
  runtime_synced: true,
  probe_status: "succeeded",
  model: "gpt-test",
  realtime_model: "gpt-realtime-test",
  operational: true,
  realtime_ready: true,
  probe_latency_ms: 120,
  probe_usage: realtimeProbe.usage,
  realtime_cutoff_ms: 2_500,
};

const emptyProviderStatus = {
  configured: false,
  runtime_synced: false,
  probe_status: "not_run",
  model: null,
  realtime_model: null,
};

const costStats = {
  currentSession: 0.12,
  today: 0.45,
  month: 1.2,
  breakdown: [{ tokens: 1234 }],
  currency: "CNY",
  costStatus: "estimated",
  estimated: true,
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(body),
  };
}

afterEach(() => {
  delete window.__TAURI__;
  delete window.__TAURI_INTERNALS__;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ProviderSettingsControl", () => {
  it("keeps the connected model label after a component remount", async () => {
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/status") return Promise.resolve(jsonResponse(connectedProviderStatus));
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

    const first = render(<ProviderSettingsControl />);
    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test"));
    first.unmount();

    render(<ProviderSettingsControl />);
    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test"));
  });

  it("keeps a failed connection explicit after refresh", async () => {
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/status") {
        return Promise.resolve(jsonResponse({
          ...connectedProviderStatus,
          probe_status: "failed",
          operational: false,
          realtime_ready: false,
          probe_latency_ms: null,
          probe_usage: null,
        }));
      }
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

    render(<ProviderSettingsControl />);
    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 连接失败 · gpt-test");
  });

  it("does not describe an open realtime circuit as connected", async () => {
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/status") {
        return Promise.resolve(jsonResponse({
          ...connectedProviderStatus,
          probe_status: "not_run",
          operational: null,
          realtime_ready: null,
          probe_latency_ms: null,
          probe_usage: null,
          realtime_circuit: {
            state: "open",
            reason: "realtime_provider_recovery_probe_required",
            failure_count: 3,
            retry_after_ms: 0,
            half_open: false,
            identity_generation: 4,
            last_failure_class: "timeout",
          },
        }));
      }
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

    render(<ProviderSettingsControl />);
    const trigger = await screen.findByRole("button", { name: "打开 AI 设置" });
    expect(trigger).toHaveTextContent("AI 实时通道异常 · gpt-test");
    expect(trigger).not.toHaveTextContent("AI 已连接");
  });

  it("keeps remote AI optional and shows the sponsor and project links", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/config") return Promise.resolve(jsonResponse(unconfigured));
      if (path === "/providers/status") return Promise.resolve(jsonResponse(emptyProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse({ breakdown: [] }));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    const baseUrlInput = within(dialog).getByLabelText("服务地址（Base URL）");
    expect(baseUrlInput).toHaveValue("https://codexai.club");
    expect(baseUrlInput).toHaveAttribute(
      "placeholder",
      "https://api.example.com/v1",
    );
    await user.clear(baseUrlInput);
    expect(baseUrlInput).toHaveValue("");
    expect(within(dialog).getByText("未配置，不影响本地转写")).toBeVisible();
    expect(within(dialog).getByRole("link", { name: "访问 AI 赞助商 codexai.club" })).toHaveAttribute(
      "href",
      "https://codexai.club/",
    );
    expect(within(dialog).getByRole("link", { name: /GitHub 仓库/ })).toHaveAttribute(
      "href",
      "https://github.com/luguochang/meeting-copilot",
    );
    expect(within(dialog).getByRole("link", { name: /CSDN 博客/ })).toHaveAttribute(
      "href",
      "https://blog.csdn.net/luguochang",
    );
    expect(dialog).not.toHaveTextContent("Provider 健康与使用边界");
    expect(dialog).not.toHaveTextContent("本地 ASR");
  });

  it("saves a Web configuration without probing, then tests it explicitly", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/providers/config" && (!init || init.method === "GET")) return Promise.resolve(jsonResponse(unconfigured));
      if (path === "/providers/status") return Promise.resolve(jsonResponse(emptyProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse(costStats));
      if (path === "/providers/config" && init?.method === "PUT") return Promise.resolve(jsonResponse(configured));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse(realtimeProbe));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    await user.clear(within(dialog).getByLabelText("服务地址（Base URL）"));
    await user.type(within(dialog).getByLabelText("服务地址（Base URL）"), "https://relay.example");
    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test");
    await user.type(within(dialog).getByLabelText("API Key"), "sk-web-test");
    await user.click(within(dialog).getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(within(dialog).getByText("AI 配置已保存，请点击“测试连接”验证 Provider")).toBeVisible());
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/providers/llm/probe")).toBe(false);
    expect(within(dialog).getByRole("region", { name: "AI 连接状态" })).toHaveClass("provider-connection--untested");

    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));

    const connectionRegion = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await waitFor(() => expect(within(connectionRegion).getByText("Provider 探测通过")).toBeVisible());
    expect(within(dialog).getByText("Provider 探测通过，实时稳定性待验收")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "AI 设置" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "测试连接" })).toHaveClass("provider-test-button--connected");
    const saveCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input) === "/providers/config" && init?.method === "PUT",
    );
    expect(saveCall?.[1]?.body).toBe(JSON.stringify({
      base_url: "https://relay.example",
      api_key: "sk-web-test",
      model: "gpt-test",
      realtime_model: null,
      correction_model: null,
      api_style: "chat_completions",
    }));
  });

  it("keeps inherited realtime models blank and warns before a later save", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/providers/config" && (!init || init.method === "GET")) {
        return Promise.resolve(jsonResponse(inheritedRealtimeModel));
      }
      if (path === "/providers/status") {
        return Promise.resolve(jsonResponse(connectedProviderStatus));
      }
      if (path === "/settings/cost-stats") {
        return Promise.resolve(jsonResponse({ breakdown: [] }));
      }
      if (path === "/providers/config" && init?.method === "PUT") {
        return Promise.resolve(jsonResponse(inheritedRealtimeModel));
      }
      if (path === "/providers/llm/probe") {
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    await user.click(within(dialog).getByText("高级设置"));

    expect(within(dialog).getByLabelText("实时模型（可选）")).toHaveValue("");
    expect(within(dialog).getByText("实时教练当前继承通用模型 gpt-test", { exact: false })).toBeVisible();
    expect(within(dialog).getByText("实时教练当前继承通用模型 gpt-test", { exact: false })).toHaveTextContent(
      "实时教练当前继承通用模型 gpt-test",
    );

    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test-updated");
    await user.click(within(dialog).getByRole("button", { name: "保存配置" }));

    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(([input, init]) =>
        String(input) === "/providers/config" && init?.method === "PUT",
      );
      expect(saveCall?.[1]?.body).toBe(JSON.stringify({
        base_url: "https://relay.example",
        api_key: null,
        model: "gpt-test-updated",
        realtime_model: null,
        correction_model: null,
        api_style: "chat_completions",
      }));
    });
  });

  it("stores a provider through Tauri without putting the saved key back into the form", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return unconfigured;
      if (command === "provider_config_save") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/status") return Promise.resolve(jsonResponse(emptyProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse(costStats));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse({ ok: true }));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    await user.clear(within(dialog).getByLabelText("服务地址（Base URL）"));
    await user.type(within(dialog).getByLabelText("服务地址（Base URL）"), "https://relay.example");
    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test");
    await user.type(within(dialog).getByLabelText("API Key"), "sk-test-only-secret");
    await user.click(within(dialog).getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_save", expect.objectContaining({
      apiKey: "sk-test-only-secret",
    })));
    expect(within(dialog).getByLabelText("API Key")).toHaveValue("");
    expect(within(dialog).getByLabelText("API Key")).toHaveAttribute("placeholder", "留空以继续使用已保存密钥");
    const connectionRegion = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await waitFor(() => expect(connectionRegion).toHaveClass("provider-connection--untested"));
    expect(connectionRegion).not.toHaveClass("provider-connection--connected");
    expect(within(connectionRegion).getByText("已保存，待测试")).toBeVisible();
  });

  it("syncs a saved desktop configuration only when the user tests it", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return saved;
      if (command === "provider_config_sync") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/status") return Promise.resolve(jsonResponse({ ...connectedProviderStatus, runtime_synced: false, probe_status: "not_run" }));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse({ breakdown: [] }));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse(realtimeProbe));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 待连接");
    expect(invoke).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_sync"));
    const connectionRegion = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await waitFor(() => expect(within(connectionRegion).getByText("Provider 探测通过")).toBeVisible());
  });

  it("uses a warning state when the provider is reachable but misses the realtime cutoff", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/status") return Promise.resolve(jsonResponse(connectedProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse({ breakdown: [] }));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse(slowProbe));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    const region = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));

    await waitFor(() => expect(region).toHaveClass("provider-connection--slow"));
    expect(region).not.toHaveClass("provider-connection--connected");
    expect(within(region).getByText("已连接，但实时响应过慢")).toBeVisible();
    expect(within(region).getByText(/3000ms > 实时窗口 2500ms/)).toBeVisible();
  });

  it("keeps readiness unknown when a legacy probe omits evidence", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/status") return Promise.resolve(jsonResponse(connectedProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse({ breakdown: [] }));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse({ ok: true }));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    const region = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));

    await waitFor(() => expect(region).toHaveClass("provider-connection--untested"));
    expect(region).not.toHaveClass("provider-connection--connected");
    expect(within(region).getByText("已保存，待测试")).toBeVisible();
    expect(within(region).getByText("配置已保存 · 尚未发起连接测试")).toBeVisible();
  });

  it("uses an in-app confirmation before removing a configuration", async () => {
    const user = userEvent.setup();
    const cleared = { ...unconfigured };
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      if (command === "provider_config_clear") return cleared;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/providers/status") return Promise.resolve(jsonResponse(connectedProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse({ breakdown: [] }));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await user.click(await screen.findByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "AI 设置" });
    await user.click(within(dialog).getByRole("button", { name: "移除配置" }));
    expect(within(dialog).getByText("确定移除已保存的 AI 配置？")).toBeVisible();
    expect(invoke).not.toHaveBeenCalledWith("provider_config_clear");
    await user.click(within(dialog).getByRole("button", { name: "确认移除" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_clear"));
    expect(within(dialog).getByLabelText("服务地址（Base URL）")).toHaveValue("https://codexai.club");
  });
});
