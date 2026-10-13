"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Err, Notice, SessionProvider, useSession } from "./ui";
import { csrfToken, get, isMock, logout, post, type Me } from "@/lib/api";
import type { Health, Status } from "@/lib/types";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";

export const NAV = [
  { href: "/learn", label: "Practise" },
  { href: "/lesson", label: "Lessons" },
  { href: "/proposals", label: "Changes" },
  { href: "/review", label: "Review" },
  { href: "/progress", label: "Progress" },
  { href: "/voice", label: "Voice" },
  { href: "/settings", label: "Settings" },
];

/** The corner tab says which section you are in, as the design snapshot does: `§ 03 · Changes`
 *  on the changes screen rather than the brand on every screen. One source of truth — the numbers
 *  come from NAV's own order, so a section cannot be numbered here and named differently there.
 *  A route with no section (onboarding, placement) keeps the brand rather than inventing one. */
export const SECTION_LABELS: Record<string, string> = {
  "/": "§ ADAPT",
  ...Object.fromEntries(NAV.map((n, i) => [n.href, `§ ${String(i + 1).padStart(2, "0")} · ${n.label}`])),
};

/** Red bar, every screen, whenever the data on it is not real. */
export function MockBanner() {
  const [mock, setMock] = useState(false);
  useEffect(() => setMock(isMock()), []);
  if (!mock) return null;
  return (
    // pl-28 clears the fixed § ADAPT index tab at the top-left corner, which used to sit on
    // top of the banner's first line on phones.
    <div className="border-b border-[var(--bad)]/50 bg-[var(--bad)]/15 py-2 pl-28 pr-4 text-center text-xs text-[var(--bad)]" style={{ fontFamily: "var(--font-mono)" }}>
      <strong>DEMO DATA</strong> — this screen is running on fixtures (<code>?mock=1</code>). Nothing here was measured from a
      learner, and nothing was saved.{" "}
      <Link className="underline" href="/?mock=off">
        turn it off
      </Link>
    </div>
  );
}

/** What the app is actually running on. Two AI roles, an encryption driver and a
 *  network reach are all optional, so the UI says which ones are live instead of
 *  assuming the configured-by-default story. */
export function StatusChip() {
  const [health, setHealth] = useState<Health | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      const h = await get<Health>("health");
      if (!alive) return;
      setHealth(h.data);
      setError(h.error);
      const s = await get<Status>("status");
      if (alive) setStatus(s.data);
    })();
    return () => {
      alive = false;
    };
  }, []);
  const llm = status ? (status.llm?.configured ? `model ${status.llm.provider ?? ""}`.trim() : "local tutor") : "tutor: not reported";
  const enc = !status
    ? "encryption: not reported"
    : status.encryption?.file_encrypted
      ? "db encrypted at rest"
      : status.encryption?.field_level_encryption
        ? "columns encrypted"
        : "no at-rest encryption";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
      {error ? (
        <span className="text-[var(--bad)]">api unreachable</span>
      ) : (
        <>
          <span title={`mode=${health?.mode ?? "not read yet"}`}>
            {health == null ? "checking the api…" : health.ok ? "api up" : "api down"} · {llm} · {enc}
          </span>
          {status?.version ? <span>v{status.version}</span> : null}
          {status?.mode === "demo" ? <span className="text-[var(--warn)]">demo mode (writes need a passphrase)</span> : null}
        </>
      )}
    </div>
  );
}

