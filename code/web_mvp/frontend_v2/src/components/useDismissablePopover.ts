import { useEffect, useRef } from "react";

export function useDismissablePopover<
  TriggerElement extends HTMLElement,
  PopoverElement extends HTMLElement,
>(open: boolean, onClose: () => void) {
  const triggerRef = useRef<TriggerElement>(null);
  const popoverRef = useRef<PopoverElement>(null);

  useEffect(() => {
    if (!open) return undefined;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      onClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  return { triggerRef, popoverRef };
}
