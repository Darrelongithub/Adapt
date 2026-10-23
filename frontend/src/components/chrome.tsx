"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AccountGate } from "./account-gate";
import { SessionProvider, useSession } from "./ui";
import { get, isMock, logout, type Me } from "@/lib/api";
import type { Health, Status } from "@/lib/types";
import { Icon, routeIcon } from "./icons";
import { IconTile } from "./ui";
import { PageShell } from "./shell/PageShell";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";
import { useVoice } from "@/components/voice/context";

/** Primary destinations, in plain words (Round 6, Track A). Five items, no more. */
export const NAV_PRIMARY: { href: string; label: string }[] = [
  { href: "/", label: "Home" },
  { href: "/learn", label: "Learn" },
  { href: "/review", label: "Review" },
  { href: "/progress", label: "Progress" },
  { href: "/settings", label: "Settings" },
];

/** The rest, under one short "More" group. Subject round is gone (Round 6). */
export const NAV_MORE: { href: string; label: string }[] = [
  { href: "/lesson", label: "Lessons" },
  { href: "/journey", label: "Your path" },
  { href: "/proposals", label: "Changes" },
  { href: "/baseline", label: "Mind check" },
  { href: "/evidence", label: "Evidence" },
  { href: "/tools", label: "Study tools" },
  { href: "/voice", label: "Voice" },
];

/** The combined list, used by `activeHref` so any route still resolves. */
export const NAV = [...NAV_PRIMARY, ...NAV_MORE];

/** Icon tone per nav destination, so the tiles read as a set. */
const navTone: Record<string, "blue" | "sand" | "mint" | "peach" | "think"> = {
  "/": "blue",
  "/learn": "blue",
  "/review": "sand",
  "/progress": "mint",
  "/settings": "think",
  "/lesson": "blue",
  "/journey": "sand",
  "/proposals": "peach",
  "/baseline": "think",
  "/evidence": "mint",
  "/tools": "mint",
  "/voice": "blue",
};

/** Red bar, every screen, whenever the data on it is not real. */
export function MockBanner() {
  const [mock, setMock] = useState(false);
  useEffect(() => setMock(isMock()), []);
  if (!mock) return null;
  return (
    <div className="border-b border-[var(--bad)]/50 bg-[var(--bad)]/15 py-2 px-4 text-center" style={{ fontSize: "var(--t-small)", color: "var(--bad)" }}>
      <strong>Example data.</strong> This preview uses sample information and does not change your saved data.{" "}
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
      const [h, s] = await Promise.all([get<Health>("health"), get<Status>("status")]);
      if (!alive) return;
      setHealth(h.data);
      setError(h.error);
      setStatus(s.data);
    })();
    return () => {
      alive = false;
    };
  }, []);
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
    <div className="status-chip" style={{ display: "block", borderRadius: "var(--r-tile)", textAlign: "left", textTransform: "none", letterSpacing: 0, lineHeight: "18px" }}>
      <span title="how lessons are produced right now">{text}</span>
      {status?.version ? <span> · v{status.version}</span> : null}
    </div>
  );
}

/** The short pitch flow is intentionally outside the full learner chrome and session gate. */
export function ProductFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  if (pathname === "/demo" || pathname.startsWith("/demo/")) return children;
  return <SessionGate><Chrome>{children}</Chrome></SessionGate>;
}

export function Chrome({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname() || "/";
  const drawerRef = useFocusTrap(open);
  const burgerRef = useRef<HTMLButtonElement>(null);

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
    <div className="app-frame">
      <button
        type="button"
        className={`sidebar-overlay${open ? " show" : ""}`}
        aria-label="Close navigation"
        tabIndex={-1}
        onClick={() => setOpen(false)}
      />

      {/* The permanent sidebar: sticky, full viewport height, icon tiles, one blue
          active fill. On phones it is a drawer behind the labelled Menu button. */}
      <aside id="adapt-sidebar" ref={drawerRef} className={`sidebar${open ? " open" : ""}`} aria-label="Main navigation">
        <Link href="/" className="sidebar-brand" onClick={() => setOpen(false)}>
          ad<b>apt</b>
        </Link>
        <nav data-testid="sidebar-nav">
          {NAV_PRIMARY.map((n) => (
            <NavLink key={n.href} n={n} current={current} onClose={() => setOpen(false)} />
          ))}
          <SidebarMore current={current} onClose={() => setOpen(false)} />
        </nav>
        <div className="sidebar-status">
          <StatusChip />
        </div>
      </aside>

      <div className="main-column">
        <div className="topbar">
          <div className="topbar-section">
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
          <div className="topbar-section topbar-who">
            <VoiceControl />
            <Who />
          </div>
        </div>

        <MockBanner />
        <VoiceGreeting />

        <main id="main-content" className="w-full">
          <PageShell>{children}</PageShell>
        </main>
        <footer className="page-footer">
          Estimates, not labels: nothing here is a fixed trait, and no lasting change is applied without you answering a
          question about it. <Link href="/settings" className="link-inline">
            What is recorded about you
          </Link>
        </footer>
      </div>
    </div>
  );
}

