/** Phrases that must not appear in learner-facing text.

The crawl fails on a case-insensitive substring. Onboarding still contains three
sentences that belong to Phase D; those exact strings are stripped before the
check, and only on that route.
*/

export const BANNED = [
  { id: "api-key", re: /API key/i },
  { id: "gemini", re: /Gemini/i },
  { id: "provider", re: /provider/i },
  { id: "model", re: /model/i },
  { id: "endpoint", re: /endpoint/i },
  { id: "get-path", re: /GET \//i },
  { id: "post-path", re: /POST \//i },
  { id: "uvicorn", re: /uvicorn/i },
  { id: "ignored-keys", re: /ignored_keys/i },
  { id: "ai-1", re: /AI 1/i },
  { id: "ai-2", re: /AI 2/i },
  { id: "ai-3", re: /AI 3/i },
  { id: "token", re: /token/i },
];

/** Exact learner-facing strings still on the onboarding page. Phase D owns that file. */
export const ONBOARDING_DEFERRED = [
  "Let a model read a summary when it would help",
  "The summary is topic mastery, hypotheses and approved changes — never your name or contact details. With no API key configured this changes nothing, because nothing is sent anyway.",
  "Anything else the client sends comes back in ignored_keys with a reason, rather than being stored as a string nobody can interpret.",
];

export function stripDeferred(text) {
  let rest = String(text || "");
  for (const phrase of ONBOARDING_DEFERRED) rest = rest.split(phrase).join(" ");
  return rest;
}

/** Banned-phrase ids still present. Onboarding ignores only the deferred sentences. */
export function bannedHits(text, route = "") {
  const scanned = route === "/onboarding" ? stripDeferred(text) : String(text || "");
  return BANNED.filter((item) => item.re.test(scanned)).map((item) => item.id);
}
