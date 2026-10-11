import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { HttpKnowledgeApi } from "../api/knowledge";
import { HttpMaterialsApi } from "../api/materials";
import { HttpProjectOutcomesApi } from "../api/projectOutcomes";
import { HttpMemoryApi } from "../api/memory";
import { HttpMemoryReconcileApi } from "../api/memoryReconcile";
import { HttpProjectReconcileApi } from "../api/projectReconcile";
import { HttpTranscriptRecoveryApi } from "../api/transcriptRecovery";
import { KnowledgeLibrary } from "../features/knowledge-library/KnowledgeLibrary";
import { HttpMeetingApi } from "../api/client";
import { PollingEventTransport, SseEventTransport } from "../api/eventTransport";
import { resolveLocalApiBase } from "../api/localApiBase";
import { LiveMeetingWorkbench } from "../features/live-meeting/LiveMeetingWorkbench";
import type { MeetingSessionState } from "../features/live-meeting/meetingSessionState";
import { useMeetingMicrophone } from "../features/live-meeting/useMeetingMicrophone";
import { LocalCapabilities } from "../features/local-capabilities/LocalCapabilities";
import { NotesCenter } from "../features/notes/NotesCenter";
import type { DocumentSourceEvidence, MeetingAttachmentSourceEvidence } from "../domain/events";
import type { ActiveMeetingNavigation } from "../components/ProductNavigation";
import { createMeetingId, resolveMeetingId } from "./meetingId";
import { discoverMeetingNavigation } from "./meetingNavigationState";

type ProductView = "meetings" | "notes" | "knowledge" | "capabilities";

function resolveView(search: string): ProductView {
  const view = new URLSearchParams(search).get("view");
  return view === "notes" || view === "knowledge" || view === "capabilities" ? view : "meetings";
}

