import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { ApiError, type MeetingApi } from "../api/client";
import type { MeetingEventTransport } from "../api/eventTransport";
import {
  isFormalLlmFirstPayload,
  isLocalReflexCoachPayload,
  isRealtimeAiProjectionEventType,
} from "../domain/events";
import type { MeetingEvent, MeetingViewState, SuggestionFeedback } from "../domain/events";
import { createInitialMeetingState, meetingReducer } from "../domain/reducer";

const ASR_FINALIZATION_RETRY_WINDOW_MS = 10_000;
const ASR_FINALIZATION_RETRY_INTERVAL_MS = 250;

function isAsrFinalizationPending(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  const body = error.body;
  if (!body || typeof body !== "object" || !("detail" in body)) return false;
  const detail = (body as { detail?: unknown }).detail;
  return Boolean(
    detail
    && typeof detail === "object"
    && "error" in detail
    && (detail as { error?: unknown }).error === "asr_finalization_pending",
  );
}

async function endMeetingAfterAsrFinalization(
  api: MeetingApi,
  meetingId: string,
): Promise<void> {
  const deadline = Date.now() + ASR_FINALIZATION_RETRY_WINDOW_MS;
  while (true) {
    try {
      await api.endMeeting(meetingId);
      return;
    } catch (error) {
      if (!isAsrFinalizationPending(error) || Date.now() >= deadline) throw error;
      await new Promise<void>((resolve) => window.setTimeout(resolve, ASR_FINALIZATION_RETRY_INTERVAL_MS));
    }
  }
}

