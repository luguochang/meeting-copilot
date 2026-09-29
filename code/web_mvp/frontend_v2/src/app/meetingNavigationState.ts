import type { MeetingHistoryItem } from "../domain/events";

export interface DiscoveredMeetingNavigation {
  externalMeetingId: string | null;
  recoverableMeetingId: string | null;
}

export function discoverMeetingNavigation(
  meetings: MeetingHistoryItem[],
  localActiveMeetingId: string | null,
  nowMs = Date.now(),
): DiscoveredMeetingNavigation {
  const liveMeetings = meetings
    .filter((meeting) => meeting.phase === "live" && meeting.meetingId !== localActiveMeetingId)
    .sort((left, right) => {
      const leftActivity = left.capture?.lastHeartbeatAtMs ?? left.updatedAtMs;
      const rightActivity = right.capture?.lastHeartbeatAtMs ?? right.updatedAtMs;
      return rightActivity - leftActivity;
    });
  const external = liveMeetings.find((meeting) => (
    meeting.capture?.state === "active"
    && meeting.capture.leaseUntilMs !== null
    && meeting.capture.leaseUntilMs > nowMs
  ));
  const recoverable = liveMeetings.find((meeting) => (
    meeting.capture?.state === "recoverable"
    || (
      meeting.capture?.state === "active"
      && meeting.capture.leaseUntilMs !== null
      && meeting.capture.leaseUntilMs <= nowMs
    )
  ));
  return {
    externalMeetingId: external?.meetingId ?? null,
    recoverableMeetingId: recoverable?.meetingId ?? null,
  };
}