function NavLink({ n, current, onClose }: { n: { href: string; label: string }; current: string | null; onClose: () => void }) {
  const here = n.href === current;
  return (
    <Link
      href={n.href}
      className={`sidebar-item${here ? " active" : ""}`}
      aria-current={here ? "page" : undefined}
      onNavigate={onClose}
    >
      <IconTile icon={<Icon name={routeIcon[n.href] ?? "book"} />} tone={navTone[n.href] ?? "blue"} />
      <span className="sidebar-label">{n.label}</span>
      <NavigationPending label={n.label} />
    </Link>
  );
}

/** Next owns the pending state, including cancelled/rapid consecutive navigations. */
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

/** The "More" group in the sidebar. Opens by default when the current route lives there. */
function SidebarMore({ current, onClose }: { current: string | null; onClose: () => void }) {
  const isOnMore = current ? NAV_MORE.some((n) => n.href === current) : false;
  const [open, setOpen] = useState<boolean>(isOnMore);
  useEffect(() => {
    setOpen(isOnMore);
  }, [isOnMore]);
  return (
    <>
      <button
        type="button"
        className="sidebar-item sidebar-more"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid="sidebar-more"
      >
        <IconTile icon={<Icon name="chevron" />} tone="blue" />
        <span className="sidebar-label">{open ? "Less" : "More"}</span>
        <span style={{ transform: open ? "rotate(180deg)" : undefined }}><Icon name="chevron" /></span>
      </button>
      {open ? (
        <div data-testid="sidebar-more-items">
          {NAV_MORE.map((n) => (
            <NavLink key={n.href} n={n} current={current} onClose={onClose} />
          ))}
        </div>
      ) : null}
    </>
  );
}

function VoiceControl() {
  const { enabled, setEnabled, listening, speaking } = useVoice();
  const { me } = useSession();
  useEffect(() => {
    if (me?.voice_enabled && typeof window !== "undefined" && window.localStorage.getItem("adapt:voice-enabled") === null) setEnabled(true);
  }, [me?.voice_enabled, setEnabled]);
  return (
    <button type="button" className={`voice-switch${enabled ? " on" : ""}`} aria-pressed={enabled} onClick={() => setEnabled(!enabled)} title={enabled ? "Turn voice off" : "Turn voice on"}>
      <Icon name="mic" /> <span>{enabled ? "Voice on" : "Voice off"}</span>
      {listening ? <span aria-label="listening">●</span> : speaking ? <span aria-label="speaking">◌</span> : null}
    </button>
  );
}

function VoiceGreeting() {
  const { me } = useSession();
  const { enabled, speak, listenOnce } = useVoice();
  const greeted = useRef<string | null>(null);
  useEffect(() => {
    if (!enabled || !me || greeted.current === me.student_id) return;
    greeted.current = String(me.student_id);
    void speak(`Welcome back, ${me.display_name || "learner"}. Ready to continue your learning?`, { interrupt: true }).then(() => listenOnce({ timeoutMs: 7000 }));
  }, [enabled, me, speak, listenOnce]);
  return null;
}

function Who() {
  const { me, loading, unknown, refresh, signOut } = useSession();
  if (loading && !me) return <span className="topbar-name">…</span>;
  if (!me && unknown)
    return (
      <button type="button" onClick={() => refresh()} className="btn btn-ctl btn-secondary">
        Checking who you are… retry
      </button>
    );
  if (!me) {
    return (
      <Link href="/" className="btn btn-ctl btn-secondary">
        Sign in
      </Link>
    );
  }
  return (
    <span className="topbar-who">
      <Link href="/settings" className="topbar-name">
        {(me as Me).display_name || (me as Me).student_id}
      </Link>
      <button
        type="button"
        className="btn btn-ctl btn-secondary"
        onClick={async () => {
          await signOut();
        }}
      >
        Sign out
      </button>
    </span>
  );
}

/**
 * Owns the only copy of "who is this browser". Every page reads it from context.
 *
 * Round 6 (Track E): the landing gate is gone. It used to render "Opening your
 * path…" over every screen while `landing` was set and the URL was anywhere but
 * /journey — including a signed-in learner who clicked a sidebar link during the
 * redirect, which is the account-gate vs /journey loop. If the server says this
 * browser is somebody, the children render; navigation is navigation's job.
 */
export function SessionGate({ children }: { children: ReactNode }) {
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
        setLoading(false);
      } else if (refused) {
        setMe(null);
        setUnknown(null);
        attempt.current = 0;
        setLoading(false);
      } else {
        setUnknown(r.error ?? `the server answered ${r.status ?? "nothing"}`);
        if (attempt.current < 4) {
          attempt.current += 1;
          if (retry.current) clearTimeout(retry.current);
          retry.current = setTimeout(() => refreshRef.current(), 700 * 2 ** attempt.current);
        }
        setLoading(false);
      }
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
    generation.current += 1;
    if (retry.current) clearTimeout(retry.current);
    attempt.current = 0;
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
    {me ? children : !loading && !unknown ? <AccountGate /> : (
      <div className="access-screen"><section id="main-content" className="access-card" aria-busy={loading}>
        <h1 className="access-title">Welcome to <b>ADAPT</b>.</h1>
        <p className="mt-3" role="status">{unknown ? "Your session could not be checked. Please retry; you do not need another account." : "Checking your session…"}</p>
        {unknown ? <button className="btn btn-ctl btn-primary mt-4" onClick={refresh}>Retry session check</button> : null}
      </section></div>
    )}
  </SessionProvider>;
}
