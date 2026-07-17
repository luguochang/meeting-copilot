import { useEffect, useRef } from "react";
import { resolveTauriInvoke } from "./tauri";

interface RuntimeStatus {
  command_status?: string;
  implementation_status?: string;
  packaged_same_chain_probe_enabled?: boolean;
}

interface CommandStatus {
  command_status?: string;
  configured?: boolean;
  runtime_synced?: boolean;
  helper_present?: boolean;
  captures_audio?: boolean;
  errors?: string[];
}

function safeErrors(value: CommandStatus): string[] {
  return Array.isArray(value.errors) ? value.errors.filter(Boolean) : [];
}

export function DesktopIpcBootProbe() {
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const invoke = resolveTauriInvoke();
    if (!invoke) return;

    void (async () => {
      let runtime: RuntimeStatus;
      try {
        runtime = await invoke<RuntimeStatus>("runtime_get_status");
      } catch {
        return;
      }
      if (runtime.packaged_same_chain_probe_enabled !== true) return;

      const errors: string[] = [];
      let provider: CommandStatus = {};
      let microphone: CommandStatus = {};
      try {
        provider = await invoke<CommandStatus>("provider_config_status");
        errors.push(...safeErrors(provider).map((error) => `provider:${error}`));
      } catch (error) {
        errors.push(`provider:${error instanceof Error ? error.message : String(error)}`);
      }
      try {
        microphone = await invoke<CommandStatus>("mic_adapter_prepare");
        errors.push(...safeErrors(microphone).map((error) => `microphone:${error}`));
      } catch (error) {
        errors.push(`microphone:${error instanceof Error ? error.message : String(error)}`);
      }

      await invoke("runtime_write_frontend_probe", {
        payload: {
          packaged_ipc_probe: true,
          page: window.location.pathname,
          runtime_command_status: runtime.command_status ?? null,
          runtime_implementation_status: runtime.implementation_status ?? null,
          provider_command_status: provider.command_status ?? null,
          provider_configured: provider.configured === true,
          provider_runtime_synced: provider.runtime_synced === true,
          microphone_command_status: microphone.command_status ?? null,
          microphone_helper_present: microphone.helper_present === true,
          microphone_captures_audio: microphone.captures_audio === true,
          consent_bypassed: false,
          errors,
        },
      });
    })().catch(() => {
      // Failure to write the opt-in smoke artifact must not affect the product UI.
    });
  }, []);

  return null;
}
