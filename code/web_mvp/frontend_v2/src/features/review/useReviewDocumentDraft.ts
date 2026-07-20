import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReviewDocument, ReviewDocumentKind } from "../../domain/events";

export type DocumentSaveState = "saved" | "unsaved" | "saving" | "error";

interface UseReviewDocumentDraftOptions<T> {
  meetingId: string;
  kind: ReviewDocumentKind;
  document: ReviewDocument | undefined;
  fallback: T;
  enabled: boolean;
  fromContent(content: unknown, fallback: T): T;
  toContent(value: T): unknown;
  onSave(expectedRevision: number, content: unknown): Promise<ReviewDocument>;
}

interface StoredDraft<T> {
  baseRevision: number;
  value: T;
}

function storageKey(meetingId: string, kind: ReviewDocumentKind): string {
  return `meeting-copilot:review-draft:${meetingId}:${kind}`;
}

function readStoredDraft<T>(key: string): StoredDraft<T> | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredDraft<T>>;
    return typeof parsed.baseRevision === "number" && "value" in parsed
      ? parsed as StoredDraft<T>
      : null;
  } catch {
    return null;
  }
}

function conflictRevision(error: unknown): number | null {
  if (!error || typeof error !== "object") return null;
  const candidate = error as { status?: unknown; body?: unknown };
  if (candidate.status !== 409 || !candidate.body || typeof candidate.body !== "object") return null;
  const body = candidate.body as Record<string, unknown>;
  const detail = body.detail && typeof body.detail === "object"
    ? body.detail as Record<string, unknown>
    : body;
  const revision = detail.current_revision ?? detail.currentRevision;
  return typeof revision === "number" && Number.isFinite(revision)
    ? Math.max(0, Math.trunc(revision))
    : null;
}

