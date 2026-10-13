'use client';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

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

export function Opening() {
  // SSR-safe: never show the opening during server render or hydration mismatch risk —
  // default to "already seen" until we've checked sessionStorage on the client.
  const [mounted, setMounted] = useState(false);
  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);
  const phraseRef = useRef(OPENING_PHRASES[0]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Where the overlay is allowed to appear. It is a full-screen layer (`position: fixed;
  // inset: 0; z-index: 200`), so anywhere it renders, it is *in front of* the page's own
  // controls. On a question screen that meant a learner's first click on a choice was
  // received by the overlay ("click anywhere to continue") and never reached the option:
  // the answer looked selected-by-intent but the box stayed empty and the submit control
  // stayed closed. The welcome only belongs on the front door, where there is nothing to
  // swallow but the door itself.
  const pathname = usePathname();
  const allowedHere = pathname === "/" || pathname === "";

  useEffect(() => {
    setMounted(true);
    if (!allowedHere) return;
    let seen = false;
    try { seen = sessionStorage.getItem(SEEN_KEY) === '1'; } catch { /* storage blocked — fail open, don't show */ seen = true; }
    if (!seen) {
      phraseRef.current = pickPhrase();
      setVisible(true);
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      // Long enough for the last word to arrive and then be read: the reveal now takes
      // ``stagger * (words - 1) + 1.15s`` and the longest phrase is seven words, so 4.2s leaves
      // about a second and a half of settled text. Reduced motion keeps a short hold, because
      // there is no reveal to wait for.
      timerRef.current = setTimeout(dismiss, reduced ? 900 : 4200);
    }
    return () => clearTimeout(timerRef.current);
  }, [allowedHere]);

  function dismiss() {
    clearTimeout(timerRef.current);
    try { sessionStorage.setItem(SEEN_KEY, '1'); } catch { /* ignore */ }
    setExiting(true);
    // Matches the overlay's own `transition: opacity`; a shorter timer would remove the node
    // mid-fade and the exit would read as a blink.
    setTimeout(() => setVisible(false), 500);
  }

  if (!mounted || !visible || !allowedHere) return null;

  const { text, blue } = phraseRef.current;
  const words = text.split(' ');

  return (
    <div
      className={`opening-overlay${exiting ? ' exiting' : ''}`}
      role="dialog"
      aria-label="Welcome"
      onClick={dismiss}
      onKeyDown={dismiss}
      tabIndex={-1}
    >
      <p className="opening-mark">A·D·A·P·T</p>
      <p className="opening-phrase" aria-live="off" aria-label={text}>
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
      </p>
      <button className="btn-ctl btn-ghost opening-skip" onClick={(e) => { e.stopPropagation(); dismiss(); }}>
        Skip →
      </button>
      <span className="sr-only">Press any key or click anywhere to continue.</span>
    </div>
  );
}
