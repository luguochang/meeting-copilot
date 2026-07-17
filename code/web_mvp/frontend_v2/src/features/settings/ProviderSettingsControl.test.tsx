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
  runtime_synced: true,
};

afterEach(() => {
  delete window.__TAURI__;
  delete window.__TAURI_INTERNALS__;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ProviderSettingsControl", () => {
  it("explains that provider configuration belongs to the desktop client", async () => {
    const user = userEvent.setup();
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("配置 AI"));
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));

    expect(screen.getByRole("dialog", { name: "OpenAI 兼容中转站" })).toHaveTextContent(
      "请在 Meeting Copilot 桌面客户端中配置 AI。",
    );
  });

  it("stores a provider through Tauri and never puts the saved key back into the form", async () => {
    const user = userEvent.setup();
    const invoke = vi.fn(async (command: string) => {
      if (command === "provider_config_status") return unconfigured;
      if (command === "provider_config_save") return configured;
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };
    render(<ProviderSettingsControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("配置 AI"));
    await user.click(screen.getByRole("button", { name: "打开 AI 设置" }));
    const dialog = screen.getByRole("dialog", { name: "OpenAI 兼容中转站" });
    await user.type(within(dialog).getByLabelText("中转站地址"), "https://relay.example");
    await user.clear(within(dialog).getByLabelText("模型"));
    await user.type(within(dialog).getByLabelText("模型"), "gpt-test");
    await user.type(within(dialog).getByLabelText("API Key"), "sk-test-only-secret");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_config_save", {
      baseUrl: "https://relay.example",
      apiKey: "sk-test-only-secret",
      model: "gpt-test",
    }));
    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已配置"));
    expect(within(dialog).getByLabelText("API Key")).toHaveValue("");
    expect(within(dialog).getByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "留空以继续使用已保存密钥",
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new Error("empty response")),
    });
    vi.stubGlobal("fetch", fetchMock);
    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/providers/llm/probe", expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(screen.getByRole("button", { name: "打开 AI 设置" })).toHaveTextContent("AI 已连接"));
  });
});
