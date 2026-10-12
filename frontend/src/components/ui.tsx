"use client";

/** The shared furniture. Everything here is deliberately dull: the only thing on a
 *  study screen that should compete for attention is the question being answered. */
import Link from "next/link";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Loader = (path: string, init: { method: string; json: unknown }) => Promise<unknown>;

export function Card({ title, right, children, tone }: { title?: ReactNode; right?: ReactNode; children: ReactNode; tone?: "ask" | "good" | "warn" | "muted" }) {
  const edge =
    tone === "ask"
      ? "border-l-2 border-l-[var(--think)]"
      : tone === "good"
        ? "border-l-2 border-l-[var(--good)]"
        : tone === "warn"
          ? "border-l-2 border-l-[var(--warn)]"
          : tone === "muted"
            ? "border-l-2 border-l-[var(--line)]"
            : "";
  return (
    <section className={`rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 ${edge}`}>
      {(title || right) && (
        <header className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-[var(--muted)] uppercase">{title}</h2>
          <div className="text-xs text-[var(--muted)]">{right}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Btn({
  children,
  onClick,
  href,
  kind = "quiet",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  href?: string;
  kind?: "quiet" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const base =
    "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm border transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const look =
    kind === "primary"
      ? `${base} border-transparent bg-[var(--accent)] text-[#05202a] font-semibold hover:brightness-110`
      : kind === "danger"
        ? `${base} border-[var(--bad)]/40 text-[var(--bad)] hover:bg-[var(--bad)]/10`
        : kind === "ghost"
          ? `${base} border-transparent text-[var(--muted)] hover:text-[var(--ink)]`
          : `${base} border-[var(--line)] hover:border-[var(--accent)]/60`;
  if (href)
    return (
      <Link href={href} className={look} title={title}>
        {children}
      </Link>
    );
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={look} title={title}>
      {children}
    </button>
  );
}

export function Tag({ children, tone = "line" }: { children: ReactNode; tone?: "line" | "good" | "warn" | "bad" | "think" | "accent" }) {
  const map: Record<string, string> = {
    line: "border-[var(--line)] text-[var(--muted)]",
    good: "border-[var(--good)]/50 text-[var(--good)]",
    warn: "border-[var(--warn)]/50 text-[var(--warn)]",
    bad: "border-[var(--bad)]/50 text-[var(--bad)]",
    think: "border-[var(--think)]/50 text-[var(--think)]",
    accent: "border-[var(--accent)]/50 text-[var(--accent)]",
  };
  return <span className={`rounded-full border px-2 py-0.5 text-[0.7rem] ${map[tone]}`}>{children}</span>;
}

/** The band word the backend chose, with a bar. No percentage is shown as a fact: the
 *  number is a model's estimate, and the CI is next to it whenever there is one. */
export function Band({ band, pKnow, ci }: { band: string; pKnow?: number; ci?: [number, number] }) {
  const tone = band === "mastered" || band === "secure" ? "good" : band === "new" || band === "gapped" ? "bad" : band === "developing" ? "accent" : "warn";
  const width = Math.max(2, Math.min(100, Math.round((pKnow ?? 0) * 100)));
  return (
    <span className="inline-flex items-center gap-2">
      <Tag tone={tone as "good"}>{band}</Tag>
      {pKnow !== undefined && (
        <span className="inline-flex items-center gap-1 text-xs text-[var(--muted)]">
          <span className="h-1.5 w-16 overflow-hidden rounded bg-[var(--line)]">
            <span className="block h-full bg-[var(--accent)]" style={{ width: `${width}%` }} />
          </span>
          {pKnow.toFixed(2)}
          {ci ? (
            <span title="95% interval - it stays wide until there are more observations">
              [{ci[0].toFixed(2)}, {ci[1].toFixed(2)}]
            </span>
          ) : null}
        </span>
      )}
    </span>
  );
}

export function Notice({ children, tone = "warn" }: { children: ReactNode; tone?: "warn" | "bad" | "good" | "info" }) {
  const map: Record<string, string> = {
    warn: "border-[var(--warn)]/40 bg-[var(--warn)]/5",
    bad: "border-[var(--bad)]/40 bg-[var(--bad)]/5",
    good: "border-[var(--good)]/40 bg-[var(--good)]/5",
    info: "border-[var(--line)] bg-black/20",
  };
  return <p className={`rounded-md border px-3 py-2 text-sm ${map[tone]}`}>{children}</p>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm text-[var(--ink)]">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-[var(--muted)]">{hint}</span>}
    </label>
  );
}

const inputLook =
  "w-full rounded-md border border-[var(--line)] bg-black/25 px-3 py-2 text-sm text-[var(--ink)] placeholder:text-[var(--muted)]/60 focus:border-[var(--accent)]";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputLook} ${props.className ?? ""}`} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputLook} min-h-24 ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputLook} ${props.className ?? ""}`} />;
}

