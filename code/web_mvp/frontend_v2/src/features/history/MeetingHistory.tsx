import {
  AlertTriangle,
  ArrowDownUp,
  CalendarClock,
  ChevronRight,
  CircleCheck,
  Database,
  FileText,
  LoaderCircle,
  MoreHorizontal,
  Radio,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MeetingApi } from "../../api/client";
import { meetingDisplayTitle } from "../../app/meetingTitle";
import { useModalDialog } from "../../components/useModalDialog";
import type {
  DataDeletionScope,
  DataRetentionPolicy,
  ImportJobStage,
  MeetingHistoryCursor,
  MeetingHistoryItem,
} from "../../domain/events";

interface MeetingHistoryProps {
  api: MeetingApi;
  onOpenMeeting(meetingId: string): void;
  activeMeetingId?: string | null;
}

type HistoryFilter = "all" | "live" | "processing" | "ready" | "failed";

interface MeetingHistoryViewState {
  query: string;
  filter: HistoryFilter;
  sortOrder: "newest" | "oldest";
  scrollY: number;
}

const HISTORY_VIEW_STATE_KEY = "talktrace.meeting-history.view.v1";
const defaultHistoryViewState: MeetingHistoryViewState = {
  query: "",
  filter: "all",
  sortOrder: "newest",
  scrollY: 0,
};

function readHistoryViewState(): MeetingHistoryViewState {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(HISTORY_VIEW_STATE_KEY) ?? "null") as Partial<MeetingHistoryViewState> | null;
    return {
      query: typeof parsed?.query === "string" ? parsed.query : "",
      filter: ["all", "live", "processing", "ready", "failed"].includes(parsed?.filter ?? "")
        ? parsed!.filter as HistoryFilter
        : "all",
      sortOrder: parsed?.sortOrder === "oldest" ? "oldest" : "newest",
      scrollY: typeof parsed?.scrollY === "number" && Number.isFinite(parsed.scrollY) ? Math.max(0, parsed.scrollY) : 0,
    };
  } catch {
    return defaultHistoryViewState;
  }
}

function writeHistoryViewState(value: MeetingHistoryViewState): void {
  try {
    window.sessionStorage.setItem(HISTORY_VIEW_STATE_KEY, JSON.stringify(value));
  } catch {
    // A disabled session store must not block local meeting access.
  }
}

const importStageLabels: Record<ImportJobStage, string> = {
  reading: "读取文件",
  normalizing: "转换录音",
  transcribing: "本地转写",
  correcting: "文字校正",
  reviewing: "会后整理",
  completed: "导入完成",
  unknown: "准备处理",
};

const deletionOptions: Array<{ scope: DataDeletionScope; label: string; description: string }> = [
  {
    scope: "recording",
    label: "仅录音",
    description: "删除原始录音和音频切片，保留会议文字、AI 整理和历史记录。",
  },
  {
    scope: "derived",
    label: "仅 AI 整理",
    description: "删除会议纪要、决策、待办和建议，保留原始录音与会议文字。",
  },
  {
    scope: "transcript",
    label: "文字及 AI",
    description: "删除会议文字及其 AI 整理，保留原始录音和历史记录。",
  },
  {
    scope: "all",
    label: "整场会议",
    description: "删除录音、文字、AI 整理和历史记录。此操作无法撤销。",
  },
];

const retentionOptions: Array<{ policy: DataRetentionPolicy; label: string; description: string; badge?: string }> = [
  {
    policy: "local_until_user_deletes",
    label: "手动删除",
    description: "不自动删除。",
    badge: "无限制",
  },
  { policy: "30_days", label: "30 天", description: "会议结束 30 天后自动删除。", badge: "推荐" },
  { policy: "90_days", label: "90 天", description: "会议结束 90 天后自动删除。" },
  { policy: "365_days", label: "365 天", description: "会议结束一年后自动删除。" },
];

