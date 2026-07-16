import type { RuntimeIndicator } from "../domain/events";

interface StatusIndicatorProps {
  label: string;
  indicator: RuntimeIndicator;
  showLevel?: boolean;
}

export function StatusIndicator({ label, indicator, showLevel = false }: StatusIndicatorProps) {
  const level = indicator.level === null ? 0 : Math.max(0, Math.min(1, indicator.level));
  return (
    <div className="status-indicator" title={indicator.detail ?? indicator.label}>
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
}
