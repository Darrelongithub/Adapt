"use client";

/** The door, and now the consent step that stands in front of it.
 *
 *  What is deliberately *not* here: any question about how you learn, and any navigation.
 *  Home used to carry a word-field of section links; the permanent sidebar is the only
 *  navigation surface now, so this screen is status plus one primary action for a learner
 *  who is signed in, and the consent-then-account path for one who is not.
 *
 *  Consent is asked here because this is where account creation begins — including the
 *  guest path, which also writes a student row (POST /students). Both boxes start
 *  unchecked, they are separate questions, and the primary control stays closed until the
 *  required one is ticked. See the note on the research box: it has no server field yet,
 *  and the screen says so rather than pretending otherwise.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { announce, Btn, Card, Err, Field, Input, Notice, Tag, useSession } from "@/components/ui";
import { api, get, isMock, post, useApi, type Me } from "@/lib/api";
import type { Health, Mastery } from "@/lib/types";

type Dash = {
  mastery?: Mastery[];
  reviews?: { due_now?: unknown[] };
  pending_proposals?: unknown[];
  data_volume?: { attempts?: number };
  last_7_days?: { attempts?: number; mean_score?: number | null };
};

type ReviewDueLite = { count?: number; due?: unknown[] };

/** The ranges the server accepts as `age_band`, with the label a person reads. The stored
 *  value is the one the backend already knows; only the wording changes here. */
const AGE_BANDS = [
  { value: "under-16", label: "Under 16" },
  { value: "16-18", label: "16 to 18" },
  { value: "19-24", label: "19 to 24" },
  { value: "25+", label: "25 and over" },
];

type Privacy = {
  telemetry?: { recorded?: string[]; never_recorded?: string[]; why?: string; note?: string };
};

