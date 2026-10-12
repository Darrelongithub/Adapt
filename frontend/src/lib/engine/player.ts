/**
 * The player's head, with no DOM in it.
 *
 * Everything that decides *where the learner is* — which frame, playing or paused, how
 * long a caption stays up, whether a prediction is still owed — lives here as a reducer
 * over plain data, for three reasons:
 *
 * 1. it is the part that can be wrong in a way the learner feels (a scene that skips a
 *    frame, a pause that resumes on its own, a caption that vanishes mid-sentence);
 * 2. it is therefore the part worth running as a test with no browser, which
 *    `player.test.mjs` does;
 * 3. and it is the part that must never disagree with the backend about the rules — the
 *    frame index the player is on is the frame index written to `lesson_runs`, so a
 *    clamping bug here is a wrong number in the learner's history.
 *
 * Nothing here computes an *answer*: `sims/*` on the backend owns what the machine does
 * next, and the grade of a prediction is the backend's to give.
 */

import type { Frame, Prompt } from "./types";

export type PlayerState = {
  frames: Frame[];
  index: number;
  playing: boolean;
  /** words-per-minute multiplier: 1 is the pace the narration was written for */
  speed: number;
  /** frame index -> the learner's own answer to that frame's prediction */
  answers: Record<number, string>;
  /** frames the learner has seen at least once, for the review screen */
  seen: number[];
  gate: boolean;
};

export type Action =
  | { type: "play" }
  | { type: "pause" }
  | { type: "toggle" }
  | { type: "step" }
  | { type: "back" }
  | { type: "jump"; index: number }
  | { type: "answer"; index: number; value: string }
  | { type: "speed"; speed: number }
  | { type: "gate"; gate: boolean }
  | { type: "restart" };

export const MIN_SPEED = 0.5;
export const MAX_SPEED = 2;
/** a caption this short would flash; a learner should be able to read it once, calmly */
export const MIN_FRAME_MS = 2600;
/** and one this long would look like the app had frozen */
export const MAX_FRAME_MS = 11000;
/** ~2.6 words a second is roughly how fast the narration was written to be read aloud */
export const WORDS_PER_SECOND = 2.6;

export function clampIndex(index: number, count: number): number {
  if (count <= 0) return 0;
  const last = count - 1;
  if (typeof index !== "number" || Number.isNaN(index)) return 0;
  // +Infinity is "take me to the end", and a slider dragged far right is exactly that;
  // NaN is not a position at all, so it is the start
  if (index === Infinity) return last;
  if (index === -Infinity) return 0;
  return Math.min(Math.max(Math.trunc(index), 0), last);
}

export function init(frames: Frame[], startIndex = 0, gate = true): PlayerState {
  const list = Array.isArray(frames) ? frames : [];
  const index = clampIndex(startIndex, list.length);
  return {
    frames: list,
    index,
    playing: false,
    speed: 1,
    answers: {},
    seen: list.length ? [list[index]?.i ?? index] : [],
    gate,
  };
}

export function promptOn(frame: Frame | undefined): Prompt | null {
  const asked = frame?.prompt;
  if (!asked || typeof asked.question !== "string" || !asked.question.trim()) return null;
  return asked;
}

/** Is a prediction on this frame still owed? An answered one never blocks anything. */
export function pendingPrompt(state: PlayerState): boolean {
  const frame = state.frames[state.index];
  const asked = promptOn(frame);
  return !!asked && state.answers[frame!.i] === undefined;
}

/** A gate the learner can be stuck by is worse than a gate, so this is the whole rule. */
export function canAdvance(state: PlayerState): boolean {
  if (state.index >= state.frames.length - 1) return false;
  return !(state.gate && pendingPrompt(state));
}

