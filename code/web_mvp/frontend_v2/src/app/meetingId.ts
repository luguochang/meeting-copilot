export function resolveMeetingId(search: string): string | null {
  const params = new URLSearchParams(search);
  for (const key of ["meeting_id", "meeting", "session_id", "session"]) {
    const value = params.get(key)?.trim();
    if (value) return value;
  }
  return null;
}

export function createMeetingId(
  nowMs = Date.now(),
  entropy = crypto.getRandomValues(new Uint8Array(6)),
): string {
  const timestamp = Math.max(0, Math.trunc(nowMs)).toString(36);
  const random = [...entropy]
    .slice(0, 12)
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `rec_${timestamp}_${random || "000000000000"}`;
}
