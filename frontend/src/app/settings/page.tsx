"use client";

/** Settings, privacy and the exit.
 *
 * The rule for this screen: the app never claims a protection it has not implemented, so
 * every claim here is read from the server that enforces it — `GET /privacy` for what may
 * be recorded, `GET /events/allowlist` for the exact event types, `GET /status` for whether
 * the database is encrypted at rest, `GET /audit` for every action the system took on your
 * account. Toggles do not merely store a wish: turning consent off stops the write.
 */
import { useEffect, useState } from "react";

import { Btn, Card, Check, Err, Field, humanize, Input, Notice, Tag, useSession } from "@/components/ui";
import { del, get, post, useApi } from "@/lib/api";

type PrefReply = { accepted?: Record<string, unknown>; rejected_keys?: string[]; rejected_reasons?: Record<string, string>; note?: string };
type Allowlist = { allowed: Record<string, string[]>; forbidden_keys: string[]; rule: string };
type Privacy = {
  telemetry?: { allowed_event_types?: string[]; forbidden_keys?: string[]; note?: string } & Record<string, unknown>;
  sent_to_gemini?: Record<string, string>;
  voice?: Record<string, string>;
  storage?: Record<string, unknown>;
  learner_controls?: string[];
};

const NUMERIC = new Set(["weekly_minutes", "session_minutes", "max_explanation_words", "items_per_session"]);
const BOOL = new Set(["push_me", "want_voice", "want_review_reminders"]);
/** Keys the server stores as a list of short phrases. They have to be typed, not ticked: a
 *  checkbox here used to post `true`, which the server wrapped into the phrase "True" and put
 *  into the learner's profile. */
const PHRASES = new Set(["avoid", "encourage"]);

/** One text box in, a list out -- commas, semicolons and new lines all separate phrases. */
function toPhrases(value: string): string[] {
  return value.split(/[,;\n]/).map((part) => part.trim()).filter(Boolean).slice(0, 8);
}

