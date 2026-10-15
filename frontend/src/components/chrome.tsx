"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AccountGate } from "./account-gate";
import { SessionProvider, useSession } from "./ui";
import { get, isMock, logout, type Me } from "@/lib/api";
import type { Health, Status } from "@/lib/types";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";

export const NAV = [
  { href: "/", label: "Home" },
  { href: "/learn", label: "Practise" },
  { href: "/lesson", label: "Lessons" },
  { href: "/proposals", label: "Changes" },
  { href: "/review", label: "Review" },
  { href: "/progress", label: "Progress" },
  // First entry opens the baseline; returning learners can resume it from Home.
  { href: "/baseline", label: "Mind check" },
  { href: "/subject", label: "Subject round" },
  { href: "/contrast", label: "Compare lessons" },
  { href: "/evidence", label: "Evidence" },
  { href: "/voice", label: "Voice" },
  { href: "/settings", label: "Settings" },
  { href: "/journey", label: "Your path" },
];

/** Red bar, every screen, whenever the data on it is not real. */
export function MockBanner() {
  const [mock, setMock] = useState(false);
  useEffect(() => setMock(isMock()), []);
  if (!mock) return null;
  return (
    // pl-4: there is no fixed corner control any more, so the banner starts at the edge
    // on every screen size.
    <div className="border-b border-[var(--bad)]/50 bg-[var(--bad)]/15 py-2 pl-4 pr-4 text-center text-xs text-[var(--bad)]" style={{ fontFamily: "var(--font-mono)" }}>
      <strong>EXAMPLE DATA.</strong> This preview uses sample information and does not change your saved data.{" "}
      <Link className="link-inline" href="/?mock=off">
        Exit the preview
      </Link>
    </div>
  );
}

/** A small learner-safe readout of lesson availability and saved-data protection. */
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
  // On every page, so it says only what a learner may know: how lessons are produced,
  // and whether answers are encrypted when that has actually been reported. A signed-out
  // reader has no status payload, so the chip omits the protection clause rather than guessing.
  const lessons = status?.lessons ?? health?.lessons;
  const tutor = lessons?.says ?? "Lessons: not reported";
  const protection = status?.data_protection;
  const enc = !status
    ? null
    : protection?.file_encrypted
      ? "whole file encrypted"
      : protection?.protected?.length
        ? "answers and notes encrypted"
        : null;
  const service = error && !health
    ? "service unreachable"
    : health == null
      ? "checking the service…"
      : health.ok
        ? "service up"
        : "service down";
  const text = [service, tutor, enc].filter(Boolean).join(" · ");
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
      <span title="how lessons are produced right now">{text}</span>
      {status?.version ? <span>v{status.version}</span> : null}
      {status?.demo_mode ? <span className="text-[var(--warn)]">demo mode: writes need a passphrase</span> : null}
    </div>
  );
}

