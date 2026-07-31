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

const saved = { ...configured, runtime_synced: false };

const connectedProviderStatus = {
  configured: true,
  runtime_synced: true,
  probe_status: "succeeded",
  model: "gpt-test",
  realtime_model: "gpt-realtime-test",
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
        return Promise.resolve(jsonResponse({ ...connectedProviderStatus, probe_status: "failed" }));
      }
      return Promise.reject(new Error(`unexpected request: ${String(input)}`));
    }));
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

    render(<ProviderSettingsControl />);
    expect(await screen.findByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 连接失败 · gpt-test");
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

  it("saves and tests a Web configuration while keeping the green result visible", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/providers/config" && (!init || init.method === "GET")) return Promise.resolve(jsonResponse(unconfigured));
      if (path === "/providers/status") return Promise.resolve(jsonResponse(emptyProviderStatus));
      if (path === "/settings/cost-stats") return Promise.resolve(jsonResponse(costStats));
      if (path === "/providers/config" && init?.method === "PUT") return Promise.resolve(jsonResponse(configured));
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse({ ok: true }));
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
    await user.click(within(dialog).getByRole("button", { name: "保存并测试" }));

    const connectionRegion = within(dialog).getByRole("region", { name: "AI 连接状态" });
    await waitFor(() => expect(within(connectionRegion).getByText("连接正常")).toBeVisible());
    expect(within(dialog).getByText("连接正常，配置已保存")).toBeVisible();
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
      api_style: "chat_completions",
    }));
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
    await user.click(within(dialog).getByRole("button", { name: "保存并测试" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_save", expect.objectContaining({
      apiKey: "sk-test-only-secret",
    })));
    expect(within(dialog).getByLabelText("API Key")).toHaveValue("");
    expect(within(dialog).getByLabelText("API Key")).toHaveAttribute("placeholder", "留空以继续使用已保存密钥");
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
      if (path === "/providers/llm/probe") return Promise.resolve(jsonResponse({ ok: true }));
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
    await waitFor(() => expect(within(connectionRegion).getByText("连接正常")).toBeVisible());
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