export function reducer(state: PlayerState, action: Action): PlayerState {
  const last = Math.max(state.frames.length - 1, 0);
  switch (action.type) {
    case "play":
      // playing at the end means "start again", which is what a learner expects and what
      // avoids a button that does nothing
      if (state.index >= last) return { ...state, index: 0, playing: true, seen: markSeen(state, 0) };
      return { ...state, playing: true };
    case "pause":
      return { ...state, playing: false };
    case "toggle":
      return reducer(state, { type: state.playing ? "pause" : "play" });
    case "step": {
      if (!canAdvance(state)) return { ...state, playing: false };
      const index = Math.min(state.index + 1, last);
      return { ...state, index, playing: state.playing && index < last, seen: markSeen(state, index) };
    }
    case "back": {
      const index = Math.max(state.index - 1, 0);
      return { ...state, index, seen: markSeen(state, index) };
    }
    case "jump": {
      // seeking is allowed past a prediction — that is the point of a slider — but the
      // run's own counters will say which moments were skipped
      const index = clampIndex(action.index, state.frames.length);
      return { ...state, index, seen: markSeen(state, index) };
    }
    case "answer":
      return { ...state, answers: { ...state.answers, [action.index]: action.value } };
    case "speed":
      return { ...state, speed: clampSpeed(action.speed) };
    case "gate":
      return { ...state, gate: action.gate };
    case "restart":
      return { ...state, index: 0, playing: false, answers: {}, seen: state.frames.length ? [state.frames[0].i] : [] };
    default:
      return state;
  }
}

function markSeen(state: PlayerState, index: number): number[] {
  const frame = state.frames[index];
  const i = frame ? frame.i : index;
  return state.seen.includes(i) ? state.seen : [...state.seen, i];
}

export function clampSpeed(speed: number): number {
  if (!Number.isFinite(speed)) return 1;
  return Math.min(Math.max(speed, MIN_SPEED), MAX_SPEED);
}

/**
 * How long a caption stays up, from its own length.
 *
 * The backend deliberately does not send a timing table: a scene is a sequence of states,
 * and the right dwell is the time it takes to read the sentence. So the player derives it
 * here, and a caption about nothing in particular is a *short* pause rather than a long
 * one — a fixed 4s-per-frame is what makes a machine look like it is thinking.
 */
export function frameMs(frame: Frame | undefined, speed = 1): number {
  const text = [frame?.narration ?? "", frame?.cause ?? ""].join(" ").trim();
  const words = text ? text.split(/\s+/).length : 0;
  const raw = (words / WORDS_PER_SECOND) * 1000 + 700;
  const capped = Math.min(Math.max(raw, MIN_FRAME_MS), MAX_FRAME_MS);
  return Math.round(capped / clampSpeed(speed));
}

/**
 * Motion policy, decided once, by the learner's own setting.
 *
 * `prefers-reduced-motion` turns autoplay off and leaves the arrows and the buttons
 * working: a learner who asked for less movement asked for less *unrequested* movement,
 * not for a scene they cannot step through.
 */
export function autoplayAllowed(prefersReducedMotion: boolean): boolean {
  return !prefersReducedMotion;
}

/** Frames that asked for a prediction and never got one, in trace order. */
export function unanswered(frames: Frame[], answers: Record<number, string>): number[] {
  return frames.filter((f) => promptOn(f) && answers[f.i] === undefined).map((f) => f.i);
}

export function nextUnanswered(frames: Frame[], from: number, answers: Record<number, string>): number | null {
  const ahead = frames.filter((f) => promptOn(f) && answers[f.i] === undefined && f.i > from);
  return ahead.length ? ahead[0].i : null;
}

export function progressPercent(index: number, count: number): number {
  if (count <= 1) return count === 1 ? 100 : 0;
  return Math.round((clampIndex(index, count) / (count - 1)) * 100);
}

export function fmtClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Which boxes a renderer should ring. Missing, empty and non-array all mean "nothing". */
export function highlights(frame: Frame | undefined): string[] {
  const h = frame?.highlights;
  return Array.isArray(h) ? h.filter((x): x is string => typeof x === "string") : [];
}

/**
 * The one keyboard map the player has.
 *
 * Space is the play/pause key and must not scroll the page; arrows step; `p` toggles;
 * and typing in the "why?" box must never move the scene — hence the tag check, which is
 * the difference between a shortcut and a stolen keystroke.
 */
export function keyAction(
  key: string,
  target: { tagName?: string; isContentEditable?: boolean } | null,
): "play" | "step" | "back" | "jump-end" | "jump-start" | null {
  const tag = (target?.tagName ?? "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return null;
  switch (key) {
    case " ":
    case "k":
    case "p":
      return "play";
    case "ArrowRight":
    case "l":
      return "step";
    case "ArrowLeft":
    case "j":
      return "back";
    case "End":
      return "jump-end";
    case "Home":
      return "jump-start";
    default:
      return null;
  }
}
