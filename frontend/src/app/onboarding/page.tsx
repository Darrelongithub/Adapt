"use client";

/**
 * Setup: the account-level settings, and nothing that gets asked twice.
 *
 * This screen used to ask a learner how long a sitting they wanted, what they were
 * working towards and which language explanations should be in — all of which the mind
 * check asks, once, in a screen built to ask them together (thirteen questions, one
 * submit, each with the sentence that says what it changes). Being asked the same thing
 * on two screens is how a learner ends up believing the two answers mean different
 * things; the journey asked for one source, so this screen keeps the settings that
 * belong to the *account* and hands over the rest:
 *
 * * **time** — how many minutes a week they want to plan for;
 * * **voice** — whether replies should be spoken, and which voice;
 * * **consent** — the interaction log, and whether a written-for-you summary may be used;
 * * **two free-text requests** — "more of this / less of this", which land in the same
 *   preference store everything else reads, so the tutor sees them in one place.
 *
 * There is still no learning-styles question, and the note the server sends back says
 * the same thing the blueprint does: how you learn best is measured from what happens
 * during practice.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { Btn, Card, Check, Err, Field, Input, Notice, useSession } from "@/components/ui";
import { get, post, useApi, type Me } from "@/lib/api";

type Prefs = {
  preferences: Record<string, unknown>;
};

/** The four preferences this screen owns. Anything else is asked once, elsewhere. */
const OWN_KEYS = ["weekly_minutes", "encourage", "avoid"] as const;

