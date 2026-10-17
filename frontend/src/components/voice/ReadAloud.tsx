"use client";

/** Read-aloud for one piece of text. Silent when voice is off — the button says so. */
import { useState } from "react";
import { useVoice } from "./context";

export function ReadAloud({ text, label = "Read aloud" }: { text: string; label?: string }) {
  const { enabled, speaking, say } = useVoice();
  const [hint, setHint] = useState(false);
  const clean = text.trim();
  if (!clean) return null;
  return (
    <span className="read-aloud">
      <button
        type="button"
        className="btn-ctl btn-secondary"
        aria-pressed={speaking}
        title={enabled ? "Read this out loud" : "Voice is off — turn it on in the sidebar"}
        onClick={() => {
          if (enabled) {
            setHint(false);
            void say(clean);
          } else {
            setHint(true);
          }
        }}
      >
        {speaking ? "Reading…" : label}
      </button>
      {hint && !enabled ? (
        <span className="t-small text-[var(--muted)]">voice is off — nothing plays until you turn it on</span>
      ) : null}
    </span>
  );
}
