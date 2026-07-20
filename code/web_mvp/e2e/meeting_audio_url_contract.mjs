export function isMeetingAudioContentUrl(value, baseUrl) {
  if (typeof value !== "string" || !value.trim()) return false;
  try {
    const base = new URL(baseUrl);
    const candidate = new URL(value, base);
    if (candidate.origin !== base.origin) return false;
    const parts = candidate.pathname.split("/").filter(Boolean);
    if (
      parts.length < 5
      || parts[0] !== "v2"
      || parts[1] !== "meetings"
      || !parts[2]
      || parts[3] !== "audio"
    ) return false;
    const tail = parts.slice(4);
    if (tail.length === 1) return tail[0] === "content";
    if (tail.length !== 3 || tail[2] !== "content" || !tail[1]) return false;
    if (tail[0] === "tracks") return tail[1] === "microphone" || tail[1] === "system_audio";
    return tail[0] === "mixed";
  } catch {
    return false;
  }
}
