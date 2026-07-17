import { render, waitFor } from "@testing-library/react";
import { DesktopIpcBootProbe } from "./DesktopIpcBootProbe";
import type { TauriInvoke } from "./tauri";

afterEach(() => {
  delete window.__TAURI__;
});

it("proves safe packaged IPC commands only when the desktop smoke flag is enabled", async () => {
  const invoke = vi.fn(async (command: string) => {
    if (command === "runtime_get_status") {
      return {
        command_status: "ok",
        implementation_status: "real",
        packaged_same_chain_probe_enabled: true,
      };
    }
    if (command === "provider_config_status") {
      return { command_status: "ok", configured: true, runtime_synced: true, errors: [] };
    }
    if (command === "mic_adapter_prepare") {
      return {
        command_status: "ok",
        helper_present: true,
        captures_audio: false,
        errors: [],
      };
    }
    if (command === "runtime_write_frontend_probe") return { command_status: "ok" };
    throw new Error(`unexpected command: ${command}`);
  });
  window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

  render(<DesktopIpcBootProbe />);

  await waitFor(() => expect(invoke).toHaveBeenCalledWith(
    "runtime_write_frontend_probe",
    expect.objectContaining({
      payload: expect.objectContaining({
        packaged_ipc_probe: true,
        provider_command_status: "ok",
        microphone_command_status: "ok",
        microphone_captures_audio: false,
        consent_bypassed: false,
        errors: [],
      }),
    }),
  ));
  expect(invoke.mock.calls.map(([command]) => command)).toEqual([
    "runtime_get_status",
    "provider_config_status",
    "mic_adapter_prepare",
    "runtime_write_frontend_probe",
  ]);
});

it("does not probe provider or microphone during normal product startup", async () => {
  const invoke = vi.fn(async () => ({
    command_status: "ok",
    packaged_same_chain_probe_enabled: false,
  }));
  window.__TAURI__ = { core: { invoke: invoke as unknown as TauriInvoke } };

  render(<DesktopIpcBootProbe />);

  await waitFor(() => expect(invoke).toHaveBeenCalledTimes(1));
  expect(invoke).toHaveBeenCalledWith("runtime_get_status");
});