export function useMeetingProjection(
  meetingId: string | null,
  api: MeetingApi,
  transport: MeetingEventTransport,
) {
  const normalizedMeetingId = meetingId?.trim() ?? "";
  const [state, dispatch] = useReducer(meetingReducer, normalizedMeetingId, createInitialMeetingState);
  const snapshotInFlight = useRef(false);
  const lastSeqRef = useRef(0);
  const pendingRenderAcksRef = useRef(new Map<string, MeetingEvent>());
  const sentRenderAcksRef = useRef(new Set<string>());

  useEffect(() => {
    dispatch({ type: "meeting.bound", meetingId: normalizedMeetingId });
    lastSeqRef.current = 0;
    pendingRenderAcksRef.current.clear();
    sentRenderAcksRef.current.clear();
  }, [normalizedMeetingId]);

  const queueRenderAck = (event: MeetingEvent) => {
    const isIntelligenceEvent = event.type === "meeting.intelligence.applied";
    if (!isIntelligenceEvent && event.type !== "suggestion.committed" && event.type !== "transcript.segment.revised") return;
    if (isIntelligenceEvent) {
      // Formal LLM-first events and validated provider-less local reflexes
      // both have a trace. Other intelligence payloads are intentionally
      // ignored so a malformed/legacy event cannot become a render sample.
      if (!isFormalLlmFirstPayload(event.payload) && !isLocalReflexCoachPayload(event.payload)) return;
      // Silent/terminal decisions do not produce a card. Do not retain them
      // in the pending map while a long meeting continues to receive events.
      if (!eventMayRenderCard(event)) return;
    } else if (isRealtimeAiProjectionEventType(event.type) && !isFormalLlmFirstPayload(event.payload)) {
      return;
    }
    const jobId = eventJobId(event);
    if (!jobId) return;
    pendingRenderAcksRef.current.set(`${jobId}:${event.seq}`, event);
  };

  useEffect(() => {
    lastSeqRef.current = state.lastSeq;
  }, [state.lastSeq]);

  const refreshSnapshot = useCallback(
    async (signal?: AbortSignal) => {
      if (!normalizedMeetingId || snapshotInFlight.current) return;
      snapshotInFlight.current = true;
      try {
        const snapshot = await api.getSnapshot(normalizedMeetingId, signal);
        dispatch({ type: "snapshot.received", snapshot, receivedAtMs: Date.now() });
        lastSeqRef.current = Math.max(lastSeqRef.current, snapshot.lastSeq);
      } finally {
        snapshotInFlight.current = false;
      }
    },
    [api, normalizedMeetingId],
  );

  const loadFullTranscript = useCallback(
    async (signal?: AbortSignal) => {
      if (!normalizedMeetingId) return;
      dispatch({ type: "transcript.loading" });
      try {
        const segments = await api.getTranscript(normalizedMeetingId, signal);
        dispatch({ type: "transcript.received", segments });
      } catch (error) {
        if (signal?.aborted) return;
        dispatch({
          type: "transcript.failed",
          error: error instanceof Error ? error.message : "完整会议文字加载失败",
        });
      }
    },
    [api, normalizedMeetingId],
  );

  const loadAudio = useCallback(
    async (signal?: AbortSignal) => {
      if (!normalizedMeetingId) return;
      dispatch({ type: "audio.loading" });
      try {
        dispatch({ type: "audio.received", audio: await api.getAudio(normalizedMeetingId, signal) });
      } catch (error) {
        if (signal?.aborted) return;
        dispatch({
          type: "audio.failed",
          error: error instanceof Error ? error.message : "录音状态加载失败",
        });
      }
    },
    [api, normalizedMeetingId],
  );

  const loadSpeakers = useCallback(
    async (signal?: AbortSignal) => {
      if (!normalizedMeetingId) return;
      dispatch({ type: "speakers.loading" });
      try {
        const speakers = await api.getSpeakers(normalizedMeetingId, signal);
        dispatch({ type: "speakers.received", speakers });
      } catch (error) {
        if (signal?.aborted) return;
        dispatch({
          type: "speakers.failed",
          error: error instanceof Error ? error.message : "说话人信息加载失败",
        });
        throw error;
      }
    },
    [api, normalizedMeetingId],
  );

  useEffect(() => {
    if (!normalizedMeetingId) return;
    const controller = new AbortController();
    let unsubscribe: () => void = () => undefined;
    let snapshotTimer: number | undefined;

    dispatch({ type: "connection.changed", connection: "connecting", error: null });
    const start = async () => {
      try {
        await refreshSnapshot(controller.signal);
        void loadSpeakers(controller.signal).catch(() => undefined);
      } catch (error) {
        if (controller.signal.aborted) return;
        dispatch({
          type: "connection.changed",
          connection: "reconnecting",
          error: error instanceof Error ? error.message : "会议数据加载失败",
        });
      }

      if (controller.signal.aborted) return;
      unsubscribe = transport.subscribe({
        meetingId: normalizedMeetingId,
        afterSeq: lastSeqRef.current,
        signal: controller.signal,
        onEvents: (events) => {
          events.forEach(queueRenderAck);
          dispatch({ type: "events.received", events, receivedAtMs: Date.now() });
          lastSeqRef.current = Math.max(lastSeqRef.current, ...events.map((event) => event.seq));
          if (events.some((event) =>
            event.type === "recording.export.ready" ||
            event.type === "recording.failed"
          )) {
            void loadAudio(controller.signal);
          }
          void refreshSnapshot(controller.signal).catch((error) => {
            if (!controller.signal.aborted) {
              dispatch({
                type: "connection.changed",
                connection: "reconnecting",
                error: error instanceof Error ? error.message : "会议状态读取失败",
              });
            }
          });
        },
        onConnection: (connection, error) =>
          dispatch({ type: "connection.changed", connection, error: error ?? null }),
      });

      snapshotTimer = window.setInterval(() => {
        void refreshSnapshot(controller.signal).catch((error) => {
          if (!controller.signal.aborted) {
            dispatch({
              type: "connection.changed",
              connection: "reconnecting",
              error: error instanceof Error ? error.message : "会议状态读取失败",
            });
          }
        });
      }, 3_000);
    };

    void start();
    return () => {
      controller.abort();
      unsubscribe();
      if (snapshotTimer !== undefined) window.clearInterval(snapshotTimer);
    };
  }, [loadAudio, loadSpeakers, normalizedMeetingId, refreshSnapshot, transport]);

  useEffect(() => {
    if (state.runtime.phase !== "ended") return;
    const controller = new AbortController();
    void loadFullTranscript(controller.signal);
    void loadAudio(controller.signal);
    return () => controller.abort();
  }, [loadAudio, loadFullTranscript, state.runtime.phase]);

  useEffect(() => {
    const ready = [...pendingRenderAcksRef.current.entries()].filter(([, event]) => eventIsRendered(state, event));
    if (!ready.length) return;
    const frame = window.requestAnimationFrame(() => {
      for (const [key, event] of ready) {
        if (sentRenderAcksRef.current.has(key)) continue;
        // State projection alone does not prove that the card is mounted:
        // switching away from the Insights tab unmounts the rail. Keep the
        // event pending until the corresponding card is present in the DOM.
        if (!eventHasVisibleUiTarget(event)) continue;
        const jobId = eventJobId(event);
        if (!jobId) continue;
        sentRenderAcksRef.current.add(key);
        pendingRenderAcksRef.current.delete(key);
        const draftSeq = typeof event.payload.final_draft_seq === "number"
          ? event.payload.final_draft_seq
          : typeof event.payload.draft_seq === "number"
            ? event.payload.draft_seq
            : 0;
        void api.markUiRendered(jobId, event.seq, draftSeq).catch(() => undefined);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [api, state]);

  const actions = useMemo(
    () => ({
      async endMeeting() {
        if (!normalizedMeetingId || state.ending) return;
        dispatch({ type: "meeting.ending" });
        try {
          await endMeetingAfterAsrFinalization(api, normalizedMeetingId);
          await refreshSnapshot();
        } catch (error) {
          dispatch({
            type: "meeting.end_failed",
            error: error instanceof Error ? error.message : "结束会议失败",
          });
          throw error;
        }
      },
      async saveSuggestionFeedback(suggestionId: string, feedback: SuggestionFeedback) {
        if (!normalizedMeetingId) return;
        await api.saveSuggestionFeedback(normalizedMeetingId, suggestionId, feedback);
        dispatch({ type: "suggestion.feedback_saved", suggestionId, feedback });
      },
      async refresh() {
        await refreshSnapshot();
        await loadSpeakers().catch(() => undefined);
      },
      loadFullTranscript: () => loadFullTranscript(),
      loadAudio: () => loadAudio(),
      async renameSpeaker(speakerId: string, speakerLabel: string) {
        if (!normalizedMeetingId) return;
        const speaker = await api.renameSpeaker(normalizedMeetingId, speakerId, speakerLabel);
        dispatch({ type: "speaker.renamed", speaker });
        await Promise.allSettled([refreshSnapshot(), loadSpeakers()]);
      },
    }),
    [api, loadAudio, loadFullTranscript, loadSpeakers, normalizedMeetingId, refreshSnapshot, state.ending],
  );

  return { state, actions, transportKind: transport.kind };
}

function eventIsRendered(state: MeetingViewState, event: MeetingEvent): boolean {
  if (event.seq > state.lastSeq) return false;
  if (event.type === "meeting.intelligence.applied") {
    if (state.runtime.phase === "ended") return false;

    const payload = event.payload;
    const intervention = payloadRecord(payload.coach_intervention ?? payload.coachIntervention);
    const semanticFollowUp = payloadRecord(payload.semantic_follow_up ?? payload.semanticFollowUp);
    const legacyFollowUp = payloadRecord(payload.follow_up ?? payload.followUp);
    const decision = payloadRecord(payload.coach_decision ?? payload.coachDecision);
    const decisionStatus = payloadString(decision, "status");
    const coachRenderable = Boolean(intervention) && (!decisionStatus || decisionStatus === "intervention");

    // A protected-silent/timeout/failed decision deliberately has no visible
    // intervention card. A semantic follow-up can still be rendered in its
    // own lane, so evaluate that lane independently.
    if (coachRenderable || (!semanticFollowUp && legacyFollowUp && (!decisionStatus || decisionStatus === "intervention"))) {
      const current = state.followUp;
      if (!current || (current.status !== undefined && current.status !== "intervention")) return false;
      return projectionMatchesEvent(current, state, event, decision);
    }
    if (semanticFollowUp) {
      const current = state.semanticFollowUp;
      if (!current || (current.status !== undefined && current.status !== "intervention")) return false;
      return projectionMatchesEvent(current, state, event, null);
    }
    return false;
  }
  if (event.type === "suggestion.committed") {
    const generationId = typeof event.payload.generation_id === "string"
      ? event.payload.generation_id
      : event.correlationId;
    return state.suggestions.some((suggestion) =>
      suggestion.suggestionId === event.aggregateId &&
      suggestion.generationId === generationId &&
      suggestion.status === "committed",
    );
  }
  if (event.type === "transcript.segment.revised") {
    const revision = typeof event.payload.revision === "number" ? event.payload.revision : 0;
    return state.segments.some((segment) =>
      segment.segmentId === event.aggregateId && segment.revision >= revision,
    );
  }
  return false;
}

function payloadRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function payloadString(value: Record<string, unknown> | null, ...keys: string[]): string | null {
  if (!value) return null;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return null;
}

function eventJobId(event: MeetingEvent): string | null {
  return payloadString(event.payload, "job_id", "jobId")
    ?? (typeof event.causationId === "string" && event.causationId.trim() ? event.causationId.trim() : null);
}

function eventMayRenderCard(event: MeetingEvent): boolean {
  if (event.type !== "meeting.intelligence.applied") return true;
  const payload = event.payload;
  const decision = payloadRecord(payload.coach_decision ?? payload.coachDecision);
  const decisionStatus = payloadString(decision, "status");
  const hasCopy = (value: Record<string, unknown> | null) => Boolean(
    payloadString(value, "say_this", "sayThis", "question", "recommendation") &&
    payloadString(value, "why_now", "whyNow", "reason"),
  );
  const intervention = payloadRecord(payload.coach_intervention ?? payload.coachIntervention);
  if (intervention && (!decisionStatus || decisionStatus === "intervention") && hasCopy(intervention)) return true;
  const semantic = payloadRecord(payload.semantic_follow_up ?? payload.semanticFollowUp);
  if (semantic && hasCopy(semantic)) return true;
  const legacy = payloadRecord(payload.follow_up ?? payload.followUp);
  return Boolean(legacy && (!decisionStatus || decisionStatus === "intervention") && hasCopy(legacy));
}

function projectionMatchesEvent(
  projection: MeetingViewState["followUp"] | MeetingViewState["semanticFollowUp"],
  state: MeetingViewState,
  event: MeetingEvent,
  decision: Record<string, unknown> | null,
): boolean {
  if (!projection) return false;
  const jobId = eventJobId(event);
  const projectionJobId = projection.formalAi?.jobId ?? (decision ? state.coachDecision?.jobId : null);
  if (jobId && projectionJobId && jobId !== projectionJobId) return false;

  const eventDecisionId = payloadString(decision, "decision_id", "decisionId");
  const projectionDecisionId = decision ? state.coachDecision?.decisionId ?? projection.decisionId : null;
  if (eventDecisionId && projectionDecisionId && eventDecisionId !== projectionDecisionId) return false;
  return true;
}

function eventHasVisibleUiTarget(event: MeetingEvent): boolean {
  // Transcript revisions have historically used state projection as their
  // receipt. Coach and suggestion traces must additionally prove that the
  // corresponding card is mounted in the browser before becoming
  // `ui_rendered`.
  if (event.type !== "meeting.intelligence.applied" && event.type !== "suggestion.committed") return true;
  const jobId = eventJobId(event);
  if (!jobId || typeof document === "undefined") return false;
  const nodes = document.querySelectorAll<HTMLElement>("[data-ui-render-job-id]");
  return Array.from(nodes).some((node) => {
    if (node.dataset.uiRenderJobId !== jobId) return false;
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return false;
    if (typeof window.getComputedStyle !== "function") return true;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden";
  });
}
