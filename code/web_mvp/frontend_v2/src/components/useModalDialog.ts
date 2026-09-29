import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function useModalDialog(
  open: boolean,
  onClose: () => void,
  closeDisabled = false,
) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);
  onCloseRef.current = onClose;
  closeDisabledRef.current = closeDisabled;

  useEffect(() => {
    if (!open) return undefined;
    const returnFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const dialog = dialogRef.current;
    const focusable = () => dialog
      ? [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)]
        .filter((element) => element.tabIndex >= 0 && !element.hidden && element.getAttribute("aria-hidden") !== "true")
      : [];
    window.requestAnimationFrame(() => {
      const candidates = focusable();
      const preferred = dialog?.querySelector<HTMLElement>("[data-dialog-initial-focus]");
      (preferred && !preferred.hasAttribute("disabled") ? preferred : candidates[0] ?? dialog)?.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !closeDisabledRef.current) {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const candidates = focusable();
      if (!candidates.length) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = candidates[0];
      const last = candidates[candidates.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.requestAnimationFrame(() => returnFocus?.focus());
    };
  }, [open]);

  return dialogRef;
}
