'use client';
import { PageShell } from './shell/PageShell';
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

const SEEN_KEY = 'adapt:seen-opening';
const IDX_KEY = 'adapt:phrase-idx';

function pickPhrase() {
  if (typeof window === 'undefined') return OPENING_PHRASES[0];
  const i = parseInt(localStorage.getItem(IDX_KEY) ?? '0', 10) || 0;
  localStorage.setItem(IDX_KEY, String((i + 1) % OPENING_PHRASES.length));
  return OPENING_PHRASES[i % OPENING_PHRASES.length];
}

export function Opening({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const allowedHere = pathname === "/" || pathname === "";
  const [phase, setPhase] = useState<"checking" | "showing" | "leaving" | "done">("checking");
  const phraseRef = useRef(OPENING_PHRASES[0]);
  const decision = useRef<"showing" | "done" | null>(null);
  const skipRef = useRef<HTMLButtonElement>(null);
  const dismiss = useCallback(() => {
    try { sessionStorage.setItem(SEEN_KEY, "1"); } catch { /* storage may be blocked */ }
    setPhase("leaving");
  }, []);

  useEffect(() => {
    if (!allowedHere) return;
    if (phase === "checking") {
      if (!decision.current) {
        try {
          decision.current = sessionStorage.getItem(SEEN_KEY) === "1" ? "done" : "showing";
          if (decision.current === "showing") phraseRef.current = pickPhrase();
        } catch {
          // Blocked storage must never trap someone behind a splash screen.
          decision.current = "done";
        }
      }
      setPhase(decision.current);
    } else if (phase === "showing") {
      skipRef.current?.focus();
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const timer = setTimeout(dismiss, reduced ? 900 : 4200);
      return () => clearTimeout(timer);
    } else if (phase === "leaving") {
      const timer = setTimeout(() => setPhase("done"), 500);
      return () => clearTimeout(timer);
    }
  }, [allowedHere, phase, dismiss]);

  // This cover is present in server HTML, not inserted after hydration. The shell and
  // its requests can mount underneath, but never flash or accept focus before the welcome.
  const covering = allowedHere && phase !== "done";
  const { text, blue } = phraseRef.current;
  const words = text.split(' ');

  return (
    <>
    <div data-app-shell hidden={covering && phase !== "leaving"} inert={covering || undefined}>{children}</div>
    {covering ? <div
      className={`opening-overlay${phase === "leaving" ? ' exiting' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label="Welcome"
      onClick={dismiss}
      onKeyDown={(e) => {
        if (e.key === "Tab") { e.preventDefault(); skipRef.current?.focus(); }
        else dismiss();
      }}
      tabIndex={-1}
    >
      <PageShell access><div className="welcome-sheet">
      <p className="opening-mark">A·D·A·P·T</p>
      {phase !== "checking" ? <p className="opening-phrase" aria-live="off" aria-label={text}>
        {/* One word per span, and the *space is the CSS* (`margin-right` on .opening-word).
            A trailing text space inside an `inline-block` span is trimmed by the line box, so
            "Let's learn the right way" rendered as "Let'slearntherightway". */}
        {words.map((w, i) => {
          const isBlue = w.replace(/[?.!,]/g, '') === blue || w.includes(blue);
          return (
            <span
              key={i}
              aria-hidden="true"
              className={`opening-word${isBlue ? ' blue' : ''}`}
              style={{ animationDelay: `${i * 0.14}s` }}
            >
              {w}
            </span>
          );
        })}
      </p> : <p role="status">Opening ADAPT…</p>}
      <button ref={skipRef} className="btn-ctl btn-ghost opening-skip" onClick={(e) => { e.stopPropagation(); dismiss(); }}>
        Skip →
      </button>
      <span className="sr-only">Press any key or click anywhere to continue.</span>
      </div></PageShell>
    </div> : null}
    </>
  );
}
