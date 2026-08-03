import colorMark from "../assets/talktrace-mark.svg";
import whiteMark from "../assets/talktrace-mark-white.svg";

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
