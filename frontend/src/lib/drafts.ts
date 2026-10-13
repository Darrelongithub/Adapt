"use client";

/**
 * The answer a learner is part-way through, kept outside the component that renders it.
 *
 * Why this exists. The answer used to live in `Question`'s own `useState`, which ties the
 * learner's typing to the *lifetime of one React element*. Anything that replaces that
 * element — navigating back to a question, a screen that blanks itself while a slow
 * response arrives, React re-mounting the subtree when a session is re-checked — threw the
 * answer away. That is the "sometimes lost" fault: it is not one bad line, it is the answer
 * being stored in the one place guaranteed to be discarded.
 *
 * What it is *not*: persisted to the server, or a cache of anything the app graded. A draft
 * is cleared the moment the answer is submitted, and it lives in `sessionStorage` so that a
 * reload is the same answer rather than a fresh box — a learner who refreshes mid-question
 * has not answered yet, and losing what they typed is the same fault wearing a different hat.
 *
 * Nothing here ever holds a correct answer: the client is not given one (see the API), so
 * the worst a stale draft can do is put the learner's own words back on the screen.
 */

export type Draft = {
  /** Chosen option index for a choice question. */
  picked: number | null;
  /** Typed response (numeric answers and teach-backs both use this). */
  typed: string;
  /** The optional "say it back" box next to a non-teachback question. */
  teach: string;
};

const EMPTY: Draft = { picked: null, typed: "", teach: "" };
const PREFIX = "adapt:draft:";

/** In-memory first: a browser with sessionStorage disabled (or a test DOM) still works. */
const memory = new Map<string, Draft>();

const key = (itemId: string) => `${PREFIX}${itemId}`;

export function loadDraft(itemId: string): Draft {
  if (!itemId) return { ...EMPTY };
  const cached = memory.get(itemId);
  if (cached) return { ...cached };
  try {
    const raw = sessionStorage.getItem(key(itemId));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Draft>;
      const draft: Draft = {
        picked: typeof parsed.picked === "number" ? parsed.picked : null,
        typed: typeof parsed.typed === "string" ? parsed.typed : "",
        teach: typeof parsed.teach === "string" ? parsed.teach : "",
      };
      memory.set(itemId, draft);
      return { ...draft };
    }
  } catch {
    /* a draft is not worth an exception, ever */
  }
  return { ...EMPTY };
}

export function saveDraft(itemId: string, patch: Partial<Draft>): Draft {
  const next = { ...loadDraft(itemId), ...patch };
  memory.set(itemId, next);
  try {
    sessionStorage.setItem(key(itemId), JSON.stringify(next));
  } catch {
    /* out of quota or storage disabled: the in-memory copy is still right */
  }
  return next;
}

/** Called once an answer is submitted: nothing of the learner's is kept after the mark. */
export function clearDraft(itemId: string): void {
  memory.delete(itemId);
  try {
    sessionStorage.removeItem(key(itemId));
  } catch {
    /* nothing to do */
  }
}

/** Test helper and the "start from scratch" path. */
export function clearAllDrafts(): void {
  memory.clear();
  try {
    for (const k of Object.keys(sessionStorage)) {
      if (k.startsWith(PREFIX)) sessionStorage.removeItem(k);
    }
  } catch {
    /* nothing to do */
  }
}
