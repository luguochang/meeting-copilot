import { Activity, CircleCheckBig, Download, LoaderCircle, RefreshCw, TriangleAlert, X } from "lucide-react";
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

function coachRuntimeIssue(state: MeetingViewState): string | null {
  const decision = state.coachDecision;
  if (decision) {
    if (decision.statusReason === "realtime_provider_circuit_open"
    || decision.statusReason === "realtime_provider_recovery_probe_required") {
      return "AI 实时通道连续失败，需要连接测试恢复";
    }
    if (decision.status === "timed_out") return "Pi 实时教练超时，本轮未生成建议";
    if (decision.status === "failed") return "Pi 实时教练执行失败，本轮未生成建议";
  }

  const history = state.diagnostics.coach_runtime_history;
  if (!Array.isArray(history)) return null;
  const failures = history.filter((item): item is Record<string, unknown> => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false;
    const value = item as Record<string, unknown>;
    return value.outcome === "failure" && (value.origin === "pi" || value.pi_provider_attempted === true);
  });
  const latest = failures.at(-1);
  if (!latest) return null;
  const reason = latest.status_reason === "provider_timeout" || latest.fallback_reason === "provider_timeout"
    ? "Pi Provider 超时"
    : latest.status === "timed_out"
      ? "Pi 实时教练超时"
      : "Pi 实时教练执行失败";
  const count = failures.length > 1 ? `（${failures.length} 次）` : "";
  return `本场曾发生 ${reason}，本场未生成 Pi 建议${count}`;
}

function coachRuntimeInfo(state: MeetingViewState): string | null {
  const history = state.diagnostics.coach_runtime_history;
  if (!Array.isArray(history)) return null;
  const fallbacks = history.filter((item): item is Record<string, unknown> => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false;
    return (item as Record<string, unknown>).outcome === "local_reflex_fallback";
  });
  if (!fallbacks.length) return null;
  const providerAttempts = fallbacks.filter((item) =>
    item.pi_provider_attempted === true || item.provider_attempted === true,
  ).length;
  if (providerAttempts === 0) return `本场有 ${fallbacks.length} 轮使用本地实时提示，未调用 Pi Provider`;
  if (providerAttempts === fallbacks.length) {
    return `本场有 ${fallbacks.length} 轮使用本地实时提示，Pi Provider 已调用但未在实时窗口内完成`;
  }
  return `本场有 ${fallbacks.length} 轮使用本地实时提示，其中 ${providerAttempts} 轮已调用 Pi Provider 但未在实时窗口内完成`;
}

export function DiagnosticsDrawer({ open, onClose, onRefresh, onExport, state, transportKind }: DiagnosticsDrawerProps) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  if (!open) return null;
  const coachIssue = coachRuntimeIssue(state);
  const coachInfo = coachRuntimeInfo(state);
  const connectionHealthy = state.connection === "live" && !state.transportError;
  const healthy = connectionHealthy && !coachIssue;
  const healthTitle = healthy
    ? "运行正常"
    : connectionHealthy
      ? "AI 实时能力异常"
      : "连接异常";
  const healthDetail = coachIssue ?? connectionLabels[state.connection];
  const technicalDetails = {
    ...state.diagnostics,
    ...(state.coachDecision?.agentMetrics
      ? { coach_agent_metrics: state.coachDecision.agentMetrics }
      : {}),
  };

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
          <h2 id="diagnostics-title">会议连接详情</h2>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭运行诊断" title="关闭">
            <X size={18} />
          </button>
        </header>

        <div className={`diagnostics-health-summary diagnostics-health-summary--${healthy ? "healthy" : "attention"}`} role="status">
          {healthy ? <CircleCheckBig size={22} /> : <TriangleAlert size={22} />}
          <div>
            <strong>{healthTitle}</strong>
            <span>{healthy ? (coachInfo ?? "本地服务和 AI 实时能力正常") : healthDetail}</span>
          </div>
        </div>

        <dl className="diagnostics-list">
          <div><dt>会议编号</dt><dd>{state.meetingId || "未提供"}</dd></div>
          <div><dt>连接状态</dt><dd>{connectionLabels[state.connection]}</dd></div>
          <div><dt>事件通道</dt><dd>{transportKind.toUpperCase()}</dd></div>
          <div><dt>最新序号</dt><dd>{state.lastSeq}</dd></div>
          <div><dt>最后读取</dt><dd>{formattedTime(state.lastSyncedAtMs)}</dd></div>
          <div><dt>已确认段落</dt><dd>{state.archivedSegmentCount + state.segments.length}</dd></div>
          <div><dt>建议记录</dt><dd>{state.suggestions.length}</dd></div>
          <div><dt>AI 教练</dt><dd>{coachIssue ?? coachInfo ?? "未发现运行异常"}</dd></div>
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

        <details className="diagnostics-raw">
          <summary>技术详情</summary>
          <pre>{JSON.stringify(technicalDetails, null, 2)}</pre>
        </details>

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
