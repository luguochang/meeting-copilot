import { useCallback, useEffect, useMemo, useState } from "react";
import { HttpMeetingApi } from "../api/client";
import { PollingEventTransport, SseEventTransport } from "../api/eventTransport";
import { LiveMeetingWorkbench } from "../features/live-meeting/LiveMeetingWorkbench";
import { createMeetingId, resolveMeetingId } from "./meetingId";

export function App() {
  const [meetingId, setMeetingId] = useState(() => resolveMeetingId(window.location.search));
  const api = useMemo(() => new HttpMeetingApi(), []);
  const transport = useMemo(
    () =>
      import.meta.env.VITE_EVENT_TRANSPORT === "poll"
        ? new PollingEventTransport(api)
        : new SseEventTransport(import.meta.env.VITE_API_BASE_URL ?? ""),
    [api],
  );

  useEffect(() => {
    const handlePopState = () => setMeetingId(resolveMeetingId(window.location.search));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const createMeeting = useCallback(() => {
    const nextMeetingId = createMeetingId();
    const url = new URL(window.location.href);
    url.searchParams.set("meeting_id", nextMeetingId);
    for (const alias of ["meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.replaceState(window.history.state, "", url);
    setMeetingId(nextMeetingId);
    return nextMeetingId;
  }, []);

  const openMeeting = useCallback((nextMeetingId: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("meeting_id", nextMeetingId);
    for (const alias of ["meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(nextMeetingId);
  }, []);

  const returnToMeetingList = useCallback(() => {
    const url = new URL(window.location.href);
    for (const alias of ["meeting_id", "meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.replaceState(window.history.state, "", url);
    setMeetingId(null);
  }, []);

  return (
    <LiveMeetingWorkbench
      meetingId={meetingId}
      api={api}
      transport={transport}
      asrBaseUrl={import.meta.env.VITE_API_BASE_URL ?? ""}
      onCreateMeeting={createMeeting}
      onOpenMeeting={openMeeting}
      onBackToMeetings={returnToMeetingList}
    />
  );
}
