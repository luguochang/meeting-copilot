import { useEffect, useRef, useState } from "react";
import type { NowRailProps } from "./nowRailTypes";

export interface CoachRequest {
  id: string;
  answerId: string;
  jobId: string;
  request: string;
  createdAtMs: number;
  status: "sending" | "pending" | "succeeded" | "failed" | "no_change";
  error?: string;
}

const submittingRequests = new Set<string>();
const REQUEST_UPDATED = "meeting-copilot:coach-request-updated";

function readRequests(key: string): CoachRequest[] {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(key) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((item): item is CoachRequest => Boolean(item)
      && typeof item.id === "string" && typeof item.answerId === "string"
      && typeof item.request === "string" && typeof item.jobId === "string"
      && typeof item.createdAtMs === "number"
      && ["sending", "pending", "succeeded", "failed", "no_change"].includes(item.status));
  } catch { return []; }
}

function initialRequests(key: string, meetingKey: string): CoachRequest[] {
  const stored = readRequests(key);
  if (stored.length) return stored.map((item) => item.status === "sending" && !submittingRequests.has(`${key}:${item.id}`)
    ? { ...item, status: "failed", error: "提交状态未确认，请重试这次补充。" } : item);
  // Preserve an in-flight request created by the previous reader UI.
  try {
    const old = JSON.parse(sessionStorage.getItem(`meeting-copilot-coach-request:${meetingKey}`) ?? "null");
    if (!old || typeof old.answerId !== "string" || typeof old.request !== "string" || typeof old.jobId !== "string") return [];
    return [{ id: old.jobId || "restored-request", answerId: old.answerId, request: old.request,
      jobId: old.jobId, createdAtMs: Date.now(), status: ["succeeded", "failed", "no_change"].includes(old.outcome) ? old.outcome : old.jobId ? "pending" : "failed" }];
  } catch { return []; }
}

export function useCoachRequests({ viewStateKey, onCoachRefine, onCoachRequestStatus }: Pick<NowRailProps,
  "viewStateKey" | "onCoachRefine" | "onCoachRequestStatus">) {
  const meetingKey = viewStateKey ?? "current";
  const key = `meeting-copilot-coach-requests:${meetingKey}`;
  const [requests, setRequests] = useState(() => initialRequests(key, meetingKey));
  const [connectionNotice, setConnectionNotice] = useState("");
  const scopeRef = useRef<object>({});
  const requestsRef = useRef(requests);
  const submitting = useRef(false);
  useEffect(() => {
    scopeRef.current = {};
    requestsRef.current = initialRequests(key, meetingKey);
    setRequests(requestsRef.current);
    submitting.current = false;
    setConnectionNotice("");
    const receive = (event: Event) => {
      const update = (event as CustomEvent<{ key: string; requests: CoachRequest[] }>).detail;
      if (update.key !== key) return;
      requestsRef.current = update.requests;
      setRequests(update.requests);
      submitting.current = false;
    };
    window.addEventListener(REQUEST_UPDATED, receive);
    return () => { scopeRef.current = {}; window.removeEventListener(REQUEST_UPDATED, receive); };
  }, [key, meetingKey]);

  const pending = requests.find((item) => item.status === "sending" || item.status === "pending");
  const persist = (next: CoachRequest[]) => {
    try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* Optional UI persistence. */ }
    requestsRef.current = next;
    setRequests(next);
  };

  useEffect(() => {
    if (!pending || pending.status !== "pending" || !pending.jobId || !onCoachRequestStatus) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await onCoachRequestStatus(pending.jobId, controller.signal);
        if (controller.signal.aborted) return;
        setConnectionNotice("");
        if (status !== "pending") {
          const next = requestsRef.current.map((item) => item.id === pending.id ? { ...item, status } : item);
          requestsRef.current = next;
          try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* Optional UI persistence. */ }
          setRequests(next);
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
        setConnectionNotice("暂时无法读取进度，正在重新连接；原回答已保留。");
      }
      timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [pending, onCoachRequestStatus, key]);

  const submit = (answerId: string, text: string, retryId?: string): string | null => {
    if (!text.trim() || !onCoachRefine || submitting.current || requestsRef.current.some((item) => ["sending", "pending"].includes(item.status))) return null;
    submitting.current = true;
    const scope = scopeRef.current;
    const entry: CoachRequest = { id: retryId ?? crypto.randomUUID(), answerId, request: text.trim(),
      jobId: "", status: "sending", createdAtMs: Date.now() };
    submittingRequests.add(`${key}:${entry.id}`);
    persist(retryId ? requestsRef.current.map((item) => item.id === retryId ? entry : item) : [...requestsRef.current, entry]);
    void (async () => {
      let updated: CoachRequest;
      try {
        const jobId = await onCoachRefine(answerId, entry.request);
        if (!jobId) throw new Error("服务未返回任务编号，无法确认补充进度。");
        updated = { ...entry, jobId, status: "pending" };
      } catch (error) {
        updated = { ...entry, status: "failed", error: error instanceof Error ? error.message : "提交失败，请重试。" };
      }
      // A request may finish submitting while the user is in another workspace.
      // Persist to its original meeting, never attach it to the new meeting.
      const current = scopeRef.current === scope ? requestsRef.current : readRequests(key);
      const next = current.map((item) => item.id === entry.id ? updated : item);
      submittingRequests.delete(`${key}:${entry.id}`);
      try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* Optional UI persistence. */ }
      if (scopeRef.current === scope) {
        requestsRef.current = next;
        setRequests(next);
        submitting.current = false;
      }
      window.dispatchEvent(new CustomEvent(REQUEST_UPDATED, { detail: { key, requests: next } }));
    })();
    return entry.id;
  };
  return { requests, pending, connectionNotice, submit };
}
