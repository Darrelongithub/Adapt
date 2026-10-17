"use client";

/** Settings, privacy and the exit.
 *
 * The rule for this screen: the app never claims a protection it has not implemented, so
 * every claim here is read from the server that enforces it — `GET /privacy` for what may
 * be recorded, `GET /events/allowlist` for the exact event types, `GET /status` for whether
 * the database is encrypted at rest, `GET /audit` for every action the system took on your
 * account. Toggles do not merely store a wish: turning consent off stops the write.
 */
import { useEffect, useRef, useState } from "react";

import { Btn, Card, Check, Err, Field, humanize, Input, Notice, PageHeader, Select, Tag, useSession } from "@/components/ui";
import { Artwork } from "@/components/art/Artwork";
import { del, post, useApi } from "@/lib/api";
import type { Status } from "@/lib/types";

type Allowlist = { allowed: Record<string, string[]>; forbidden_keys: string[]; rule: string };
type PrefReply = { accepted?: Record<string, unknown>; rejected_keys?: string[]; rejected_reasons?: Record<string, string>; note?: string };
type Privacy = {
  telemetry?: {
    recorded?: string[];
    never_recorded?: string[];
    why?: string;
    note?: string;
  } & Record<string, unknown>;
  sent_to_model?: Record<string, string>;
  voice?: Record<string, string>;
  storage?: {
    file_level?: string;
    underlying_engine?: string;
    protected?: string[];
    not_protected?: string[];
    note?: string;
  };
  /** §3: the file-level claim plus what is and is not protected -- no paths, keys or drivers. */
  data_protection?: {
    file_level?: string;
    underlying_engine?: string;
    protected?: string[];
    not_protected?: string[];
    note?: string;
  };
  learner_controls?: string[];
};

const NUMERIC = new Set(["weekly_minutes", "session_minutes", "max_explanation_words", "items_per_session"]);
const BOOL = new Set(["push_me", "want_voice", "want_review_reminders"]);
/** Keys the server stores as a list of short phrases. They have to be typed, not ticked: a
 *  checkbox here used to post `true`, which the server wrapped into the phrase "True" and put
 *  into the learner's profile. */
const PHRASES = new Set(["avoid", "encourage"]);
const SHARING_LABELS: Record<string, string> = {
  when: "When you choose to share",
  identity: "How your identity is protected",
  redaction: "Before sharing",
  on_refusal: "If a reply cannot be prepared",
};
const VOICE_LABELS: Record<string, string> = {
  stt: "Speech recognition",
  tts: "Spoken replies",
  stored: "Voice data that may be saved",
  off_means: "When voice is off",
};

/** One text box in, a list out -- commas, semicolons and new lines all separate phrases. */
function toPhrases(value: string): string[] {
  return value.split(/[,;\n]/).map((part) => part.trim()).filter(Boolean).slice(0, 8);
}