export function App() {
  const [meetingId, setMeetingId] = useState(() => resolveMeetingId(window.location.search));
  const [view, setView] = useState<ProductView>(() => resolveView(window.location.search));
  const [attachmentSource, setAttachmentSource] = useState<MeetingAttachmentSourceEvidence | null>(null);
  const [activeMeetingId, setActiveMeetingId] = useState<string | null>(null);
  const [recoverableMeetingId, setRecoverableMeetingId] = useState<string | null>(null);
  const [externalCaptureMeetingId, setExternalCaptureMeetingId] = useState<string | null>(null);
  const apiBase = useMemo(
    () => resolveLocalApiBase(import.meta.env.VITE_API_BASE_URL ?? ""),
    [],
  );
  const api = useMemo(() => new HttpMeetingApi(apiBase), [apiBase]);
  const knowledgeApi = useMemo(() => new HttpKnowledgeApi(apiBase), [apiBase]);
  const materialsApi = useMemo(() => new HttpMaterialsApi(apiBase), [apiBase]);
  const projectApi = useMemo(() => new HttpProjectOutcomesApi(apiBase), [apiBase]);
  const memoryApi = useMemo(() => new HttpMemoryApi(apiBase), [apiBase]);
  const memoryReconcileApi = useMemo(() => new HttpMemoryReconcileApi(apiBase), [apiBase]);
  const reconcileApi = useMemo(() => new HttpProjectReconcileApi(apiBase), [apiBase]);
  const recoveryApi = useMemo(() => new HttpTranscriptRecoveryApi(apiBase), [apiBase]);
  const microphone = useMeetingMicrophone({ asrBaseUrl: apiBase });
  const transport = useMemo(
    () =>
      import.meta.env.VITE_EVENT_TRANSPORT === "poll"
        ? new PollingEventTransport(api)
        : new SseEventTransport(apiBase),
    [api, apiBase],
  );

  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refreshMeetingNavigation = async () => {
      controller?.abort();
      const requestController = new AbortController();
      controller = requestController;
      try {
        const history = api.listMeetingsPage
          ? await api.listMeetingsPage({ status: "live", limit: 100 }, requestController.signal)
          : await api.listMeetings(requestController.signal);
        if (disposed || requestController.signal.aborted) return;
        const discovered = discoverMeetingNavigation(history.meetings, activeMeetingId);
        setExternalCaptureMeetingId(discovered.externalMeetingId);
        setRecoverableMeetingId(activeMeetingId ? null : discovered.recoverableMeetingId);
      } catch {
        // Keep the last known target during a transient refresh failure.
      }
    };
    void refreshMeetingNavigation();
    const timer = window.setInterval(() => void refreshMeetingNavigation(), 3_000);
    return () => {
      disposed = true;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [activeMeetingId, api]);

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
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    url.searchParams.delete("source");
    url.searchParams.delete("project_id");
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
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    url.searchParams.delete("source");
    url.searchParams.delete("project_id");
    for (const alias of ["meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(nextMeetingId);
    setView("meetings");
  }, []);

  const openNotes = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "notes");
    url.searchParams.delete("evidence");
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    url.searchParams.delete("source");
    url.searchParams.delete("project_id");
    for (const alias of ["meeting_id", "meeting", "session_id", "session"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(null);
    setView("notes");
  }, []);

  const openKnowledge = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "knowledge");
    url.searchParams.delete("document_id");
    url.searchParams.delete("project_tab");
    for (const alias of ["meeting_id", "meeting", "session_id", "session", "evidence", "source", "project_id"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    window.history.replaceState(window.history.state, "", url);
    setMeetingId(null);
    setView("knowledge");
  }, []);

  const openKnowledgeDocument = useCallback((target: { documentId: string; projectId: string }) => {
    const url = new URL(window.location.href); url.searchParams.set("view", "knowledge"); url.searchParams.set("document_id", target.documentId); url.searchParams.set("project_id", target.projectId);
    for (const field of ["meeting_id", "meeting", "session_id", "session", "evidence", "source", "project_tab", "source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    window.history.pushState(window.history.state, "", url); setMeetingId(null); setView("knowledge");
  }, []);
  const openProjectProposals = useCallback((projectId: string) => {
    const url = new URL(window.location.href); url.searchParams.set("view", "knowledge"); url.searchParams.set("project_id", projectId); url.searchParams.set("project_tab", "proposals");
    for (const field of ["meeting_id", "meeting", "session_id", "session", "evidence", "document_id", "source", "source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    window.history.pushState(window.history.state, "", url); setMeetingId(null); setView("knowledge");
  }, []);

  const openKnowledgeSource = useCallback((value: DocumentSourceEvidence, sourceMeetingId?: string) => {
    if (value.sourceType === "meeting_attachment") {
      const url = new URL(window.location.href);
      url.searchParams.set("meeting_id", value.meetingId); url.searchParams.delete("view");
      window.history.pushState(window.history.state, "", url);
      setMeetingId(value.meetingId); setView("meetings"); setAttachmentSource(value);
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("view", "knowledge");
    url.searchParams.set("source", value.sourceId);
    url.searchParams.delete("project_tab");
    url.searchParams.delete("document_id");
    if (sourceMeetingId) url.searchParams.set("source_meeting_id", sourceMeetingId);
    else url.searchParams.delete("source_meeting_id");
    url.searchParams.set("source_context_revision", String(value.contextRevision));
    url.searchParams.set("source_version_id", value.versionId);
    url.searchParams.set("source_content_hash", value.contentHash);
    url.searchParams.set("project_id", value.projectId);
    for (const alias of ["meeting_id", "meeting", "session_id", "session", "evidence"]) url.searchParams.delete(alias);
    window.history.pushState(window.history.state, "", url);
    setMeetingId(null); setView("knowledge");
  }, []);

  const openCapabilities = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "capabilities");
    url.searchParams.delete("evidence");
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    url.searchParams.delete("source");
    url.searchParams.delete("project_id");
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
    for (const field of ["source_meeting_id", "source_context_revision", "source_version_id", "source_content_hash"]) url.searchParams.delete(field);
    url.searchParams.delete("source");
    url.searchParams.delete("project_id");
    window.history.pushState(window.history.state, "", url);
    setMeetingId(null);
    setView("meetings");
  }, []);

  const activeMeeting = useMemo<ActiveMeetingNavigation | null>(() => {
    const navigableMeetingId = activeMeetingId ?? externalCaptureMeetingId ?? recoverableMeetingId;
    if (!navigableMeetingId) return null;
    const state: ActiveMeetingNavigation["state"] = !activeMeetingId && externalCaptureMeetingId
      ? "external"
      : microphone.state.phase === "paused"
      ? "paused"
      : microphone.state.phase === "reconnecting"
        ? "reconnecting"
        : ["requesting", "connecting", "starting", "recording", "stopping"].includes(microphone.state.phase)
          ? "capturing"
          : "recoverable";
    return {
      meetingId: navigableMeetingId,
      state,
      elapsedMs: activeMeetingId ? microphone.state.elapsedMs : null,
    };
  }, [activeMeetingId, externalCaptureMeetingId, microphone.state.elapsedMs, microphone.state.phase, recoverableMeetingId]);

  const openActiveMeeting = useCallback(() => {
    if (activeMeeting) openMeeting(activeMeeting.meetingId);
  }, [activeMeeting, openMeeting]);

  const markMeetingStarted = useCallback((startedMeetingId: string) => {
    setRecoverableMeetingId((current) => current === startedMeetingId ? null : current);
    setExternalCaptureMeetingId((current) => current === startedMeetingId ? null : current);
    setActiveMeetingId(startedMeetingId);
  }, []);

  const markMeetingEnded = useCallback((endedMeetingId: string) => {
    setActiveMeetingId((current) => current === endedMeetingId ? null : current);
    setRecoverableMeetingId((current) => current === endedMeetingId ? null : current);
    setExternalCaptureMeetingId((current) => current === endedMeetingId ? null : current);
  }, []);

  const trackMeetingSessionState = useCallback((trackedMeetingId: string, sessionState: MeetingSessionState) => {
    if (sessionState === "capturing_elsewhere") {
      setExternalCaptureMeetingId(trackedMeetingId);
      setRecoverableMeetingId((current) => current === trackedMeetingId ? null : current);
      return;
    }
    if (sessionState === "recoverable") {
      setExternalCaptureMeetingId((current) => current === trackedMeetingId ? null : current);
      setRecoverableMeetingId((current) => activeMeetingId ? current : trackedMeetingId);
      return;
    }
    if (sessionState === "ended" || sessionState === "idle") {
      setRecoverableMeetingId((current) => current === trackedMeetingId ? null : current);
      setExternalCaptureMeetingId((current) => current === trackedMeetingId ? null : current);
    }
  }, [activeMeetingId]);

  const clearEvidenceTarget = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("evidence");
    window.history.replaceState(window.history.state, "", url);
  }, []);

  if (view === "knowledge") {
    const search = new URLSearchParams(window.location.search);
    return <KnowledgeLibrary api={knowledgeApi} projectApi={projectApi} memoryApi={memoryApi} memoryReconcileApi={memoryReconcileApi} reconcileApi={reconcileApi} initialProjectTab={search.get("project_tab") === "proposals" ? "proposals" : undefined} onOpenMeetings={returnToMeetingList} onOpenNotes={openNotes}
      onOpenCapabilities={openCapabilities} onOpenMeeting={openMeeting} activeMeeting={activeMeeting} initialDocumentId={search.get("document_id")}
      onOpenActiveMeeting={openActiveMeeting} initialSourceId={search.get("source")} initialProjectId={search.get("project_id")} initialMeetingId={search.get("source_meeting_id")} initialContextRevision={search.has("source_context_revision") ? Number(search.get("source_context_revision")) : null} initialVersionId={search.get("source_version_id")} initialContentHash={search.get("source_content_hash")} />;
  }

  if (view === "notes") {
    return (
      <NotesCenter
        onOpenKnowledgeDocument={openKnowledgeDocument}
        api={api}
        knowledgeApi={knowledgeApi}
        onKnowledgeSource={openKnowledgeSource}
        onOpenMeetings={returnToMeetingList}
        onOpenMeeting={openMeeting}
        onOpenEvidence={openMeetingEvidence}
        onOpenCapabilities={openCapabilities}
        onOpenKnowledge={openKnowledge}
        activeMeeting={activeMeeting}
        onOpenActiveMeeting={openActiveMeeting}
      />
    );
  }

  if (view === "capabilities") {
    return (
      <LocalCapabilities
        api={api}
        onOpenMeetings={returnToMeetingList}
        onOpenNotes={openNotes}
        onOpenKnowledge={openKnowledge}
        activeMeeting={activeMeeting}
        onOpenActiveMeeting={openActiveMeeting}
      />
    );
  }

  return (
    <LiveMeetingWorkbench
      materialsApi={materialsApi}
      initialAttachmentSource={attachmentSource}
      onAttachmentSourceClosed={() => setAttachmentSource(null)}
      meetingId={meetingId}
      api={api}
      knowledgeApi={knowledgeApi}
      projectApi={projectApi}
      memoryApi={memoryApi}
      reconcileApi={reconcileApi}
      recoveryApi={recoveryApi}
      onOpenKnowledgeDocument={openKnowledgeDocument}
      onOpenProjectProposals={openProjectProposals}
      onKnowledgeSource={openKnowledgeSource}
      transport={transport}
      asrBaseUrl={apiBase}
      onCreateMeeting={createMeeting}
      onOpenMeeting={openMeeting}
      onBackToMeetings={returnToMeetingList}
      onOpenNotes={openNotes}
      onOpenCapabilities={openCapabilities}
      onOpenKnowledge={openKnowledge}
      initialEvidenceSegmentId={new URLSearchParams(window.location.search).get("evidence")}
      onEvidenceFocused={clearEvidenceTarget}
      microphoneController={microphone}
      activeCaptureMeetingId={activeMeetingId}
      activeMeeting={activeMeeting}
      onOpenActiveMeeting={openActiveMeeting}
      onMeetingStarted={markMeetingStarted}
      onMeetingEnded={markMeetingEnded}
      onMeetingSessionStateChange={trackMeetingSessionState}
    />
  );
}
