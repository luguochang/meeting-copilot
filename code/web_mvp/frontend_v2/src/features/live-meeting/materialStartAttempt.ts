import type { MeetingPreparationInput } from "../../domain/events";

export const MATERIAL_START_KEY = "talktrace-material-start-attempt";
export interface MaterialStartAttempt {
  meetingId: string; setId: string; setRevision: number; key: string; created: boolean;
  title: string | null; inputSource: MeetingPreparationInput["inputSource"];
}

export function readMaterialStartAttempt(meetingId: string | null): MaterialStartAttempt | null {
  try {
    const x: unknown = JSON.parse(sessionStorage.getItem(MATERIAL_START_KEY) ?? "null");
    if (!x || typeof x !== "object" || Array.isArray(x)) return null;
    const value = x as Record<string, unknown>;
    if (typeof value.meetingId !== "string" || !value.meetingId || typeof value.setId !== "string" || !value.setId
      || typeof value.key !== "string" || !value.key || !Number.isSafeInteger(value.setRevision) || (value.setRevision as number) < 1
      || typeof value.created !== "boolean" || (value.title !== null && typeof value.title !== "string")
      || !["microphone", "system_audio", "dual_track"].includes(String(value.inputSource))) return null;
    if (meetingId && meetingId !== value.meetingId) return null;
    return value as unknown as MaterialStartAttempt;
  } catch { return null; }
}

export function persistMaterialStartAttempt(value: MaterialStartAttempt | null): void {
  try { if (value) sessionStorage.setItem(MATERIAL_START_KEY, JSON.stringify(value)); else sessionStorage.removeItem(MATERIAL_START_KEY); }
  catch { /* Recording can still start if optional browser storage is unavailable. */ }
}