export function Chrome({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname() || "/";
  const section = SECTION_LABELS[pathname] ?? "§ ADAPT";
  const overlayRef = useFocusTrap(open);
  const tabRef = useRef<HTMLButtonElement>(null);

  // Close on Escape at Chrome level as well (focus trap handles inward, this handles state)
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        tabRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <MockBanner />
      {/* corner index tab - always visible, triggers full-screen overlay */}
      <button
        ref={tabRef}
        className="index-tab"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="indexOverlay"
        // The visible text is the section; the accessible name still says what the control does,
        // because "§ 05 · Progress" on its own reads as a heading and not as a button.
        aria-label={`Section index — you are in ${section}`}
        title={`Open the section index (you are in ${section})`}
        onClick={() => setOpen((v) => !v)}
      >
        {section}
      </button>

      {/* full-screen index overlay */}
      {open ? (
        <div
          ref={overlayRef}
          id="indexOverlay"
          className="index-overlay open"
          role="dialog"
          aria-modal="true"
          aria-label="Section index"
        >
          <button className="index-close" onClick={() => { setOpen(false); tabRef.current?.focus(); }}>
            close ✕
          </button>
          <Link href="/" className="index-brand" onClick={() => setOpen(false)}>
            ad<b>apt</b> — index
          </Link>
          <nav className="index-list" aria-label="Sections">
            {NAV.map((n, i) => (
              <Link
                key={n.href}
                href={n.href}
                className="index-row"
                onClick={() => setOpen(false)}
              >
                <span className="n">{String(i + 1).padStart(2, "0")}</span>
                <span className="t">{n.label}</span>
              </Link>
            ))}
          </nav>
          <div className="mt-10 flex items-center gap-3 text-xs">
            <Who />
          </div>
        </div>
      ) : null}

      {/* Fallback header for who/status when overlay not open - keep accessible.
          pl-28 keeps a long name or the sign-in link from running under the fixed index tab. */}
      <div className="flex justify-end py-2 pl-28 pr-4 text-xs" style={{ fontFamily: "var(--font-mono)" }}>
        <div className="flex items-center gap-3">
          <Who />
        </div>
      </div>

      <main id="main-content" className="mx-auto max-w-5xl px-4 py-6 pt-2">
        {children}
      </main>
      <footer className="mx-auto max-w-5xl px-4 pb-10 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        {/* What the app is running on. In the footer's flow on phones (never covering the
            content); the CSS lifts it to the fixed bottom-left corner on wide screens. */}
        <div className="status-corner mb-3" aria-hidden="false">
          <StatusChip />
        </div>
        Estimates, not labels: nothing here is a fixed trait, and no lasting change is applied without you answering a
        question about it. <Link href="/settings" className="underline">
          what is recorded about you
        </Link>
      </footer>
    </>
  );
}

function Who() {
  const { me, loading, unknown, refresh, signOut } = useSession();
  if (loading && !me) return <span className="text-xs text-[var(--muted)]">…</span>;
  if (!me && unknown)
    return (
      <button type="button" onClick={() => refresh()} className="text-xs text-[var(--warn)] underline decoration-dotted">
        checking who you are… retry
      </button>
    );
  if (!me)
    return (
      <Link href="/" className="rounded-[2px] border border-[var(--line)] px-2 py-1 text-xs hover:border-[var(--accent)]" style={{ fontFamily: "var(--font-mono)" }}>
        sign in
      </Link>
    );
  return (
    <span className="flex items-center gap-2 text-xs" style={{ fontFamily: "var(--font-mono)" }}>
      <Link href="/settings" className="text-[var(--muted)] hover:text-[var(--ink)] hover:underline">
        {(me as Me).display_name || (me as Me).student_id}
      </Link>
      <button
        type="button"
        className="text-[var(--muted)] underline decoration-dotted hover:text-[var(--ink)]"
        onClick={async () => {
          await signOut();
          refresh();
        }}
      >
        sign out
      </button>
    </span>
  );
}

/** Owns the only copy of "who is this browser". Every page reads it from context, so a
 *  sign-in on one screen cannot leave another one showing a stale name. */
export function SessionGate({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  // `unknown` is not an error to show, it is a *state*: the server has not said this browser
  // is anybody or nobody. Conflating the two is how a rate limit becomes a lost session.
  const [unknown, setUnknown] = useState<string | null>(null);
  const attempt = useRef(0);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refresh = useCallback(() => {
    setLoading(true);
    get<Me>("me").then((r) => {
      const refused = r.status === 401 || r.status === 403;
      if (r.data) {
        setMe(r.data);
        setUnknown(null);
        attempt.current = 0;
      } else if (refused) {
        // the server answered, and the answer was no: that really is signed out
        setMe(null);
        setUnknown(null);
        attempt.current = 0;
      } else {
        // no answer, or an answer that says nothing about the session (429, 5xx, network).
        // Keep whoever was on screen and try again a few times, backing off.
        setUnknown(r.error ?? `the server answered ${r.status ?? "nothing"}`);
        if (attempt.current < 4) {
          attempt.current += 1;
          if (retry.current) clearTimeout(retry.current);
          retry.current = setTimeout(() => refreshRef.current(), 700 * 2 ** attempt.current);
        }
      }
      setLoading(false);
    });
  }, []);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  useEffect(() => {
    refresh();
    return () => {
      if (retry.current) clearTimeout(retry.current);
    };
  }, [refresh]);

  const value = useMemo(
    () => ({
      me: me as unknown as Record<string, unknown> | null,
      loading,
      unknown,
      refresh,
      signOut: async () => {
        await logout();
        refresh();
      },
    }),
    [me, loading, unknown, refresh],
  );
  return <SessionProvider value={value}>{children}</SessionProvider>;
}

