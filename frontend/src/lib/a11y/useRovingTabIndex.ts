import { useRef, useState, useCallback } from "react";

export function useRovingTabIndex(count: number, orientation: "horizontal" | "vertical" = "vertical") {
  const [activeIndex, setActiveIndex] = useState(0);
  const refs = useRef<(HTMLElement | null)[]>([]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent, i: number) => {
      const next =
        orientation === "vertical"
          ? e.key === "ArrowDown"
            ? i + 1
            : e.key === "ArrowUp"
              ? i - 1
              : null
          : e.key === "ArrowRight"
            ? i + 1
            : e.key === "ArrowLeft"
              ? i - 1
              : null;
      if (next === null) return;
      e.preventDefault();
      const wrapped = (next + count) % count;
      setActiveIndex(wrapped);
      refs.current[wrapped]?.focus();
    },
    [count, orientation],
  );

  return {
    activeIndex,
    setActiveIndex,
    getTabIndex: (i: number) => (i === activeIndex ? 0 : -1),
    registerRef: (i: number) => (el: HTMLElement | null) => {
      refs.current[i] = el;
    },
    onKeyDown,
  };
}
