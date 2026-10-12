"use client";

/** The door. Three ways in, because the pilot runs on a school laptop where nobody has
 *  an account yet: a guest learner (nothing to remember), email + password, or the demo
 *  passphrase if the operator set one.
 *
 *  What is deliberately *not* here: any question about how you learn. The blueprint
 *  forbids the learning-styles inventory, and the onboarding screen explains that
 *  measurement happens during practice instead.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Btn, Card, Err, Field, Input, Notice, Tag, useSession } from "@/components/ui";
import { api, get, isMock, post, type Me } from "@/lib/api";
import type { Health } from "@/lib/types";

const WORD_FIELD = [
  { href: "/learn", label: "Practise", n: "01", size: "clamp(28px,4vw,52px)" },
  { href: "/lesson", label: "Lessons", n: "02", size: "clamp(20px,2.6vw,34px)" },
  { href: "/proposals", label: "Changes", n: "03", size: "clamp(22px,3vw,40px)" },
  { href: "/review", label: "Review", n: "04", size: "clamp(18px,2.4vw,30px)" },
  { href: "/progress", label: "Progress", n: "05", size: "clamp(24px,3.4vw,46px)" },
  { href: "/voice", label: "Voice", n: "06", size: "clamp(16px,2vw,26px)" },
  { href: "/settings", label: "Settings", n: "07", size: "clamp(14px,1.8vw,22px)" },
];

export default function Home() {
  const { me, loading, refresh } = useSession();
  const router = useRouter();
  const [mode, setMode] = useState<"guest" | "account">("guest");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [ageBand, setAgeBand] = useState("16-18");
  const [passphrase, setPassphrase] = useState("");
  const [needsPassphrase, setNeedsPassphrase] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [mock, setMock] = useState(false);

  useEffect(() => {
    setMock(isMock());
    get<Health>("health").then((r) => setHealth(r.data));
  }, []);

  async function go(payload: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api("students", {
        method: "POST",
        json: payload,
        headers: passphrase ? { "x-adapt-demo-passphrase": passphrase } : undefined,
      });
      refresh();
      router.push(me?.onboarding_complete ? "/learn" : "/onboarding");
    } catch (err) {
      const e = err as { status?: number; shown?: string; detail?: unknown };
      if (e?.status === 403 && JSON.stringify(e.detail ?? "").includes("demo_passphrase_required")) setNeedsPassphrase(true);
      setError(e?.shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const greet = me ? `Good evening, ${(me as Me).display_name || "learner"}` : "Good evening, Guest learner";

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
        ADAPT records the order of what you do — when a question appeared, when you first typed, how many times you changed
        your answer, whether you opened a hint — and turns that into estimates of what you know. When a pattern is
        consistent it writes a proposal with its evidence, its alternative explanations and its disproof condition. Nothing
        changes how you learn until you answer it.
      </p>

      <nav className="word-field" aria-label="Jump to a section">
        {WORD_FIELD.map((w) => (
          <Link key={w.href} href={w.href} className="wf-item" style={{ fontSize: w.size }}>
            <span className="n">{w.n}</span>
            {w.label}
          </Link>
        ))}
      </nav>

      <div className="mt-8 flex flex-wrap gap-2 text-xs">
        <Tag tone="good">no learning-styles question</Tag>
        <Tag tone="think">hypotheses, not labels</Tag>
        <Tag tone="accent">keystrokes never recorded</Tag>
        <Tag>{health?.mode ? `mode: ${health.mode}` : "api not answering"}</Tag>
        {mock ? <Tag tone="bad">showing demo data</Tag> : null}
      </div>
      {mock ? (
        <div className="mt-3 max-w-[640px]">
          <Notice>
            You are looking at fixtures. <Link className="underline" href="/" onClick={() => window.sessionStorage.removeItem("adapt-mock")}>clear it</Link>{" "}
            and the same screens read the real API.
          </Notice>
        </div>
      ) : null}

      <div className="mt-10 grid gap-5 md:grid-cols-[1.15fr_1fr]">
        <div className="space-y-4">
          <Card title="The loop" tone="muted">
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              <li>
                <strong>Placement</strong> — twelve questions across the topics, no hints, one band estimate each.
              </li>
              <li>
                <strong>Practise</strong> — answer, get feedback written from your own material, ask for a hint if you want.
              </li>
              <li>
                <strong>Analysis</strong> — evidence, a hypothesis, and a proposal that says what it would change and what it
                would watch.
              </li>
              <li>
                <strong>You decide</strong> — yes, no, later, or change the numbers. A no is recorded and stops the same
                question coming back tomorrow.
              </li>
              <li>
                <strong>Review</strong> — topics come back at the point your own recall is projected to fall to ~85%, not on a
                fixed schedule.
              </li>
            </ol>
          </Card>
        </div>

        <div className="space-y-4">
          {loading ? (
            <Card title="Checking the session" tone="muted">
              <p className="text-sm text-[var(--muted)]">…</p>
            </Card>
          ) : me ? (
            <Card title={`Signed in as ${(me as Me).display_name || (me as Me).student_id}`} tone="good">
              <div className="space-y-3 text-sm">
                <p className="text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}>
                  {(me as Me).sessions} session{(me as Me).sessions === 1 ? "" : "s"} so far ·{" "}
                  {(me as Me).onboarding_complete ? "onboarding done" : "onboarding not finished"} ·{" "}
                  {(me as Me).diagnostic_complete ? "placed" : "not placed yet"}
                </p>
                <div className="flex flex-wrap gap-2">
                  {(me as Me).onboarding_complete ? null : <Btn kind="primary" href="/onboarding">Finish setup</Btn>}
                  <Btn kind={(me as Me).diagnostic_complete ? "quiet" : "primary"} href={(me as Me).diagnostic_complete ? "/learn" : "/diagnose"}>
                    {(me as Me).diagnostic_complete ? "Practise" : "Start placement"}
                  </Btn>
                  <Btn href="/proposals">Changes waiting</Btn>
                  <Btn href="/review">Review queue</Btn>
                </div>
              </div>
            </Card>
          ) : (
            <Card title={mode === "guest" ? "Start as a guest" : "Sign in"} tone="ask">
              <div className="mb-3 flex gap-2 text-xs">
                <button
                  type="button"
                  className={`rounded-[2px] border px-2 py-1 ${mode === "guest" ? "border-[var(--accent)]" : "border-[var(--line)] text-[var(--muted)]"}`}
                  onClick={() => setMode("guest")}
                  style={{ fontFamily: "var(--font-mono)" }}
                >
                  guest
                </button>
                <button
                  type="button"
                  className={`rounded-[2px] border px-2 py-1 ${mode === "account" ? "border-[var(--accent)]" : "border-[var(--line)] text-[var(--muted)]"}`}
                  onClick={() => setMode("account")}
                  style={{ fontFamily: "var(--font-mono)" }}
                >
                  email + password
                </button>
              </div>
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (mode === "guest") void go({ display_name: name || "Guest learner", age_band: ageBand, as_guest: true });
                  else void go({ display_name: name || email.split("@")[0], email, password });
                }}
              >
                {mode === "guest" ? (
                  <>
                    <Field label="A name" hint="Anything. A guest learner is stored the same way; you can add an email later.">
                      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Rowan" />
                    </Field>
                    <Field label="Age band" hint="Used only to keep the wording age-appropriate.">
                      <select
                        className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
                        value={ageBand}
                        onChange={(e) => setAgeBand(e.target.value)}
                      >
                        {["under-16", "16-18", "19-24", "25+"].map((a) => (
                          <option key={a} value={a}>
                            {a}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </>
                ) : (
                  <>
                    <Field label="Email">
                      <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required autoComplete="username" />
                    </Field>
                    <Field label="Password" hint="At least 10 characters. Argon2id, and the address itself is encrypted at rest.">
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
                  <Btn kind="primary" type="submit" disabled={busy}>
                    {busy ? "…" : mode === "guest" ? "Start learning" : name || email ? "Register" : "Sign in"}
                  </Btn>
                  {mode === "account" ? (
                    <span className="text-xs text-[var(--muted)]">
                      already registered?{" "}
                      <button
                        type="button"
                        className="underline"
                        onClick={() => {
                          setBusy(true);
                          setError(null);
                          post("login", { email, password })
                            .then(() => {
                              refresh();
                              router.push("/learn");
                            })
                            .catch((err) => setError((err as Error).message))
                            .finally(() => setBusy(false));
                        }}
                      >
                        sign in
                      </button>
                    </span>
                  ) : null}
                </div>
                {error ? <Err error={error} status={null} /> : null}
              </form>
              <p className="mt-3 text-xs text-[var(--muted)]">
                A guest keeps the same data and the same rights as an account, including the delete button. Nothing is sent to a
                model that you have not agreed to, and{" "}
                <Link href="/settings" className="underline">
                  the settings screen lists every field that is stored
                </Link>
                .
              </p>
            </Card>
          )}
          <Card title="If you are running this" tone="muted">
            <ul className="space-y-1 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
              <li>
                backend: <code>uvicorn adapt.api.app:app --port 8000</code>
              </li>
              <li>
                one-command loop demo: <code>adapt demo</code>
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
    </div>
  );
}
