"use client";

/** The learning loop as one hook, because /diagnose and /learn are the same machine
 *  with a different reason for asking. Splitting it across two pages is how one of
 *  them quietly stops recording events.
 *
 *  What it owns:
 *   - the session id (kept in sessionStorage: a reload is the same session, a new tab
 *     is a new one -- a learner who refreshes should not be shown a fresh diagnostic);
 *   - the event queue (question shown, first interaction, changed answer, hint opened,
 *     submitted) -- the numbers AI 1 uses come from this log, so a page that forgets to
 *     push is a page that measures nothing;
 *   - attempt submission and its receipt;
 *   - the in-progress draft of each answer, which lives here rather than inside the question
 *     card precisely so that re-rendering the card cannot lose it (`@/lib/drafts`).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { EventQueue, useEventQueue } from "@/components/ui";
import { api, get, post } from "@/lib/api";
import { clearAllDrafts } from "@/lib/drafts";
import type { PlanEntry, Receipt } from "@/lib/types";

const SESSION_KEY = "adapt:session";

export type FlowError = string | null;

export function useFlow(mode: "practice" | "diagnostic") {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<FlowError>(null);
  const [startedAt] = useState<number | null>(Date.now());
  const { q, dropped } = useEventQueue(sessionId);
  const queue = q as EventQueue;
  const firstInteraction = useRef<Record<string, boolean>>({});
  const changes = useRef<Record<string, number>>({});
  // Per-item clocks: `ms_since_shown` has to be measured from when *this* question
  // appeared, not from the start of the session, or every timing metric is a lie that
  // grows with the length of the session.
  const shownAt = useRef<Record<string, number>>({});
  const since = (id: string) => Math.max(0, Date.now() - (shownAt.current[id] ?? Date.now()));

  useEffect(() => {
    const stored = sessionStorage.getItem(SESSION_KEY);
    if (stored) {
      const [id, when, storedMode] = stored.split("|");
      // The mode is part of the key because it changes what the session means: a placement
      // session and a practice session are not interchangeable, and reusing one across
      // screens would mislabel every event pushed into it.
      if (id && storedMode === mode) {
        setSessionId(id);
        void when;
        return;
      }
    }
    let alive = true;
    (async () => {
      try {
        const out = await post<{ session_id: string }>("sessions", { mode, device_hint: navigator.hardwareConcurrency ? `${navigator.hardwareConcurrency} cores` : null });
        if (!alive) return;
        setSessionId(out.session_id);
        sessionStorage.setItem(SESSION_KEY, `${out.session_id}|${Date.now()}|${mode}`);
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [mode]);

  const push = useCallback(
    (type: string, payload: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => queue.push(type, payload, extra),
    [queue],
  );

  /** Called when a question appears. `question_shown` is what "did they look before
   *  they answered" is measured against, so it has to be pushed by the code that shows
   *  the question, not by a page that happens to remember. */
  const onShown = useCallback(
    (entry: PlanEntry) => {
      firstInteraction.current[entry.item.id] = false;
      changes.current[entry.item.id] = 0;
      shownAt.current[entry.item.id] = Date.now();
      push("question_shown", { stem_words: (entry.item.stem || "").split(/\s+/).filter(Boolean).length, variant: entry.item.id }, { item_id: entry.item.id, topic_id: entry.topic_id });
    },
    [push],
  );

  const onInteract = useCallback(
    (item: { id: string; topic_id: string }) => {
      if (firstInteraction.current[item.id]) return;
      firstInteraction.current[item.id] = true;
      push("first_interaction", { ms_since_shown: since(item.id) }, { item_id: item.id, topic_id: item.topic_id });
    },
    [push],
  );

  const onChange = useCallback(
    (item: { id: string; topic_id: string }, from: unknown, to: unknown) => {
      if (from === to) return;
      changes.current[item.id] = (changes.current[item.id] ?? 0) + 1;
      push("response_change", { from: short(from), to: short(to), ms_since_shown: since(item.id) }, { item_id: item.id, topic_id: item.topic_id });
    },
    [push],
  );

  const onHint = useCallback(
    (item: { id: string; topic_id: string }, hintNo: number) => push("hint_opened", { hint_no: hintNo }, { item_id: item.id, topic_id: item.topic_id }),
    [push],
  );

  const onExample = useCallback(
    (item: { id: string; topic_id: string }, step = 1) => push("example_opened", { step }, { item_id: item.id, topic_id: item.topic_id }),
    [push],
  );

  const onExplanation = useCallback(
    (item: { id: string; topic_id: string }, chars: number) => push("explanation_opened", { chars }, { item_id: item.id, topic_id: item.topic_id }),
    [push],
  );

  /** The session is created by the effect above, so a learner who answers within a
   *  hundred milliseconds of the page appearing would otherwise submit an orphaned
   *  attempt. Waiting a moment is cheaper than an event that never reaches the analysis. */
  const ready = useCallback(async (): Promise<string | null> => {
    if (sessionId) return sessionId;
    for (let t = 0; t < 40; t++) {
      await new Promise((r) => setTimeout(r, 50));
      const stored = sessionStorage.getItem(SESSION_KEY);
      const id = stored ? stored.split("|")[0] : null;
      if (id) {
        setSessionId(id);
        return id;
      }
    }
    return null;
  }, [sessionId]);

  const submit = useCallback(
    async (
      itemId: string,
      answer: unknown,
      extra: { teachback_text?: string; attempt_no?: number } = {},
    ): Promise<Receipt> => {
      const id = await ready();
      // Support openings must reach the server before attribution, not after it.
      await queue.flush();
      const body: Record<string, unknown> = { session_id: id, item_id: itemId, answer, ...extra };
      const out = await api<Receipt>("/api/v1/attempts", { method: "POST", json: body });
      push(
        "answer_submitted",
        {
          correct: out.correct,
          value: short(answer),
          changed_times: changes.current[itemId] ?? 0,
          ms_since_shown: since(itemId),
        },
        { item_id: itemId, topic_id: out.topic_id },
      );
      await queue.flush();
      return out;
    },
    [push, queue, ready, sessionId],
  );

  /** Finish returns `{ok, session_id, ...}` plus the id it used, because the caller has
   *  to run /analyze against that session and the local state is cleared here. */
  const finish = useCallback(async () => {
    await queue.flush();
    if (!sessionId) return null;
    const used = sessionId;
    try {
      const out = await post<Record<string, unknown>>(`sessions/${sessionId}/finish`, {});
      sessionStorage.removeItem(SESSION_KEY);
      // Drafts live no longer than the session that produced them: finishing is the point at
      // which the answers have been sent and there is no reason for anything of the learner's
      // to still be sitting in this browser.
      clearAllDrafts();
      setSessionId(null);
      return { ...out, session_id: used };
    } catch (err) {
      setError((err as Error).message);
      return null;
    }
  }, [queue, sessionId]);

  const stream = useCallback(async () => {
    if (!sessionId) return [];
    const r = await get<{ events: Record<string, unknown>[] }>(`sessions/${sessionId}/events`);
    return r.data?.events ?? [];
  }, [sessionId]);

  return { sessionId, error, dropped, queue, push, ready, onShown, onInteract, onChange, onHint, onExample, onExplanation, submit, finish, stream, startedAt };
}

const short = (v: unknown): string => (typeof v === "string" ? v.slice(0, 60) : JSON.stringify(v ?? null)?.slice(0, 60) ?? "");
