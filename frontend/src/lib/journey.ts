"use client";

/**
 * The journey's live half: read the path's state, and record the learner's answer.
 *
 * The path itself — the stage ids, the labels, what each step is for — lives in
 * ``./journeyPath.ts``, which imports nothing. That split is not decoration: it means the
 * frozen order and the copy can be unit-tested without a DOM, a React runtime or a
 * backend, and it keeps a data structure from depending on a fetch helper.
 */

import { useCallback, useEffect, useState } from "react";

import { get, post } from "./api";
import type { JourneyState, StageId, StageStatus } from "./journeyPath";

export { isBehind, percent, STAGE_IDS, STEPS } from "./journeyPath";
export type { JourneyState, StageId, StageRow, StageStatus } from "./journeyPath";

export type Journey = {
  state: JourneyState | null;
  error: string | null;
  status: number | null;
  loading: boolean;
  reload: () => void;
  /** Mark a stage done or skipped; resolves with the server's new state, or an error. */
  advance: (stage: StageId, status: StageStatus, note?: string) => Promise<{ ok: boolean; error?: string }>;
  /** The stage the learner is on, from the server's answer. */
  current: StageId | null;
  /** `true` while a write is in flight, for disabling the control that caused it. */
  busy: boolean;
};

export function useJourney(enabled = true): Journey {
  const [state, setState] = useState<JourneyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    get<JourneyState>("journey/state").then((r) => {
      if (!alive) return;
      setState(r.data);
      setError(r.error);
      setStatus(r.status);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [enabled, tick]);

  const advance = useCallback(async (stage: StageId, st: StageStatus, note?: string) => {
    setBusy(true);
    try {
      const body = note ? { stage, status: st, note } : { stage, status: st };
      const next = await post<JourneyState>("journey/advance", body);
      setState(next);
      setError(null);
      setStatus(200);
      return { ok: true as const };
    } catch (err) {
      const shown = (err as { shown?: string }).shown ?? String(err);
      setError(shown);
      return { ok: false as const, error: shown };
    } finally {
      setBusy(false);
    }
  }, []);

  return {
    state,
    error,
    status,
    loading,
    reload: () => setTick((t) => t + 1),
    advance,
    current: state?.stage ?? null,
    busy,
  };
}
