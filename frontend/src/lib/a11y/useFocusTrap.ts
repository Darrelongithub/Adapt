import { useEffect, useRef } from "react";

export function useFocusTrap(active: boolean) {
  const containerRef = useRef<HTMLDivElement>(null);
  const lastFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;
    lastFocused.current = document.activeElement as HTMLElement;
    const container = containerRef.current;
    const focusables = () =>
      Array.from(
        container?.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter(element => !element.matches(":disabled") && element.tabIndex >= 0);
    focusables()[0]?.focus();

    function onKeyDown(e: KeyboardEvent) {
      // The welcome cover may temporarily hide a mounted account gate. Only the
      // foreground dialog may handle focus while it is visible.
      if (container?.closest("[hidden], [inert]")) return;
      if (e.key === "Escape") {
        lastFocused.current?.focus();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0],
        last = items[items.length - 1];
      if (!container?.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      lastFocused.current?.focus();
    };
  }, [active]);

  return containerRef;
}