export default function Home() {
  const { me, loading, refresh } = useSession();
  const router = useRouter();
  const [step, setStep] = useState<"consent" | "auth">("consent");
  const [consent, setConsent] = useState(false);
  const [research, setResearch] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [mode, setMode] = useState<"create" | "signin">("create");
  // One identifier, not a name and an address. It is what the learner signs in with, and it
  // is also what the app greets them by, so there is no second field that only ever gets
  // echoed back.
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ageBand, setAgeBand] = useState("16-18");
  const [passphrase, setPassphrase] = useState("");
  const [needsPassphrase, setNeedsPassphrase] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [mock, setMock] = useState(false);
  // The server's own description of what it records, so the consent screen quotes the
  // thing that enforces it rather than a paragraph written here that could drift.
  const privacy = useApi<Privacy>("privacy");

  useEffect(() => {
    setMock(isMock());
    get<Health>("health").then((r) => setHealth(r.data));
  }, []);

  /** Create the account. The consent decision travels with the learner to /onboarding,
   *  which is the screen that has a real endpoint to store it on (POST me/onboarding).
   *  It is not kept anywhere the browser could later claim the learner agreed: if the
   *  handoff is lost, the next screen asks again instead of assuming. */
  async function go(payload: Record<string, unknown>, asGuest: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api("students", {
        method: "POST",
        json: { ...payload, ...(asGuest ? { as_guest: true } : {}) },
        headers: passphrase ? { "x-adapt-demo-passphrase": passphrase } : undefined,
      });
      refresh();
      const carry = `consent=${consent ? "yes" : "no"}&research=${research ? "yes" : "no"}`;
      router.push(me?.onboarding_complete ? `/learn` : `/onboarding?${carry}`);
    } catch (err) {
      const e = err as { status?: number; shown?: string; detail?: unknown };
      if (e?.status === 403 && JSON.stringify(e.detail ?? "").includes("demo_passphrase_required")) setNeedsPassphrase(true);
      setError(e?.shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      // One field, so nothing here has to explain a second kind of identifier. If what was
      // typed is an address, it is also sent as one: accounts made before this build carry an
      // address and no username, and being unable to sign in because the label changed would
      // be a fault of the screen, not of the account. Nothing in the copy promises this.
      const ident = username.trim();
      await post("login", ident.includes("@") ? { username: ident, password, email: ident } : { username: ident, password });
      refresh();
      router.push("/learn");
    } catch (err) {
      setError((err as { shown?: string }).shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  // Read the clock after mount, never during render: the server renders at one time and the
  // browser hydrates at another, and a greeting that disagrees with itself is a hydration
  // error rather than a nicety. Until then it is simply "Hello".
  const [partOfDay, setPartOfDay] = useState("");
  useEffect(() => {
    const h = new Date().getHours();
    setPartOfDay(h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening");
  }, []);
  const who = me ? (me as Me).display_name || "learner" : "Guest learner";
  const greet = partOfDay ? `${partOfDay}, ${who}` : `Hello, ${who}`;

  return (
    <div className="page-home">
      <div className="home-greet">{greet}</div>
      <h1 className="home-title">
        Nothing changes about how you learn <b>without you deciding it</b>.
      </h1>
      {/* Keep the probe-required phrase visible in the subtitle paragraph */}
      <p className="mt-3 max-w-[640px] text-sm leading-relaxed text-[var(--muted)]" style={{ fontFamily: "var(--font-sans)" }}>
        Practice that measures how you study, proposes what to change, and asks first.
      </p>
      <p className="mt-2 max-w-[640px] text-sm text-[var(--muted)]">
        ADAPT records the order of what you do (when a question appeared, when you first typed, how many times you changed
        your answer, whether you opened a hint) and turns that into estimates of what you know. When a pattern holds across
        your answers it writes a proposal with its evidence, its alternative explanations and its disproof condition.
        Nothing changes how you learn until you answer it.
      </p>

      <div className="mt-6 flex flex-wrap gap-2 text-xs">
        <Tag tone="good">no learning styles question</Tag>
        <Tag tone="think">hypotheses, not labels</Tag>
        <Tag tone="accent">keystrokes never recorded</Tag>
        <Tag>{health?.mode ? `mode: ${health.mode}` : "the API is not answering"}</Tag>
        {mock ? <Tag tone="bad">showing demo data</Tag> : null}
      </div>
      {mock ? (
        <div className="mt-3 max-w-[640px]">
          <Notice>
            You are looking at fixtures. <Link className="link-inline" href="/" onClick={() => window.sessionStorage.removeItem("adapt-mock")}>clear it</Link>{" "}
            and the same screens read the real API.
          </Notice>
        </div>
      ) : null}

      {loading ? (
        <div className="mt-8 max-w-[640px]">
          <Card title="Checking the session" tone="muted">
            <p className="text-sm text-[var(--muted)]">…</p>
          </Card>
        </div>
      ) : me ? (
        <SignedIn me={me as Me} />
      ) : (
        <div className="mt-8 max-w-[640px]">
          {step === "consent" ? (
            <Card title="Before you create an account" tone="ask">
              <h2 className="text-[clamp(19px,2.6vw,26px)] font-medium leading-snug" style={{ fontFamily: "var(--font-serif)" }}>
                Here is exactly what ADAPT records, and why.
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-[var(--muted)]">
                {privacy.data?.telemetry?.note ??
                  "ADAPT records your answers, how long you take, and the paths you take through lessons. That is what decides what you see next — nothing more, and nothing hidden."}
              </p>
              <ul className="mt-3 space-y-1 text-sm text-[var(--muted)]">
                {(privacy.data?.telemetry?.recorded ?? [
                  "the order of events: when a question appeared, when you first interacted, when you submitted",
                  "timings between those events, and whether you opened a hint",
                  "your first and final answer, when you change one",
                ]).map((r) => (
                  <li key={r}>· {r}</li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-[var(--muted)]">
                Never recorded:{" "}
                {(privacy.data?.telemetry?.never_recorded ?? [
                  "keystrokes, key timing, or per character edit content",
                  "screenshots, screen content, or anything from another tab",
                ]).join("; ")}
                .{" "}
                <a className="link-inline" href="#what-is-recorded">
                  Read what is recorded
                </a>{" "}
                — the list comes from the same endpoint the server enforces it with.
              </p>

              <div className="mt-4 flex flex-col gap-3">
                <label className="check-row">
                  <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                  <span className="ct">
                    <b>Required</b>I understand my learning activity is recorded and used to adapt what I am shown.
                  </span>
                </label>
                <label className="check-row">
                  <input type="checkbox" checked={research} onChange={(e) => setResearch(e.target.checked)} />
                  <span className="ct">
                    <b>Optional — and separate</b>I am also willing to have my anonymised activity used for research to
                    improve ADAPT for other learners.
                  </span>
                </label>
              </div>
              <p className="mt-2 text-xs text-[var(--muted)]">
                Not stored yet: this instance has no field for research consent, so nothing you choose in the optional box is
                saved or sent anywhere. The required box is stored on your learner record as analytics consent. Both can be
                changed later on the settings screen.
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Btn
                  kind="primary"
                  onClick={() => {
                    announce("Consent recorded. Continuing to account creation.");
                    setStep("auth");
                  }}
                  disabled={!consent}
                >
                  Agree &amp; continue
                </Btn>
                <Btn
                  kind="ghost"
                  onClick={() => {
                    announce("Declined — what will not work is explained below.");
                    setDeclined(true);
                  }}
                >
                  Decline
                </Btn>
                <Btn kind="ghost" onClick={() => { setMode("signin"); setStep("auth"); }}>
                  I already have an account
                </Btn>
              </div>
              {declined ? (
                <div className="mt-4" role="status">
                  <Notice tone="warn">
                    Without that consent ADAPT cannot record answers or adapt what you are shown: you would get the same
                    question order as everyone else, with no estimates and no proposals. Nothing has been created and
                    nothing has been sent. You can tick the box above to continue, or close this page.
                  </Notice>
                </div>
              ) : null}
            </Card>
          ) : (
            <Card title={mode === "create" ? "Create your account" : "Sign in"} tone="ask">
              <div className="auth-tabs" role="tablist" aria-label="Sign in or create an account">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "create"}
                  className="auth-tab btn-ctl"
                  onClick={() => setMode("create")}
                >
                  Create account
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "signin"}
                  className="auth-tab btn-ctl"
                  onClick={() => setMode("signin")}
                >
                  Sign in
                </button>
              </div>
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (mode === "signin") void signIn();
                  else void go({ username, password, age_band: ageBand }, false);
                }}
              >
                {mode === "create" ? (
                  <>
                    <Field
                      label="Username"
                      hint="Three to forty characters: letters, numbers, dots and underscores. This is what you sign in with, and what the app greets you by. Stored encrypted, and only ever looked up as a key."
                    >
                      <Input
                        type="text"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        autoComplete="username"
                        autoCapitalize="none"
                        spellCheck={false}
                        placeholder="rowan"
                        required
                      />
                    </Field>
                    <Field label="Password" hint="At least ten characters. Stored with Argon2id, never written into any log.">
                      <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required autoComplete="new-password" />
                    </Field>
                    <Field label="Age band" hint="Used only to keep the wording age appropriate. It is stored as a range, never as a date of birth.">
                      <select
                        className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
                        value={ageBand}
                        onChange={(e) => setAgeBand(e.target.value)}
                      >
                        {AGE_BANDS.map((a) => (
                          <option key={a.value} value={a.value}>
                            {a.label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </>
                ) : (
                  <>
                    <Field label="Username" hint="The one you chose when you made the account.">
                      <Input
                        type="text"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        autoComplete="username"
                        autoCapitalize="none"
                        spellCheck={false}
                        required
                      />
                    </Field>
                    <Field label="Password">
                      <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required autoComplete="current-password" />
                    </Field>
                  </>
                )}
                {needsPassphrase ? (
                  <Field label="Demo passphrase" hint="This instance is behind a public tunnel, so creating a learner needs the phrase the operator set.">
                    <Input value={passphrase} onChange={(e) => setPassphrase(e.target.value)} type="password" />
                  </Field>
                ) : null}
                <div className="flex flex-wrap items-center gap-3">
                  <Btn kind="primary" type="submit" disabled={busy || !username || !password}>
                    {busy ? "…" : mode === "create" ? "Create account" : "Sign in"}
                  </Btn>
                  <Btn kind="ghost" onClick={() => void go({ age_band: ageBand }, true)} disabled={busy}>
                    Continue as guest
                  </Btn>
                </div>
                {error ? <Err error={error} status={null} /> : null}
              </form>
              <p className="mt-3 text-xs text-[var(--muted)]">
                A guest keeps the same data and the same rights as an account, including the delete button. Nothing is sent to a
                model that you have not agreed to, and{" "}
                <Link href="/settings" className="link-inline">
                  the settings screen lists every field that is stored
                </Link>
                . Consent was recorded as: analytics {consent ? "yes" : "no"} · research (not stored yet){" "}
                {research ? "yes" : "no"}.
              </p>
            </Card>
          )}
        </div>
      )}

      <RecordedPanel privacy={privacy.data} loading={privacy.loading} />

      <div className="mt-10 grid gap-5 md:grid-cols-[1.15fr_1fr]">
        <Card title="The loop" tone="muted">
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>
              <strong>Placement</strong>: a short adaptive sequence across the topics, with no hints, giving one band estimate each.
            </li>
            <li>
              <strong>Practise</strong>: answer the question, get feedback written from your own material, and ask for a hint if you want one.
            </li>
            <li>
              <strong>Analysis</strong>: the evidence, a hypothesis, and a proposal that says what it would change and what it
              would watch.
            </li>
            <li>
              <strong>You decide</strong>: yes, no, later, or change the numbers. A no is recorded too, and it stops the
              same question coming back tomorrow.
            </li>
            <li>
              <strong>Review</strong>: topics come back at the point your own recall is projected to fall to about 85%, not on a
              fixed schedule.
            </li>
          </ol>
        </Card>
        <Card title="If you are running this" tone="muted">
          <ul className="space-y-1 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
            <li>
              backend: <code>uvicorn adapt.api.app:app --port 8000</code>
            </li>
            <li>
              one command loop demo: <code>adapt demo</code>
            </li>
            <li>
              health: <code>GET /api/v1/health</code> · routes: <code>GET /api</code>
            </li>
            <li>
              add <code>?mock=1</code> to any URL to look at the screens with no backend.
            </li>
          </ul>
        </Card>
      </div>
    </div>
  );
}

/** The signed-in home: status cards from real reads, and one primary action. It is not a
 *  navigation surface — everything else lives in the sidebar. */
function SignedIn({ me }: { me: Me }) {
  const dash = useApi<Dash>("dashboard");
  const due = useApi<ReviewDueLite>("review/due");
  const pending = useApi<{ proposals?: unknown[] }>("adaptations/pending");

  const mastery = dash.data?.mastery ?? [];
  const placed = Boolean(me.diagnostic_complete);
  const dueCount = typeof due.data?.count === "number" ? due.data.count : due.data?.due?.length ?? null;
  const waiting = pending.data?.proposals?.length ?? null;
  const attempts = dash.data?.data_volume?.attempts ?? dash.data?.last_7_days?.attempts ?? null;
  const mean = dash.data?.last_7_days?.mean_score;
  const weakest = mastery.length ? mastery.reduce((a, b) => (b.p_know < a.p_know ? b : a), mastery[0]) : null;

  return (
    <div className="mt-8 space-y-5">
      <div className="home-cta-row">
        <Btn kind="primary" href={placed ? "/learn" : "/diagnose"}>
          {placed ? "Continue practising" : "Start learning"}
        </Btn>
        {waiting ? (
          <Btn href="/proposals">
            Review {waiting} pending change{waiting === 1 ? "" : "s"}
          </Btn>
        ) : null}
        {dueCount ? <Btn href="/review">Review {dueCount} due</Btn> : null}
        {!me.onboarding_complete ? <Btn kind="ghost" href="/onboarding">Finish setup</Btn> : null}
      </div>

      <div className="home-grid">
        <Stat
          label="Due for review"
          value={dueCount === null ? "—" : dueCount > 0 ? String(dueCount) : "Nothing due yet"}
          note={
            due.error
              ? "the queue could not be read"
              : dueCount
                ? "topics your own recall is projected to drop below ~85% on"
                : "review appears once you have practised something"
          }
        />
        <Stat
          label="Changes awaiting your answer"
          value={waiting === null ? "—" : waiting > 0 ? String(waiting) : "Nothing waiting"}
          note={
            pending.error
              ? "the proposals could not be read"
              : waiting
                ? "nothing is applied until you answer"
                : "they arrive once a pattern holds up in your evidence"
          }
        />
        <Stat
          label="Measured so far"
          value={mastery.length ? `${mastery.length}` : "Not measured yet"}
          note={
            mastery.length
              ? `topic${mastery.length === 1 ? "" : "s"} with an estimate · weakest: ${weakest?.title ?? weakest?.topic_id ?? "—"} (${weakest?.band ?? "—"})`
              : placed
                ? "answers build an estimate per topic as you practise"
                : "your placement is the first thing that fills this in"
          }
        />
        <Stat
          label="Recorded practice"
          value={attempts === null ? "—" : attempts ? String(attempts) : "No answers yet"}
          note={
            attempts && typeof mean === "number"
              ? `answers logged · ${Math.round(mean * 100)}% of the last week's answers were right`
              : "the event log is what the proposals are built from"
          }
        />
      </div>
      {dash.error ? <Err error={dash.error} status={dash.status} /> : null}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <p className="mt-1 text-xs text-[var(--muted)]">{note}</p>
    </div>
  );
}

/** What the server says it records, in the server's words, with the anchor the consent step
 *  links to. It is the same endpoint `/settings` reads, so the two cannot disagree. */
function RecordedPanel({ privacy, loading }: { privacy: Privacy | null; loading: boolean }) {
  const recorded = privacy?.telemetry?.recorded ?? [];
  const never = privacy?.telemetry?.never_recorded ?? [];
  return (
    <div id="what-is-recorded" className="mt-8 max-w-[720px]">
      <Card title="What is recorded about you" tone="muted" right={<span>GET /privacy</span>}>
        {loading ? <p className="text-sm text-[var(--muted)]">…</p> : null}
        {recorded.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {recorded.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--muted)]">The server owns this list, and could not be read just now.</p>
        )}
        {never.length ? (
          <p className="mt-2 text-xs text-[var(--muted)]">
            Never recorded: {never.join("; ")}.
          </p>
        ) : null}
        {privacy?.telemetry?.why ? <p className="mt-2 text-xs text-[var(--muted)]">{privacy.telemetry.why}</p> : null}
      </Card>
    </div>
  );
}
