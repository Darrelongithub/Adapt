"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, post, useApi, type Me } from "@/lib/api";
import { PageShell } from "./shell/PageShell";
import { LEARNER_LANDING } from "@/lib/landing";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";
import { Btn, Err, Field, Input, useSession } from "./ui";

/** This is the only signed-out UI. Protected pages are not mounted behind it. */
export function AccountGate() {
  const { establish } = useSession();
  const router = useRouter();
  const focus = useFocusTrap(true);
  const [step, setStep] = useState<"consent" | "auth">("consent");
  const [consent, setConsent] = useState(false);
  const [research, setResearch] = useState(false);
  const [mode, setMode] = useState<"signin" | "create">("signin");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ageBand, setAgeBand] = useState("16-18");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [needsPassphrase, setNeedsPassphrase] = useState(false);
  useEffect(() => { focus.current?.querySelector<HTMLElement>("input, button:not(:disabled)")?.focus(); }, [step, focus]);
  const privacy = useApi<{ telemetry?: { note?: string } }>("privacy");

  async function enter(guest = false) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      let profile: Me;
      if (guest || mode === "create") {
        profile = await api<Me>("students", {
          method: "POST",
          json: {
            ...(guest ? { as_guest: true } : { username: username.trim(), password }),
            age_band: ageBand,
            consent_analytics: consent,
            // The research checkbox is not consent to share with a model.
            consent_share_with_llm: false,
          },
          headers: passphrase ? { "x-adapt-demo-passphrase": passphrase } : undefined,
        });
      } else {
        const identifier = username.trim();
        profile = await post<Me>("login", {
          username: identifier, password,
          ...(identifier.includes("@") ? { email: identifier } : {}),
        });
      }
      establish(profile);
      // Round 3 (Track T): a new learner is routed to the journey stage
      // flow rather than dropped into the mind check.  The journey page is
      // now a one-screen flow that runs each stage in turn and auto-
      // advances; the menu stays for returning learners.
      router.replace(LEARNER_LANDING);
    } catch (err) {
      const e = err as { status?: number; shown?: string; detail?: unknown };
      if (e.status === 403 && JSON.stringify(e.detail ?? "").includes("demo_passphrase_required")) {
        setNeedsPassphrase(true);
      }
      setError(e.shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell access><div className="access-screen" data-auth-step={step}>
      <div className="access-wordmark" aria-hidden="true">ad<b>apt</b></div>
      <div ref={focus} id="main-content" className="access-card" role="dialog" aria-modal="true" aria-labelledby="access-title" data-account-gate onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); } }}>
        <p className="access-step">{step === "consent" ? "01 · Before you begin" : "02 · Your place to learn"}</p>
        <h1 id="access-title" className="access-title">{step === "consent" ? "Welcome to ADAPT." : "Welcome to ADAPT."}</h1>
        {step === "consent" ? (
          <>
            <p className="mt-3 text-sm text-[var(--muted)]">Practice that asks before it changes anything.</p>
            <ul className="mt-4 space-y-2 text-sm">
              <li className="flex items-start gap-2">
                <span className="choice-icon" aria-hidden="true">✓</span>
                <span><strong>Needed for your account:</strong> scored answers and progress.</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="choice-icon" aria-hidden="true">✕</span>
                <span><strong>Never recorded:</strong> keystrokes, screenshots, other tabs.</span>
              </li>
            </ul>
            <details className="r3-question-why-tap mt-2">
              <summary>more on privacy</summary>
              <p className="r3-question-why">
                {privacy.data?.telemetry?.note ?? "ADAPT records answers, time spent on questions, and the steps you take through lessons to estimate progress."}
              </p>
            </details>
            <div className="space-y-4 mt-4">
              <label className="check-row">
                <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />
                <span>Also record question times and lesson-step analytics. <strong>Optional.</strong></span>
              </label>
              <label className="check-row">
                <input type="checkbox" checked={research} onChange={e => setResearch(e.target.checked)} />
                <span>Research interest <span className="text-[var(--muted)]">(optional; not saved yet).</span></span>
              </label>
            </div>
            <div className="mt-6 flex flex-wrap gap-3">
              <Btn kind="primary" onClick={() => { setStep("auth"); setError(null); }}>Save choice and continue</Btn>
            </div>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm text-[var(--muted)]">Sign in, create an account, or continue as guest.</p>
            <div className="auth-tabs mt-5" role="tablist" aria-label="Sign in or create an account">
              {(["signin", "create"] as const).map(value => (
                <button key={value} type="button" role="tab" aria-selected={mode === value} disabled={busy}
                  className="auth-tab btn-ctl" onClick={() => { setMode(value); setError(null); }}>
                  {value === "signin" ? "Sign in" : "Create account"}
                </button>
              ))}
            </div>
            <form className="space-y-4" onSubmit={e => { e.preventDefault(); void enter(); }}>
              <Field label="Username" hint={mode === "create" ? "3–40 letters, numbers, dots or underscores." : "An older account may use email."}>
                <Input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} required disabled={busy} />
              </Field>
              <Field label="Password" hint={mode === "create" ? "At least ten characters." : undefined}>
                <Input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={mode === "create" ? "new-password" : "current-password"} required disabled={busy} minLength={mode === "create" ? 10 : undefined} />
              </Field>
              {mode === "create" ? <Field label="Age band">
                <select className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-2" value={ageBand} onChange={e => setAgeBand(e.target.value)}>
                  <option value="under-16">Under 16</option><option value="16-18">16 to 18</option><option value="19-24">19 to 24</option><option value="25+">25 and over</option>
                </select>
              </Field> : null}
              {needsPassphrase ? <Field label="Demo passphrase"><Input type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)} /></Field> : null}
              <div className="flex flex-wrap gap-3">
                <Btn kind="primary" type="submit" disabled={busy || !username.trim() || !password}>{busy ? "Signing you in…" : mode === "signin" ? "Sign in" : "Create account"}</Btn>
                <Btn kind="ghost" disabled={busy} onClick={() => void enter(true)}>Continue as guest</Btn>
              </div>
              {error ? <Err error={error} /> : null}
            </form>
            <p className="mt-4 text-xs text-[var(--muted)]">Guest progress stays in this session.</p>
            <div className="mt-4"><Btn kind="ghost" disabled={busy} onClick={() => setStep("consent")}>Review consent</Btn></div>
          </>
        )}

      </div>
    </div></PageShell>
  );
}
