import type { RuntimeIndicator } from "../domain/events";
import { ChevronDown } from "lucide-react";

interface StatusIndicatorProps {
  label: string;
  indicator: RuntimeIndicator;
  showLevel?: boolean;
}

export function StatusIndicator({ label, indicator, showLevel = false }: StatusIndicatorProps) {
  const level = indicator.level === null ? 0 : Math.max(0, Math.min(1, indicator.level));
  const content = (
    <div
      className="status-indicator"
      title={indicator.detail ?? indicator.label}
      aria-label={`${label} ${indicator.label}`}
    >
      <span className={`status-dot status-dot--${indicator.state}`} aria-hidden="true" />
      <span className="status-label">{label}</span>
      <span className="status-value">{indicator.label}</span>
      {showLevel && indicator.level !== null ? (
        <span className="input-meter" aria-label={`输入电平 ${Math.round(level * 100)}%`}>
          <span style={{ transform: `scaleX(${level})` }} />
        </span>
      ) : null}
    </div>
  );
  if (!indicator.capabilities || !Object.keys(indicator.capabilities).length) return content;
  const capabilityLabels: Record<string, string> = {
    provider: "模型连接",
    transcript: "文字理解",
    intelligence: "实时洞察",
    proactive_suggestions: "主动建议",
    review: "会后整理",
    realtime_suggestions: "实时建议",
    minutes: "会议纪要",
    index: "全文索引",
  };
  return (
    <details className="status-capability-menu">
      <summary>{content}<ChevronDown size={12} /></summary>
      <div className="status-capability-popover">
        {Object.entries(indicator.capabilities).map(([key, capability]) => (
          <div key={key}>
            <span className={`status-dot status-dot--${capability.state}`} />
            <span>{capabilityLabels[key] ?? key}</span>
            <strong>{capability.label}</strong>
          </div>
        ))}
      </div>
    </details>
  );
}
