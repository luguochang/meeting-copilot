import { CalendarClock, ChevronRight, LoaderCircle, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { MeetingApi } from "../../api/client";
import type { MeetingHistoryItem } from "../../domain/events";

interface MeetingHistoryProps {
  api: MeetingApi;
  onOpenMeeting(meetingId: string): void;
}

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

export function MeetingHistory({ api, onOpenMeeting }: MeetingHistoryProps) {
  const [meetings, setMeetings] = useState<MeetingHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const history = await api.listMeetings(signal);
      setMeetings(history.meetings.slice(0, 8));
    } catch (loadError) {
      if (signal?.aborted) return;
      setError(loadError instanceof Error ? loadError.message : "会议记录加载失败");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const deleteMeeting = async (meeting: MeetingHistoryItem) => {
    if (deletingId) return;
    const title = meeting.title ?? "未命名会议";
    if (!window.confirm(`删除“${title}”及其录音和文字？此操作无法撤销。`)) return;
    setDeletingId(meeting.meetingId);
    setError(null);
    try {
      await api.deleteMeeting(meeting.meetingId);
      setMeetings((current) => current.filter((item) => item.meetingId !== meeting.meetingId));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "会议删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="history-section" aria-labelledby="history-heading">
      <div className="history-heading-row">
        <div>
          <span className="section-kicker">最近</span>
          <h2 id="history-heading">会议记录</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={() => void load()}
          disabled={loading}
          title="刷新会议记录"
          aria-label="刷新会议记录"
        >
          {loading ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
        </button>
      </div>

      {error ? <p className="inline-error">{error}</p> : null}
      {!loading && !error && meetings.length === 0 ? (
        <div className="history-empty-state">
          <CalendarClock size={32} strokeWidth={1.5} />
          <p>完成的会议会出现在这里</p>
        </div>
      ) : null}
      <div className="history-list">
        {meetings.map((meeting) => (
          <div className="history-row" key={meeting.meetingId}>
            <span className={`history-state history-state--${meeting.phase}`} aria-hidden="true" />
            <button
              type="button"
              className="history-row-open"
              data-meeting-id={meeting.meetingId}
              onClick={() => onOpenMeeting(meeting.meetingId)}
              aria-label={`打开会议：${meeting.title ?? "未命名会议"}`}
            >
              <span className="history-row-main">
                <strong>{meeting.title ?? "未命名会议"}</strong>
                <span>
                  <CalendarClock size={14} />
                  {formatDate(meeting.startedAtMs ?? meeting.createdAtMs)}
                  <span aria-hidden="true">·</span>
                  {formatDuration(meeting.audioDurationMs)}
                </span>
              </span>
              <span className="history-row-meta">
                {meeting.segmentCount} 段文字
                {meeting.hasMinutes ? " · 已复盘" : meeting.phase === "live" ? " · 进行中" : " · 待整理"}
              </span>
              <ChevronRight size={17} aria-hidden="true" />
            </button>
            <button
              className="icon-button icon-button--small history-delete"
              type="button"
              onClick={() => void deleteMeeting(meeting)}
              disabled={Boolean(deletingId)}
              title="删除会议"
              aria-label={`删除会议：${meeting.title ?? "未命名会议"}`}
            >
              {deletingId === meeting.meetingId ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
