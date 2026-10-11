/** Persist an identity only; server GET still decides whether it is a draft. */
export const MATERIAL_DRAFT_SESSION_KEY = "talktrace-material-draft";

export function materialDraftPointerKey(serverScope: string) {
  return `talktrace-material-draft:v1:${encodeURIComponent(serverScope)}`;
}

export function readMaterialDraftPointer(serverScope: string): string | null {
  try {
    const raw = window.localStorage.getItem(materialDraftPointerKey(serverScope));
    if (raw) {
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) return null;
      const pointer = value as Record<string, unknown>;
      if (typeof pointer.setId !== "string" || !pointer.setId.trim() || pointer.setId.length > 200
        || !Number.isSafeInteger(pointer.updatedAtMs) || (pointer.updatedAtMs as number) <= 0
        || (pointer.updatedAtMs as number) > Date.now() + 300_000) return null;
      return pointer.setId;
    }
  } catch { /* Storage may be blocked; the legacy session pointer remains useful. */ }
  // An unscoped legacy receipt can only be migrated for this page's service.
  if (serverScope !== window.location.origin) return null;
  try { return sessionStorage.getItem(MATERIAL_DRAFT_SESSION_KEY); } catch { return null; }
}

export function rememberMaterialDraft(serverScope: string, setId: string) {
  try { window.localStorage.setItem(materialDraftPointerKey(serverScope), JSON.stringify({ setId, updatedAtMs: Date.now() })); } catch { /* Optional persistence. */ }
  if (serverScope === window.location.origin) {
    try { sessionStorage.setItem(MATERIAL_DRAFT_SESSION_KEY, setId); } catch { /* Optional persistence. */ }
  }
}

export function forgetMaterialDraft(serverScope: string, setId: string) {
  try {
    const raw = window.localStorage.getItem(materialDraftPointerKey(serverScope));
    if (raw && (JSON.parse(raw) as { setId?: unknown }).setId === setId) window.localStorage.removeItem(materialDraftPointerKey(serverScope));
  } catch { /* Do not delete a different receipt on malformed input. */ }
  try { if (serverScope === window.location.origin && sessionStorage.getItem(MATERIAL_DRAFT_SESSION_KEY) === setId) sessionStorage.removeItem(MATERIAL_DRAFT_SESSION_KEY); } catch { /* Optional persistence. */ }
}
