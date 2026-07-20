export function fallbackMeetingTitle(timestamp = Date.now(), meetingId = ""): string {
  if (Number.isFinite(timestamp) && timestamp > 0) {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(timestamp);
    const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${value.year}年${value.month}月${value.day}日 ${value.hour}:${value.minute} 的会议`;
  }
  const suffix = meetingId.trim().slice(-8);
  return suffix ? `会议 ${suffix}` : "会议记录";
}

export function meetingDisplayTitle(
  title: string | null | undefined,
  timestamp?: number | null,
  meetingId = "",
): string {
  return title?.trim() || fallbackMeetingTitle(timestamp ?? 0, meetingId);
}
