import {
  CalendarClock,
  ChevronRight,
  Database,
  LoaderCircle,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { MeetingApi } from "../../api/client";
import { meetingDisplayTitle } from "../../app/meetingTitle";
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
}

type HistoryFilter = "all" | "live" | "processing" | "ready" | "failed";

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

const retentionOptions: Array<{ policy: DataRetentionPolicy; label: string }> = [
  { policy: "local_until_user_deletes", label: "由我手动删除（默认）" },
  { policy: "30_days", label: "会议结束 30 天后自动删除" },
  { policy: "90_days", label: "会议结束 90 天后自动删除" },
  { policy: "365_days", label: "会议结束 365 天后自动删除" },
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

function meetingStatus(meeting: MeetingHistoryItem): { text: string; filter: Exclude<HistoryFilter, "all"> } {
  const importJob = meeting.importJob;
  if (importJob?.status === "failed" || importJob?.status === "cancelled") {
    return { text: `导入失败 · ${importStageLabels[importJob.stage]}`, filter: "failed" };
  }
  if (importJob && ["pending", "running", "retry_wait"].includes(importJob.status)) {
    const progress = importJob.progress !== null ? ` · ${Math.round(importJob.progress)}%` : "";
    return { text: `${importStageLabels[importJob.stage]}${progress}`, filter: "processing" };
  }
  if (meeting.phase === "live") return { text: "会议进行中", filter: "live" };
  const failed = failedReviewNames(meeting);
  if (failed.length) return { text: `文字和录音已保留 · ${failed.join("、")}失败`, filter: "failed" };
  const reviewWorking = Object.values(meeting.reviewJobs ?? {}).some((job) =>
    job && ["pending", "running", "retry_wait"].includes(job.status),
  );
  if (reviewWorking) return { text: "会后整理中", filter: "processing" };
  if (meeting.hasMinutes) return { text: "复盘已就绪", filter: "ready" };
  return { text: "文字和录音已保存", filter: "ready" };
}

export function MeetingHistory({ api, onOpenMeeting }: MeetingHistoryProps) {
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingImportId, setRetryingImportId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<HistoryFilter>("all");
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
          const status = meetingStatus(meeting);
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
  }, [api, filter, query]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal, "replace");
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    const hasActiveWork = meetings.some((meeting) => meetingStatus(meeting).filter === "processing");
    if (!hasActiveWork) return;
    const timer = window.setInterval(() => void load(undefined, "quiet"), 3_000);
    return () => window.clearInterval(timer);
  }, [load, meetings]);

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
    } catch (settingsLoadError) {
      setSettingsError(settingsLoadError instanceof Error ? settingsLoadError.message : "保留策略加载失败");
    } finally {
      setSettingsLoading(false);
    }
  };

  const openDataGovernanceSettings = () => {
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

  return (
    <section className="history-section" aria-labelledby="history-heading">
      <div className="history-heading-row">
        <div>
          <span className="section-kicker">历史</span>
          <h2 id="history-heading">会议记录</h2>
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
      </div>

      {error ? <p className="inline-error">{error}</p> : null}
      {!loading && !error && meetings.length === 0 ? (
        <div className="history-empty-state">
          <CalendarClock size={32} strokeWidth={1.5} />
          <p>{meetings.length ? "没有符合条件的会议" : "完成的会议会出现在这里"}</p>
        </div>
      ) : null}
      <div className="history-list">
        {meetings.map((meeting) => {
          const title = meetingDisplayTitle(meeting.title, meeting.startedAtMs ?? meeting.createdAtMs, meeting.meetingId);
          const status = meetingStatus(meeting);
          return (
            <div className="history-row" key={meeting.meetingId}>
              <span className={`history-state history-state--${status.filter}`} aria-hidden="true" />
              <button
                type="button"
                className="history-row-open"
                data-meeting-id={meeting.meetingId}
                onClick={() => onOpenMeeting(meeting.meetingId)}
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
                <span className={`history-row-meta history-row-meta--${status.filter}`}>
                  {meeting.segmentCount} 段文字 · {status.text}
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
              <button
                className="icon-button icon-button--small history-delete"
                type="button"
                onClick={() => openDeleteDialog(meeting)}
                disabled={Boolean(deletingId)}
                title="管理或删除本地数据"
                aria-label={`管理本地数据：${title}`}
              >
                {deletingId === meeting.meetingId ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
              </button>
              {meeting.importJob?.retryable && ["failed", "cancelled"].includes(meeting.importJob.status) ? (
                <button
                  className="icon-button icon-button--small history-retry"
                  type="button"
                  onClick={() => void retryImport(meeting)}
                  disabled={Boolean(retryingImportId)}
                  title="重试录音导入"
                  aria-label={`重试录音导入：${title}`}
                >
                  {retryingImportId === meeting.meetingId ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
                </button>
              ) : null}
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
            onClick={() => setDeleteTarget(null)}
            disabled={Boolean(deletingId)}
          />
          <section
            className="data-governance-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-local-data-title"
          >
            <header className="drawer-header">
              <div>
                <span className="eyebrow">本地数据</span>
                <h2 id="delete-local-data-title">删除会议数据</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setDeleteTarget(null)}
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
                onClick={() => setDeleteTarget(null)}
                disabled={Boolean(deletingId)}
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
            onClick={() => setSettingsOpen(false)}
            disabled={settingsSaving}
          />
          <section
            className="data-governance-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="data-retention-title"
          >
            <header className="drawer-header">
              <div>
                <span className="eyebrow">本地数据</span>
                <h2 id="data-retention-title">数据保留策略</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                onClick={() => setSettingsOpen(false)}
                disabled={settingsSaving}
                aria-label="关闭本地数据设置"
                title="关闭"
              >
                <X size={18} />
              </button>
            </header>
            <div className="data-governance-body">
              <p className="data-governance-intro">
                录音、文字和 AI 整理保存在这台电脑上。默认不会自动删除。
              </p>
              {settingsLoading ? (
                <p className="data-governance-loading" role="status">
                  <LoaderCircle className="spin" size={16} />
                  正在读取设置
                </p>
              ) : (
                <label className="retention-policy-field">
                  <span>会议数据保留时间</span>
                  <select
                    value={retentionPolicy}
                    onChange={(event) => {
                      setRetentionPolicy(event.target.value as DataRetentionPolicy);
                      setSettingsSaved(false);
                    }}
                    disabled={settingsSaving || Boolean(settingsError)}
                  >
                    {retentionOptions.map((option) => (
                      <option value={option.policy} key={option.policy}>{option.label}</option>
                    ))}
                  </select>
                </label>
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
            </div>
            <footer className="data-governance-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setSettingsOpen(false)}
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
