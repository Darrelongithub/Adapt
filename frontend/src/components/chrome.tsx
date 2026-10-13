"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { SessionProvider, useSession } from "./ui";
import { get, isMock, logout, type Me } from "@/lib/api";
import type { Health, Status } from "@/lib/types";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";

export const NAV = [
  { href: "/baseline", label: "Mind check" },
  { href: "/learn", label: "Practise" },
  { href: "/lesson", label: "Lessons" },
  { href: "/proposals", label: "Changes" },
  { href: "/review", label: "Review" },
  { href: "/progress", label: "Progress" },
  { href: "/voice", label: "Voice" },
  { href: "/settings", label: "Settings" },
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
      <strong>DEMO DATA.</strong> This screen is running on fixtures (<code>?mock=1</code>), so nothing you see here was
      measured from a learner and nothing on it was saved.{" "}
      <Link className="link-inline" href="/?mock=off">
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
        : "no encryption at rest";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
      {error ? (
        <span className="text-[var(--bad)]">the API is unreachable</span>
      ) : (
        <>
          <span title={`mode=${health?.mode ?? "not read yet"}`}>
            {health == null ? "checking the API…" : health.ok ? "API up" : "API down"} · {llm} · {enc}
          </span>
          {status?.version ? <span>v{status.version}</span> : null}
          {status?.mode === "demo" ? <span className="text-[var(--warn)]">demo mode: writes need a passphrase</span> : null}
        </>
      )}
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
                onClick={() => setOpen(false)}
              >
                <span className="sidebar-num">{String(i + 1).padStart(2, "0")}</span>
                <span className="sidebar-label">{n.label}</span>
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

/** Which NAV entry the current route belongs to. `/lesson` marks Lessons; `/` marks nothing,
 *  because home is not a section — the sidebar still shows it as the brand. */
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

