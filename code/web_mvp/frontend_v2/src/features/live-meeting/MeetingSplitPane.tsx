import { useRef, useState, type CSSProperties, type ReactNode } from "react";

const STORAGE_KEY = "meeting-copilot-transcript-share";
function readShare() {
  try { const value = Number(localStorage.getItem(STORAGE_KEY)); return value >= 35 && value <= 55 ? value : 42; }
  catch { return 42; }
}

export function MeetingSplitPane({ children }: { children: ReactNode }) {
  const container = useRef<HTMLElement>(null);
  const [share, setShare] = useState(readShare);
  const update = (value: number) => {
    const next = Math.round(Math.max(35, Math.min(55, value)));
    setShare(next);
    try { localStorage.setItem(STORAGE_KEY, String(next)); } catch { /* Optional layout preference. */ }
  };
  return <main ref={container} className="meeting-grid meeting-split-pane" style={{ "--transcript-share": `${share}%` } as CSSProperties}>
    {children}
    <div className="meeting-column-divider" role="separator" tabIndex={0} aria-label="调整文字稿与教练宽度"
      aria-orientation="vertical" aria-valuemin={35} aria-valuemax={55} aria-valuenow={share} aria-valuetext={`文字稿 ${share}%，教练 ${100 - share}%`}
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={(event) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId) || !container.current) return;
        const rect = container.current.getBoundingClientRect();
        update((event.clientX - rect.left) / rect.width * 100);
      }}
      onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onDoubleClick={() => update(42)}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        update(event.key === "Home" ? 35 : event.key === "End" ? 55 : share + (event.key === "ArrowLeft" ? -2 : 2));
      }}><span aria-hidden="true" /></div>
  </main>;
}
