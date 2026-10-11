import type { KnowledgeApi, MeetingKnowledgeSelection } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import type { MemoryApi } from "../../api/memory";
import type { ProjectReconcileApi } from "../../api/projectReconcile";
import type { DocumentSourceEvidence } from "../../domain/events";
import { isPartialCoveredBySegment } from "../../domain/events";
import {
  AlertCircle,
  ArrowLeft,
  CalendarDays,
  CircleCheck,
  Clock3,
  FileAudio,
  FileText,
  Lightbulb,
  LoaderCircle,
  Mic,
  Pause,
  Play,
  Square,
  Users,
  Video,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { MaterialsApi, MaterialSet } from "../../api/materials";
import type { MeetingAttachmentSourceEvidence } from "../../domain/events";
import { MeetingAttachments } from "./MeetingAttachments";
import { persistMaterialStartAttempt, readMaterialStartAttempt, type MaterialStartAttempt } from "./materialStartAttempt";
import type { MeetingApi } from "../../api/client";
import { ApiError, checkAsrCapturePreparation } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import { motionAwareScrollBehavior } from "../../app/motion";
import { useMeetingProjection } from "../../app/useMeetingProjection";
import { BrandMark } from "../../components/BrandMark";
import { DiagnosticsDrawer } from "../../components/DiagnosticsDrawer";
import { MeetingTitleEditor } from "../../components/MeetingTitleEditor";
import { ProductNavigation, type ActiveMeetingNavigation } from "../../components/ProductNavigation";
import type {
  MeetingFactKind,
  MeetingFactStatus,
  MeetingPreparationInput,
  LiveAnalysisProfile,
  MeetingCorrectionMode,
  RuntimeIndicator,
} from "../../domain/events";
import { segmentDomId } from "./domIds";
import { AiWorkspace } from "./AiWorkspace";
import { MeetingSplitPane } from "./MeetingSplitPane";
import { MeetingPreflightDialog } from "./MeetingPreflightDialog";
import { captureHealthSummary, resolveMeetingSessionState, type MeetingSessionState } from "./meetingSessionState";
import { TranscriptRecoveryStatus } from "./TranscriptRecoveryStatus";
import { readTranscriptRecovery } from "./transcriptRecovery";
import { TranscriptPane, type TranscriptSelection, type TranscriptSelectionAction } from "./TranscriptPane";
import { MeetingHistory } from "../history/MeetingHistory";
import { ImportRecordingDialog } from "../history/ImportRecordingDialog";
import { ImportedRecordingStatus } from "../history/ImportedRecordingStatus";
import { ReviewWorkspace } from "../review/ReviewWorkspace";
import type { TranscriptRecoveryApi } from "../../api/transcriptRecovery";
import { ProviderSettingsControl } from "../settings/ProviderSettingsControl";
import {
  type BrowserMicrophoneController,
  type BrowserMicrophoneState,
} from "./useBrowserMicrophone";
import { useMeetingMicrophone } from "./useMeetingMicrophone";

interface LiveMeetingWorkbenchProps {
  materialsApi?: MaterialsApi;
  initialAttachmentSource?: MeetingAttachmentSourceEvidence | null;
  onAttachmentSourceClosed?(): void;
  recoveryApi?: TranscriptRecoveryApi;
  reconcileApi?: ProjectReconcileApi;
  onOpenKnowledgeDocument?: (target: { documentId: string; projectId: string }) => void;
  onOpenProjectProposals?: (projectId: string) => void;
  memoryApi?: MemoryApi;
  projectApi?: ProjectOutcomesApi;
  knowledgeApi?: KnowledgeApi;
  onKnowledgeSource?: (value: DocumentSourceEvidence, sourceMeetingId?: string) => void;
  meetingId: string | null;
  api: MeetingApi;
  transport: MeetingEventTransport;
  asrBaseUrl?: string;
  onCreateMeeting?: () => string;
  onOpenMeeting?: (meetingId: string) => void;
  onBackToMeetings?: () => void;
  onOpenNotes?: () => void;
  onOpenCapabilities?: () => void;
  onOpenKnowledge?: () => void;
  initialEvidenceSegmentId?: string | null;
  onEvidenceFocused?: () => void;
  microphoneController?: BrowserMicrophoneController;
  activeCaptureMeetingId?: string | null;
  activeMeeting?: ActiveMeetingNavigation | null;
  onOpenActiveMeeting?: () => void;
  onMeetingStarted?: (meetingId: string) => void;
  onMeetingEnded?: (meetingId: string) => void;
  onMeetingSessionStateChange?: (meetingId: string, state: MeetingSessionState) => void;
}

type MeetingPanel = "transcript" | "coach";

function panelFromHistory(meetingId: string | null): MeetingPanel {
  if (!meetingId) return "transcript";
  const historyState = window.history.state as { meetingPanel?: unknown; meetingPanelMeetingId?: unknown } | null;
  return historyState?.meetingPanelMeetingId === meetingId && historyState.meetingPanel === "coach"
    ? "coach"
    : "transcript";
}

function formatElapsed(milliseconds: number | null): string {
  if (milliseconds === null) return "--:--";
  const seconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function formatMeetingDate(timestamp: number | null | undefined): string {
  if (!timestamp) return "时间待同步";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function localInputIndicator(
  state: BrowserMicrophoneState,
  inputSource: BrowserMicrophoneController["inputSource"],
): RuntimeIndicator | null {
  if (state.phase === "idle") return null;
  if (state.phase === "paused") {
    return { state: "paused", label: "已暂停", level: 0, detail: null };
  }
  if (state.phase === "error") {
    return { state: "error", label: "不可用", level: 0, detail: state.error };
  }
  const active = state.phase === "recording";
  const nativeHealth = state.systemAudioHealth;
  if (active && (inputSource === "system_audio" || inputSource === "dual_track") && nativeHealth) {
    if (!nativeHealth.transportReady) {
      return { state: "error", label: "传输未就绪", level: 0, detail: "系统音频传输未就绪" };
    }
    if (!nativeHealth.pcmSeen) {
      return { state: "error", label: "无 PCM", level: 0, detail: "系统音频未收到 PCM 数据" };
    }
    if (!nativeHealth.audiblePcmSeen) {
      return { state: "paused", label: "当前无声音", level: 0, detail: "已连接但当前无系统声音" };
    }
  }
  if (active && state.inputLevelAvailable === false) {
    return {
      state: "active",
      label: "已连接",
      level: null,
      detail: inputSource === "dual_track"
        ? "麦克风 + 系统音频"
        : inputSource === "system_audio" ? "系统音频" : "系统麦克风",
    };
  }
  return {
    state: active ? "active" : "busy",
    label: active ? (state.inputLevel >= 0.035 ? "有声音" : "声音较弱") : "检测中",
    level: state.inputLevel,
    detail: null,
  };
}

const endableCapturePhases = new Set(["requesting", "connecting", "reconnecting", "starting", "recording", "paused", "stopping", "error"]);

export function LiveMeetingWorkbench({
  materialsApi, initialAttachmentSource, onAttachmentSourceClosed,
  reconcileApi, recoveryApi, onOpenKnowledgeDocument, onOpenProjectProposals,
  memoryApi,
  projectApi,
  knowledgeApi,
  onKnowledgeSource,
  meetingId,
  api,
  transport,
  asrBaseUrl = "",
  onCreateMeeting,
  onOpenMeeting,
  onBackToMeetings,
  onOpenNotes,
  onOpenCapabilities,
  onOpenKnowledge,
  initialEvidenceSegmentId,
  onEvidenceFocused,
  microphoneController,
  activeCaptureMeetingId = null,
  activeMeeting,
  onOpenActiveMeeting,
  onMeetingStarted,
  onMeetingEnded,
  onMeetingSessionStateChange,
}: LiveMeetingWorkbenchProps) {
  const { state, actions, transportKind } = useMeetingProjection(meetingId, api, transport);
  const liveMicrophone = useMeetingMicrophone({ asrBaseUrl });
  const microphone = microphoneController ?? liveMicrophone;
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [message, setMessage] = useState("");
  const startCheckPending = useRef(false);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [materialSet, setMaterialSet] = useState<MaterialSet | null>(null);
  const [restoredStart] = useState(() => readMaterialStartAttempt(meetingId));
  const startAttempt = useRef<MaterialStartAttempt | null>(restoredStart);
  const [startRecovery, setStartRecovery] = useState<"reading" | "ready" | "blocked" | null>(restoredStart ? "reading" : null);
  const [startRecoveryError, setStartRecoveryError] = useState("");
  const [profileRead, setProfileRead] = useState<{ meetingId: string; status: "reading" | "ready" | "failed"; value: LiveAnalysisProfile; correctionMode?: MeetingCorrectionMode; error: string } | null>(null);
  const [profileReload, setProfileReload] = useState(0);
  const [profileReadKey, setProfileReadKey] = useState(0);
  const startRecoveryConsumed = useRef(false);
  useEffect(() => {
    if (startAttempt.current && meetingId !== startAttempt.current.meetingId) startAttempt.current = null;
    setMaterialSet((current) => current?.state === "bound" && current.meeting_id !== meetingId ? null : current);
  }, [meetingId]);
  useEffect(() => {
    if (!restoredStart || startRecoveryConsumed.current || (meetingId && meetingId !== restoredStart.meetingId)) { setStartRecovery(null); return; }
    if (!materialsApi) { setStartRecovery("blocked"); setStartRecoveryError("当前无法读取上次的资料准备，请从会议列表查看原会议。"); return; }
    const controller = new AbortController(); setStartRecovery("reading");
    void materialsApi.get(restoredStart.setId, controller.signal).then((value) => {
      if (controller.signal.aborted || startRecoveryConsumed.current) return;
      const material = value.material_set;
      if (material.state === "deleted" || (material.state === "bound" && material.meeting_id !== restoredStart.meetingId)) throw new Error("上次资料已删除或属于其他会议，请从会议列表核对原会议。");
      if (restoredStart.created && material.state !== "bound") throw new Error("上次会议与资料绑定状态不一致，请从会议列表核对；不会猜测新会议。");
      const attempt = { ...restoredStart, created: material.state === "bound" };
      startAttempt.current = attempt; persistMaterialStartAttempt(attempt); setMaterialSet(material); setStartRecovery("ready");
    }).catch((error) => { if (!controller.signal.aborted) { setStartRecovery("blocked"); setStartRecoveryError(error instanceof Error ? error.message : "上次会议准备状态无法确认，请从会议列表核对。"); } });
    return () => controller.abort();
  }, [meetingId, materialsApi, restoredStart]);
  const existingPreparedMeetingId = meetingId ?? (startRecovery === "ready" && startAttempt.current?.created ? startAttempt.current.meetingId : null);
  useEffect(() => {
    if (!preflightOpen || !existingPreparedMeetingId) return;
    const controller = new AbortController();
    const target = existingPreparedMeetingId;
    const fallback = state.liveAnalysisProfile ?? "legacy";
    if (!api.getMeetingPreparation) {
      setProfileRead({ meetingId: target, status: "ready", value: fallback, error: "" });
      setProfileReadKey((value) => value + 1);
      return;
    }
    setProfileRead({ meetingId: target, status: "reading", value: fallback, error: "" });
    const timer = window.setTimeout(() => {
      controller.abort();
      setProfileRead({ meetingId: target, status: "failed", value: fallback, error: "本场帮助方式读取超时，请重试；原会议和资料会保留。" });
    }, 6_000);
    void api.getMeetingPreparation(target, controller.signal).then((preparation) => {
      if (controller.signal.aborted) return;
      if (preparation.meetingId !== target) throw new Error("读取到的准备内容不属于本场会议，请重新核对。");
      setProfileRead({ meetingId: target, status: "ready", value: preparation.liveAnalysisProfile ?? "legacy", correctionMode: preparation.correctionMode ?? "local_and_llm", error: "" });
      setProfileReadKey((value) => value + 1);
    }).catch((failure) => {
      if (controller.signal.aborted) return;
      // A known old meeting may legitimately have no preparation yet.
      if (failure instanceof ApiError && failure.status === 404) { setProfileRead({ meetingId: target, status: "ready", value: "legacy", error: "" }); setProfileReadKey((value) => value + 1); }
      else setProfileRead({ meetingId: target, status: "failed", value: fallback, error: failure instanceof Error ? failure.message : "无法读取本场帮助方式，请重试。" });
    }).finally(() => window.clearTimeout(timer));
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api, existingPreparedMeetingId, preflightOpen, profileReload, state.liveAnalysisProfile]);
  const profileMatches = profileRead?.meetingId === existingPreparedMeetingId;
  const liveAnalysisProfileLoading = startRecovery === "reading" || Boolean(existingPreparedMeetingId && (!profileMatches || profileRead?.status === "reading"));
  const liveAnalysisProfileError = profileMatches && profileRead?.status === "failed" ? profileRead.error : "";
  const liveAnalysisProfileLocked = Boolean(existingPreparedMeetingId && (
    state.meetingId !== existingPreparedMeetingId || state.lastSyncedAtMs === null
    || state.liveAnalysisProfileLocked === true
    || state.archivedSegmentCount > 0 || state.segments.length > 0 || state.audio.chunkCount > 0 || state.audio.fileSizeBytes > 0 || (state.audio.durationMs ?? 0) > 0
    || ((activeCaptureMeetingId === existingPreparedMeetingId || meetingId === existingPreparedMeetingId) && ["recording", "paused", "stopping"].includes(microphone.state.phase))
  ));
  const profilePreflightProps = {
    initialLiveAnalysisProfile: existingPreparedMeetingId ? (profileMatches ? profileRead?.value : state.liveAnalysisProfile) ?? "legacy" : "participation" as LiveAnalysisProfile,
    initialCorrectionMode: (profileMatches ? profileRead?.correctionMode : undefined) ?? "local_and_llm" as MeetingCorrectionMode,
    liveAnalysisProfileLoading,
    liveAnalysisProfileReadKey: existingPreparedMeetingId ? profileReadKey : 0,
    liveAnalysisProfileError,
    liveAnalysisProfileLocked,
    onRetryLiveAnalysisProfile: () => setProfileReload((value) => value + 1),
  };
  const [transcriptSelection, setTranscriptSelection] = useState<TranscriptSelection | null>(null);
  const [askSelectionNonce, setAskSelectionNonce] = useState(0);
  const [askSelectionAction, setAskSelectionAction] = useState<TranscriptSelectionAction>("ask");
  const [preparationRefreshKey, setPreparationRefreshKey] = useState(0);
  const [mobileMeetingView, setMobileMeetingView] = useState<MeetingPanel>(() => panelFromHistory(meetingId));
  const [evidenceReturnAvailable, setEvidenceReturnAvailable] = useState(false);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 3_000);
    return () => window.clearTimeout(timer);
  }, [message]);

  useEffect(() => {
    if (activeCaptureMeetingId && activeCaptureMeetingId !== meetingId) return;
    microphone.acknowledgeCommitted(state.segments.map((segment) => segment.segmentId));
  }, [activeCaptureMeetingId, meetingId, microphone, state.segments]);

  useEffect(() => {
    const restoreMeetingPanel = () => {
      const panel = panelFromHistory(meetingId);
      setEvidenceReturnAvailable(false);
      setMobileMeetingView(panel);
      window.requestAnimationFrame(() => {
        document.getElementById(`meeting-mobile-panel-${panel}`)?.focus({ preventScroll: true });
      });
    };
    setEvidenceReturnAvailable(false);
    setMobileMeetingView(panelFromHistory(meetingId));
    window.addEventListener("popstate", restoreMeetingPanel);
    return () => window.removeEventListener("popstate", restoreMeetingPanel);
  }, [meetingId]);

  const focusEvidence = (segmentId: string) => {
    setEvidenceReturnAvailable(true);
    setMobileMeetingView("transcript");
    window.requestAnimationFrame(() => {
      const element = document.getElementById(segmentDomId(segmentId));
      if (!element) {
        setMessage("对应文字暂未加载");
        return;
      }
      element.scrollIntoView({ behavior: motionAwareScrollBehavior(), block: "center" });
      element.focus({ preventScroll: true });
      element.classList.remove("is-evidence-target");
      window.requestAnimationFrame(() => element.classList.add("is-evidence-target"));
    });
  };

  const focusMeetingPanel = (view: MeetingPanel, pushHistory = false) => {
    if (view === "coach") setEvidenceReturnAvailable(false);
    if (pushHistory && meetingId && panelFromHistory(meetingId) !== view) {
      window.history.pushState({
        ...(window.history.state ?? {}),
        meetingPanel: view,
        meetingPanelMeetingId: meetingId,
      }, "", window.location.href);
    }
    setMobileMeetingView(view);
    window.requestAnimationFrame(() => {
      document.getElementById(`meeting-mobile-panel-${view}`)?.focus({ preventScroll: true });
    });
  };

  useEffect(() => {
    if (!initialEvidenceSegmentId) return;
    const frame = window.requestAnimationFrame(() => {
      const element = document.getElementById(segmentDomId(initialEvidenceSegmentId));
      if (!element) return;
      focusEvidence(initialEvidenceSegmentId);
      onEvidenceFocused?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [initialEvidenceSegmentId, onEvidenceFocused, state.segments.length, state.semanticParagraphs?.length]);

  useEffect(() => {
    if (!meetingId || state.meetingId !== meetingId.trim() || state.lastSyncedAtMs === null) return;
    if (state.importJob) { onMeetingSessionStateChange?.(meetingId, "ended"); return; }
    onMeetingSessionStateChange?.(meetingId, resolveMeetingSessionState({
      meetingPhase: state.runtime.phase,
      capturePhase: microphone.state.phase,
      captureBelongsToMeeting: !activeCaptureMeetingId || activeCaptureMeetingId === meetingId,
      backendCaptureState: state.runtime.capture?.state,
    }));
  }, [
    activeCaptureMeetingId,
    meetingId,
    microphone.state.phase,
    onMeetingSessionStateChange,
    state.lastSyncedAtMs,
    state.importJob,
    state.meetingId,
    state.runtime.capture?.state,
    state.runtime.phase,
  ]);

  const startMeeting = async (preparation: MeetingPreparationInput, knowledgeSelection?: MeetingKnowledgeSelection) => {
    if (startRecovery === "reading" || startRecovery === "blocked") throw new Error(startRecoveryError || "正在核对上次会议准备，请稍候；不会重复新建会议。");
    if (liveAnalysisProfileLoading || liveAnalysisProfileError) throw new Error(liveAnalysisProfileError || "正在读取本场帮助方式，请稍候；不会重复新建会议。");
    if (liveAnalysisProfileLocked && preparation.liveAnalysisProfile !== profilePreflightProps.initialLiveAnalysisProfile) throw new Error("本场已开始采集或已有会议文字，不能切换帮助方式，请沿用本场设置。");
    if (liveAnalysisProfileLocked && preparation.correctionMode !== profilePreflightProps.initialCorrectionMode) throw new Error("本场已开始采集或已有会议文字，不能切换校对模式，请沿用本场设置。");
    if (startCheckPending.current) return;
    startCheckPending.current = true;
    setStarting(true);
    let activeMeetingId: string | undefined | null;
    try {
      await checkAsrCapturePreparation(preparation.correctionMode);
      activeMeetingId = startAttempt.current?.meetingId ?? meetingId ?? onCreateMeeting?.();
    } catch (error) {
      startCheckPending.current = false;
      setStarting(false);
      throw error;
    }
    const createdFromList = meetingId === null && Boolean(activeMeetingId);
    if (!activeMeetingId) {
      setMessage("无法创建会议");
      startCheckPending.current = false;
      setStarting(false);
      return;
    }
    let meetingCreated = false;
    const hasMaterials = Boolean(materialSet);
    if (materialSet && (!startAttempt.current || startAttempt.current.setId !== materialSet.id)) startAttempt.current = { meetingId: activeMeetingId, setId: materialSet.id, setRevision: materialSet.revision, key: crypto.randomUUID(), created: false, title: preparation.title ?? null, inputSource: preparation.inputSource };
    if (hasMaterials && startAttempt.current) persistMaterialStartAttempt(startAttempt.current);
    setStarting(true);
    try {
      if (!startAttempt.current?.created) {
        if (hasMaterials && startAttempt.current) await api.createMeeting(activeMeetingId, startAttempt.current.title, startAttempt.current.inputSource, undefined, { materialSetId: startAttempt.current.setId, expectedMaterialSetRevision: startAttempt.current.setRevision, idempotencyKey: startAttempt.current.key });
        else await api.createMeeting(activeMeetingId, preparation.title ?? null, preparation.inputSource);
        if (startAttempt.current) { startAttempt.current.created = true; persistMaterialStartAttempt(startAttempt.current); }
      }
      meetingCreated = true;
      if (hasMaterials && materialsApi) setMaterialSet(await materialsApi.forMeeting(activeMeetingId));
      await api.saveMeetingPreparation(activeMeetingId, preparation);
      if (preparation.liveAnalysisProfile) setProfileRead({ meetingId: activeMeetingId, status: "ready", value: preparation.liveAnalysisProfile, correctionMode: preparation.correctionMode, error: "" });
      if (knowledgeSelection) {
        if (!knowledgeApi) throw new Error("当前无法保存所选资料范围，会议尚未开始。");
        await knowledgeApi.saveMeetingContext(activeMeetingId, knowledgeSelection);
      }
      setPreparationRefreshKey((current) => current + 1);
      await checkAsrCapturePreparation(preparation.correctionMode, activeMeetingId);
      if (createdFromList) onOpenMeeting?.(activeMeetingId);
      await microphone.start(activeMeetingId, {
        inputDeviceId: preparation.inputDeviceId,
        inputSource: preparation.inputSource,
      });
      onMeetingStarted?.(activeMeetingId);
      startRecoveryConsumed.current = true; persistMaterialStartAttempt(null); setStartRecovery(null);
      setPreflightOpen(false);
      setMessage("会议已开始");
    } catch (error) {
      const profileLockedOnServer = error instanceof ApiError && error.status === 409 && (error.body as { detail?: { error?: string } } | null)?.detail?.error === "live_analysis_profile_locked";
      const captureError = profileLockedOnServer ? "本场已产生录音或会议文字，不能切换帮助方式。正在重新读取原设置，请沿用本场方式继续。" : error instanceof Error ? error.message : "声音采集启动失败";
      if (profileLockedOnServer) { setProfileReload((value) => value + 1); void actions.refresh(); }
      // A known revision conflict has not consumed a draft. Refresh its CAS
      // revision for the next explicit attempt, while unknown network outcomes
      // retain the original body and idempotency key for safe receipt replay.
      if (hasMaterials && !meetingCreated && error instanceof ApiError && error.status === 409 && materialsApi && startAttempt.current) {
        try {
          const fresh = (await materialsApi.get(startAttempt.current.setId)).material_set;
          setMaterialSet(fresh);
          if (fresh.state === "draft") startAttempt.current = { ...startAttempt.current, setRevision: fresh.revision, key: crypto.randomUUID() };
          else if (fresh.meeting_id === activeMeetingId) startAttempt.current.created = true;
          persistMaterialStartAttempt(startAttempt.current);
        } catch { /* Keep the original attempt if its outcome is not confirmed. */ }
      }
      if (createdFromList && meetingCreated && !hasMaterials) {
        try {
          await api.deleteMeeting(activeMeetingId);
          startAttempt.current = null;
          persistMaterialStartAttempt(null);
        } catch (rollbackError) {
          const rollbackMessage = rollbackError instanceof Error ? rollbackError.message : "会议回滚失败";
          const combinedMessage = `${captureError}；新会议清理失败：${rollbackMessage}`;
          onBackToMeetings?.();
          throw new Error(combinedMessage);
        }
      }
      if (hasMaterials) {
        if (meetingCreated) onOpenMeeting?.(activeMeetingId);
        throw new Error(`${captureError}；${meetingCreated ? "资料已保留，请重试同一会议的声音输入。" : "资料仍在本地，请核对状态后重新开始会议。"}`);
      }
      if (createdFromList) onBackToMeetings?.();
      throw new Error(captureError);
    } finally {
      startCheckPending.current = false;
      setStarting(false);
    }
  };

  const endMeeting = async () => {
    try {
      if (endableCapturePhases.has(microphone.state.phase)) await microphone.end();
      await actions.endMeeting();
      if (meetingId) onMeetingEnded?.(meetingId);
      setMessage("会议已结束，正在整理复盘");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "结束会议失败");
    }
  };

  const saveFactStatus = async (
    factType: MeetingFactKind,
    factId: string,
    status: Extract<MeetingFactStatus, "confirmed" | "dismissed">,
  ) => {
    if (!meetingId) return;
    await api.saveFactStatus(meetingId, factType, factId, status);
    await actions.refresh();
  };

  const updateFact = async (
    _factType: MeetingFactKind,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
  ) => {
    if (!meetingId || !api.updateFact) return;
    await api.updateFact(meetingId, factId, changes, expectedVersion);
    await actions.refresh();
  };

  const mergeFacts = async (
    _factType: MeetingFactKind,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
  ) => {
    if (!meetingId || !api.mergeFacts) return;
    await api.mergeFacts(meetingId, targetFactId, sourceFactId, expectedTargetVersion, expectedSourceVersion);
    await actions.refresh();
  };

  if (!meetingId) {
    const activeMeetingAction = activeMeeting?.state === "external"
      ? "查看录音中的会议"
      : activeMeeting?.state === "recoverable"
        ? "恢复未结束会议"
        : activeMeeting?.state === "paused"
          ? "返回已暂停会议"
          : "返回正在会议";
    return (
      <div className="product-app product-app--home">
        <ProductNavigation active="meetings" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} onOpenKnowledge={onOpenKnowledge} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
        <main className="start-home">
          <section className="start-command">
            <div className="start-command-copy">
              <span className="section-kicker">会议工作台</span>
              <h1>会议记录</h1>
              <p>管理本机会议、录音、文字与 AI 整理结果。</p>
            </div>
            <div className="start-command-actions">
              <ProviderSettingsControl />
              <button
                className="secondary-button"
                type="button"
                onClick={() => setImportDialogOpen(true)}
                disabled={microphone.state.phase === "requesting"}
              >
                <FileAudio size={17} />
                导入录音
              </button>
              {activeMeeting ? (
                <button
                  className="start-meeting-button"
                  type="button"
                  onClick={onOpenActiveMeeting}
                  disabled={!onOpenActiveMeeting}
                >
                  <Video size={17} />
                  {activeMeetingAction}
                </button>
              ) : (
                <button
                  className="start-meeting-button"
                  type="button"
                  onClick={() => setPreflightOpen(true)}
                  disabled={startRecovery === "reading" || startRecovery === "blocked"}
                >
                  {microphone.state.phase === "requesting" ? <LoaderCircle className="spin" size={17} /> : <Mic size={17} />}
                  {microphone.state.phase === "requesting" ? "正在请求权限" : "开始会议"}
                </button>
              )}
            </div>
            {microphone.state.error ? <p className="unbound-error">{microphone.state.error}</p> : null}
            {startRecovery ? <div className="start-command-message" role="status"><p>{startRecovery === "reading" ? "正在只读核对上次会议准备；尚未开启任何声音输入。" : startRecovery === "ready" ? "上次会议准备已恢复。点击开始会议继续同一场；尚未开启声音输入。" : startRecoveryError}</p>{startRecovery !== "reading" ? <><button type="button" className="secondary-button" onClick={() => { const id = startAttempt.current?.meetingId ?? restoredStart?.meetingId; if (id) onOpenMeeting?.(id); }}>只查看原会议</button><button type="button" className="secondary-button" onClick={() => { startRecoveryConsumed.current = true; startAttempt.current = null; persistMaterialStartAttempt(null); setStartRecovery(null); setMaterialSet(null); onBackToMeetings?.(); }}>结束恢复，查看会议列表</button></> : null}</div> : null}
            {message ? (
              <p
                className="start-command-message"
                role="alert"
                aria-live="polite"
              >
                {message}
              </p>
            ) : null}
          </section>
          <MeetingHistory api={api} onOpenMeeting={onOpenMeeting ?? (() => undefined)} activeMeetingId={activeCaptureMeetingId} />
        </main>
        <ImportRecordingDialog
          open={importDialogOpen}
          onClose={() => setImportDialogOpen(false)}
          onImport={async (file, title) => {
            return api.importRecording(file, title);
          }}
          onReadImportJob={async (importMeetingId) => (await api.getSnapshot(importMeetingId)).importJob ?? null}
          onRetryImport={(importMeetingId) => api.retryImportJob(importMeetingId)}
          onOpenMeeting={(importedMeetingId) => {
            setImportDialogOpen(false);
            onOpenMeeting?.(importedMeetingId);
          }}
        />
        <MeetingPreflightDialog materialsApi={materialsApi} materialSet={materialSet} onMaterialSetChange={setMaterialSet}
          {...profilePreflightProps}
          projectApi={projectApi}
          knowledgeApi={knowledgeApi}
          memoryApi={memoryApi}
          open={preflightOpen}
          busy={starting}
          onCancel={() => setPreflightOpen(false)}
          onStart={startMeeting}
        />
      </div>
    );
  }

  const normalizedMeetingId = meetingId.trim();
  const snapshotLoading = state.meetingId !== normalizedMeetingId || state.lastSyncedAtMs === null;
  if (snapshotLoading) {
    return (
      <div className="product-app product-app--live">
        <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} onOpenKnowledge={onOpenKnowledge} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
        <div className="workbench-shell">
          <header className="app-header">
            <div className="meeting-identity">
              {onBackToMeetings ? (
                <button
                  className="icon-button meeting-back-button"
                  type="button"
                  onClick={onBackToMeetings}
                  title="返回会议列表"
                  aria-label="返回会议列表"
                >
                  <ArrowLeft size={18} />
                </button>
              ) : null}
              <BrandMark />
              <div>
                <span className="brand-name">言迹 Talktrace</span>
                <h1>会议状态加载中</h1>
              </div>
            </div>
            <div className="header-actions">
              <ProviderSettingsControl />
            </div>
          </header>
          <main className="meeting-loading-state" role="status" aria-live="polite">
            <LoaderCircle className="spin" size={22} />
            <span>正在加载会议状态</span>
          </main>
        </div>
        <MeetingPreflightDialog materialsApi={materialsApi} materialSet={materialSet} onMaterialSetChange={setMaterialSet}
          {...profilePreflightProps}
          projectApi={projectApi}
          knowledgeApi={knowledgeApi}
          memoryApi={memoryApi}
          open={preflightOpen}
          busy={starting}
          onCancel={() => setPreflightOpen(false)}
          onStart={startMeeting}
        />
      </div>
    );
  }

  const localInput = localInputIndicator(microphone.state, microphone.inputSource);
  const transcriptBackfill = readTranscriptRecovery(
    state.diagnostics.transcript_backfill ?? state.diagnostics.transcriptBackfill,
  );
  const transcriptBackfillRange = transcriptBackfill?.range
    ? `${formatElapsed(transcriptBackfill.range.startMs)}–${formatElapsed(transcriptBackfill.range.endMs)}`
    : "待读取的范围";
  const backfillInputIndicator: RuntimeIndicator | null = transcriptBackfill?.status === "running"
    ? {
        state: "busy",
        label: "正在补齐",
        level: null,
        detail: `正在补齐 ${transcriptBackfillRange} 的会议文字`,
      }
    : null;
  const importedRecording = Boolean(state.importJob);
  const meetingEnded = state.runtime.phase === "ended" || importedRecording;
  const captureBelongsToMeeting = !activeCaptureMeetingId || activeCaptureMeetingId === meetingId;
  const otherMeetingCapturing = Boolean(activeCaptureMeetingId && activeCaptureMeetingId !== meetingId);
  const sessionState = resolveMeetingSessionState({
    meetingPhase: state.runtime.phase,
    capturePhase: microphone.state.phase,
    captureBelongsToMeeting,
    backendCaptureState: state.runtime.capture?.state,
  });
  const remoteCaptureActive = sessionState === "capturing_elsewhere";
  const localCaptureActive = !importedRecording && ["preparing", "capturing", "paused", "reconnecting", "ending"].includes(sessionState);
  const inputIndicator = meetingEnded
    ? state.runtime.input
    : backfillInputIndicator ?? (captureBelongsToMeeting ? localInput : null) ?? state.runtime.input;
  const elapsedMs = meetingEnded || !captureBelongsToMeeting
    ? state.runtime.elapsedMs
    : microphone.state.elapsedMs ?? state.runtime.elapsedMs;
  const showEndCommand = localCaptureActive && sessionState !== "ending";
  const canStartCapture = !importedRecording && ["idle", "recoverable", "error"].includes(sessionState) && !otherMeetingCapturing;
  const candidatePartial = meetingEnded ? null
    : (captureBelongsToMeeting ? microphone.state.activePartial : null) ?? state.activePartial;
  const partial = candidatePartial && ![...state.segments, ...state.fullTranscript]
    .some((segment) => isPartialCoveredBySegment(candidatePartial, segment))
    ? candidatePartial
    : null;
  const nativeSystemAudioHealth = !meetingEnded
    && (microphone.inputSource === "system_audio" || microphone.inputSource === "dual_track")
    ? microphone.state.systemAudioHealth ?? null
    : null;
  const aiCapabilities = state.runtime.ai.capabilities ?? {};
  const ownTranscriptIndicator = aiCapabilities.transcript;
  const asrIndicator: RuntimeIndicator = transcriptBackfill?.status === "running"
    ? { state: "busy", label: "补齐中", level: null, detail: "正在补齐中断处会议文字" }
    : meetingEnded
      ? { state: "idle", label: "已完成", level: null, detail: null }
      : state.connection === "offline"
        ? { state: "error", label: "已断开", level: null, detail: "实时识别连接已中断，录音仍会保存" }
        : state.connection === "connecting" || state.connection === "reconnecting"
          ? { state: "busy", label: "连接中", level: null, detail: null }
          : ownTranscriptIndicator ?? { state: "active", label: "实时识别", level: null, detail: null };
  const llmIndicator: RuntimeIndicator = aiCapabilities.provider ?? {
    state: state.runtime.ai.state,
    label: state.runtime.ai.label,
    level: null,
    detail: state.runtime.ai.detail,
  };
  const reportedCoachRuntime = aiCapabilities.proactive_suggestions ?? aiCapabilities.intelligence ?? state.runtime.ai;
  const coachWaitingForTranscript = !meetingEnded && state.archivedSegmentCount + state.segments.length === 0
    && state.fullTranscript.length === 0 && reportedCoachRuntime.state !== "busy";
  const taskCapabilities = {
    realtime_suggestions: coachWaitingForTranscript
      ? { ...reportedCoachRuntime, state: "paused" as const, label: "教练等待确认文字", detail: "录音与识别草稿仍保留，尚无已确认文字可供现场分析。" }
      : reportedCoachRuntime,
    minutes: state.reviewJobs.minutes
      ? {
          state: state.reviewJobs.minutes.status === "failed" ? "error" as const : ["pending", "running", "retry_wait"].includes(state.reviewJobs.minutes.status) ? "busy" as const : "idle" as const,
          label: state.reviewJobs.minutes.status === "failed" ? "纪要失败" : ["pending", "running", "retry_wait"].includes(state.reviewJobs.minutes.status) ? "纪要处理中" : "纪要已完成",
          level: null,
          detail: state.reviewJobs.minutes.errorMessage ?? null,
        }
      : { state: "idle" as const, label: meetingEnded ? "纪要待开始" : "会中待命", level: null, detail: null },
    index: state.reviewJobs.index
      ? {
          state: state.reviewJobs.index.status === "failed" ? "error" as const : ["pending", "running", "retry_wait"].includes(state.reviewJobs.index.status) ? "busy" as const : "idle" as const,
          label: state.reviewJobs.index.status === "failed" ? "索引失败" : ["pending", "running", "retry_wait"].includes(state.reviewJobs.index.status) ? "索引处理中" : "索引已完成",
          level: null,
          detail: state.reviewJobs.index.errorMessage ?? null,
        }
      : { state: "idle" as const, label: "索引待开始", level: null, detail: null },
  };
  const captureHealth = captureHealthSummary(sessionState, inputIndicator, asrIndicator, llmIndicator);

  return (
    <div className={`product-app ${meetingEnded ? "product-app--review" : "product-app--live"}`}>
      {!meetingEnded ? (
        <nav className="meeting-skip-links" aria-label="跳过导航">
          <a
            href="#meeting-mobile-panel-transcript"
            onClick={(event) => {
              event.preventDefault();
              focusMeetingPanel("transcript", true);
            }}
          >
            跳到会议文字
          </a>
          <a
            href="#meeting-mobile-panel-coach"
            onClick={(event) => {
              event.preventDefault();
              focusMeetingPanel("coach", true);
            }}
          >
            跳到实时教练
          </a>
        </nav>
      ) : null}
      <ProductNavigation active="live" onOpenMeetings={onBackToMeetings} onOpenNotes={onOpenNotes} onOpenCapabilities={onOpenCapabilities} onOpenKnowledge={onOpenKnowledge} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting} />
      <div className={`workbench-shell${nativeSystemAudioHealth || transcriptBackfill || canStartCapture || otherMeetingCapturing || remoteCaptureActive ? " workbench-shell--status-band" : ""}`}>
      <header className="app-header">
        <div className="meeting-identity">
          {onBackToMeetings ? (
            <button
              className="icon-button meeting-back-button"
              type="button"
              onClick={onBackToMeetings}
              title="返回会议列表"
              aria-label="返回会议列表"
            >
              <ArrowLeft size={18} />
            </button>
          ) : null}
          <BrandMark />
          <div>
            <span className="brand-name">言迹 Talktrace</span>
              <MeetingTitleEditor
                meetingId={meetingId}
                title={state.title}
                timestamp={state.updatedAtMs}
                onSave={async (title) => {
                  await api.updateMeetingTitle(meetingId, title);
                  await actions.refresh();
                }}
              />
              <div className="meeting-header-meta" aria-label="会议基本信息">
                <span className="meeting-header-updated-at" aria-label={`最近更新 ${formatMeetingDate(state.updatedAtMs)}`}><CalendarDays size={12} aria-hidden="true" />最近更新 {formatMeetingDate(state.updatedAtMs)}</span>
                <span><Clock3 size={12} />{formatElapsed(elapsedMs)}</span>
                <span><FileText size={12} />{state.archivedSegmentCount + state.segments.length} 段文字</span>
                <span><Users size={12} />{state.speakers.length || 1} 个说话人</span>
                {!importedRecording ? <span aria-label="本场主要帮助">{(profileRead?.meetingId === meetingId && profileRead.status === "ready" ? profileRead.value : state.liveAnalysisProfile) === "participation" ? "帮助我参与讨论" : "优先回答听到的问题"}</span> : null}
              </div>
          </div>
        </div>

        <div className="meeting-statuses" aria-label="会议运行状态">
          <button
            className="capture-health-summary"
            type="button"
            onClick={() => setDiagnosticsOpen(true)}
            aria-label={importedRecording ? "本地录音导入。打开运行诊断" : `采集健康：${captureHealth.label}；${captureHealth.aiLabel}。打开运行诊断`}
            title="查看录音、声音、识别、精修、说话人、模型和任务详情"
          >
            <span className={`status-dot status-dot--${captureHealth.state}`} aria-hidden="true" />
            <span>
              <strong>{importedRecording ? "本地录音导入" : captureHealth.label}</strong>
              <small>{captureHealth.aiLabel}</small>
            </span>
          </button>
          <time className="elapsed-time" aria-label={`会议时长 ${formatElapsed(elapsedMs)}`}>
            {formatElapsed(elapsedMs)}
          </time>
        </div>

        <div className="header-actions">
          {materialsApi ? <MeetingAttachments api={materialsApi} meetingId={meetingId} materialSet={materialSet?.meeting_id === meetingId ? materialSet : null} onSetChange={setMaterialSet} source={initialAttachmentSource} onSourceClosed={onAttachmentSourceClosed} onChanged={() => { setPreparationRefreshKey((n) => n + 1); void actions.refresh(); }} /> : null}
          <ProviderSettingsControl />
          {localCaptureActive
          && microphone.state.phase !== "stopping"
          && microphone.supportsPause !== false ? (
            <button
              className="icon-button"
              type="button"
              onClick={microphone.togglePause}
              title={microphone.state.phase === "paused" ? "继续录音" : "暂停录音"}
              aria-label={microphone.state.phase === "paused" ? "继续录音" : "暂停录音"}
            >
              {microphone.state.phase === "paused" ? <Play size={18} fill="currentColor" /> : <Pause size={18} fill="currentColor" />}
            </button>
          ) : null}
          {showEndCommand ? (
            <button
              className="end-meeting-button"
              type="button"
              onClick={() => void endMeeting()}
              disabled={state.ending || microphone.state.phase === "stopping"}
              title="结束并整理"
              aria-label="结束并整理"
            >
              {state.ending || microphone.state.phase === "stopping" ? <LoaderCircle className="spin" size={16} /> : <Square size={14} fill="currentColor" />}
              <span className="meeting-command-label">{state.ending || microphone.state.phase === "stopping" ? "正在结束" : "结束并整理"}</span>
            </button>
          ) : null}
        </div>
      </header>

      {nativeSystemAudioHealth || transcriptBackfill || canStartCapture || otherMeetingCapturing || remoteCaptureActive ? (
        <div className="meeting-status-bands">
        {canStartCapture ? (
          <div className="capture-inactive-status" role="alert" aria-live="polite">
            <AlertCircle size={17} aria-hidden="true" />
            <div>
              <strong>
                {sessionState === "error" ? "录音已中断" : sessionState === "recoverable" ? "会议录音待恢复" : "当前没有录音输入"}
              </strong>
              <span>
                {sessionState === "recoverable"
                  ? "浏览器无法自动恢复上次的声音权限；已保存的文字和会议上下文不会丢失。"
                  : "现在说话不会进入会议文字，Pi 教练也不会收到新内容。"}
              </span>
            </div>
            <button
              className="start-recording-button capture-inactive-status__action"
              type="button"
              onClick={() => setPreflightOpen(true)}
            >
              <Mic size={15} />
              {sessionState === "error" ? "立即恢复录音" : sessionState === "recoverable" ? "恢复录音" : "立即开始录音"}
            </button>
            <button
              className="secondary-button capture-inactive-status__action"
              type="button"
              onClick={() => void endMeeting()}
              disabled={state.ending}
            >
              <Square size={14} fill="currentColor" />
              {state.ending ? "正在结束" : "直接结束并整理"}
            </button>
          </div>
        ) : null}
        {otherMeetingCapturing ? (
          <div className="capture-inactive-status" role="status" aria-live="polite">
            <AlertCircle size={17} aria-hidden="true" />
            <div>
              <strong>另一场会议正在录音</strong>
              <span>当前页面只读展示；返回正在进行的会议后再暂停或结束录音。</span>
            </div>
            <button className="secondary-button capture-inactive-status__action" type="button" onClick={onOpenActiveMeeting}>
              <Play size={15} />
              返回正在会议
            </button>
          </div>
        ) : null}
        {remoteCaptureActive && !otherMeetingCapturing ? (
          <div className="capture-inactive-status" role="status" aria-live="polite">
            <CircleCheck size={17} aria-hidden="true" />
            <div>
              <strong>另一窗口正在录音</strong>
              <span>服务端仍收到新鲜采集心跳；当前页面保持只读，避免重复占用麦克风或覆盖录音。</span>
            </div>
          </div>
        ) : null}
        <TranscriptRecoveryStatus diagnostic={state.diagnostics.transcript_backfill ?? state.diagnostics.transcriptBackfill}
          audioAvailable={Boolean(state.audioDetail?.assembled && state.audioDetail.playbackUrl)} />
        {nativeSystemAudioHealth ? (
          <div className="native-capture-health" role="status" aria-live="polite" aria-label="系统音频分层健康状态">
          <span data-ready={nativeSystemAudioHealth.transportReady}>
            <small>传输</small>
            <strong>{nativeSystemAudioHealth.transportReady ? "已连接" : "未就绪"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.pcmSeen}>
            <small>PCM</small>
            <strong>{nativeSystemAudioHealth.pcmSeen ? "已接收" : "未收到"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.audiblePcmSeen}>
            <small>声音</small>
            <strong>{nativeSystemAudioHealth.audiblePcmSeen ? "已检测" : "当前静音"}</strong>
          </span>
          <span data-ready={nativeSystemAudioHealth.asrReady}>
            <small>识别</small>
            <strong>{nativeSystemAudioHealth.asrReady ? "已就绪" : "准备中"}</strong>
          </span>
          {!nativeSystemAudioHealth.audiblePcmSeen
            && nativeSystemAudioHealth.transportReady
            && nativeSystemAudioHealth.pcmSeen ? (
              <strong className="native-capture-health__message">已连接但当前无系统声音</strong>
            ) : null}
          </div>
        ) : null}
        </div>
      ) : null}

      {state.importJob ? <ImportedRecordingStatus key={meetingId} job={state.importJob}
        onRefresh={actions.refresh}
        onRetry={async () => {
          await api.retryImportJob(meetingId);
          try { await actions.refresh(); }
          catch { setMessage("录音导入重试已提交，但状态刷新未完成。请刷新导入状态；无需再次提交重试。"); }
        }} /> : null}
      {meetingEnded ? (
        <ReviewWorkspace
          recoveryApi={recoveryApi}
          onKnowledgeSource={onKnowledgeSource}
          knowledgeApi={knowledgeApi} projectApi={projectApi} reconcileApi={reconcileApi} onOpenKnowledgeDocument={onOpenKnowledgeDocument} onOpenProjectProposals={onOpenProjectProposals}
          state={state}
          onReloadTranscript={actions.loadFullTranscript}
          onReloadAudio={actions.loadAudio}
          onExport={(format) => api.exportMeeting(meetingId, format)}
          onSaveDocument={(kind, expectedRevision, content) =>
            api.saveReviewDocument(meetingId, kind, expectedRevision, content)}
          onLoadDocumentRevisions={(kind) => api.getDocumentRevisions(meetingId, kind)}
          onRegenerateDocument={(kind) => api.regenerateDocument(meetingId, kind)}
          onRetryReviewJob={(kind) => api.retryReviewJob(meetingId, kind)}
          onRetryTranscriptCorrection={() => api.retryTranscriptCorrection(meetingId)}
          onRenameSpeaker={actions.renameSpeaker}
          onRefresh={actions.refresh}
        />
      ) : (
        <MeetingSplitPane>
          <div className="meeting-mobile-view-switch" role="group" aria-label="会中视图">
            <button
              type="button"
              aria-pressed={mobileMeetingView === "transcript"}
              aria-controls="meeting-mobile-panel-transcript"
              onClick={() => focusMeetingPanel("transcript", true)}
            >
              <FileText size={15} />会议文字
            </button>
            <button
              type="button"
              aria-pressed={mobileMeetingView === "coach"}
              aria-controls="meeting-mobile-panel-coach"
              onClick={() => focusMeetingPanel("coach", true)}
            >
              <Lightbulb size={15} />实时教练
            </button>
          </div>
          <section
            id="meeting-mobile-panel-transcript"
            className={`meeting-view-panel meeting-view-panel--transcript${mobileMeetingView === "transcript" ? " is-selected" : ""}`}
            aria-label="会议文字视图"
            tabIndex={-1}
          >
            {evidenceReturnAvailable ? (
              <button
                className="evidence-return-control"
                type="button"
                onClick={() => focusMeetingPanel("coach")}
              >
                <ArrowLeft size={15} />
                返回实时教练
              </button>
            ) : null}
            <TranscriptPane
              segments={state.segments}
              semanticParagraphs={state.semanticParagraphs}
              archivedTranscript={state.archivedTranscript}
              archivedSegmentCount={state.archivedSegmentCount}
              activePartial={partial}
              connection={state.connection}
              aiIndicator={state.runtime.ai}
              speakers={state.speakers}
              onRenameSpeaker={actions.renameSpeaker}
              onSelectionChange={setTranscriptSelection}
              onAskSelection={(selection, action) => {
                setTranscriptSelection(selection);
                setAskSelectionAction(action);
                setAskSelectionNonce((current) => current + 1);
                setEvidenceReturnAvailable(false);
                focusMeetingPanel("coach", true);
              }}
              onSaveSelection={async (selection) => {
                if (!api.createNote) return;
                const selectedSegments = state.segments.filter((segment) => selection.segmentIds.includes(segment.segmentId));
                await api.createNote(meetingId, {
                  body: selection.text,
                  sourceKind: "selection",
                  evidence: selectedSegments.map((segment) => ({
                    segmentId: segment.segmentId,
                    transcriptSeq: segment.transcriptSeq,
                    startMs: segment.startedAtMs,
                    endMs: segment.endedAtMs,
                    quote: segment.normalizedText.trim() || segment.text.trim(),
                  })),
                });
                setMessage("已保存到笔记");
              }}
            />
          </section>
          <section
            id="meeting-mobile-panel-coach"
            className={`meeting-view-panel meeting-view-panel--coach${mobileMeetingView === "coach" ? " is-selected" : ""}`}
            aria-label="实时教练视图"
            tabIndex={-1}
          >
            <AiWorkspace
              projectApi={projectApi}
              memoryApi={memoryApi}
              knowledgeApi={knowledgeApi}
              onKnowledgeSource={onKnowledgeSource}
              onEntitiesChanged={() => void actions.refresh()}
              onKnowledgeContextSaved={() => { setPreparationRefreshKey((value) => value + 1); void actions.refresh(); }}
              meetingId={meetingId}
              api={api}
              selection={transcriptSelection}
              askSelectionNonce={askSelectionNonce}
              selectionAction={askSelectionAction}
              preparationRefreshKey={preparationRefreshKey}
              currentTopic={state.currentTopic}
              followUp={state.followUp}
              semanticFollowUp={state.semanticFollowUp}
              coachDecision={state.coachDecision}
              coachHistory={state.coachHistory}
              coachDueItems={state.coachDueItems}
              recentContextHistory={state.recentContextHistory}
              coachRuntime={taskCapabilities.realtime_suggestions}
              discussionRuntime={aiCapabilities.discussion_state}
              openQuestions={state.openQuestions}
              suggestions={state.suggestions}
              decisionCandidates={state.decisionCandidates}
              actionItems={state.actionItems}
              risks={state.risks}
              onEvidence={focusEvidence}
              onFeedback={actions.saveSuggestionFeedback}
              onFactStatus={saveFactStatus}
              onFactEdit={api.updateFact ? updateFact : undefined}
              onFactMerge={api.mergeFacts ? mergeFacts : undefined}
              onMessage={setMessage}
            />
          </section>
        </MeetingSplitPane>
      )}

      <div className="sr-live" role="status" aria-label="操作反馈" aria-live="polite">{message}</div>
      {state.endError || message ? (
        <div className={`toast ${state.endError ? "toast--error" : ""}`} aria-hidden="true">
          {state.endError ?? message}
        </div>
      ) : null}

      <DiagnosticsDrawer
        open={diagnosticsOpen}
        onClose={() => setDiagnosticsOpen(false)}
        onRefresh={() => void actions.refresh()}
        onExport={() => api.exportDiagnosticBundle()}
        state={state}
        transportKind={transportKind}
      />
      <MeetingPreflightDialog materialsApi={materialsApi} materialSet={materialSet} onMaterialSetChange={setMaterialSet}
          {...profilePreflightProps}
          projectApi={projectApi}
          knowledgeApi={knowledgeApi}
          memoryApi={memoryApi}
        open={preflightOpen}
        busy={starting}
        onCancel={() => setPreflightOpen(false)}
        onStart={startMeeting}
      />
      </div>
    </div>
  );
}