export default function Settings() {
  const { me, refresh, unknown, loading: sessionLoading } = useSession();
  const prefs = useApi<{ preferences: Record<string, unknown>; allowed_keys: string[] }>("me/preferences");
  const privacy = useApi<Privacy>("privacy");
  const allow = useApi<Allowlist>("events/allowlist");
  const status = useApi<Status>("status");
  const audit = useApi<{ entries: { action: string; object?: string; outcome?: string; created_at?: string | null }[] }>("audit", { limit: 50 });
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [reply, setReply] = useState<PrefReply | null>(null);
  const [consent, setConsent] = useState<{ analytics: boolean; llm: boolean; voice: boolean; gender: string } | null>(null);
  const consentOwner = useRef<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState("");
  const [gone, setGone] = useState<string | null>(null);

  const allowed = prefs.data?.allowed_keys ?? [];

  useEffect(() => {
    const p = prefs.data?.preferences;
    if (p)
      setForm(
        Object.fromEntries(
          Object.entries(p).map(([k, v]) => [k, typeof v === "boolean" ? v : Array.isArray(v) ? v.join(", ") : String(v ?? "")]),
        ),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(prefs.data?.allowed_keys ?? [])]);

  useEffect(() => {
    // Reuse the session already loaded by the shell. Never render made-up consent values
    // or let a late request overwrite switches the learner has just edited.
    if (!me) {
      consentOwner.current = null;
      setConsent(null);
      return;
    }
    if (consentOwner.current === me.student_id) return;
    consentOwner.current = me.student_id;
    setConsent({
      analytics: Boolean(me.consent_analytics),
      llm: Boolean(me.consent_share_with_ai),
      voice: Boolean(me.voice_enabled),
      gender: String(me.voice_gender || "female"),
    });
  }, [me]);

  async function savePrefs() {
    if (prefs.loading || !prefs.data || busy) return;
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const payload: Record<string, unknown> = {};
      for (const k of Object.keys(form)) {
        const v = form[k];
        if (typeof v === "string" && v.trim() === "") continue;
        if (typeof v === "boolean") payload[k] = v;
        else if (NUMERIC.has(k)) payload[k] = Number(v);
        else if (PHRASES.has(k)) payload[k] = toPhrases(String(v));
        else payload[k] = v;
      }
      setReply(await post<PrefReply>("me/preferences", { preferences: payload, source: "settings" }));
      prefs.reload();
      refresh();
    } catch (err) {
      setError(msg(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveConsent() {
    if (!consent || busy) return;
    setBusy(true);
    setError(null);
    try {
      await post("me/onboarding", {
        preferences: {},
        consent_analytics: consent.analytics,
        consent_share_with_llm: consent.llm,
        voice_enabled: consent.voice,
        voice_gender: consent.gender,
      });
      refresh();
      privacy.reload();
    } catch (err) {
      setError(msg(err));
    } finally {
      setBusy(false);
    }
  }

  const entries = audit.data?.entries ?? [];
  const rejected = reply?.rejected_keys ?? [];
  const recorded = privacy.data?.telemetry?.recorded ?? [];
  const neverRecorded = privacy.data?.telemetry?.never_recorded ?? [];
  const protection = privacy.data?.data_protection ?? privacy.data?.storage;
  const protectedFields = protection?.protected ?? [];
  const notProtectedFields = protection?.not_protected ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings, and what is kept"
        subtitle="Every claim here is read from the server that enforces it."
        art={<Artwork name="gears" />}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Your preferences" tone="blue" right={<span>{allowed.length} preferences</span>}>
          {prefs.error ? <Err error={prefs.error} status={prefs.status} /> : null}
          {prefs.loading ? <p role="status" className="text-sm text-[var(--muted)]">Loading your preferences…</p> : null}
          <div className="space-y-2">
            {allowed.filter((k) => k !== "language" && k !== "want_voice").map((k) => (
              <Field key={k} label={k.replace(/_/g, " ")} hint={hintFor(k)}>
                {PHRASES.has(k) ? (
                  <Input
                    value={String(form[k] ?? "")}
                    onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
                    placeholder={k === "avoid" ? "sticker charts, being rushed" : "diagrams before symbols"}
                  />
                ) : BOOL.has(k) || typeof form[k] === "boolean" ? (
                  <Check
                    label={form[k] === true ? "on" : "off"}
                    checked={form[k] === true || form[k] === "true"}
                    onChange={(v) => setForm((f) => ({ ...f, [k]: v }))}
                  />
                ) : NUMERIC.has(k) ? (
                  <Input
                    type="number"
                    value={String(form[k] ?? "")}
                    onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
                  />
                ) : (
                  <Input value={String(form[k] ?? "")} onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} />
                )}
              </Field>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Btn kind="primary" onClick={savePrefs} disabled={busy || prefs.loading || !prefs.data}>
              {busy ? "…" : "Save"}
            </Btn>
            <span className="text-xs text-[var(--muted)]">These apply straight away and need no approval, because they are requests you made.</span>
          </div>
          {reply ? (
            <Notice tone={rejected.length ? "warn" : "good"}>
              {rejected.length
                ? "Some preferences could not be saved. Please review them and try again."
                : "Your preferences were saved."}
            </Notice>
          ) : null}
          {error ? <Err error={error} status={null} /> : null}
        </Card>

        <div className="space-y-4">
          <Card title="Voice" tone="mint">
            {!consent ? (
              <p role="status" className="text-sm text-[var(--muted)]">Your voice settings appear once you are signed in.</p>
            ) : (
              <div className="space-y-3">
                <Check
                  label="Voice"
                  checked={consent.voice}
                  onChange={(v) => setConsent((c) => c && ({ ...c, voice: v }))}
                  hint="Off means no microphone access at all. The keyboard path teaches the same thing. The top bar toggle turns voice on or off instantly."
                />
                {consent.voice ? (
                  <Field label="Voice for replies">
                    <Select value={consent.gender} onChange={(e) => setConsent((c) => c && ({ ...c, gender: e.target.value }))}>
                      <option value="female">female</option>
                      <option value="male">male</option>
                    </Select>
                  </Field>
                ) : null}
                {allowed.includes("want_voice") ? (
                  <Check
                    label="Start with voice on"
                    checked={form.want_voice === true || form.want_voice === "true"}
                    onChange={(v) => setForm((f) => ({ ...f, want_voice: v }))}
                  />
                ) : null}
                <Btn onClick={saveConsent} disabled={busy} kind="primary">Save consent</Btn>
              </div>
            )}
          </Card>

          <Card title="Language" tone="sand">
            <Field label="Language" hint="Lessons and replies follow this language.">
              <Input
                value={String(form.language ?? "")}
                onChange={(e) => setForm((f) => ({ ...f, language: e.target.value }))}
                placeholder="en · sw"
              />
            </Field>
            <div className="mt-3">
              <Btn kind="primary" onClick={savePrefs} disabled={busy || prefs.loading || !prefs.data}>Save</Btn>
            </div>
          </Card>

          <Card title="Consent" tone="peach">
            {!consent ? (
              <p role="status" className="text-sm text-[var(--muted)]">
                {unknown ? "Could not load your consent settings. Use the session retry above." : sessionLoading || me ? "Loading your consent settings…" : "Your consent settings will appear once you are signed in."}
              </p>
            ) : <div className="space-y-3">
              <Check
                label="Keep the interaction log"
                checked={consent.analytics}
                onChange={(v) => setConsent((c) => c && ({ ...c, analytics: v }))}
                hint="Off means the server rejects the events, so no proposals can be built and no schedule can be derived. Practice keeps working."
              />
              <Check
                label="Allow a written-for-you summary when it would help"
                checked={consent.llm}
                onChange={(v) => setConsent((c) => c && ({ ...c, llm: v }))}
                hint="Off means every reply is written from your own material. A summary is shared only when this is on and a written-for-you reply is available."
              />
              <Check
                label="Voice"
                checked={consent.voice}
                onChange={(v) => setConsent((c) => c && ({ ...c, voice: v }))}
                hint="Off means no microphone access at all. The keyboard path teaches the same thing."
              />
              {consent.voice ? (
                <Field label="Voice for replies">
                  <Select
                    value={consent.gender}
                    onChange={(e) => setConsent((c) => c && ({ ...c, gender: e.target.value }))}
                  >
                    <option value="female">female</option>
                    <option value="male">male</option>
                  </Select>
                </Field>
              ) : null}
              <p className="text-xs text-[var(--muted)]">
                You can change these choices at any time. Your preferences and voice settings are saved when you select Save consent.
              </p>
              <Btn onClick={saveConsent} disabled={busy} kind="primary">
                Save consent
              </Btn>
            </div>}
          </Card>

          <Card title="Model key status" tone="plain">
            {status.error ? <Err error={status.error} status={status.status} /> : null}
            <ul className="space-y-1 text-xs text-[var(--muted)]">
              <li>{status.data?.lessons?.says ?? "Lessons: not reported"}</li>
              {status.data?.data_protection?.file_level ? <li>{status.data.data_protection.file_level}</li> : null}
              {status.data?.data_protection?.note ? <li>{status.data.data_protection.note}</li> : null}
              {status.data?.demo_mode ? <li>demo mode: writes need a passphrase</li> : null}
            </ul>
          </Card>
        </div>
      </div>

      <Card title="Privacy: what may be recorded about you" tone="plain" right={<span>what is recorded</span>}>
        <p className="text-sm">{privacy.data?.telemetry?.note ?? "The server owns this list; the client cannot add a field to it."}</p>
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <div>
            <p className="text-xs uppercase text-[var(--muted)]">Activity that may be recorded</p>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-sm">
              {recorded.map((item) => <li key={item}>{item}</li>)}
              {!recorded.length ? <li>Activity details could not be loaded just now.</li> : null}
            </ul>
          </div>
          <div>
            <p className="text-xs uppercase text-[var(--muted)]">Never collected</p>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-sm">
              {neverRecorded.map((item) => <li key={item}>{item}</li>)}
              {!neverRecorded.length ? <li>Details could not be loaded just now.</li> : null}
            </ul>
          </div>
        </div>
        <div className="mt-3 grid gap-2 text-xs text-[var(--muted)] md:grid-cols-2">
          {Object.entries(privacy.data?.sent_to_model ?? {}).map(([k, v]) => (
            <p key={k}>
              <span className="text-ink-2">if a summary is shared · {k.replace(/_/g, " ")}:</span> {v}
            </p>
          ))}
          {Object.entries(privacy.data?.voice ?? {}).map(([key, value]) => (
            <p key={key}>
              <span className="text-ink-2">{VOICE_LABELS[key] ?? "Voice"}:</span> {value}
            </p>
          ))}
        </div>
        <div className="mt-3 text-xs">
          <p className="uppercase text-[var(--muted)]">How saved information is protected</p>
          <p className="mt-1 text-[var(--muted)]">
            {protection?.file_level ?? "Protection details could not be loaded just now."}
          </p>
          {protectedFields.length || notProtectedFields.length ? (
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {protectedFields.length ? (
                <div>
                  <p className="text-[var(--muted)]">Protected information</p>
                  <ul className="mt-1 list-disc pl-4 text-[var(--muted)]">
                    {protectedFields.map((field) => <li key={field}>{field}</li>)}
                  </ul>
                </div>
              ) : null}
              {notProtectedFields.length ? (
                <div>
                  <p className="text-[var(--muted)]">Not encrypted</p>
                  <ul className="mt-1 list-disc pl-4 text-[var(--muted)]">
                    {notProtectedFields.map((field) => <li key={field}>{field}</li>)}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          <p className="mt-2 uppercase text-[var(--muted)]">What you can always do</p>
          <ul className="mt-1 list-disc pl-4 text-[var(--muted)]">
            {(privacy.data?.learner_controls ?? []).map((control) => (
              <li key={control}>{control}</li>
            ))}
          </ul>
        </div>
      </Card>

      <Card title="Everything this system has done to your account" tone="plain" right={<span>{entries.length} rows</span>}>
        {audit.error ? <Err error={audit.error} status={audit.status} /> : null}
        {entries.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            Empty until something happens: approving or revoking a change, signing in, or a deletion. It is not a log of your
            answers — it is the log of the system acting, which is the thing worth auditing.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="w-full text-sm">
              <thead className="text-xs text-[var(--muted)]">
                <tr>
                  <th className="text-left">when</th>
                  <th className="text-left">action</th>
                  <th className="text-left">object</th>
                  <th className="text-right">outcome</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={i} className="border-t border-[var(--line)]">
                    <td className="whitespace-nowrap py-1 text-xs text-[var(--muted)]">{e.created_at ? new Date(e.created_at).toLocaleString() : "—"}</td>
                    <td>{e.action}</td>
                    <td className="text-xs text-[var(--muted)]">{e.object ?? "—"}</td>
                    <td className="text-right">
                      <Tag tone={e.outcome === "ok" ? "good" : "bad"}>{e.outcome ?? "?"}</Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Delete this learner" tone="peach">
        {gone ? (
          <Notice tone="warn">{gone}</Notice>
        ) : (
          <>
            <p className="text-sm text-[var(--muted)]">
              Soft delete: the learner is removed from every list and the answers, events, hypotheses and approved changes stop
              being reachable as yours. The rows are purged by whoever runs the instance; if you need them physically gone today,
              that is a request for the operator, and this screen will not pretend otherwise.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="type DELETE to confirm" className="max-w-56" />
              <Btn
                kind="danger"
                disabled={confirm !== "DELETE" || busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const out = await del<{ deleted?: boolean; note?: string }>("me");
                    await post("logout", {}).catch(() => null);
                    setGone(out?.note ?? `deleted: ${String((out as { deleted?: boolean })?.deleted ?? "unknown")}. You are signed out.`);
                    refresh();
                  } catch (err) {
                    setError(msg(err));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Delete me
              </Btn>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function hintFor(k: string): string | undefined {
  switch (k) {
    case "push_me":
      return "turns rereading off and puts an attempt first";
    case "max_explanation_words": {
      return "the full explanation stays one click away";
    }
    case "weekly_minutes":
    case "session_minutes":
      return "decides how much goes in the plan, nothing else";
    case "want_review_reminders":
      return "this app cannot message you; it only decides what is in the queue when you arrive";
    case "encourage":
    case "avoid":
      return "free text, read by the tutor as a request rather than a trait";
    default:
      return undefined;
  }
}

function msg(err: unknown): string {
  const e = err as { shown?: string; message?: string };
  return e?.shown ?? e?.message ?? String(err);
}
