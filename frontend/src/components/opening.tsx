'use client';
import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

// Exported so this is trivial to extend later — exact wording and blue word preserved
// from product requirements; do not reorder or reword without explicit approval.
export const OPENING_PHRASES: { text: string; blue: string }[] = [
  { text: "Are you ready to ADAPT?", blue: "ADAPT" },
  { text: "Let's learn the right way.", blue: "right" },
  { text: "Your next breakthrough starts here.", blue: "breakthrough" },
  { text: "Time to learn differently.", blue: "differently" },
  { text: "Let's ADAPT.", blue: "ADAPT" },
  { text: "What you know is just the beginning.", blue: "beginning" },
  { text: "Built around how you learn.", blue: "learn" },
  { text: "Ready to learn? The ADAPT way.", blue: "ADAPT" },
];

const IDX_KEY = 'adapt:phrase-idx';

function pickPhrase() {
  if (typeof window === 'undefined') return OPENING_PHRASES[0];
  try {
    const i = parseInt(localStorage.getItem(IDX_KEY) ?? '0', 10) || 0;
    localStorage.setItem(IDX_KEY, String((i + 1) % OPENING_PHRASES.length));
    return OPENING_PHRASES[i % OPENING_PHRASES.length];
  } catch {
    return OPENING_PHRASES[0];
  }
}

export function Opening({ children }: { children?: ReactNode }) {
  const pathname = usePathname();
  const allowedHere = pathname === "/" || pathname === "";
  const [phase, setPhase] = useState<"checking" | "showing" | "leaving" | "done">("checking");
  const [phrase, setPhrase] = useState<{ text: string; blue: string } | null>(null);
  const initialized = useRef(false);

  const dismiss = useCallback(() => {
    setPhase((prev) => (prev === "done" || prev === "leaving" ? prev : "leaving"));
  }, []);

  useEffect(() => {
    if (!allowedHere) {
      setPhase("done");
      return;
    }
    if (phase === "checking") {
      if (!initialized.current) {
        initialized.current = true;
        setPhrase(pickPhrase());
      }
      setPhase("showing");
    } else if (phase === "showing") {
      const timer = setTimeout(dismiss, 10000);
      return () => clearTimeout(timer);
    } else if (phase === "leaving") {
      const timer = setTimeout(() => setPhase("done"), 500);
      return () => clearTimeout(timer);
    }
  }, [allowedHere, phase, dismiss]);

  const covering = allowedHere && phase !== "done";
  const words = phrase ? phrase.text.split(' ') : [];

  return (
    <>
      <div data-app-shell hidden={covering && phase !== "leaving"} inert={covering || undefined}>{children}</div>
      {covering ? (
        <div
          className={`opening-overlay${phase === "leaving" ? ' exiting' : ''}`}
          role="status"
          aria-live="polite"
          aria-label="Loading ADAPT"
        >
          {phrase ? (
            <p className="opening-phrase" aria-label={phrase.text}>
              {words.map((w, i) => {
                const isBlue = w.replace(/[?.!,]/g, '') === phrase.blue || w.includes(phrase.blue);
                return (
                  <span
                    key={`${phrase.text}-${i}`}
                    aria-hidden="true"
                    className={`opening-word${isBlue ? ' blue' : ''}`}
                    style={{ animationDelay: `${i * 0.14}s` }}
                  >
                    {w}
                  </span>
                );
              })}
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
