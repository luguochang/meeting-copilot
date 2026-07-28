import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { HttpMeetingApi } from "../api/client";
import { PollingEventTransport, SseEventTransport } from "../api/eventTransport";
import { resolveLocalApiBase } from "../api/localApiBase";
import { LiveMeetingWorkbench } from "../features/live-meeting/LiveMeetingWorkbench";
import { LocalCapabilities } from "../features/local-capabilities/LocalCapabilities";
import { NotesCenter } from "../features/notes/NotesCenter";
import { createMeetingId, resolveMeetingId } from "./meetingId";

type ProductView = "meetings" | "notes" | "capabilities";

function resolveView(search: string): ProductView {
  const view = new URLSearchParams(search).get("view");
  return view === "notes" || view === "capabilities" ? view : "meetings";
}

export function App() {
  const [meetingId, setMeetingId] = useState(() => resolveMeetingId(window.location.search));
  const [view, setView] = useState<ProductView>(() => resolveView(window.location.search));
  const apiBase = useMemo(
    () => resolveLocalApiBase(import.meta.env.VITE_API_BASE_URL ?? ""),
    [],
  );
  const api = useMemo(() => new HttpMeetingApi(apiBase), [apiBase]);
  const transport = useMemo(
    () =>
      import.meta.env.VITE_EVENT_TRANSPORT === "poll"
        ? new PollingEventTransport(api)
        : new SseEventTransport(apiBase),
    [api, apiBase],
  );

  useEffect(() => {
    const handlePopState = () => {
      setMeetingId(resolveMeetingId(window.location.search));
      setView(resolveView(window.location.search));
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useLayoutEffect(() => {
    const scrollingElement = document.scrollingElement ?? document.documentElement;
    scrollingElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, [meetingId, view]);

  const createMeeting = useCallback(() => {
    return createMeetingId();
  }, []);

  const openMeeting = useCallback((nextMeetingId: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("meeting_id", nextMeetingId);
    url.searchParams.delete("view");
    url.searchParams.delete("evidence");
    for (const alias of ["meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(nextMeetingId);
    setView("meetings");
  }, []);

  const openMeetingEvidence = useCallback((nextMeetingId: string, segmentId: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("meeting_id", nextMeetingId);
    url.searchParams.set("evidence", segmentId);
    url.searchParams.delete("view");
    for (const alias of ["meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(nextMeetingId);
    setView("meetings");
  }, []);

  const openNotes = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "notes");
    url.searchParams.delete("evidence");
    for (const alias of ["meeting_id", "meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(null);
    setView("notes");
  }, []);

  const openCapabilities = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "capabilities");
    url.searchParams.delete("evidence");
    for (const alias of ["meeting_id", "meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(null);
    setView("capabilities");
  }, []);

  const returnToMeetingList = useCallback(() => {
    const url = new URL(window.location.href);
    for (const alias of ["meeting_id", "meeting", "session_id", "session"]) url.searchParams.delete(alias);
    url.searchParams.delete("view");
    url.searchParams.delete("evidence");
    window.history.replaceState(window.history.state, "", url);
    setMeetingId(null);
    setView("meetings");
  }, []);

  const clearEvidenceTarget = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("evidence");
    window.history.replaceState(window.history.state, "", url);
  }, []);

  if (view === "notes") {
    return (
      <NotesCenter
        api={api}
        onOpenMeetings={returnToMeetingList}
        onOpenMeeting={openMeeting}
        onOpenEvidence={openMeetingEvidence}
        onOpenCapabilities={openCapabilities}
      />
    );
  }

  if (view === "capabilities") {
    return (
      <LocalCapabilities
        api={api}
        onOpenMeetings={returnToMeetingList}
        onOpenNotes={openNotes}
      />
    );
  }

  return (
    <LiveMeetingWorkbench
      meetingId={meetingId}
      api={api}
      transport={transport}
      asrBaseUrl={apiBase}
      onCreateMeeting={createMeeting}
      onOpenMeeting={openMeeting}
      onBackToMeetings={returnToMeetingList}
      onOpenNotes={openNotes}
      onOpenCapabilities={openCapabilities}
      initialEvidenceSegmentId={new URLSearchParams(window.location.search).get("evidence")}
      onEvidenceFocused={clearEvidenceTarget}
    />
  );
}