function formatDate(timestamp: number): string {
  if (!timestamp) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function formatDuration(milliseconds: number): string {
  const minutes = Math.max(0, Math.round(milliseconds / 60_000));
  return minutes ? `${minutes} 分钟` : "不足 1 分钟";
}

function failedReviewNames(meeting: MeetingHistoryItem): string[] {
  const labels = { minutes: "纪要", approach: "方案", index: "索引" } as const;
  return (Object.keys(labels) as Array<keyof typeof labels>).flatMap((kind) =>
    ["failed", "cancelled"].includes(meeting.reviewJobs?.[kind]?.status ?? "") ? [labels[kind]] : [],
  );
}

function meetingStatus(
  meeting: MeetingHistoryItem,
  activeMeetingId: string | null,
): { text: string; filter: Exclude<HistoryFilter, "all"> } {
  const importJob = meeting.importJob;
  if (importJob?.status === "failed" || importJob?.status === "cancelled") {
    return { text: `导入失败 · ${importStageLabels[importJob.stage]}`, filter: "failed" };
  }
  if (importJob && ["pending", "running", "retry_wait"].includes(importJob.status)) {
    const progress = importJob.progress !== null ? ` · ${Math.round(importJob.progress)}%` : "";
    return { text: `${importStageLabels[importJob.stage]}${progress}`, filter: "processing" };
  }
  if (meeting.phase === "live") {
    if (meeting.meetingId === activeMeetingId) return { text: "会议进行中", filter: "live" };
    if (meeting.capture?.state === "active") return { text: "另一窗口录音中", filter: "live" };
    if (meeting.capture?.state === "inactive") return { text: "会议未结束 · 可开始", filter: "live" };
    return { text: "会议未结束 · 可恢复", filter: "live" };
  }
  const failed = failedReviewNames(meeting);
  if (failed.length) return { text: `文字和录音已保留 · ${failed.join("、")}失败`, filter: "failed" };
  const reviewWorking = Object.values(meeting.reviewJobs ?? {}).some((job) =>
    job && ["pending", "running", "retry_wait"].includes(job.status),
  );
  if (reviewWorking) return { text: "会后整理中", filter: "processing" };
  if (meeting.hasMinutes) return { text: "复盘已就绪", filter: "ready" };
  return { text: "文字和录音已保存", filter: "ready" };
}

export function MeetingHistory({ api, onOpenMeeting, activeMeetingId = null }: MeetingHistoryProps) {
  const [initialViewState] = useState(readHistoryViewState);
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingImportId, setRetryingImportId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState(initialViewState.query);
  const [filter, setFilter] = useState<HistoryFilter>(initialViewState.filter);
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">(initialViewState.sortOrder);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<MeetingHistoryCursor | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MeetingHistoryItem | null>(null);
  const [deleteScope, setDeleteScope] = useState<DataDeletionScope>("recording");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [retentionPolicy, setRetentionPolicy] = useState<DataRetentionPolicy>("local_until_user_deletes");
  const [savedRetentionPolicy, setSavedRetentionPolicy] = useState<DataRetentionPolicy>("local_until_user_deletes");
  const [discardSettingsOpen, setDiscardSettingsOpen] = useState(false);
  const [openRowMenuId, setOpenRowMenuId] = useState<string | null>(null);
  const rowMenuRefs = useRef(new Map<string, HTMLDivElement>());
  const rowMenuTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const historyViewStateRef = useRef(initialViewState);
  const restoredScrollRef = useRef(false);

  useEffect(() => {
    historyViewStateRef.current = { ...historyViewStateRef.current, query, filter, sortOrder };
  }, [filter, query, sortOrder]);

  useEffect(() => () => {
    writeHistoryViewState({ ...historyViewStateRef.current, scrollY: window.scrollY });
  }, []);

  useLayoutEffect(() => {
    if (loading || restoredScrollRef.current) return;
    restoredScrollRef.current = true;
    if (initialViewState.scrollY <= 0) return;
    window.requestAnimationFrame(() => window.scrollTo({ top: initialViewState.scrollY, behavior: "auto" }));
  }, [initialViewState.scrollY, loading]);

  const openMeetingFromHistory = (meetingId: string) => {
    writeHistoryViewState({ query, filter, sortOrder, scrollY: window.scrollY });
    onOpenMeeting(meetingId);
  };

  const closeDeleteDialog = useCallback(() => {
    if (!deletingId) setDeleteTarget(null);
  }, [deletingId]);
  const settingsDirty = retentionPolicy !== savedRetentionPolicy;
  const closeSettingsDialog = useCallback(() => {
    if (settingsSaving) return;
    if (settingsDirty) {
      setDiscardSettingsOpen(true);
      return;
    }
    setSettingsOpen(false);
  }, [settingsDirty, settingsSaving]);
  const deleteDialogRef = useModalDialog(Boolean(deleteTarget), closeDeleteDialog, Boolean(deletingId));
  const settingsDialogRef = useModalDialog(settingsOpen, closeSettingsDialog, settingsSaving);

  const closeRowMenu = useCallback((restoreFocus: boolean) => {
    const meetingId = openRowMenuId;
    setOpenRowMenuId(null);
    if (restoreFocus && meetingId) {
      window.requestAnimationFrame(() => rowMenuTriggerRefs.current.get(meetingId)?.focus());
    }
  }, [openRowMenuId]);

  useEffect(() => {
    if (!openRowMenuId) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rowMenuRefs.current.get(openRowMenuId)?.contains(event.target as Node)) closeRowMenu(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeRowMenu(true);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [closeRowMenu, openRowMenuId]);

  const load = useCallback(async (
    signal?: AbortSignal,
    mode: "replace" | "append" | "quiet" = "replace",
    cursor: MeetingHistoryCursor | null = null,
  ) => {
    if (mode === "replace") setLoading(true);
    if (mode === "append") setLoadingMore(true);
    setError(null);
    try {
      if (api.listMeetingsPage) {
        const page = await api.listMeetingsPage({ query, status: filter, limit: 12, cursor }, signal);
        setMeetings((current) => {
          const byId = new Map((mode === "append" ? current : []).map((meeting) => [meeting.meetingId, meeting]));
          for (const meeting of page.meetings) byId.set(meeting.meetingId, meeting);
          return [...byId.values()].sort((a, b) => b.updatedAtMs - a.updatedAtMs);
        });
        setHasMore(page.hasMore);
        setNextCursor(page.nextCursor);
      } else {
        const history = await api.listMeetings(signal);
        const normalizedQuery = query.trim().toLocaleLowerCase();
        const filtered = history.meetings.filter((meeting) => {
          const status = meetingStatus(meeting, activeMeetingId);
          if (filter !== "all" && status.filter !== filter) return false;
          return !normalizedQuery || meetingDisplayTitle(
            meeting.title,
            meeting.startedAtMs ?? meeting.createdAtMs,
            meeting.meetingId,
          ).toLocaleLowerCase().includes(normalizedQuery);
        });
        setMeetings(filtered.sort((a, b) => b.updatedAtMs - a.updatedAtMs));
        setHasMore(false);
        setNextCursor(null);
      }
    } catch (loadError) {
      if (signal?.aborted) return;
      setError(loadError instanceof Error ? loadError.message : "会议记录加载失败");
    } finally {
      if (!signal?.aborted) {
        if (mode === "replace") setLoading(false);
        if (mode === "append") setLoadingMore(false);
      }
    }
  }, [activeMeetingId, api, filter, query]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal, "replace");
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    const hasActiveWork = meetings.some((meeting) =>
      meetingStatus(meeting, activeMeetingId).filter === "processing" || meeting.capture?.state === "active",
    );
    if (!hasActiveWork) return;
    const timer = window.setInterval(() => void load(undefined, "quiet"), 3_000);
    return () => window.clearInterval(timer);
  }, [activeMeetingId, load, meetings]);

  const openDeleteDialog = (meeting: MeetingHistoryItem) => {
    if (deletingId) return;
    setDeleteTarget(meeting);
    setDeleteScope("recording");
    setDeleteError(null);
  };

  const deleteMeeting = async () => {
    if (deletingId || !deleteTarget) return;
    const meetingId = deleteTarget.meetingId;
    setDeletingId(meetingId);
    setDeleteError(null);
    try {
      await api.deleteMeeting(meetingId, deleteScope);
      if (deleteScope === "all") {
        setMeetings((current) => current.filter((item) => item.meetingId !== meetingId));
      } else {
        await load(undefined, "quiet");
      }
      setDeleteTarget(null);
    } catch (deleteError) {
      setDeleteError(deleteError instanceof Error ? deleteError.message : "本地数据删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  const loadDataGovernanceSettings = async () => {
    setSettingsLoading(true);
    setSettingsError(null);
    setSettingsSaved(false);
    try {
      if (!api.getDataGovernanceSettings) throw new Error("当前运行版本不支持本地数据设置");
      const settings = await api.getDataGovernanceSettings();
      setRetentionPolicy(settings.retentionPolicy);
      setSavedRetentionPolicy(settings.retentionPolicy);
    } catch (settingsLoadError) {
      setSettingsError(settingsLoadError instanceof Error ? settingsLoadError.message : "保留策略加载失败");
    } finally {
      setSettingsLoading(false);
    }
  };

  const openDataGovernanceSettings = () => {
    setDiscardSettingsOpen(false);
    setSettingsOpen(true);
    void loadDataGovernanceSettings();
  };

  const saveDataGovernanceSettings = async () => {
    if (settingsSaving) return;
    setSettingsSaving(true);
    setSettingsError(null);
    setSettingsSaved(false);
    try {
      if (!api.updateDataGovernanceSettings) throw new Error("当前运行版本不支持本地数据设置");
      const settings = await api.updateDataGovernanceSettings(retentionPolicy);
      setRetentionPolicy(settings.retentionPolicy);
      setSavedRetentionPolicy(settings.retentionPolicy);
      setSettingsSaved(true);
    } catch (settingsSaveError) {
      setSettingsError(settingsSaveError instanceof Error ? settingsSaveError.message : "保留策略保存失败");
    } finally {
      setSettingsSaving(false);
    }
  };

  const retryImport = async (meeting: MeetingHistoryItem) => {
    if (retryingImportId || !meeting.importJob?.retryable) return;
    setRetryingImportId(meeting.meetingId);
    setError(null);
    try {
      await api.retryImportJob(meeting.meetingId);
      await load(undefined, "quiet");
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : "录音导入重试失败");
    } finally {
      setRetryingImportId(null);
    }
  };

  const historyStats = meetings.reduce(
    (summary, meeting) => {
      summary[meetingStatus(meeting, activeMeetingId).filter] += 1;
      summary.segments += meeting.segmentCount;
      return summary;
    },
    { live: 0, processing: 0, ready: 0, failed: 0, segments: 0 },
  );
  const displayedMeetings = [...meetings].sort((left, right) => (
    sortOrder === "newest" ? right.updatedAtMs - left.updatedAtMs : left.updatedAtMs - right.updatedAtMs
  ));

  return (
    <section className="history-section" aria-labelledby="history-heading">
      <div className="history-heading-row">
        <div>
          <span className="section-kicker">会议总览</span>
          <h2 id="history-heading">全部会议</h2>
        </div>
        <div className="history-heading-actions">
          <button
            type="button"
            className="secondary-button history-data-settings"
            onClick={openDataGovernanceSettings}
          >
            <Database size={16} />
            本地数据
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={() => void load(undefined, "replace")}
            disabled={loading}
            title="刷新会议记录"
            aria-label="刷新会议记录"
          >
            {loading ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
          </button>
        </div>
      </div>

      <div className="history-overview" aria-label="会议记录统计">
        <article>
          <span className="history-overview-icon history-overview-icon--live"><Radio size={20} /></span>
          <div><span>进行中</span><strong>{historyStats.live}</strong><small>实时转写</small></div>
        </article>
        <article>
          <span className="history-overview-icon history-overview-icon--processing"><RefreshCw size={20} /></span>
          <div><span>处理中</span><strong>{historyStats.processing}</strong><small>转写 / AI 整理</small></div>
        </article>
        <article>
          <span className="history-overview-icon history-overview-icon--ready"><CircleCheck size={20} /></span>
          <div><span>已完成</span><strong>{historyStats.ready}</strong><small>复盘就绪</small></div>
        </article>
        <article>
          <span className="history-overview-icon history-overview-icon--segments"><FileText size={20} /></span>
          <div><span>本地文字</span><strong>{historyStats.segments}</strong><small>已保存片段</small></div>
        </article>
      </div>

      <div className="history-controls">
        <label className="history-search">
          <Search size={16} />
          <span className="sr-only">搜索会议</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会议名称" />
        </label>
        <label className="history-filter">
          <span className="sr-only">按状态筛选</span>
          <select value={filter} onChange={(event) => setFilter(event.target.value as HistoryFilter)}>
            <option value="all">全部状态</option>
            <option value="live">进行中</option>
            <option value="processing">处理中</option>
            <option value="ready">已完成</option>
            <option value="failed">需要处理</option>
          </select>
        </label>
        <label className="history-sort">
          <ArrowDownUp size={16} aria-hidden="true" />
          <span className="sr-only">会议排序</span>
          <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value as "newest" | "oldest") }>
            <option value="newest">按时间倒序</option>
            <option value="oldest">按时间正序</option>
          </select>
        </label>
      </div>

      {error ? <p className="inline-error">{error}</p> : null}
      {!loading && !error && meetings.length === 0 ? (
        <div className="history-empty-state">
          <CalendarClock size={32} strokeWidth={1.5} />
          <p>{meetings.length ? "没有符合条件的会议" : "完成的会议会出现在这里"}</p>
        </div>
      ) : null}
      <div className="history-table-header" aria-hidden="true">
        <span />
        <span>会议信息</span>
        <span>文字</span>
        <span>状态</span>
        <span>操作</span>
      </div>
      <div className="history-list">
        {displayedMeetings.map((meeting) => {
          const title = meetingDisplayTitle(meeting.title, meeting.startedAtMs ?? meeting.createdAtMs, meeting.meetingId);
          const status = meetingStatus(meeting, activeMeetingId);
          return (
            <div className="history-row" key={meeting.meetingId}>
              <span className={`history-state history-state--${status.filter}`} aria-hidden="true" />
              <button
                type="button"
                className="history-row-open"
                data-meeting-id={meeting.meetingId}
                onClick={() => openMeetingFromHistory(meeting.meetingId)}
                aria-label={`打开会议：${title}`}
              >
                <span className="history-row-main">
                  <strong>{title}</strong>
                  <span>
                    <CalendarClock size={14} />
                    {formatDate(meeting.startedAtMs ?? meeting.createdAtMs)}
                    <span aria-hidden="true">·</span>
                    {formatDuration(meeting.audioDurationMs)}
                  </span>
                </span>
                <span className="history-row-count">{meeting.segmentCount} 段文字</span>
                <span className={`history-row-meta history-row-meta--${status.filter}`}>{status.text}</span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
              <div
                className="history-row-menu"
                ref={(node) => {
                  if (node) rowMenuRefs.current.set(meeting.meetingId, node);
                  else rowMenuRefs.current.delete(meeting.meetingId);
                }}
              >
                <button
                  ref={(node) => {
                    if (node) rowMenuTriggerRefs.current.set(meeting.meetingId, node);
                    else rowMenuTriggerRefs.current.delete(meeting.meetingId);
                  }}
                  className="icon-button icon-button--small"
                  type="button"
                  onClick={() => setOpenRowMenuId((current) => current === meeting.meetingId ? null : meeting.meetingId)}
                  disabled={Boolean(deletingId)}
                  aria-label={`会议操作：${title}`}
                  aria-haspopup="menu"
                  aria-expanded={openRowMenuId === meeting.meetingId}
                  title="会议操作"
                >
                  {deletingId === meeting.meetingId ? <LoaderCircle className="spin" size={15} /> : <MoreHorizontal size={17} />}
                </button>
                {openRowMenuId === meeting.meetingId ? (
                  <div className="history-row-menu__popover" role="menu" aria-label={`会议操作：${title}`}>
                    {meeting.importJob?.retryable && ["failed", "cancelled"].includes(meeting.importJob.status) ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setOpenRowMenuId(null);
                          void retryImport(meeting);
                        }}
                        disabled={Boolean(retryingImportId)}
                        aria-label={`重试录音导入：${title}`}
                      >
                        {retryingImportId === meeting.meetingId ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
                        重试录音导入
                      </button>
                    ) : null}
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setOpenRowMenuId(null);
                        rowMenuTriggerRefs.current.get(meeting.meetingId)?.focus();
                        openDeleteDialog(meeting);
                      }}
                      aria-label={`管理本地数据：${title}`}
                    >
                      <Trash2 size={15} />
                      管理本地数据
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {hasMore && nextCursor ? (
        <button
          className="secondary-button history-load-more"
          type="button"
          disabled={loadingMore}
          onClick={() => void load(undefined, "append", nextCursor)}
        >
          {loadingMore ? <LoaderCircle className="spin" size={16} /> : null}
          {loadingMore ? "正在加载" : "加载更多"}
        </button>
      ) : null}

      {deleteTarget ? (
        <div className="drawer-layer" role="presentation">
          <button
            className="drawer-scrim"
            type="button"
            aria-label="关闭删除本地数据"
            onClick={closeDeleteDialog}
            disabled={Boolean(deletingId)}
          />
          <section
            ref={deleteDialogRef}
            className="data-governance-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-local-data-title"
            aria-busy={Boolean(deletingId)}
            tabIndex={-1}
          >
            <header className="drawer-header">
              <h2 id="delete-local-data-title">删除会议数据</h2>
              <button
                className="icon-button"
                type="button"
                onClick={closeDeleteDialog}
                disabled={Boolean(deletingId)}
                aria-label="关闭删除本地数据"
                title="关闭"
              >
                <X size={18} />
              </button>
            </header>
            <div className="data-governance-body">
              <p className="data-governance-intro">
                选择要从“{meetingDisplayTitle(
                  deleteTarget.title,
                  deleteTarget.startedAtMs ?? deleteTarget.createdAtMs,
                  deleteTarget.meetingId,
                )}”中删除的内容。
              </p>
              <div className="deletion-warning" role="note">
                <AlertTriangle size={16} aria-hidden="true" />
                <span>删除后无法恢复，请确认所选范围。未选择的录音、文字或 AI 内容会继续保留。</span>
              </div>
              <fieldset className="deletion-scope-list" disabled={Boolean(deletingId)}>
                <legend className="sr-only">选择删除范围</legend>
                {deletionOptions.map((option) => (
                  <label
                    className={`deletion-scope-option${deleteScope === option.scope ? " is-selected" : ""}`}
                    key={option.scope}
                  >
                    <input
                      type="radio"
                      name="deletion-scope"
                      value={option.scope}
                      checked={deleteScope === option.scope}
                      onChange={() => setDeleteScope(option.scope)}
                    />
                    <span>
                      <strong>{option.label}</strong>
                      <small>{option.description}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
              {deleteError ? <p className="inline-error" role="alert">{deleteError}</p> : null}
            </div>
            <footer className="data-governance-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={closeDeleteDialog}
                disabled={Boolean(deletingId)}
                data-dialog-initial-focus
              >
                取消
              </button>
              <button
                className="danger-button"
                type="button"
                onClick={() => void deleteMeeting()}
                disabled={Boolean(deletingId)}
              >
                {deletingId ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}
                {deletingId ? "正在删除" : `删除${deletionOptions.find((item) => item.scope === deleteScope)?.label ?? "所选数据"}`}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {settingsOpen ? (
        <div className="drawer-layer" role="presentation">
          <button
            className="drawer-scrim"
            type="button"
            aria-label="关闭本地数据设置"
            onClick={closeSettingsDialog}
            disabled={settingsSaving}
          />
          <section
            ref={settingsDialogRef}
            className="data-governance-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="data-retention-title"
            aria-busy={settingsLoading || settingsSaving}
            tabIndex={-1}
          >
            <header className="drawer-header">
              <h2 id="data-retention-title">数据保留策略</h2>
              <button
                className="icon-button"
                type="button"
                onClick={closeSettingsDialog}
                disabled={settingsSaving}
                aria-label="关闭本地数据设置"
                title="关闭"
              >
                <X size={18} />
              </button>
            </header>
            <div className="data-governance-body">
              <p className="data-governance-intro">
                会议数据默认保存在这台电脑上。
              </p>
              {settingsLoading ? (
                <p className="data-governance-loading" role="status">
                  <LoaderCircle className="spin" size={16} />
                  正在读取设置
                </p>
              ) : (
                <fieldset className="retention-policy-field" disabled={settingsSaving || Boolean(settingsError)}>
                  <legend>会议数据保留时间</legend>
                  <div className="retention-choice-list">
                    {retentionOptions.map((option) => (
                      <label
                        className={`retention-choice${retentionPolicy === option.policy ? " is-selected" : ""}`}
                        key={option.policy}
                      >
                        <input
                          type="radio"
                          name="retention-policy"
                          value={option.policy}
                          checked={retentionPolicy === option.policy}
                          onChange={() => {
                            setRetentionPolicy(option.policy);
                            setSettingsSaved(false);
                            setDiscardSettingsOpen(false);
                          }}
                        />
                        <span>
                          <strong>{option.label}</strong>
                          <small>{option.description}</small>
                        </span>
                        {option.badge ? <em>{option.badge}</em> : null}
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
              <p className="data-governance-note">
                自动删除仅处理已结束且超过所选期限的会议，删除范围为整场会议。
              </p>
              {settingsError ? (
                <div className="data-governance-error">
                  <p className="inline-error" role="alert">{settingsError}</p>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => void loadDataGovernanceSettings()}
                    disabled={settingsLoading}
                  >
                    <RefreshCw size={15} />
                    重试
                  </button>
                </div>
              ) : null}
              {settingsSaved ? <p className="inline-success" role="status">保留策略已保存</p> : null}
              {discardSettingsOpen ? (
                <div className="settings-discard-confirm" role="alert">
                  <div>
                    <strong>放弃未保存的修改？</strong>
                    <span>关闭后，本次保留策略修改不会生效。</span>
                  </div>
                  <div className="settings-discard-confirm__actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => setDiscardSettingsOpen(false)}
                    >
                      继续编辑
                    </button>
                    <button
                      className="danger-button"
                      type="button"
                      onClick={() => {
                        setRetentionPolicy(savedRetentionPolicy);
                        setDiscardSettingsOpen(false);
                        setSettingsOpen(false);
                      }}
                    >
                      放弃修改
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <footer className="data-governance-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={closeSettingsDialog}
                disabled={settingsSaving}
              >
                关闭
              </button>
              <button
                className="primary-button"
                type="button"
                onClick={() => void saveDataGovernanceSettings()}
                disabled={settingsLoading || settingsSaving || Boolean(settingsError)}
              >
                {settingsSaving ? <LoaderCircle className="spin" size={16} /> : null}
                {settingsSaving ? "正在保存" : "保存设置"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
