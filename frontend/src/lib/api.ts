/**
 * One place where the browser talks to the backend.
 *
 * Three rules this file exists to enforce:
 *
 * 1. **The CSRF header goes on every mutation.** The backend refuses state-changing
 *    calls without it, and a UI that forgets it once fails in a way that looks like a
 *    login problem. Here it is automatic.
 * 2. **Errors stay errors.** `ApiError` carries the status and the backend's own
 *    `detail`, so a page can show "no local speech model installed" instead of a
 *    spinner that never ends. Nothing here invents a fallback payload.
 * 3. **`?mock=1` is loud.** The mock flag exists so the design can be reviewed without
 *    a backend; when it is on, every response comes from recorded fixtures and the
 *    banner in the layout says so on every screen.
 */
"use client";

import { useCallback, useEffect, useState } from "react";

import { mockLookup } from "./mock";

export const CSRF_COOKIE = "adapt_csrf";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
  /**
   * The sentence to put on screen.
   *
   * The backend's `detail` is either a plain string or a structured object, and the
   * object carries both a machine code (`error`) and, usually, prose written for a
   * person (`note`, `hint`, `detail`, `next`). Reading only `error` — which this
   * function used to do — showed a learner the token `model_budget_exhausted` while the
   * sentence explaining that teaching continues locally sat unused in `note`. So:
   * prefer the prose, keep the code as the last resort, and say how long to wait when
   * the answer was "not yet".
   */
  get shown(): string {
    if (typeof this.detail === "string") return this.detail;
    if (this.detail && typeof this.detail === "object") {
      const d = this.detail as Record<string, unknown>;
      const prose = ["note", "hint", "detail", "next"]
        .map((k) => (typeof d[k] === "string" ? (d[k] as string) : ""))
        .find((v) => v.length > 0);
      const code = typeof d.error === "string" ? d.error : "";
      const wait =
        typeof d.retry_after_s === "number" && d.retry_after_s > 0
          ? ` (about ${Math.ceil(d.retry_after_s)}s)`
          : "";
      if (prose) return `${prose}${code === "rate_limited" || code === "too_many_attempts" ? wait : ""}`;
      if (code) return wait ? `${code}${wait}` : code;
      return JSON.stringify(d).slice(0, 220);
    }
    return this.message;
  }
}

export function cookieValue(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export function csrfToken(): string | null {
  return cookieValue(CSRF_COOKIE);
}

let mockFlag: boolean | null = null;

/** True when the page was opened with `?mock=1` (kept in sessionStorage so it survives navigation). */
export function isMock(): boolean {
  if (mockFlag !== null) return mockFlag;
  if (typeof window === "undefined") return false;
  const url = new URL(window.location.href);
  const inUrl = url.searchParams.get("mock");
  if (inUrl === "1" || inUrl === "true") {
    window.sessionStorage.setItem("adapt-mock", "1");
    mockFlag = true;
  } else if (inUrl) {
    window.sessionStorage.removeItem("adapt-mock");
    mockFlag = false;
  } else {
    mockFlag = window.sessionStorage.getItem("adapt-mock") === "1";
  }
  return mockFlag;
}

type Options = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  /** attach the CSRF token even on a safe method (see /items/{id}/reveal) */
  csrf?: boolean;
};

export function qs(query?: Options["query"]): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T = Record<string, unknown>>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const url = `/api/v1/${path.replace(/^\/?api\/v1\/?/, "").replace(/^\/+/, "")}`;
  if (isMock()) {
    const found = mockLookup(method, url + qs(opts.query));
    if (!found.found) throw new ApiError(404, `mock: no fixture for ${method} ${url}`);
    if (found.error) throw new ApiError(found.status ?? 400, found.error.message, found.error.detail);
    return found.body as T;
  }
  const headers: Record<string, string> = { accept: "application/json", ...(opts.headers ?? {}) };
  // `csrf` is opt-in on safe methods: one GET in this API demands the token anyway
  // (the answer-reveal route, because it is the one read that spoils a question).
  if (method !== "GET" || opts.csrf) {
    const token = csrfToken();
    if (token) headers["x-csrf-token"] = token;
  }
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  let res: Response;
  try {
    res = await fetch(url + qs(opts.query), { method, headers, body, credentials: "same-origin" });
  } catch (err) {
    throw new ApiError(0, `the backend is not reachable from the browser (${(err as Error).message})`);
  }
  const text = await res.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!res.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in (payload as Record<string, unknown>)
        ? (payload as Record<string, unknown>).detail
        : payload;
    throw new ApiError(res.status, `${method} ${url} -> ${res.status}`, detail);
  }
  return payload as T;
}

/** GET with the failure kept as data, for screens that should still render their frame. */
export async function get<T = Record<string, unknown>>(
  path: string,
  query?: Options["query"],
): Promise<{ data: T | null; error: string | null; status: number | null }> {
  try {
    return { data: await api<T>(path, { query }), error: null, status: 200 };
  } catch (err) {
    const e = err as ApiError;
    return { data: null, error: e.shown ?? String(err), status: e.status ?? null };
  }
}

export const post = <T = Record<string, unknown>>(path: string, json?: unknown, query?: Options["query"]) =>
  api<T>(path, { method: "POST", json: json ?? {}, query });

export const del = <T = Record<string, unknown>>(path: string, json?: unknown) =>
  api<T>(path, { method: "DELETE", json: json ?? {} });

export type Load<T> = { data: T | null; error: string | null; status: number | null; loading: boolean; reload: () => void };

export function useApi<T = Record<string, unknown>>(path: string | null, query?: Options["query"]): Load<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; status: number | null; loading: boolean }>({
    data: null,
    error: null,
    status: null,
    loading: path !== null,
  });
  const [tick, setTick] = useState(0);
  const key = JSON.stringify([path, query ?? null, tick]);

  useEffect(() => {
    let alive = true;
    if (!path) {
      setState({ data: null, error: null, status: null, loading: false });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    get<T>(path, query).then((r) => {
      if (alive) setState({ ...r, loading: false });
    });
    return () => {
      alive = false;
    };
    // The path/query/tick tuple is the whole dependency set: `query` is a fresh object
    // on most renders, so depending on it directly would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { ...state, reload: () => setTick((t) => t + 1) };
}

/** Who is this browser? `null` means signed out, and every page has to handle that. */
export type Me = {
  student_id: string;
  display_name: string;
  role: string;
  onboarding_complete: boolean;
  diagnostic_complete: boolean;
  voice_enabled: boolean;
  voice_gender: string;
  language: string;
  consent_analytics: boolean;
  consent_share_with_ai: boolean;
  sessions: number;
};


export async function logout(): Promise<void> {
  try {
    await post("logout");
  } catch {
    /* already signed out */
  }
}