export function Chrome({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname() || "/";
  // The drawer's focus trap only exists while it is open; the sidebar is always in the
  // document on desktop, where trapping focus in it would be exactly the trap that makes
  // the rest of the page unreachable.
  const drawerRef = useFocusTrap(open);
  const burgerRef = useRef<HTMLButtonElement>(null);

  // Escape closes the drawer and hands focus back to the control that opened it.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        burgerRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const current = activeHref(pathname);

  return (
    <>
      {/* Below 860px the sidebar slides in from here. The control is a labelled button,
          not a bare glyph: navigation must never be something you have to discover. */}
      <div className="topbar">
        <button
          ref={burgerRef}
          type="button"
          className="hamburger"
          aria-expanded={open}
          aria-controls="adapt-sidebar"
          onClick={() => setOpen((v) => !v)}
        >
          <span aria-hidden="true">☰</span>
          <span>Menu</span>
        </button>
        <span className="topbar-label">{current ? NAV.find((n) => n.href === current)?.label : "ADAPT"}</span>
      </div>
      <button
        type="button"
        className={`sidebar-overlay${open ? " show" : ""}`}
        aria-label="Close navigation"
        tabIndex={-1}
        onClick={() => setOpen(false)}
      />

      {/* The permanent sidebar: every top-level destination, in plain words, with the page
          you are on marked in three ways (aria-current, colour, and the accent edge). */}
      <aside id="adapt-sidebar" ref={drawerRef} className={`sidebar${open ? " open" : ""}`} aria-label="Main navigation">
        <Link href="/" className="sidebar-brand" onClick={() => setOpen(false)}>
          ad<b>apt</b>
        </Link>
        <nav>
          {NAV.map((n, i) => {
            const here = n.href === current;
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`sidebar-item${here ? " active" : ""}`}
                aria-current={here ? "page" : undefined}
                onNavigate={() => setOpen(false)}
              >
                <span className="sidebar-num">{i === 0 ? "⌂" : String(i).padStart(2, "0")}</span>
                <span className="sidebar-label">{n.label}</span>
                <NavigationPending label={n.label} />
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-status">
          {/* What the app is running on, in the one place that is on every screen. */}
          <StatusChip />
        </div>
      </aside>

      <div className="main-content">
        {/* Inside the content column, so it never runs under the fixed sidebar. */}
        <MockBanner />
        <div className="flex justify-end px-4 py-2 text-xs" style={{ fontFamily: "var(--font-mono)" }}>
          <div className="flex items-center gap-3">
            <Who />
          </div>
        </div>

        <main id="main-content" className="mx-auto max-w-5xl px-4 py-6 pt-2">
          {children}
        </main>
        <footer className="mx-auto max-w-5xl px-4 pb-10 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
          Estimates, not labels: nothing here is a fixed trait, and no lasting change is applied without you answering a
          question about it. <Link href="/settings" className="link-inline">
            what is recorded about you
          </Link>
        </footer>
      </div>
    </>
  );
}

/** Next owns the pending state, including cancelled/rapid consecutive navigations.
 * A click-local boolean would get stuck on back/forward or when a later click wins. */
function NavigationPending({ label }: { label: string }) {
  const { pending } = useLinkStatus();
  if (!pending) return null;
  return (
    <span className="navigation-pending" role="status">
      <span aria-hidden="true">…</span>
      <span className="sr-only">Opening {label}…</span>
    </span>
  );
}

/** Which NAV entry the current route belongs to, including the explicit Home link. */
export function activeHref(pathname: string): string | null {
  const hit = NAV.find((n) => pathname === n.href || pathname.startsWith(`${n.href}/`));
  return hit ? hit.href : null;
}

function Who() {
  const { me, loading, unknown, refresh, signOut } = useSession();
  if (loading && !me) return <span className="text-xs text-[var(--muted)]">…</span>;
  if (!me && unknown)
    return (
      <button type="button" onClick={() => refresh()} className="btn-ctl btn-ghost">
        checking who you are… retry
      </button>
    );
  if (!me) {
    // Navigation, so a plain link is the right control for it; everything that *acts*
    // wears one of the three button classes.
    return (
      <Link href="/" className="link-inline text-xs" style={{ fontFamily: "var(--font-mono)" }}>
        sign in
      </Link>
    );
  }
  return (
    <span className="flex items-center gap-2 text-xs" style={{ fontFamily: "var(--font-mono)" }}>
      <Link href="/settings" className="text-[var(--muted)] hover:text-[var(--ink)] hover:underline">
        {(me as Me).display_name || (me as Me).student_id}
      </Link>
      <button
        type="button"
        className="btn-ctl btn-ghost"
        onClick={async () => {
          await signOut();
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
  const pathname = usePathname();
  const [landing, setLanding] = useState<string | null>(null);
  useEffect(() => { if (pathname === landing) setLanding(null); }, [pathname, landing]);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  // `unknown` is not an error to show, it is a *state*: the server has not said this browser
  // is anybody or nobody. Conflating the two is how a rate limit becomes a lost session.
  const [unknown, setUnknown] = useState<string | null>(null);
  const attempt = useRef(0);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  const refresh = useCallback(() => {
    const request = ++generation.current;
    if (retry.current) clearTimeout(retry.current);
    setLoading(true);
    get<Me>("me").then((r) => {
      if (request !== generation.current) return;
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
      generation.current += 1;
      if (retry.current) clearTimeout(retry.current);
    };
  }, [refresh]);

  const establish = useCallback((profile: Record<string, unknown>) => {
    // Invalidate any older /me request (including a pre-login 401) before accepting
    // the profile returned with the new session cookie. No second network round trip.
    generation.current += 1;
    if (retry.current) clearTimeout(retry.current);
    attempt.current = 0;
    setLanding(profile.baseline_started ? "/" : "/baseline");
    setMe(profile as Me);
    setUnknown(null);
    setLoading(false);
  }, []);

  const value = useMemo(
    () => ({
      me: me as unknown as Record<string, unknown> | null,
      loading,
      unknown,
      refresh,
      establish,
      signOut: async () => {
        await logout();
        refresh();
      },
    }),
    [me, loading, unknown, refresh, establish],
  );
  return <SessionProvider value={value}>
    {me && (!landing || pathname === landing) ? children : !me && !loading && !unknown ? <AccountGate /> : (
      <div className="access-screen"><section id="main-content" className="access-card" aria-busy={loading}>
        <h1 className="access-title">Welcome to <b>ADAPT</b>.</h1>
        <p className="mt-3" role="status">{unknown ? "Your session could not be checked. Please retry; you do not need another account." : landing === "/baseline" ? "Opening your first mind check…" : "Checking your session…"}</p>
        {unknown ? <button className="btn-ctl btn-primary mt-4" onClick={refresh}>Retry session check</button> : null}
      </section></div>
    )}
  </SessionProvider>;
}

