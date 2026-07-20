import colorMark from "../assets/meeting-copilot-mark.png";
import whiteMark from "../assets/meeting-copilot-mark-white.png";

interface BrandMarkProps {
  tone?: "color" | "white";
  size?: "navigation" | "header" | "start";
}

export function BrandMark({ tone = "color", size = "header" }: BrandMarkProps) {
  return (
    <span className={`brand-mark brand-mark--${size}`} aria-hidden="true">
      <img src={tone === "white" ? whiteMark : colorMark} alt="" />
    </span>
  );
}
