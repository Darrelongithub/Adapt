"use client";

/** The shared furniture. Everything here is deliberately dull: the only thing on a
 *  study screen that should compete for attention is the question being answered. */
import Link from "next/link";
import { Icon, type IconName } from "./icons";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { pushEntry, readLedger, writeLedger } from "@/lib/ledger";

type Loader = (path: string, init: { method: string; json: unknown }) => Promise<unknown>;

export function Card({
  title,
  sub,
  right,
  children,
  tone,
  icon,
  id,
  "data-empty": dataEmpty,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  tone?: "blue" | "sand" | "mint" | "peach" | "plain" | "ask" | "good" | "warn" | "muted";
  icon?: ReactNode;
  id?: string;
  "data-empty"?: string;
}) {
  // The one Card component (Round 6, Track A). Tones are tinted backgrounds from
  // tokens.css. The four semantic names map onto tints so older call sites keep
  // their meaning without keeping a second card style.
  const tint = tone === "ask" ? "blue" : tone === "good" ? "mint" : tone === "warn" ? "peach" : tone === "muted" ? "plain" : tone;
  return (
    <section className="card" id={id} data-tone={tint ?? undefined} data-empty={dataEmpty}>
      {(title || right || sub) && (
        <header className="card-head">
          <div className="min-w-0">
            {title ? (
              <h2 className="card-title">
                {icon ? <span className="icon-tile" aria-hidden="true">{icon}</span> : null}
                {title}
              </h2>
            ) : null}
            {sub ? <p className="card-sub t-small" style={{ marginTop: "4px", color: "var(--muted)" }}>{sub}</p> : null}
          </div>
          {right ? <div className="card-right">{right}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

/**
 * The one page header (Round 6, Track A): serif title, one subtitle line, optional
 * art on the right. Every page uses this instead of an ad-hoc header block.
 */
export function PageHeader({
  title,
  subtitle,
  art,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  art?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        <h1 className="t-h1 page-title">{title}</h1>
        {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
        {actions ? <div className="wrap-2" style={{ marginTop: "var(--s3)" }}>{actions}</div> : null}
      </div>
      {art ? <div className="page-header-art" aria-hidden="true">{art}</div> : null}
    </header>
  );
}

/** A rounded tinted square behind an icon. One icon tile, used everywhere. */
export function IconTile({ icon, tone = "blue", size }: { icon: ReactNode; tone?: "blue" | "sand" | "mint" | "peach" | "think"; size?: number }) {
  return (
    <span className="icon-tile" data-tone={tone} aria-hidden="true" style={size ? { width: size, height: size } : undefined}>
      {icon}
    </span>
  );
}

/** Every control that does something goes through here, so it wears one of the three
 *  contract looks from `globals.css` (brief 2.2). The `kind` names are the ones call
 *  sites already pass; what each one *looks* like is the contract's business, not the
 *  page's. `danger` is a secondary button with the refusal colour, because a refusal
 *  is a decision a learner makes, not a destructive action they can slip into.
 *
 *  `pressed` draws the selected state (`aria-pressed`) — that is for a control whose
 *  job is to record which answer was given, e.g. a decision on /proposals. */
export function Btn({
  children,
  onClick,
  href,
  kind = "quiet",
  disabled,
  type = "button",
  title,
  pressed,
  ariaLabel,
  icon,
}: {
  children: ReactNode;
  onClick?: () => void;
  href?: string;
  kind?: "quiet" | "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
  pressed?: boolean;
  ariaLabel?: string;
  icon?: IconName;
}) {
  // `btn` + `btn-ctl`: the design-system button and the marker the coarse-pointer
  // tap-target rules in globals.css key off. The look is the tokens.css contract's.
  const look =
    kind === "primary"
      ? "btn btn-ctl btn-primary"
      : kind === "danger"
        ? "btn btn-ctl btn-danger"
        : kind === "ghost"
          ? "btn btn-ctl btn-ghost"
          : "btn btn-ctl btn-secondary"; // secondary + quiet share the white/blue look
  const pressedAttr = pressed === undefined ? {} : { "aria-pressed": pressed };
  if (href)
    return (
      <Link
        href={href}
        className={look}
        title={title}
        aria-label={ariaLabel}
        aria-disabled={disabled ? "true" : undefined}
        {...pressedAttr}
      >
        {icon ? <Icon name={icon} /> : null}{children}
      </Link>
    );
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={look} title={title} aria-label={ariaLabel} {...pressedAttr}>
      {icon ? <Icon name={icon} /> : null}{children}
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
  // Tiny status chips are the one allowed uppercase-mono label (tokens.css .status-chip).
  return <span className={`status-chip ${map[tone]}`}>{children}</span>;
}

/** The pill chip from the design system: sentence case, 14px, medium weight. */
export function Chip({ children, tone, selected, onClick }: { children: ReactNode; tone?: "accent" | "good" | "warn" | "bad"; selected?: boolean; onClick?: () => void }) {
  if (onClick) {
    return (
      <button type="button" className="tap-chip" data-tone={tone} aria-pressed={selected ?? false} onClick={onClick}>
        {children}
      </button>
    );
  }
  return <span className="chip" data-tone={tone}>{children}</span>;
}

/** The band word the backend chose, with a bar. No percentage is shown as a fact: the
 *  number is a model's estimate, and the CI is next to it whenever there is one. */
export function Band({
  label,
  band,
  interval,
  value,
  n,
  pKnow,
  ci,
}: {
  label?: string;
  band: string;
  interval?: [number, number];
  value?: number;
  n?: number;
  pKnow?: number;
  ci?: [number, number];
}) {
  // Backwards compat: pKnow -> value, ci -> interval
  const resolvedValue = value ?? pKnow;
  const resolvedInterval = interval ?? ci;
  const resolvedN = n;
  const hasRange = Array.isArray(resolvedInterval) && resolvedInterval.length === 2;
  const hasPoint = typeof resolvedValue === "number";
  const hasN = typeof resolvedN === "number";
  const tone = band === "mastered" || band === "secure" ? "good" : band === "new" || band === "gapped" ? "bad" : band === "developing" ? "accent" : "warn";
  // If we have range/point we render the new track visualization, otherwise fallback to old compact Tag+bar for call sites that only provide band.
  if (hasRange || hasPoint) {
    const displayLabel = label ?? band;
    return (
      <div className="band-row" role="group" aria-label={`${displayLabel}: ${band} band${hasPoint ? `, estimate ${(resolvedValue! * 100).toFixed(0)} percent` : ""}`}>
        {label ? <span className="band-label">{label}</span> : null}
        <span className="band-track" aria-hidden="true">
          {hasRange && (
            <span
              className="band-fill"
              style={{ left: `${resolvedInterval![0] * 100}%`, width: `${(resolvedInterval![1] - resolvedInterval![0]) * 100}%` }}
            />
          )}
          {hasPoint && <span className="band-point" style={{ left: `${resolvedValue! * 100}%` }} />}
        </span>
        <span className="band-val">
          band <b>{band}</b>
          {hasN && <span className="band-n"> · n={resolvedN}</span>}
          {hasPoint && !hasRange ? <span className="ml-1 text-[var(--muted)]">{resolvedValue!.toFixed(2)}</span> : null}
          {hasRange ? <span className="ml-1 text-[var(--muted)]">[{resolvedInterval![0].toFixed(2)}, {resolvedInterval![1].toFixed(2)}]</span> : null}
        </span>
      </div>
    );
  }
  // Fallback compact rendering for call sites that only pass band (or band+pKnow without interval)
  const width = Math.max(2, Math.min(100, Math.round((resolvedValue ?? 0) * 100)));
  const fallbackHasPoint = typeof resolvedValue === "number";
  const fallbackHasRange = Array.isArray(resolvedInterval) && resolvedInterval.length === 2;
  return (
    <span className="inline-flex items-center gap-2">
      <Tag tone={tone as "good"}>{band}</Tag>
      {fallbackHasPoint && (
        <span className="inline-flex items-center gap-1 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
          <span className="h-1.5 w-16 overflow-hidden rounded-[2px] bg-[var(--panel-2)] border border-[var(--line)]">
            <span className="block h-full bg-[var(--accent)]" style={{ width: `${width}%` }} />
          </span>
          {(resolvedValue as number).toFixed(2)}
          {fallbackHasRange ? (
            <span title="95% interval - it stays wide until there are more observations">
              [{(resolvedInterval as [number, number])[0].toFixed(2)}, {(resolvedInterval as [number, number])[1].toFixed(2)}]
            </span>
          ) : null}
          {hasN ? <span>· n={resolvedN}</span> : null}
        </span>
      )}
    </span>
  );
}

export function Notice({ children, tone = "warn" }: { children: ReactNode; tone?: "warn" | "bad" | "good" | "info" }) {
  const map: Record<string, string> = {
    warn: "border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[color-mix(in_srgb,var(--warn)_6%,white)]",
    bad: "border-[color-mix(in_srgb,var(--bad)_40%,transparent)] bg-[color-mix(in_srgb,var(--bad)_6%,white)]",
    good: "border-[color-mix(in_srgb,var(--good)_40%,transparent)] bg-[color-mix(in_srgb,var(--good)_6%,white)]",
    info: "border-[var(--line)] bg-[var(--panel-2)]",
  };
  return <p className={`rounded-[12px] border px-4 py-3 ${map[tone]}`} style={{ fontSize: "var(--t-body)", lineHeight: "var(--t-body-lh)", color: "var(--body)" }}>{children}</p>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block t-label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-[var(--muted)]">{hint}</span>}
    </label>
  );
}

const inputLook = "input";

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
      <div className="mt-2 space-y-1 rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] p-3">{children}</div>
      <button
        type="button"
        className="btn-ctl btn-ghost"
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
  if (Number.isNaN(d.getTime())) return <span className="text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>{ts}</span>;
  return (
    <span className="text-xs text-[var(--muted)]" title={d.toLocaleString()} style={{ fontFamily: "var(--font-mono)" }}>
      {d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}{" "}
      {d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
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
          your session. Wait a few seconds and try again.
        </>
      ) : status === 0 ? (
        <>
          {error}. Nothing you did caused this and nothing has been lost — your answers are saved on the server, and
          this screen will load again as soon as it is back. (If you are running ADAPT yourself, the “Run it” section
          of `README.md` has the start-up command.)
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

export function Receipt({ tone = "good", children }: { tone?: "good" | "bad" | "neutral"; children: ReactNode }) {
  return (
    <div className={`receipt receipt-${tone}`} role="status">
      {children}
    </div>
  );
}

export function Ledger({ children }: { children: ReactNode }) {
  return <div className="ledger">{children}</div>;
}

export function LedgerEntry({
  time,
  title,
  children,
  actions,
}: {
  time: ReactNode;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <article className="ledger-entry">
      <div className="ledger-time">{time}</div>
      <div className="ledger-main">
        <h3 className="ledger-title">{title}</h3>
        <div className="ledger-evidence">{children}</div>
        {actions && <div className="ledger-actions">{actions}</div>}
      </div>
    </article>
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

export function announce(msg: string) {
  if (typeof document === "undefined") return;
  const el = document.getElementById("adapt-live-region");
  if (el) el.textContent = msg;
}

/** What this browser decided or rated, newest first, surviving a navigation.
 *  See `lib/ledger.ts` for why it is storage and not state: the heading says *this session*. */
export function useSessionLedger(key: string) {
  const [entries, setEntries] = useState<string[]>([]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    setEntries(readLedger(window.sessionStorage, key));
  }, [key]);
  const add = useCallback(
    (entry: string) => {
      setEntries((prev) => {
        const next = pushEntry(prev, entry);
        if (typeof window !== "undefined") writeLedger(window.sessionStorage, key, next);
        return next;
      });
    },
    [key],
  );
  return { entries, add };
}

/** The session log itself. An announcement is said once and is gone; this stays on screen.
 *  Not called `Ledger`: that name is the proposal cards above it, which is a different thing. */
export function SessionLog({ title, entries, empty }: { title: string; entries: readonly string[]; empty: string }) {
  return (
    <div className="ledger-log">
      <h5>{title}</h5>
      <ul>
        {entries.length ? (
          entries.map((entry, i) => <li key={`${i}-${entry}`}>{entry}</li>)
        ) : (
          <li className="empty">{empty}</li>
        )}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* session + status, kept in one place so every page agrees            */
/* ------------------------------------------------------------------ */

type SessionState = { me: Record<string, unknown> | null; loading: boolean; unknown?: string | null; refresh: () => void; establish: (me: Record<string, unknown>) => void; signOut: () => Promise<void> };
const SessionCtx = createContext<SessionState>({ me: null, loading: true, unknown: null, refresh: () => {}, establish: () => {}, signOut: async () => {} });
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