export default function Settings() {
  const { refresh } = useSession();
  const prefs = useApi<{ preferences: Record<string, unknown>; allowed_keys: string[] }>("me/preferences");
  const privacy = useApi<Privacy>("privacy");
  const allow = useApi<Allowlist>("events/allowlist");
  const status = useApi<Record<string, unknown>>("status");
  const audit = useApi<{ entries: { action: string; object?: string; outcome?: string; created_at?: string | null }[] }>("audit", { limit: 50 });
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [reply, setReply] = useState<PrefReply | null>(null);
  const [consent, setConsent] = useState({ analytics: true, llm: true, voice: false, gender: "female" });
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
    get<{ consent_analytics?: boolean; consent_share_with_ai?: boolean; voice_enabled?: boolean; voice_gender?: string }>("me").then((r) => {
      if (!r.data) return;
      setConsent({
        analytics: Boolean(r.data.consent_analytics),
        llm: Boolean(r.data.consent_share_with_ai),
        voice: Boolean(r.data.voice_enabled),
        gender: r.data.voice_gender || "female",
      });
    });
  }, []);

  async function savePrefs() {
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

  return (
    <div className="space-y-4">
      <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>§ 06 · Settings</div>
      <h1 className="text-xl" style={{ fontFamily: "var(--font-serif)" }}>Settings, and what is kept</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Your preferences" right={<span>{allowed.length} keys the server accepts</span>}>
          {prefs.error ? <Err error={prefs.error} status={prefs.status} /> : null}
          <div className="space-y-2">
            {allowed.map((k) => (
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
                  <input
                    type="number"
                    className="w-full rounded-md border border-[var(--line)] bg-black/25 px-3 py-2 text-sm"
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
            <Btn kind="primary" onClick={savePrefs} disabled={busy}>
              {busy ? "…" : "Save"}
            </Btn>
            <span className="text-xs text-[var(--muted)]">these apply now, without approval — they are requests you made</span>
          </div>
          {reply ? (
            <Notice tone={rejected.length ? "warn" : "good"}>
              {Object.keys(reply.accepted ?? {}).length} stored
              {rejected.length ? (
                <>
                  {" "}
                  · refused:{" "}
                  {rejected.map((k) => (
                    <Tag key={k} tone="bad">
                      {k} — {reply.rejected_reasons?.[k] ?? "unknown key"}
                    </Tag>
                  ))}
                </>
              ) : null}
            </Notice>
          ) : null}
          {error ? <Err error={error} status={null} /> : null}
        </Card>

        <div className="space-y-4">
          <Card title="Consent" tone="warn">
            <div className="space-y-3">
              <Check
                label="Keep the interaction log"
                checked={consent.analytics}
                onChange={(v) => setConsent((c) => ({ ...c, analytics: v }))}
                hint="Off means the server rejects the events, so no proposals can be built and no schedule can be derived. Practice keeps working."
              />
              <Check
                label="Allow a model summary when it would help"
                checked={consent.llm}
                onChange={(v) => setConsent((c) => ({ ...c, llm: v }))}
                hint={`Currently: ${humanize(status.data?.llm ?? {}) || "no model configured"}`}
              />
              <Check
                label="Voice"
                checked={consent.voice}
                onChange={(v) => setConsent((c) => ({ ...c, voice: v }))}
                hint="Off means no microphone access at all. The keyboard path teaches the same thing."
              />
              {consent.voice ? (
                <Field label="Voice for replies">
                  <select
                    className="w-full rounded-md border border-[var(--line)] bg-black/25 px-3 py-2 text-sm"
                    value={consent.gender}
                    onChange={(e) => setConsent((c) => ({ ...c, gender: e.target.value }))}
                  >
                    <option value="female">female</option>
                    <option value="male">male</option>
                  </select>
                </Field>
              ) : null}
              <Btn onClick={saveConsent} disabled={busy} kind="primary">
                Save consent
              </Btn>
            </div>
          </Card>

          <Card title="This instance" tone="muted" right={<Tag>{String(status.data?.mode ?? "?")}</Tag>}>
            {status.error ? <Err error={status.error} status={status.status} /> : null}
            <ul className="space-y-1 text-xs text-[var(--muted)]">
              {Object.entries(status.data ?? {}).map(([k, v]) => (
                <li key={k}>
                  <span className="text-[var(--fg)]/70">{k.replace(/_/g, " ")}:</span> {typeof v === "object" ? humanize(v) : String(v)}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <Card title="What may be recorded about you" tone="muted" right={<span>GET /privacy · /events/allowlist</span>}>
        <p className="text-sm">{privacy.data?.telemetry?.note ?? "The server owns this list; the client cannot add a field to it."}</p>
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <div>
            <p className="text-xs uppercase text-[var(--muted)]">event types the server accepts ({(privacy.data?.telemetry?.allowed_event_types ?? []).length})</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {(privacy.data?.telemetry?.allowed_event_types ?? Object.keys(allow.data?.allowed ?? {})).map((t) => (
                <Tag key={t}>{t}</Tag>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs uppercase text-[var(--muted)]">fields that are rejected outright</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {(privacy.data?.telemetry?.forbidden_keys ?? allow.data?.forbidden_keys ?? []).map((t) => (
                <Tag key={t} tone="bad">
                  {t}
                </Tag>
              ))}
            </div>
            <p className="mt-1 text-xs text-[var(--muted)]">{allow.data?.rule}</p>
          </div>
        </div>
        <div className="mt-3 grid gap-2 text-xs text-[var(--muted)] md:grid-cols-2">
          {Object.entries(privacy.data?.sent_to_gemini ?? {}).map(([k, v]) => (
            <p key={k}>
              <span className="text-[var(--fg)]/70">to the model · {k.replace(/_/g, " ")}:</span> {v}
            </p>
          ))}
          {Object.entries(privacy.data?.voice ?? {}).map(([k, v]) => (
            <p key={k}>
              <span className="text-[var(--fg)]/70">voice · {k.replace(/_/g, " ")}:</span> {v}
            </p>
          ))}
        </div>
        <div className="mt-3 text-xs">
          <p className="uppercase text-[var(--muted)]">storage</p>
          <p className="mt-1 text-[var(--muted)]">{humanize(privacy.data?.storage ?? {})}</p>
          <p className="mt-2 uppercase text-[var(--muted)]">what you can always do</p>
          <ul className="mt-1 list-disc pl-4 text-[var(--muted)]">
            {(privacy.data?.learner_controls ?? []).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      </Card>

      <Card title="Everything this system has done to your account" tone="muted" right={<span>{entries.length} rows</span>}>
        {audit.error ? <Err error={audit.error} status={audit.status} /> : null}
        {entries.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            Empty until something happens: approving or revoking a change, a sign-in, a deletion. It is not a log of your
            answers — it is the log of the system acting, which is the thing worth auditing.
          </p>
        ) : (
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
                  <td className="py-1 text-xs text-[var(--muted)]">{e.created_at ? new Date(e.created_at).toLocaleString() : "—"}</td>
                  <td>{e.action}</td>
                  <td className="text-xs text-[var(--muted)]">{e.object ?? "—"}</td>
                  <td className="text-right">
                    <Tag tone={e.outcome === "ok" ? "good" : "bad"}>{e.outcome ?? "?"}</Tag>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Delete this learner" tone="warn">
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
      return "turns re-reading off and puts an attempt first";
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
