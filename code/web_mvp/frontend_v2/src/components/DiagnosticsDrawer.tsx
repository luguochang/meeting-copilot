import { Activity, Download, LoaderCircle, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import type { MeetingViewState } from "../domain/events";

interface DiagnosticsDrawerProps {
  open: boolean;
  onClose(): void;
  onRefresh(): void;
  onExport(): Promise<void>;
  state: MeetingViewState;
  transportKind: "poll" | "sse";
}

function formattedTime(value: number | null): string {
  if (value === null) return "尚未读取";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(value);
}

const connectionLabels: Record<MeetingViewState["connection"], string> = {
  idle: "尚未连接",
  connecting: "正在连接本地服务",
  live: "本地服务已连接",
  reconnecting: "正在重新连接本地服务",
  offline: "本地服务不可用",
};

export function DiagnosticsDrawer({ open, onClose, onRefresh, onExport, state, transportKind }: DiagnosticsDrawerProps) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  if (!open) return null;

  const exportBundle = async () => {
    if (exporting) return;
    setExportError("");
    setExporting(true);
    try {
      await onExport();
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "诊断包导出失败");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="drawer-layer" role="presentation">
      <button className="drawer-scrim" aria-label="关闭运行诊断" onClick={onClose} />
      <aside className="diagnostics-drawer" role="dialog" aria-modal="true" aria-labelledby="diagnostics-title">
        <header className="drawer-header">
          <div>
            <span className="eyebrow">运行诊断</span>
            <h2 id="diagnostics-title">会议连接详情</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭运行诊断" title="关闭">
            <X size={18} />
          </button>
        </header>

        <dl className="diagnostics-list">
          <div><dt>会议编号</dt><dd>{state.meetingId || "未提供"}</dd></div>
          <div><dt>连接状态</dt><dd>{connectionLabels[state.connection]}</dd></div>
          <div><dt>事件通道</dt><dd>{transportKind.toUpperCase()}</dd></div>
          <div><dt>最新序号</dt><dd>{state.lastSeq}</dd></div>
          <div><dt>最后读取</dt><dd>{formattedTime(state.lastSyncedAtMs)}</dd></div>
          <div><dt>已确认段落</dt><dd>{state.archivedSegmentCount + state.segments.length}</dd></div>
          <div><dt>建议记录</dt><dd>{state.suggestions.length}</dd></div>
        </dl>

        {state.transportError ? (
          <div className="diagnostic-alert" role="alert">
            <Activity size={16} />
            <span>{state.transportError}</span>
          </div>
        ) : null}

        {exportError ? (
          <div className="diagnostic-alert" role="alert">
            <Activity size={16} />
            <span>{exportError}</span>
          </div>
        ) : null}

        <section className="diagnostics-raw" aria-labelledby="diagnostics-raw-title">
          <h3 id="diagnostics-raw-title">内部运行字段</h3>
          <pre>{JSON.stringify(state.diagnostics, null, 2)}</pre>
        </section>

        <div className="drawer-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => void exportBundle()}
            disabled={exporting}
          >
            {exporting ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}
            {exporting ? "正在导出" : "导出脱敏诊断包"}
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={onRefresh}
            title="重新从本地会议服务读取当前状态"
          >
            <RefreshCw size={16} />
            重新读取状态
          </button>
        </div>
      </aside>
    </div>
  );
}
