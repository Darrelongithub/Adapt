"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { Err, SessionProvider, useSession } from "./ui";
import { csrfToken, get, isMock, logout, post, type Me } from "@/lib/api";
import type { Health, Status } from "@/lib/types";

const NAV = [
  { href: "/learn", label: "Practise" },
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
    <div className="border-b border-[var(--bad)]/50 bg-[var(--bad)]/15 px-4 py-2 text-center text-xs text-[var(--bad)]">
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
  const llm = status?.llm?.configured ? `model ${status.llm.provider ?? ""}`.trim() : "local tutor";
  const enc = status?.encryption?.file_encrypted ? "db encrypted at rest" : status?.encryption?.field_level_encryption ? "columns encrypted" : "no at-rest encryption";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-[var(--muted)]">
      {error ? (
        <span className="text-[var(--bad)]">api unreachable</span>
      ) : (
        <>
          <span title={`mode=${health?.mode ?? "?"}`}>
            {health?.ok ? "api up" : "api down"} · {llm} · {enc}
          </span>
          {status?.version ? <span>v{status.version}</span> : null}
          {status?.mode === "demo" ? <span className="text-[var(--warn)]">demo mode (writes need a passphrase)</span> : null}
        </>
      )}
    </div>
  );
}

export function Chrome({ children }: { children: ReactNode }) {
  return (
    <>
      <MockBanner />
      <header className="border-b border-[var(--line)]/70 px-4 py-3">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2">
          <Link href="/" className="text-sm font-semibold tracking-[0.18em] uppercase">
            Adapt
          </Link>
          <nav className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} className="text-[var(--muted)] hover:text-[var(--ink)] hover:underline">
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <StatusChip />
            <Who />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-5xl px-4 pb-10 text-[0.7rem] text-[var(--muted)]">
        Estimates, not labels: nothing here is a fixed trait, and no lasting change is applied without you answering a
        question about it. <Link href="/settings" className="underline">
          what is recorded about you
        </Link>
      </footer>
    </>
  );
}

function Who() {
  const { me, loading, refresh, signOut } = useSession();
  if (loading) return <span className="text-xs text-[var(--muted)]">…</span>;
  if (!me)
    return (
      <Link href="/" className="rounded-md border border-[var(--line)] px-2 py-1 text-xs hover:border-[var(--accent)]">
        sign in
      </Link>
    );
  return (
    <span className="flex items-center gap-2 text-xs">
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
  const refresh = useCallback(() => {
    setLoading(true);
    get<Me>("me").then((r) => {
      setMe(r.data);
      setLoading(false);
    });
  }, []);
  useEffect(() => refresh(), [refresh]);

  const value = useMemo(
    () => ({
      me: me as unknown as Record<string, unknown> | null,
      loading,
      refresh,
      signOut: async () => {
        await logout();
        refresh();
      },
    }),
    [me, loading, refresh],
  );
  return <SessionProvider value={value}>{children}</SessionProvider>;
}

/** A learner who has not been placed yet should not land on an empty practice screen. */
export function NeedSetup() {
  const { me, loading } = useSession();
  if (loading) return null;
  if (me) return null;
  return (
    <Err error="Not signed in — open /api/v1/me directly or sign in on the home screen." status={401} />
  );
}

export function csrfHint() {
  return csrfToken() ? "session cookie present" : "no csrf cookie yet";
}

export async function startSession(mode = "practice"): Promise<string | null> {
  try {
    const out = await post<{ session_id: string }>("sessions", { mode });
    return out.session_id;
  } catch {
    return null;
  }
}
