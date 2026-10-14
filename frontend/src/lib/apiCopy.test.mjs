/**
 * The sentence a refusal puts on screen (`apiCopy.ts`).
 *
 * Load-bearing in both directions: the prose has to win over the machine code, and a "not yet"
 * answer has to carry its wait time. The pacing refusals added with the concurrency guard are
 * exactly the case where a code without a number leaves someone clicking again immediately.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { apiErrorSentence, PACING_CODES } from "./apiCopy.ts";

test("prose beats the machine code", () => {
  const shown = apiErrorSentence(503, "boom", {
    error: "model_budget_exhausted",
    note: "the free Gemini budget for today is spent. Teaching continues from the local tutor",
  });
  assert.match(shown, /Teaching continues from the local tutor/);
  assert.doesNotMatch(shown, /model_budget_exhausted/);
});

test("a pacing refusal carries the wait", () => {
  const limited = apiErrorSentence(429, "boom", {
    error: "rate_limited",
    retry_after_s: 2.2,
    message: "rate limit exceeded - this connection is asking too often",
  });
  assert.match(limited, /about 3s/, "the learner has to be told when to come back");

  const concurrent = apiErrorSentence(429, "boom", {
    error: "too_many_concurrent",
    retry_after_s: 1.4,
    message: "too many of your requests are being answered at once - wait for the ones already running",
  });
  assert.match(concurrent, /too many of your requests are being answered at once/);
  assert.match(concurrent, /about 2s/, "a newer pacing code must not lose the wait time");

  // A refusal the server wrote without a wait time still reads as a sentence, not a bare code.
  assert.equal(
    apiErrorSentence(429, "boom", { error: "too_many_concurrent", message: "too many at once" }),
    "too many at once",
  );
  assert.ok(PACING_CODES.includes("too_many_concurrent"));
});

test("an unstructured error keeps its own message, and never comes back empty", () => {
  assert.equal(apiErrorSentence(500, "the server fell over", undefined), "the server fell over");
  assert.equal(apiErrorSentence(403, "nope", "csrf token missing or wrong"), "csrf token missing or wrong");
  assert.equal(apiErrorSentence(500, "", undefined), "request failed (500)");
  assert.equal(apiErrorSentence(500, "boom", { weird: true }).slice(0, 12), '{"weird":tru');
});