export function Check({ label, checked, onChange, hint }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; hint?: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 accent-[var(--accent)]" />
      <span>
        {label}
        {hint && <span className="mt-0.5 block text-xs text-[var(--muted)]">{hint}</span>}
      </span>
    </label>
  );
}

/** For the "why this question / why this change" disclosures. The backend returns a
 *  trace; showing it collapsed keeps it available without making every screen a dump. */
export function Trace({ title = "why", children }: { title?: string; children: ReactNode }) {
  return (
    <details className="group text-xs text-[var(--muted)]">
      <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-4 group-open:hidden">{title}</summary>
      <div className="mt-2 space-y-1 rounded-md border border-[var(--line)] bg-black/20 p-3">{children}</div>
      <button
        type="button"
        className="mt-1 underline decoration-dotted underline-offset-4 group-open:hidden"
        onClick={(e) => {
          const d = (e.target as HTMLElement).closest("details");
          if (d) d.removeAttribute("open");
        }}
      >
        hide
      </button>
    </details>
  );
}

export function When({ ts }: { ts?: string | null }) {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return <span className="text-xs text-[var(--muted)]">{ts}</span>;
  return (
    <span className="text-xs text-[var(--muted)]" title={d.toLocaleString()}>
      {d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}{" "}
      {d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export function Loading({ label = "loading" }: { label?: string }) {
  return <p className="text-sm text-[var(--muted)]">{label}…</p>;
}

export function Err({ error, status }: { error: string | null; status?: number | null }) {
  if (!error) return null;
  return (
    <Notice tone={status === 401 ? "info" : "bad"}>
      {status === 401 ? (
        <>
          Not signed in. <Link className="underline" href="/">Sign in or start as a guest</Link> and reload.
        </>
      ) : status === 429 ? (
        <>
          The server is asking for a moment between requests, so this screen could not load. Nothing is wrong with
          your session - wait a few seconds and try again.
        </>
      ) : status === 0 ? (
        <>
          {error} — start the backend with{" "}
          <code className="text-xs">uvicorn adapt.api.app:app --port 8000</code>, or open this page with{" "}
          <code className="text-xs">?mock=1</code> to look at the design without it.
        </>
      ) : (
        <>
          {error}
          {status ? ` (${status})` : ""}
        </>
      )}
    </Notice>
  );
}

/* ------------------------------------------------------------------ */
/* objects must not reach a learner as JSON                             */
/* ------------------------------------------------------------------ */

const WORDS_FIRST = ["verdict_words", "explanation", "summary", "note", "message", "detail", "text", "label", "why", "what"];

/** Renders whatever a screen has been handed as a sentence.
 *
 *  Several endpoints return a structured finding (a measured effect, a privacy statement, a
 *  voice capability list) and screens used to `JSON.stringify` them, so the learner read
 *  `{"baseline_mean":0.12,...}` where an answer to "did it help?" should have been. Sentences
 *  win when the payload has one; otherwise the pairs are labelled, quoted-free and
 *  brace-free. Numbers keep two decimals at most.
 */
export function humanize(value: unknown, depth = 0): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(Math.round(value * 100) / 100);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) {
    const parts = value.map((v) => humanize(v, depth + 1)).filter(Boolean);
    // a list of findings is a list of sentences, not a comma soup
    return parts.length > 1 && parts.some((p) => p.length > 60) ? parts.join(" ") : parts.join(", ");
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of WORDS_FIRST) {
      const v = obj[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    const parts: string[] = [];
    for (const [k, v] of Object.entries(obj)) {
      if (v === null || v === undefined || v === "") continue;
      const label = k.replace(/_/g, " ");
      if (typeof v === "object") {
        if (depth >= 2) continue;
        const inner = humanize(v, depth + 1);
        if (inner) parts.push(`${label}: ${inner}`);
        continue;
      }
      parts.push(`${label}: ${humanize(v, depth + 1)}`);
    }
    return parts.join(" \u00b7 ");
  }
  return String(value);
}

/* ------------------------------------------------------------------ */
/* session + status, kept in one place so every page agrees            */
/* ------------------------------------------------------------------ */

type SessionState = { me: Record<string, unknown> | null; loading: boolean; unknown?: string | null; refresh: () => void; signOut: () => Promise<void> };
const SessionCtx = createContext<SessionState>({ me: null, loading: true, unknown: null, refresh: () => {}, signOut: async () => {} });
export const useSession = () => useContext(SessionCtx);
export const SessionProvider = SessionCtx.Provider;

/* ------------------------------------------------------------------ */
/* telemetry: the learner-side event log                                */
/* ------------------------------------------------------------------ */

/** Events are queued and flushed in small batches. A learner should never wait on an
 *  analytics call, and a closed tab should not lose the last question: the queue is
 *  flushed on `pagehide` with `keepalive` where the browser allows it. */
export class EventQueue {
  private queue: Record<string, unknown>[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushPromise: Promise<void> = Promise.resolve();
  constructor(
    private sessionId: string | null,
    private onDropped?: (n: number) => void,
  ) {}

  /** Best-effort send when the tab is going away: `navigator.sendBeacon` cannot set a
   *  header, so the CSRF token travels as the documented `?csrf=` fallback. */
  flushBeacon() {
    if (!this.sessionId || !this.queue.length) return;
    const batch = this.queue.splice(0, 100);
    const body = JSON.stringify({ session_id: this.sessionId, events: batch });
    try {
      void import("@/lib/api").then(({ csrfToken }) => {
        const token = csrfToken();
        const url = `/api/v1/sessions/${this.sessionId}/events${token ? `?csrf=${encodeURIComponent(token)}` : ""}`;
        if (navigator.sendBeacon) {
          navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        } else {
          void fetch(url, { method: "POST", body, headers: { "content-type": "application/json" }, credentials: "same-origin", keepalive: true }).catch(() =>
            this.onDropped?.(batch.length),
          );
        }
      });
    } catch {
      this.onDropped?.(batch.length);
    }
  }

  setSession(id: string | null) {
    this.sessionId = id;
  }

  push(type: string, payload: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
    if (!this.sessionId) return;
    this.queue.push({ type, payload, ...extra });
    if (this.queue.length >= 12) void this.flush();
    else if (!this.timer) this.timer = setTimeout(() => void this.flush(), 2500);
  }

  async flush() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (!this.sessionId || !this.queue.length) return;
    const batch = this.queue.splice(0, 100);
    const path = `sessions/${this.sessionId}/events`;
    const body = { session_id: this.sessionId, events: batch };
    // Serialise the writes: seq numbers are assigned in order by the server, and two
    // in-flight batches arriving out of order would scramble the timeline.
    this.flushPromise = this.flushPromise.then(async () => {
      try {
        const { api } = await import("@/lib/api");
        await api(`/${path}`, { method: "POST", json: body });
      } catch {
        this.onDropped?.(batch.length);
      }
    });
    return this.flushPromise;
  }
}

export function useEventQueue(sessionId: string | null) {
  const [dropped, setDropped] = useState(0);
  const [q] = useState(() => new EventQueue(sessionId, (n) => setDropped((d) => d + n)));
  useEffect(() => {
    q.setSession(sessionId);
    const onHide = () => void q.flushBeacon();
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      void q.flush();
    };
  }, [q, sessionId]);
  return { q, dropped };
}
