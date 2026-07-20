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
  api_style: "chat_completions",
  runtime_synced: true,
};

const saved = {
  ...configured,
  runtime_synced: false,
};

const connectedProviderStatus = {
  configured: true,
  runtime_synced: true,
  probe_status: "succeeded",
  model: "gpt-test",
  realtime_model: "gpt-realtime-test",
};

const providerHealth = {
  schema_version: "provider_health.v1",
  llm: {
    configured: true,
    provider: "openai_compatible_gateway",
    model: "gpt-test",
    is_mock: false,
    credential_configured: true,
  },
  asr: {
    file_provider: "local_funasr_batch",
    file_asr_available: true,
    realtime_providers: ["funasr_realtime"],
    realtime_asr_available: true,
  },
  remote_asr: {
    default_enabled: false,
    enabled: false,
    providers: [],
  },
  cost_policy: {
    default_paid_services: ["llm_gateway_when_ai_analysis_enabled"],
    remote_asr_default_enabled: false,
    raw_audio_uploaded_by_default: false,
  },
};

const estimatedCostStats = {
  currentSession: 0.12,
  today: 0.45,
  month: 1.2,
  breakdown: [{ purpose: "meeting_summary", provider: "openai_compatible_gateway", model: "gpt-test", tokens: 1234 }],
  currency: "CNY",
  costStatus: "estimated",
  estimated: true,
};

const unavailableCostStats = {
  currentSession: null,
  today: null,
  month: null,
  breakdown: [{ purpose: "meeting_summary", provider: "openai_compatible_gateway", model: "gpt-test", tokens: 2048 }],
  currency: "CNY",
  costStatus: "unavailable",
  estimated: false,
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
    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test");
    first.unmount();

    render(<ProviderSettingsControl />);
    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test");
  });

  it("keeps a failed provider probe explicit after refresh", async () => {
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/status") {
        return Promise.resolve(jsonResponse({
          ...connectedProviderStatus,
          probe_status: "failed",
        }));
      }
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

    render(<ProviderSettingsControl />);

    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 连接失败 · gpt-test");
  });

  it("allows the local Web runtime to configure a provider without Tauri", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/providers/config" && (!init || init.method === "GET")) {
        return Promise.resolve(jsonResponse(unconfigured));
      }
      if (path === "/providers/status") {
        return Promise.resolve(jsonResponse({
          configured: false,
          runtime_synced: false,
          probe_status: "not_run",
          model: null,
          realtime_model: null,
        }));
      }
      if (path === "/providers/health") return Promise.resolve(jsonResponse(providerHealth));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse(estimatedCostStats));
      if (path === "/providers/config" && init?.method === "PUT") {
        return Promise.resolve(jsonResponse(configured));
      }
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse({ ok: true }));
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("配置 AI"));
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));

    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });
    expect(dialog).toHaveTextContent("密钥尚未保存");
    await user.type(within(dialog).getByLabelText("中转站地址"), "https://relay.example");
    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test");
    await user.type(within(dialog).getByLabelText("API Key"), "sk-web-test");
    await user.click(within(dialog).getByRole("button", { name: "保存并连接" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test"));
    const saveCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input) === "/providers/config" && init?.method === "PUT",
    );
    expect(saveCall?.[1]?.body).toBe(JSON.stringify({
      base_url: "https://relay.example",
      api_key: "sk-web-test",
      model: "gpt-test",
      realtime_model: null,
      api_style: "chat_completions",
    }));
  });

  it("stores a provider through Tauri and never puts the saved key back into the form", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return unconfigured;
      if (command === "provider_config_save") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/health") return Promise.resolve(jsonResponse(providerHealth));
      if (String(input) === "/settings/cost-stats") return Promise.resolve(jsonResponse(estimatedCostStats));
      if (String(input) === "/providers/llm/probe") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: vi.fn().mockRejectedValue(new Error("empty response")),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("配置 AI"));
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });
    await user.type(within(dialog).getByLabelText("中转站地址"), "https://relay.example");
    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test");
    await user.type(within(dialog).getByLabelText("实时模型（可选）"), "gpt-realtime-test");
    await user.type(within(dialog).getByLabelText("API Key"), "sk-test-only-secret");
    await user.click(within(dialog).getByRole("button", { name: "保存并连接" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_save", {
      baseUrl: "https://relay.example",
      apiKey: "sk-test-only-secret",
      model: "gpt-test",
      realtimeModel: "gpt-realtime-test",
      apiStyle: "chat_completions",
    }));
    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接 · gpt-test"));
    expect(screen.queryByRole("dialog", { name: "OpenAI 兼容中转站" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const reopened = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });
    expect(within(reopened).getByLabelText("API Key")).toHaveValue("");
    expect(within(reopened).getByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "留空以继续使用已保存密钥",
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/providers/llm/probe", expect.objectContaining({ method: "POST" })));
  });

  it("does not read the saved credential until the user explicitly connects", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return saved;
      if (command === "provider_config_sync") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new Error("empty response")),
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 待连接"));
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenLastCalledWith("provider_config_status");

    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });
    await user.click(within(dialog).getByRole("button", { name: "连接并测试" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_sync"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/providers/llm/probe",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("loads provider health and cost data only after the settings drawer opens", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/health") return Promise.resolve(jsonResponse(providerHealth));
      if (String(input) === "/settings/cost-stats") return Promise.resolve(jsonResponse(estimatedCostStats));
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已配置"));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith("/providers/status", expect.objectContaining({ method: "GET" }));

    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual(
      expect.arrayContaining(["/providers/status", "/providers/health", "/settings/cost-stats"]),
    );
    await waitFor(() => expect(dialog).toHaveTextContent("openai_compatible_gateway"));
    expect(within(dialog).getByText("通用 / 会后模型").nextElementSibling).toHaveTextContent("gpt-test");
    expect(within(dialog).getByText("实时模型").nextElementSibling).toHaveTextContent("gpt-realtime-test");
    expect(within(dialog).getByText("默认免费（本机处理）")).toBeInTheDocument();
    expect(within(dialog).getByText("默认关闭")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("原始音频默认不上传；只有稳定文字发送到 LLM");
    expect(dialog).toHaveTextContent("本次估算费用 ¥0.12");
    expect(dialog).toHaveTextContent("今日估算费用 ¥0.45");
    expect(dialog).toHaveTextContent("本月已记录 1,234 token");
    expect(dialog).toHaveTextContent("“保存并连接”和“测试连接”会产生一次真实中转站调用，并可能产生费用");
  });

  it("states that tokens were recorded without showing a misleading zero cost when pricing is missing", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/providers/health") return Promise.resolve(jsonResponse(providerHealth));
      if (String(input) === "/settings/cost-stats") return Promise.resolve(jsonResponse(unavailableCostStats));
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已配置"));
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });

    await waitFor(() => expect(dialog).toHaveTextContent("已记录 token，未配置单价，无法估算"));
    expect(dialog).toHaveTextContent("本月已记录 2,048 token");
    expect(dialog).not.toHaveTextContent("0 元");
  });
});