export function useReviewDocumentDraft<T>({
  meetingId,
  kind,
  document,
  fallback,
  enabled,
  fromContent,
  toContent,
  onSave,
}: UseReviewDocumentDraftOptions<T>) {
  const key = storageKey(meetingId, kind);
  const serverValue = useMemo(
    () => fromContent(document?.contentJson, fallback),
    [document?.contentJson, fallback, fromContent],
  );
  const serverValueJson = JSON.stringify(serverValue);
  const storedOnMount = useMemo(() => readStoredDraft<T>(key), [key]);
  const initialValue = storedOnMount?.value ?? serverValue;
  const [draft, setDraftState] = useState<T>(initialValue);
  const [revision, setRevision] = useState(storedOnMount?.baseRevision ?? document?.revision ?? 0);
  const [saveState, setSaveState] = useState<DocumentSaveState>(storedOnMount ? "unsaved" : "saved");
  const [error, setError] = useState<string | null>(null);
  const [savedAtMs, setSavedAtMs] = useState<number | null>(document?.updatedAtMs ?? null);
  const [recoveredLocalDraft, setRecoveredLocalDraft] = useState(Boolean(storedOnMount));
  const [userFinalProtected, setUserFinalProtected] = useState(document?.source === "user_final");
  const draftRef = useRef(draft);
  const revisionRef = useRef(revision);
  const saveStateRef = useRef(saveState);
  const userFinalProtectedRef = useRef(userFinalProtected);
  const draftIdentityRef = useRef(key);
  const requestGenerationRef = useRef(0);

  useEffect(() => { draftRef.current = draft; }, [draft]);
  useEffect(() => { revisionRef.current = revision; }, [revision]);
  useEffect(() => { saveStateRef.current = saveState; }, [saveState]);
  useEffect(() => { userFinalProtectedRef.current = userFinalProtected; }, [userFinalProtected]);

  useEffect(() => {
    if (draftIdentityRef.current === key) return;
    draftIdentityRef.current = key;
    requestGenerationRef.current += 1;
    const next = storedOnMount?.value ?? (JSON.parse(serverValueJson) as T);
    const nextRevision = storedOnMount?.baseRevision ?? document?.revision ?? 0;
    const nextSaveState = storedOnMount ? "unsaved" : "saved";
    const nextUserFinalProtected = document?.source === "user_final";
    setDraftState(next);
    draftRef.current = next;
    setRevision(nextRevision);
    revisionRef.current = nextRevision;
    setSaveState(nextSaveState);
    saveStateRef.current = nextSaveState;
    setError(null);
    setSavedAtMs(document?.updatedAtMs ?? null);
    setRecoveredLocalDraft(Boolean(storedOnMount));
    setUserFinalProtected(nextUserFinalProtected);
    userFinalProtectedRef.current = nextUserFinalProtected;
  }, [document?.revision, document?.source, document?.updatedAtMs, key, serverValueJson, storedOnMount]);

  useEffect(() => {
    if (saveStateRef.current === "unsaved" || saveStateRef.current === "saving" || saveStateRef.current === "error") return;
    if (document?.source === "ai_generated" && userFinalProtectedRef.current) return;
    const next = JSON.parse(serverValueJson) as T;
    setDraftState(next);
    draftRef.current = next;
    const nextRevision = document?.revision ?? 0;
    setRevision(nextRevision);
    revisionRef.current = nextRevision;
    setSavedAtMs(document?.updatedAtMs ?? null);
    if (document?.source === "user_final") {
      setUserFinalProtected(true);
      userFinalProtectedRef.current = true;
    }
  }, [document?.revision, document?.source, document?.updatedAtMs, serverValueJson]);

  const setDraft = useCallback((value: T | ((current: T) => T)) => {
    setDraftState((current) => {
      const next = typeof value === "function" ? (value as (current: T) => T)(current) : value;
      draftRef.current = next;
      try {
        window.localStorage.setItem(key, JSON.stringify({ baseRevision: revisionRef.current, value: next }));
      } catch {
        // In-memory editing remains available when local storage is unavailable.
      }
      return next;
    });
    setSaveState("unsaved");
    saveStateRef.current = "unsaved";
    setError(null);
  }, [key]);

  const saveNow = useCallback(async (): Promise<boolean> => {
    if (saveStateRef.current === "saving") return false;
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    setSaveState("saving");
    saveStateRef.current = "saving";
    setError(null);
    try {
      const saved = await onSave(revisionRef.current, toContent(draftRef.current));
      if (requestGenerationRef.current !== generation) return false;
      setRevision(saved.revision);
      revisionRef.current = saved.revision;
      setSavedAtMs(saved.updatedAtMs || Date.now());
      setSaveState("saved");
      saveStateRef.current = "saved";
      setRecoveredLocalDraft(false);
      if (saved.source === "user_final") {
        setUserFinalProtected(true);
        userFinalProtectedRef.current = true;
      }
      try {
        window.localStorage.removeItem(key);
      } catch {
        // The server save succeeded even if local storage is unavailable.
      }
      return true;
    } catch (saveError) {
      if (requestGenerationRef.current !== generation) return false;
      const currentRevision = conflictRevision(saveError);
      if (currentRevision !== null) {
        setRevision(currentRevision);
        revisionRef.current = currentRevision;
        try {
          window.localStorage.setItem(key, JSON.stringify({
            baseRevision: currentRevision,
            value: draftRef.current,
          }));
        } catch {
          // The in-memory draft remains available for an explicit retry.
        }
      }
      setSaveState("error");
      saveStateRef.current = "error";
      setError(currentRevision === null
        ? saveError instanceof Error ? saveError.message : "文档保存失败"
        : `服务端已有新版本（版本 ${currentRevision}），本地草稿已保留。确认后可重试保存。`);
      return false;
    }
  }, [key, onSave, toContent]);

  useEffect(() => {
    if (!enabled || saveState !== "unsaved") return;
    const timer = window.setTimeout(() => void saveNow(), 800);
    return () => window.clearTimeout(timer);
  }, [enabled, saveNow, saveState]);

  return {
    draft,
    setDraft,
    saveNow,
    saveState,
    error,
    revision,
    savedAtMs,
    recoveredLocalDraft,
    isUserFinal: userFinalProtected,
  };
}
