import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { MeetingApi } from "../api/client";
import type { MeetingEventTransport } from "../api/eventTransport";
import type { MeetingEvent, MeetingViewState, SuggestionFeedback } from "../domain/events";
import { createInitialMeetingState, meetingReducer } from "../domain/reducer";

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
    if (event.type !== "suggestion.committed" && event.type !== "transcript.segment.revised") return;
    const jobId = typeof event.payload.job_id === "string" && event.payload.job_id.trim()
      ? event.payload.job_id.trim()
      : event.causationId;
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

  useEffect(() => {
    if (!normalizedMeetingId) return;
    const controller = new AbortController();
    let unsubscribe: () => void = () => undefined;
    let snapshotTimer: number | undefined;

    dispatch({ type: "connection.changed", connection: "connecting", error: null });
    const start = async () => {
      try {
        await refreshSnapshot(controller.signal);
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
                error: error instanceof Error ? error.message : "会议状态同步失败",
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
              error: error instanceof Error ? error.message : "会议状态同步失败",
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
  }, [loadAudio, normalizedMeetingId, refreshSnapshot, transport]);

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
        const jobId = typeof event.payload.job_id === "string" && event.payload.job_id.trim()
          ? event.payload.job_id.trim()
          : event.causationId;
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
          await api.endMeeting(normalizedMeetingId);
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
      refresh: () => refreshSnapshot(),
      loadFullTranscript: () => loadFullTranscript(),
      loadAudio: () => loadAudio(),
    }),
    [api, loadAudio, loadFullTranscript, normalizedMeetingId, refreshSnapshot, state.ending],
  );

  return { state, actions, transportKind: transport.kind };
}

function eventIsRendered(state: MeetingViewState, event: MeetingEvent): boolean {
  if (event.seq > state.lastSeq) return false;
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
