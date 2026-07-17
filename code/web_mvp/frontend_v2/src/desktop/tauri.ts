export type TauriInvoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

declare global {
  interface Window {
    __TAURI__?: { core?: { invoke?: TauriInvoke } };
    __TAURI_INTERNALS__?: { invoke?: TauriInvoke };
  }
}

export function resolveTauriInvoke(): TauriInvoke | null {
  const globalInvoke = window.__TAURI__?.core?.invoke;
  if (typeof globalInvoke === "function") return globalInvoke;
  const internalInvoke = window.__TAURI_INTERNALS__?.invoke;
  return typeof internalInvoke === "function" ? internalInvoke : null;
}