export default function Onboarding() {
  const { refresh } = useSession();
  const prefs = useApi<Prefs>("me/preferences");
  const [form, setForm] = useState<Record<string, string | boolean | number>>({
    weekly_minutes: 90,
    encourage: "",
    avoid: "",
  });
  // Unchecked until something says otherwise. The learner's own answer at the door arrives
  // in the URL because this is the first screen with a session to store it on; without one,
  // the boxes stay empty rather than being pre-ticked on their behalf.
  const [consentAnalytics, setConsentAnalytics] = useState(false);
  const [consentLlm, setConsentLlm] = useState(false);
  const [carriedConsent, setCarriedConsent] = useState<string | null>(null);
  const [voiceWant, setVoiceWant] = useState(false);
  const [voiceGender, setVoiceGender] = useState("female");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const p = prefs.data?.preferences;
    if (!p) return;
    setForm((f) => ({ ...f, ...pick(p, [...OWN_KEYS]) }) as Record<string, string | boolean | number>);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.data?.preferences && JSON.stringify(prefs.data?.preferences)]);

  useEffect(() => {
    // What the door recorded, when the learner came through it. A decision they made is
    // never flipped back to the row's default on the way in.
    const carried = new URLSearchParams(window.location.search).get("consent");
    setCarriedConsent(carried);
    if (carried === "yes" || carried === "no") {
      setConsentAnalytics(carried === "yes");
    }
    get<Me>("me").then((r) => {
      if (!r.data) return;
      if (carried !== "yes" && carried !== "no") setConsentAnalytics(Boolean(r.data.consent_analytics));
      setConsentLlm(Boolean(r.data.consent_share_with_ai));
      setVoiceWant(Boolean(r.data.voice_enabled));
      setVoiceGender(r.data.voice_gender || "female");
    });
  }, []);

  const set = (k: string) => (v: string | boolean | number) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const out = await post<Record<string, unknown>>("me/onboarding", {
        preferences: clean(form),
        consent_analytics: consentAnalytics,
        consent_share_with_llm: consentLlm,
        voice_enabled: voiceWant,
        voice_gender: voiceGender,
      });
      setResult(out);
      refresh();
    } catch (err) {
      setError((err as { shown?: string }).shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  // Whether anything was refused, read from the reasons map rather than the list of
  // rejected key names: the names are for the operator's log, and renaming what the
  // server calls them is not this screen's business.
  const notStored = Object.keys((result?.rejected_reasons as Record<string, string> | undefined) ?? {}).length > 0;

  return (
    <div className="mx-auto max-w-5xl px-3 py-4" style={{ padding: "clamp(24px,4vw,48px) clamp(16px,4vw,32px)" }}>
      <div className="grid gap-6 md:grid-cols-[1.15fr_0.85fr]">
        <div className="space-y-6">
          <div>
            <div className="t-label">
              § Setup
            </div>
            <h1 className="mt-2 text-[clamp(24px,3.4vw,36px)] font-medium leading-tight" style={{ fontFamily: "var(--font-serif)" }}>
              Set up how this should run
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-[var(--muted)]">
              These are requests — they apply right away. <a href="#how-this-works" className="link-inline">how it works</a>
            </p>
            <details id="how-this-works" className="r3-question-why-tap">
              <summary>how it works</summary>
              <p className="r3-question-why">
                Changes you make here are requests, applied immediately. Changes the app proposes from measurement
                go to a separate approval queue. You can change any of it later in{" "}
                <Link href="/settings" className="link-inline">settings</Link>.
              </p>
            </details>
          </div>

          <Card title="Time">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Minutes a week" hint="Your weekly plan.">
                <Input
                  type="number"
                  min={10}
                  step={5}
                  value={String(form.weekly_minutes)}
                  onChange={(e) => set("weekly_minutes")(Number(e.target.value))}
                />
              </Field>
              <p className="self-end text-xs text-[var(--muted)]">
                Session length is asked later.
              </p>
            </div>
          </Card>

          <Card title="Teaching you can ask for">
            <div className="space-y-3">
              <Field label="More of this" hint="A stated preference.">
                <Input value={String(form.encourage)} onChange={(e) => set("encourage")(e.target.value)} placeholder="diagrams before symbols" />
              </Field>
              <Field label="Less of this">
                <Input value={String(form.avoid)} onChange={(e) => set("avoid")(e.target.value)} placeholder="anything that feels like a sticker chart" />
              </Field>
              <p className="text-xs text-[var(--muted)]">
                Change explanation length in Settings.
              </p>
            </div>
          </Card>

          <Card title="What may be measured" tone="warn">
            <div className="space-y-3">
              {carriedConsent ? (
                <p className="text-xs text-[var(--muted)]">
                  You answered this before your account existed, and this screen stores the answer:{" "}
                  {carriedConsent === "yes" ? "analytics consent given" : "analytics consent declined"}. Change it here if you
                  want to — the boxes are never set on your behalf.
                </p>
              ) : null}
              <Check
                label="Keep the interaction log"
                checked={consentAnalytics}
                onChange={setConsentAnalytics}
                hint="Order of events and timings only. Not keystrokes, not screenshots, not other tabs."
              />
              <Check
                label="Allow a written-for-you summary when it would help"
                checked={consentLlm}
                onChange={setConsentLlm}
                hint="Topic mastery, hypotheses and approved changes. Never your name or contact details."
              />
            </div>
          </Card>

          <div className="flex flex-wrap items-center gap-3">
            <Btn kind="primary" onClick={save} disabled={busy}>
              {busy ? "…" : "Save and continue"}
            </Btn>
            <Btn href="/journey" kind="ghost">
              skip for now
            </Btn>
            {error ? <span className="text-xs text-[var(--bad)]">{error}</span> : null}
          </div>
        </div>

        <div className="space-y-4">
          {result ? (
            <Card title="Saved" tone="good">
              <p className="text-sm">{String(result.note ?? "")}</p>
              {notStored ? (
                <p className="mt-2 text-xs text-[var(--muted)]">
                  Some of what was sent was not stored. Only the questions on this page are kept.
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Btn kind="primary" href="/journey">
                  Continue to your path
                </Btn>
                <Btn href="/learn" kind="ghost">
                  Skip to practice
                </Btn>
              </div>
              <p className="mt-2 text-xs text-[var(--muted)]">
                Your path takes you to the questions, then the games, then a subject — in that order, and you can stop after any
                step.
              </p>
            </Card>
          ) : (
            <>
              <details><summary>What happens next</summary><Card title="What happens next" tone="muted">
                <ol className="space-y-2 text-sm">
                  <li>
                    <strong>1. How you work</strong> — thirteen questions, in one sitting, on the mind check.
                  </li>
                  <li>
                    <strong>2. Mind games</strong> — six short games that measure attention, recall and what you do when you are
                    stuck.
                  </li>
                  <li>
                    <strong>3. A subject, then a lesson</strong> — the first lesson says why it looks the way it does.
                  </li>
                </ol>
                <p className="mt-2 text-xs text-[var(--muted)]">
                  Every step can be skipped, and a skipped step is recorded as not measured rather than as a zero.
                </p>
              </Card></details>
              <Card title="Voice and speech">
                <div className="space-y-3">
                  <Check
                    label="Use voice when I want it"
                    checked={voiceWant}
                    onChange={setVoiceWant}
                    hint="Nothing is recorded until you press the button on the voice screen."
                  />
                  {voiceWant ? (
                    <Field label="Which voice replies">
                      <select
                        className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
                        value={voiceGender}
                        onChange={(e) => setVoiceGender(e.target.value)}
                      >
                        <option value="female">female</option>
                        <option value="male">male</option>
                      </select>
                    </Field>
                  ) : null}
                </div>
              </Card>
              {prefs.error ? <Err error={prefs.error} status={prefs.status} /> : null}
              {prefs.loading ? <Notice tone="info">reading the settings already saved…</Notice> : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function pick(src: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of keys) if (src[k] !== undefined && src[k] !== null) out[k] = src[k];
  return out;
}

function clean(form: Record<string, string | boolean | number>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(form)) {
    if (typeof v === "string" && v.trim() === "") continue;
    out[k] = typeof v === "string" ? v.trim() : v;
  }
  return out;
}
