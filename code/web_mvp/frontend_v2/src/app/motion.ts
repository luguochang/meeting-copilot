export function motionAwareScrollBehavior(): ScrollBehavior {
  if (typeof window.matchMedia !== "function") return "smooth";
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
