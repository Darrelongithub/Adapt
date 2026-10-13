# Frontend integration notes — master prompts 3, 4, 5, 6

Everything below is already live on the backend (branch `arena/aaa1ac75-adapt`, PR #12).
None of it requires a model key: every response shape is produced deterministically when
`GEMINI_API_KEY` is unset, and the response tells you which path produced it
(`feedback_source` / `picks_source` / `source` = `rules` or `model`).

These files are integrator-owned per `docs/AGENT_BRIEFS.md`, so the edits are written as
exact instructions rather than being applied.

---

## 1. Teach-back receipt: show the grader's feedback (prompt 3)

The attempt receipt's `teachback` object now also carries:

```json
{
  "score": 0.667, "coverage": 0.667, "method": "checklist_3_point",
  "matched": ["..."], "missing": ["..."], "followup": "...", "words": 38, "note": "...",
  "feedback": "You are right about the common denominator. Still to show: equal parts - say the sentence that shows it and you have it.",
  "misconceptions": [],
  "feedback_source": "rules"
}
```

`misconceptions` is non-empty **only** when a configured model named one and its whole
judgement passed the verbatim-quote gate; the rules never claim one. `feedback` is
always present, <=220 chars, second person.

### `frontend/src/lib/types.ts` (Receipt.teachback, ~line 107)

```diff
   teachback?: {
     score: number;
     coverage?: number;
     method: string;
     matched: string[];
     missing: string[];
     followup?: string | null;
     words?: number;
     note?: string;
+    feedback?: string;
+    misconceptions?: string[];
+    feedback_source?: string;
   } | null;
```

### `frontend/src/components/question.tsx` (ReceiptView, ~line 578)

```diff
       {tb ? (
-        <p className="mt-2 text-xs text-[var(--muted)]">
-          teach back: {(tb.matched ?? []).length} of {(tb.matched ?? []).length + (tb.missing ?? []).length} ideas
-          {tb.missing?.length ? <> — missing <em>{tb.missing.join(", ")}</em></> : <> — all there</>}
-          {tb.followup ? <>. {tb.followup}</> : null}. {tb.note}
-        </p>
+        <div className="mt-2 text-xs text-[var(--muted)]">
+          {tb.feedback ? <p>{tb.feedback}</p> : null}
+          <p>
+            teach back: {(tb.matched ?? []).length} of {(tb.matched ?? []).length + (tb.missing ?? []).length} ideas
+            {tb.missing?.length ? <> — missing <em>{tb.missing.join(", ")}</em></> : <> — all there</>}
+            {tb.followup && !tb.feedback ? <>. {tb.followup}</> : null}. {tb.note}
+          </p>
+          {(tb.misconceptions ?? []).length ? (
+            <p>
+              one thing to double-check: {tb.misconceptions!.join("; ")}
+            </p>
+          ) : null}
+        </div>
       ) : null}
```

Copy note: misconceptions are about the *answer's content* (e.g. "adding the denominators
as well as the numerators"), not about the learner. Keep the wording "one thing to
double-check", not "you misunderstand" — the existing copy rules (no deficit language)
apply unchanged.

### `frontend/src/lib/mock.ts` (line 1087, the `items/[id]/teachback` mock)

The mock still carries the old `method: "rubric_concept_coverage"`; the live contract is
`method: "checklist_3_point"` plus the new fields:

```diff
-  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/teachback/, body: { score: 0.67, coverage: 0.67, method: "rubric_concept_coverage", matched: ["common denominator", "equal parts"], missing: ["numerator scales too"], followup: "Which part of the fraction changes when you scale it up?", words: 38, note: "scored on ideas covered, not fluency or spelling" } },
+  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/teachback/, body: { score: 0.67, coverage: 0.67, method: "checklist_3_point", matched: ["common denominator", "equal parts"], missing: ["numerator scales too"], followup: "Which part of the fraction changes when you scale it up?", words: 38, note: "scored on ideas covered, not fluency or spelling", feedback: "You are right about the common denominator. Still to show: numerator scales too - say the sentence that shows it and you have it.", misconceptions: [], feedback_source: "rules" } },
```

---

## 2. Resource picks (prompt 4)

`POST /api/v1/tutor/resources/search` (optional new body field `level`, e.g.
`"developing"` | `"secure"`) now returns, on top of the unchanged `results` list:

```json
{
  "picks": [{"title": "...", "url": "https://...", "source": "openalex", "why": "Start here: an article written to be approachable — a good first pass before the denser material. (2019)"}],
  "picks_source": "rules",
  "picks_note": "only when the search returned nothing",
  "picks_llm_picks": "only when a model answered and was rejected (the reason)"
}
```

Max 4 picks; every `url` is guaranteed to be one of the search results (the backend
refuses model curation that cites anything else). The screen already shown to users via
`retrieval_note` keeps its meaning.

The frontend has **no screen calling this endpoint today**, so there is nothing to break.
When a Resources surface lands: render `picks` first (they are the curation), keep
`retrieval_note` visible, label the source honestly (`picks_source === "model"` → nothing
special to say; `"rules"` → the picks are the ranked order, do not claim a model chose
them), and add a `?mock=1` entry for the path at the same time — `npm run lint`
(`scripts/check-contract.mjs`) refuses any UI-called path without mock coverage.

---

## 3. The retention check screen (prompt 5)

New endpoint, companion to the existing `GET review/due` that `/review` already renders:

```
GET /api/v1/review/check?topic_id=<id>            → the question + why now
GET /api/v1/review/check?topic_id=<id>&with_notes=1 → adds expected_answer_notes
```

Without notes:

```json
{
  "topic_id": "top_fraction_intro", "title": "what a fraction means",
  "question": "Last time you worked on what a fraction means. Without looking anything up: <their own last item, re-rendered with their variant numbers>",
  "rationale": "Forgetting curve: projected recall is about 62% after 3.0 days. Checking here tests the memory at its most informative point.",
  "lag_hours": 72.0, "expected_recall": 0.62, "grounded_item_id": "itm_...",
  "source": "rules",
  "notes": "what a good answer covers arrives with with_notes=1, after you have answered"
}
```

`404` = the topic has no schedule (nothing was practised; the row should not render a
check button in the first place — gate it on the row already coming from `review/due`).
`expected_recall` is a model-derived estimate and `rationale` already carries it with the
words "about", matching the copy policy (estimates say so). Notes are the **answer key**:
only fetch `with_notes=1` after the learner has answered (or explicitly asked
"what should it have covered?"), never render them beside the question.

### `frontend/src/app/review/page.tsx` — per due row, a check step

```tsx
// types (frontend/src/lib/types.ts, near ReviewDue):
type RetentionCheck = {
  topic_id: string; title: string; question: string; rationale: string;
  lag_hours?: number; expected_recall?: number | null; source: "rules" | "model";
  expected_answer_notes?: string;
};

// per row: a "Show me the check" button that
const check = await get<RetentionCheck>(`review/check`, { topic_id: row.topic_id });
// render check.question + check.rationale (the "why now");
// after the learner answers (the existing review/complete self-report stays the write),
// fetch again with with_notes=1 and show expected_answer_notes as "what it should have covered".
```

### `frontend/src/lib/mock.ts` — required mock (contract checker)

```js
{ method: "GET", match: /^\/api\/v1\/review\/check/, body: { topic_id: "top_mock", title: "what a fraction means", question: "Last time you worked on what a fraction means. Without looking anything up: which number names the parts you took?", rationale: "Forgetting curve: projected recall is about 62% after 3.0 days. Checking here tests the memory at its most informative point.", lag_hours: 72, expected_recall: 0.62, grounded_item_id: null, source: "rules", notes: "what a good answer covers arrives with with_notes=1, after you have answered" } },
```

---

## 4. The approval script (prompt 6)

Proposal rows (`/api/v1/adaptations/pending`, rendered on `/proposals`) now carry an
`ask_text` filled into the set's exact script whenever the evidence sentence shape allows:

> "Hey, we noticed you did averaged 0.40 right after reading a full explanation, but 0.70
> after a worked example. Based on what we've observed, we want to change the current
> approach to Worked example before the next attempt. Do you want to continue, and is
> there anything else you'd like to add?"

Nothing to change for correctness — `ask_text` was already rendered on the proposal
screen — but if the screen emphasises it, the script is now the thing being emphasised.
The other proposal fields (`facts/pattern/hypothesis/change/caveat/expected_effect`) are
unchanged and remain the evidence view. Gate mechanics are unchanged: `ask_text` is a
question, and only `POST /adaptations/{id}/decision` applies anything.

---

## Verification already done on the backend side

- `pytest -q --ignore=tests/test_baseline.py`: **757 passed** (32 new across the two commits).
- `scripts/journey_check.py`: **59/59**; `scripts/adaptation_check.py`: **adapts to the profile**;
  `scripts/failure_matrix_check.py`: **25/25** (includes the resources-search path with all
  providers unreachable, which now also exercises the deterministic picks with an empty list).
- `tests/test_baseline.py` has a pre-existing hang in this sandbox (identical on the base
  commit `38020e7`, verified on a clean worktree) — unrelated to these changes.
