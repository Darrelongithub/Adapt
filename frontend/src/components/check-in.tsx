"use client";

/**
 * The 10-second check-in (Round 9, Track M): "How is this going?"
 *
 * Three tiles and an optional line -- effort from 1 (very light) to 9
 * (very heavy), one feeling about this sitting, one "what would help".
 * What comes back is an estimate for this sitting with its evidence,
 * what it will do to the next lesson in the learner's words, and -- only
 * after a hard sitting, only when the operator turned it on -- general
 * wellbeing reading links with their attribution. Nothing here scores
 * the person, and the note is theirs to read back or remove.
 *
 * Prompting is once per browser sitting at most (the server's prompt
 * check decides); the collapsed card is always there to open on demand.
 */

import { useEffect, useRef, useState } from "react";

import { Btn, Card, Notice } from "./ui";
import { Icon } from "./icons";
import { api, del, get, post } from "@/lib/api";
import { EFFORT_WORDS, EMOTIONS, HELP_CHOICES, type CheckInReply } from "@/lib/round9";
import "./check-in.css";

type Context = "after_test" | "after_lesson" | "home" | "other";

const SITTING_KEY = "adapt:checkin-sitting";
const ASKED_KEY = "adapt:checkin-asked";

function wasAsked(): boolean {
  try {
    return window.sessionStorage.getItem(ASKED_KEY) === "1";
  } catch {
    return false;
  }
}

function markAsked() {
  try {
    window.sessionStorage.setItem(ASKED_KEY, "1");
  } catch {
    /* A browser that refuses storage just means we may ask again. */
  }
}

function sittingId(): string {
  if (typeof window === "undefined") return "sitting";
  let value = window.sessionStorage.getItem(SITTING_KEY);
  if (!value) {
    value = `sitting-${Date.now().toString(36)}`;
    window.sessionStorage.setItem(SITTING_KEY, value);
  }
  return value;
}

