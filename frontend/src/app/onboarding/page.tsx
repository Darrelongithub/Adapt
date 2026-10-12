"use client";

/** Setup, and the one screen that has to be explicit about what it is *not* asking.
 *
 * There is no learning-styles question here by design: the onboarding payload has no
 * key for it, and the note the server sends back says the same thing the blueprint does
 * — how you learn best is measured from what happens during practice. Free text about
 * what you want more or less of is welcome, because that is a request you are making,
 * not a label you are being given.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Btn, Card, Check, Err, Field, Input, Tag, useSession } from "@/components/ui";
import { get, post, useApi, type Me } from "@/lib/api";

type Prefs = {
  preferences: Record<string, unknown>;
  allowed_keys: string[];
};

export default function Onboarding() {
  const router = useRouter();
  const { me, refresh } = useSession();
  const prefs = useApi<Prefs>("me/preferences");
  const [form, setForm] = useState<Record<string, string | boolean | number>>({
    goal: "",
    weekly_minutes: 90,
    session_minutes: 20,
    max_explanation_words: 90,
    push_me: true,
    want_voice: false,
    language: "en",
    encourage: "",
    avoid: "",
  });
  const [consentAnalytics, setConsentAnalytics] = useState(true);
  const [consentLlm, setConsentLlm] = useState(true);
  const [voiceGender, setVoiceGender] = useState("female");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const p = prefs.data?.preferences;
    if (!p) return;
    setForm((f) => ({ ...f, ...pick(p, Object.keys(f)) }) as Record<string, string | boolean | number>);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.data?.preferences && JSON.stringify(prefs.data?.preferences)]);

  useEffect(() => {
    get<Me>("me").then((r) => {
      if (r.data) {
        setConsentAnalytics(Boolean(r.data.consent_analytics));
        setConsentLlm(Boolean(r.data.consent_share_with_ai));
        setVoiceGender(r.data.voice_gender || "female");
        void r.data;
      }
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
        voice_enabled: Boolean(form.want_voice),
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

  const ignored = (result?.ignored_keys as string[] | undefined) ?? [];
  const reasons = (result?.rejected_reasons as Record<string, string> | undefined) ?? {};

  return (
    <div className="grid gap-5 md:grid-cols-[1.1fr_1fr]">
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Set up how this should run</h1>
        <p className="text-sm text-[var(--muted)]">
          Everything here is a request you are making, so it applies straight away and needs no approval — the approval gate is
          for changes the system proposes from measurement. Change any of it later on the settings screen.
        </p>
        <Card title="Time and words">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="What are you working towards?" hint="Shown back to you in the plan, and used to pick wording.">
              <Input value={String(form.goal)} onChange={(e) => set("goal")(e.target.value)} placeholder="GCSE maths, higher tier" />
            </Field>
            <Field label="Minutes a week" hint="A number, not a promise. It only decides how much to put in the plan.">
              <Input
                type="number"
                min={10}
                step={5}
                value={String(form.weekly_minutes)}
                onChange={(e) => set("weekly_minutes")(Number(e.target.value))}
              />
            </Field>
            <Field label="Minutes in one sitting">
              <Input
                type="number"
                min={5}
                step={5}
                value={String(form.session_minutes)}
                onChange={(e) => set("session_minutes")(Number(e.target.value))}
              />
            </Field>
            <Field label="Longest explanation you want to read" hint="The full version is always one click away; this is the default length.">
              <Input
                type="number"
                min={20}
                step={10}
                value={String(form.max_explanation_words)}
                onChange={(e) => set("max_explanation_words")(Number(e.target.value))}
              />
            </Field>
          </div>
        </Card>

        <Card title="Teaching you can ask for">
          <div className="space-y-3">
            <Check
              label="Push me to try before showing"
              checked={Boolean(form.push_me)}
              onChange={set("push_me")}
              hint="Turns re-reading off and puts a short attempt first. It feels harder; that is the mechanism, not a side effect."
            />
            <Field label="More of this" hint="Free text. It becomes a stated preference, and the tutor reads it.">
              <Input value={String(form.encourage)} onChange={(e) => set("encourage")(e.target.value)} placeholder="diagrams before symbols" />
            </Field>
            <Field label="Less of this">
              <Input value={String(form.avoid)} onChange={(e) => set("avoid")(e.target.value)} placeholder="anything that feels like a sticker chart" />
            </Field>
          </div>
        </Card>

        <Card title="Voice and speech">
          <div className="space-y-3">
            <Check label="Use voice when I want it" checked={Boolean(form.want_voice)} onChange={set("want_voice")} hint="Nothing is recorded until you press the button on the voice screen." />
            {form.want_voice ? (
              <Field label="Which voice replies">
                <select
                  className="w-full rounded-md border border-[var(--line)] bg-black/25 px-3 py-2 text-sm"
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

        <Card title="What may be measured" tone="warn">
          <div className="space-y-3">
            <Check
              label="Keep the interaction log"
              checked={consentAnalytics}
              onChange={setConsentAnalytics}
              hint="The order of events and timings — not keystrokes, not screenshots, not other tabs. Without it there is no evidence and no proposals, only questions."
            />
            <Check
              label="Let a model read a summary when it would help"
              checked={consentLlm}
              onChange={setConsentLlm}
              hint="The summary is topic mastery, hypotheses and approved changes — never your name or contact details. With no API key configured this changes nothing, because nothing is sent anyway."
            />
          </div>
        </Card>

        <div className="flex flex-wrap items-center gap-3">
          <Btn kind="primary" onClick={save} disabled={busy}>
            {busy ? "…" : "Save and continue"}
          </Btn>
          <Btn href="/diagnose" kind="ghost">
            skip for now
          </Btn>
          {error ? <span className="text-xs text-[var(--bad)]">{error}</span> : null}
        </div>
      </div>

      <div className="space-y-4">
        {result ? (
          <Card title="Saved" tone="good">
            <p className="text-sm">{String(result.note ?? "")}</p>
            {ignored.length ? (
              <div className="mt-2 text-xs text-[var(--muted)]">
                not stored: {ignored.map((k) => <Tag key={k} tone="warn">{k} — {reasons[k] ?? "unknown key"}</Tag>)}
              </div>
            ) : null}
            <div className="mt-3 flex gap-2">
              <Btn kind="primary" href="/diagnose">
                Start placement
              </Btn>
              <Btn onClick={() => router.push("/learn")}>Skip to practice</Btn>
            </div>
          </Card>
        ) : null}
        <Card title="What this screen refuses to ask" tone="muted">
          <ul className="space-y-2 text-sm text-[var(--muted)]">
            <li>“Are you a visual learner?” — the evidence does not support matching instruction to a stated style, and an answer here would only let the app label you.</li>
            <li>“How smart are you?” — nothing on this app needs that number, and every downstream use of it would be worse.</li>
            <li>Any permission it does not use. Consent for analytics and for the model summary are the only two, and both can be turned off later without losing practice.</li>
          </ul>
        </Card>
        <Card title="Allowed preference keys" tone="muted" right={<span>from the API</span>}>
          {prefs.loading ? <p className="text-sm text-[var(--muted)]">…</p> : null}
          {prefs.error ? <Err error={prefs.error} status={prefs.status} /> : null}
          <div className="flex flex-wrap gap-1">
            {(prefs.data?.allowed_keys ?? []).map((k) => (
              <Tag key={k}>{k}</Tag>
            ))}
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">
            Anything else the client sends comes back in <code>ignored_keys</code> with a reason, rather than being stored as a
            string nobody can interpret.
          </p>
        </Card>
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
