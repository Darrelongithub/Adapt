/* Adapt TypeScript SDK — generated from the engine's OpenAPI document.
 *
 * Do not edit by hand: `python sdk/generate.py` rewrites this file, and
 * `tests/test_sdk.py` fails when the committed copy is stale.
 *
 *   const client = new Client("https://api.example.com", process.env.ADAPT_KEY!);
 *   const decision = await client.decide("learn-0001", "skill.fractions");
 *
 * Uses the platform `fetch`: no dependencies, no build step beyond `tsc`.
 */

export class AdaptError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
    message: string,
    readonly requestId: string | null,
    readonly retryable: boolean,
    readonly details: Record<string, unknown>,
  ) {
    super(`${status} ${code ?? "error"}: ${message}`);
    this.name = "AdaptError";
  }
}

export type Json = Record<string, unknown>;

export interface RequestOptions {
  body?: Json;
  params?: Record<string, string | number | boolean | undefined>;
  idempotencyKey?: string;
  testTraffic?: boolean;
}

export class Client {
  constructor(
    readonly baseUrl: string,
    readonly apiKey: string,
    readonly userAgent = "adapt-ts-sdk/generated",
  ) {}

  async request(method: string, path: string, options: RequestOptions = {}): Promise<any> {
    const url = new URL(this.baseUrl.replace(/\/$/, "") + path);
    for (const [key, value] of Object.entries(options.params ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
      "User-Agent": this.userAgent,
    };
    const key = options.idempotencyKey ?? Client.deriveKey(method, path, JSON.stringify(options.body ?? null));
    headers["Idempotency-Key"] = key;
    if (options.testTraffic) headers["X-Adapt-Test"] = "1";
    const response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const error = payload?.error ?? {};
      throw new AdaptError(
        response.status,
        error.code ?? null,
        error.message ?? response.statusText,
        error.request_id ?? null,
        Boolean(error.retryable),
        error.details ?? {},
      );
    }
    return payload;
  }

  /** A stable idempotency key from the logical operation, not the attempt.
   *
   * FNV-1a rather than a cryptographic hash because this has to be synchronous
   * and dependency-free: the key's job is to be *stable*, not secret. Pass your
   * own when you need a coarser logical operation than one request.
   */
  static deriveKey(...parts: Array<string | number>): string {
    let hash = 0x811c9dc5;
    for (const character of parts.map(String).join("|")) {
      hash ^= character.codePointAt(0)!;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return `adapt-${hash.toString(16).padStart(8, "0")}${parts.length}`;
  }
}

  /** Get Balance */
  async creditsBalance(
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/credits/balance`, {
      ...options,
    });
  }

  /** Decide */
  async decide(
    body: Json,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("POST", `/v1/decide`, {
      body,
      ...options,
    });
  }

  /** Replay */
  async replay(
    decision_id: string,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("POST", `/v1/decisions/${decision_id}/replay`, {
      ...options,
    });
  }

  /** List Learners */
  async learners(
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/learners`, {
      ...options,
    });
  }

  /** Create Learner */
  async createLearner(
    body: Json,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("POST", `/v1/learners`, {
      body,
      ...options,
    });
  }

  /** Get Learner */
  async learner(
    learner_id: string,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/learners/${learner_id}`, {
      ...options,
    });
  }

  /** Mastery Snapshot */
  async mastery(
    learner_id: string,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/mastery`, {
      params: { learner_id: learner_id },
      ...options,
    });
  }

  /** Mastery For Skill */
  async masteryFor(
    skill: string,
    learner_id: string,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/mastery/${skill}`, {
      params: { learner_id: learner_id },
      ...options,
    });
  }

  /** Observe */
  async observe(
    body: Json,
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("POST", `/v1/observe`, {
      body,
      ...options,
    });
  }

  /** Whoami */
  async whoami(
    options: Omit<RequestOptions, "body" | "params"> = {},
  ): Promise<any> {
    return this.request("GET", `/v1/whoami`, {
      ...options,
    });
  }