export function CheckInCard({
  context = "other",
  subjectId = null,
  autoPrompt = false,
  title = "How is this going?",
}: {
  context?: Context;
  subjectId?: string | null;
  /** Open by itself when this sitting has not been asked yet. */
  autoPrompt?: boolean;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [effort, setEffort] = useState<number | null>(null);
  const [emotion, setEmotion] = useState<string | null>(null);
  const [help, setHelp] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<CheckInReply | null>(null);
  const asked = useRef(false);

  useEffect(() => {
    if (!autoPrompt || asked.current) return;
    asked.current = true;
    // The server knows about completed check-ins; this flag remembers a
    // prompt the learner already saw (and maybe dismissed) this sitting.
    if (wasAsked()) return;
    void get<{ show: boolean }>("check-in/prompt", { session_id: sittingId() }).then((reply) => {
      if (reply.data?.show) {
        markAsked();
        setOpen(true);
      }
    });
  }, [autoPrompt]);

  async function submit() {
    if (effort === null || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const out = await post<CheckInReply>("check-in", {
        effort,
        emotion,
        help,
        note: note.trim(),
        context,
        session_id: sittingId(),
        subject_id: subjectId,
      });
      setResult(out);
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That could not be saved just now.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!result || busy) return;
    setBusy(true);
    try {
      await del(`check-in/${encodeURIComponent(result.id)}`);
      setResult(null);
      setEffort(null);
      setEmotion(null);
      setHelp(null);
      setNote("");
      setOpen(false);
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That could not be removed just now.");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const next = result.next_lesson;
    const n = result.metrics?.n_recent ?? 0;
    return (
      <Card title="Thanks — noted" tone="mint" icon={<Icon name="message" />}>
        <div className="stack-3" data-testid="check-in-done">
          <p>{result.estimate?.summary || "Saved for this sitting."}</p>
          <p className="t-small" style={{ color: "var(--muted)" }}>
            An estimate for this sitting, from your taps{n ? ` and your last ${n} answers` : ""}. You can read or
            remove it any time under what is recorded about you.
          </p>
          {next?.changed ? (
            <Notice tone="info">
              Next lesson: {next.reason}
              {next.break_suggested ? " A pause might help too — nothing is paused for you." : ""}
            </Notice>
          ) : null}
          {result.read_more?.length ? (
            <div className="check-in-reading stack-2">
              <p className="t-h3">Read more</p>
              {result.read_more.map((link) => (
                <p key={link.url}>
                  <a className="link-inline" href={link.url} target="_blank" rel="noreferrer">
                    {link.title}
                  </a>{" "}
                  <span className="t-small" style={{ color: "var(--muted)" }}>
                    {link.attribution}
                  </span>
                </p>
              ))}
            </div>
          ) : null}
          <div className="wrap-2">
            <Btn kind="ghost" disabled={busy} onClick={() => void remove()}>
              Remove this check-in
            </Btn>
          </div>
        </div>
      </Card>
    );
  }

  if (!open) {
    return (
      <Card title={title} tone="sand" icon={<Icon name="message" />}>
        <div className="wrap-2" data-testid="check-in-open">
          <p style={{ margin: 0 }}>Ten seconds. It only shapes your next lesson.</p>
          <Btn kind="secondary" onClick={() => setOpen(true)}>
            Check in
          </Btn>
        </div>
      </Card>
    );
  }

  return (
    <Card title={title} tone="sand" icon={<Icon name="message" />}>
      <form
        className="stack-4"
        data-testid="check-in-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {notice ? <Notice tone="warn">{notice}</Notice> : null}
        <div className="stack-2">
          <p className="t-h3" id="check-in-effort-label">
            How much effort did that take?
          </p>
          <div className="check-in-effort" role="group" aria-labelledby="check-in-effort-label">
            {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((value) => (
              <button
                key={value}
                type="button"
                className="tap-chip"
                aria-pressed={effort === value}
                aria-label={`Effort ${value} of 9, ${EFFORT_WORDS[value].toLowerCase()}`}
                onClick={() => setEffort(value)}
              >
                {value}
              </button>
            ))}
          </div>
          <div className="check-in-ends t-small">
            <span>Very light</span>
            <span>{effort ? EFFORT_WORDS[effort] : ""}</span>
            <span>Very heavy</span>
          </div>
        </div>

        <div className="stack-2">
          <p className="t-h3">How did it feel? <span className="t-small" style={{ color: "var(--muted)" }}>(optional)</span></p>
          <div className="check-in-tiles" role="group" aria-label="How it felt">
            {EMOTIONS.map((row) => (
              <button
                key={row.id}
                type="button"
                className="tap-chip"
                aria-pressed={emotion === row.id}
                onClick={() => setEmotion(emotion === row.id ? null : row.id)}
              >
                {row.label}
              </button>
            ))}
          </div>
        </div>

        <div className="stack-2">
          <p className="t-h3">What would help next? <span className="t-small" style={{ color: "var(--muted)" }}>(optional)</span></p>
          <div className="check-in-tiles" role="group" aria-label="What would help">
            {HELP_CHOICES.map((row) => (
              <button
                key={row.id}
                type="button"
                className="tap-chip"
                aria-pressed={help === row.id}
                onClick={() => setHelp(help === row.id ? null : row.id)}
              >
                {row.label}
              </button>
            ))}
          </div>
        </div>

        <label className="stack-2">
          <span className="t-h3">One line for yourself <span className="t-small" style={{ color: "var(--muted)" }}>(optional)</span></span>
          <input
            className="input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={280}
            placeholder="Only you will read this back"
            aria-label="One line for yourself"
          />
        </label>

        <div className="wrap-2">
          <Btn kind="primary" type="submit" disabled={effort === null || busy}>
            {busy ? "Saving…" : "Save"}
          </Btn>
          <Btn
            kind="ghost"
            disabled={busy}
            onClick={() => {
              markAsked();
              setOpen(false);
            }}
          >
            Not now
          </Btn>
        </div>
      </form>
    </Card>
  );
}

/** The learner's own check-ins, for "what we recorded about you". */
export async function loadMyCheckIns(): Promise<CheckInReply[]> {
  const reply = await api<{ check_ins: CheckInReply[] }>("check-in");
  return reply.check_ins ?? [];
}

function emotionWords(id?: string | null): string {
  return EMOTIONS.find((row) => row.id === id)?.label ?? "";
}

/** Every saved check-in, readable and removable by its owner. */
export function MyCheckIns() {
  const [rows, setRows] = useState<CheckInReply[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void loadMyCheckIns()
      .then((found) => {
        if (alive) setRows(found);
      })
      .catch(() => {
        if (alive) setRows([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function removeOne(id: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await del(`check-in/${encodeURIComponent(id)}`);
      setRows((current) => (current ?? []).filter((row) => row.id !== id));
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That could not be removed just now.");
    } finally {
      setBusy(false);
    }
  }

  async function removeAll() {
    if (busy || !rows?.length) return;
    setBusy(true);
    setNotice(null);
    try {
      await del("check-in");
      setRows([]);
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "Those could not be removed just now.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Your check-ins" tone="plain" icon={<Icon name="message" />}>
      <div className="stack-3" data-testid="my-check-ins">
        <p className="t-small" style={{ margin: 0 }}>
          What you tapped at a check-in, and the estimate it made for that sitting. Yours to remove.
        </p>
        {notice ? <Notice tone="warn">{notice}</Notice> : null}
        {rows === null ? (
          <p role="status" className="t-small">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="t-small">Nothing saved yet.</p>
        ) : (
          <>
            {rows.map((row) => (
              <div key={row.id} className="stack-2" style={{ borderTop: "1px solid var(--line)", paddingTop: "var(--s2)" }}>
                <p style={{ margin: 0 }}>
                  Effort {row.tiles.effort} of 9{emotionWords(row.tiles.emotion) ? ` · ${emotionWords(row.tiles.emotion)}` : ""}
                </p>
                {row.estimate?.summary ? <p className="t-small" style={{ margin: 0 }}>{row.estimate.summary}</p> : null}
                {row.note ? (
                  <p className="t-small" style={{ margin: 0 }}>
                    Your line: “{row.note}”
                  </p>
                ) : null}
                <div className="wrap-2">
                  <Btn kind="ghost" disabled={busy} onClick={() => void removeOne(row.id)}>
                    Remove
                  </Btn>
                </div>
              </div>
            ))}
            <div className="wrap-2">
              <Btn kind="secondary" disabled={busy} onClick={() => void removeAll()}>
                Remove all check-ins
              </Btn>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
