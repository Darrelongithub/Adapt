'use client';
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

  useEffect(() => {
    setMounted(true);
    let seen = false;
    try { seen = sessionStorage.getItem(SEEN_KEY) === '1'; } catch { /* storage blocked — fail open, don't show */ seen = true; }
    if (!seen) {
      phraseRef.current = pickPhrase();
      setVisible(true);
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      timerRef.current = setTimeout(dismiss, reduced ? 500 : 1500);
    }
    return () => clearTimeout(timerRef.current);
  }, []);

  function dismiss() {
    clearTimeout(timerRef.current);
    try { sessionStorage.setItem(SEEN_KEY, '1'); } catch { /* ignore */ }
    setExiting(true);
    setTimeout(() => setVisible(false), 350);
  }

  if (!mounted || !visible) return null;

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
      <p className="opening-phrase" aria-live="off">
        {words.map((w, i) => {
          const isBlue = w.replace(/[?.!,]/g, '') === blue || w.includes(blue);
          return (
            <span
              key={i}
              className={`opening-word${isBlue ? ' blue' : ''}`}
              style={{ animationDelay: `${i * 0.08}s` }}
            >
              {w}{' '}
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
