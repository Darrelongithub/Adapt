/**
 * Debounce, dedupe and in-flight guards for the browser side of "one request per intention".
 *
 * The server can refuse a flood, but by then the request has already cost a round trip, a
 * websocket frame and a slot in whatever queue is behind it. Two of the screens in this app
 * can produce that flood without anyone being malicious:
 *
 * - **the voice screen**: hold-to-talk dictation and a held Enter key can send the same text
 *   frame several times a second, and every frame is a turn;
 * - **the practice screen**: a double-click on *submit*, or a keyboard repeat, submits the
 *   same answer twice — which is not one wasted request but two attempts recorded against
 *   the learner's evidence.
 *
 * So the same two ideas live here as on the server (`security/ratelimit.py`): a *recent*
 * window that forgets one key at a time, and an *in-flight* counter that refuses to start
 * what is already running. Both are tiny, both are pure, and both are tested without a
 * browser — which is the point of keeping them out of the components.
 */

/** Milliseconds after the last call before `fn` runs. */
export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): ((...args: A) => void) & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const wrapped = (...args: A) => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, ms);
  };
  wrapped.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}

/** The leading-edge variant: run now, then ignore everything for `ms`. */
export function throttle<A extends unknown[]>(fn: (...args: A) => void, ms: number): (...args: A) => boolean {
  let until = 0;
  return (...args: A) => {
    const now = Date.now();
    if (now < until) return false;
    until = now + ms;
    fn(...args);
    return true;
  };
}

export type Deduper = {
  /** True when `key` was already seen inside the window — i.e. this call is a repeat. */
  seen: (key: string, at?: number) => boolean;
  /** Forget one key (a new conversation, a cleared box). */
  forget: (key?: string) => void;
  /** How many repeats were suppressed, for a screen that wants to say so. */
  suppressed: () => number;
};

/**
 * A repeat window keyed by whatever identifies the intention (the text, the item id).
 *
 * It remembers one key, not a history: the case it exists for is the *same* message arriving
 * twice — a double-click, a key repeat, a replayed frame after a reconnect — not a learner
 * who repeats themselves. Someone who asks the same question twice on purpose deserves two
 * answers, which is why the window is short (under a second by default) and why
 * `forget()` exists.
 */
export function createDeduper(ms = 900): Deduper {
  const last = new Map<string, number>();
  let suppressed = 0;
  return {
    seen(key: string, at = Date.now()) {
      const previous = last.get(key);
      if (previous !== undefined && at - previous <= ms) {
        suppressed += 1;
        // The window is refreshed, not left to expire: a key held down produces a repeat
        // every few milliseconds, and each one has to be refused, not just the first.
        last.set(key, at);
        return true;
      }
      last.set(key, at);
      if (last.size > 256) {
        for (const [k, when] of last) if (at - when > ms * 4) last.delete(k);
      }
      return false;
    },
    forget(key?: string) {
      if (key === undefined) last.clear();
      else last.delete(key);
    },
    suppressed: () => suppressed,
  };
}

export type Inflight = {
  /** Run `fn` unless its key is already running; returns `undefined` when it is. */
  run: <T>(key: string, fn: () => Promise<T>) => Promise<T | undefined>;
  /**
   * Run `fn` once and hand the *same* promise to every caller for that key.
   *
   * This is what a submission wants. `run` tells the second caller "no" and leaves it with
   * nothing to render; `share` lets the second click join the first request and receive its
   * answer, which is both cheaper and more honest — the button was pressed twice, the answer
   * is one answer.
   */
  share: <T>(key: string, fn: () => Promise<T>) => Promise<T>;
  busy: (key: string) => boolean;
  active: () => number;
};

/**
 * "One at a time, per key", for anything that ends in a request.
 *
 * A submit button that is disabled while its request is in flight is the usual answer, and
 * it fails at exactly the moment it matters: a keyboard-held Enter, a second pointer, or two
 * components sharing the same submission path. This counts instead, and the count is released
 * in a `finally` so a rejected request cannot leave a key permanently busy.
 */
export function createInflight(): Inflight {
  const shared = new Map<string, Promise<unknown>>();
  const active = new Set<string>();
  return {
    async run<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
      if (active.has(key)) return undefined;
      active.add(key);
      try {
        return await fn();
      } finally {
        active.delete(key);
      }
    },
    share<T>(key: string, fn: () => Promise<T>): Promise<T> {
      const existing = shared.get(key);
      if (existing) return existing as Promise<T>;
      const started = (async () => {
        active.add(key);
        try {
          return await fn();
        } finally {
          shared.delete(key);
          active.delete(key);
        }
      })();
      shared.set(key, started);
      return started;
    },
    busy: (key: string) => shared.has(key) || active.has(key),
    active: () => active.size,
  };
}
