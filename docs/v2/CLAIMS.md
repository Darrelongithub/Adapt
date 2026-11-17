# The evidence surface ("psychological facts")

The engine must be able to say *why* it did something, and that reason must be a fact from the
literature with a citation — not a plausible sentence generated on the spot.

This is the surface that answers "is there a psychological API?" **No such public API existed when
we looked.** What exists is: bibliographic corpora (free), item pools and instruments (mostly
licensed), and normed tests (commercial, restricted). So we build the facts layer ourselves, and we
are explicit about what we may redistribute.

**Hard boundary:** this surface describes *knowledge*, never *people*. It does not accept a learner
ID, and no response may be phrased as a judgement about an individual.

---

## 1. The claim record

```json
{
  "id": "clm_0142",
  "version": 3,
  "statement": "Worked examples outperform equivalent problem practice for novices acquiring a schema.",
  "constructs": ["worked_examples", "problem_solving"],
  "population": {"education": "novice", "age": "adult", "clinical": "general"},
  "effect": {
    "measure": "post-test accuracy",
    "estimate": 1.0,
    "metric": "hedges_g",
    "ci": [0.7, 1.3],
    "k_studies": 12,
    "k_participants": 1400
  },
  "certainty": "moderate",
  "applies_when": ["novice", "procedural or well-structured skill", "high intrinsic load"],
  "does_not_apply_when": ["expert learners — expertise reversal", "ill-structured problems"],
  "mechanism": "reduces extraneous load during schema acquisition",
  "sources": [
    {"ref": "Sweller & Cooper 1985", "doi": "10.1207/s1532690xci0201_3", "type": "primary"},
    {"ref": "meta-analysis of worked-example effect", "doi": "…", "type": "synthesis"}
  ],
  "licence": {"text": "facts and citation only; no full text stored", "redistribution": "permitted"},
  "reviewed_by": "internal-reviewer-1",
  "reviewed_at": "2026-10-01",
  "supersedes": "clm_0142@2",
  "status": "active"
}
```

Certainty levels: `high` (multiple syntheses, consistent, replicated), `moderate` (syntheses
consistent but heterogeneous), `low` (single studies or conflicting), `contested` (evidence points
both ways — served with both sides). `contested` claims may be cited in an explanation but may not
drive a decision alone.

`does_not_apply_when` is mandatory, not optional. A claim without a stated boundary is how a
learning-science engine turns into a horoscope.

---

## 2. Endpoints

| Endpoint | Notes |
|---|---|
| `GET /v1/evidence/claims` | Filter by construct, population, certainty, profile. Paginated, ETag'd. |
| `GET /v1/evidence/claims/{id}` | Single record, versioned; `?version=` for historical decisions. |
| `GET /v1/evidence/claims/{id}/history` | Supersession chain, so an old decision's citation still resolves. |
| `GET /v1/constructs` | Construct registry incl. `does_not_mean` and prohibited claim templates. |
| `GET /v1/instruments` | Instrument metadata; includes licence and whether norms exist. |
| `GET /v1/instruments/{id}/norms` | **Only** when the licence permits redistribution; 404 otherwise, with the reason. |

All read-only, cached, no learner data, safe to expose to a tenant's engineers.

---

## 3. Ingestion pipeline

```
OpenAlex (CC0, ~250m works) ──┐
PubMed ───────────────────────┼─▶ candidate retrieval ─▶ extraction ─▶ human review ─▶ versioned claim
CORE ─────────────────────────┘        (scripted)        (structured)     (required)
```

Rules:

* **Nothing becomes a claim without a named human reviewer.** An LLM may draft the extraction; it may
  not publish it. Same rule as the v1 approval gate, applied to our own knowledge base.
* Every claim stores **DOI + effect + direction + population + boundary**. A claim we cannot cite at
  that granularity is downgraded to `low` or dropped.
* **Re-review on a schedule** (24 months) and on any superseding meta-analysis. The service reports
  staleness; `decide` downweights claims past their review date.
* **Versioning is append-only.** Decisions cite `claim@version`. Superseding a claim never rewrites
  history — the old decision must still reproduce.

---

## 4. Licensing, concretely

| Source | Status | What we may do |
|---|---|---|
| OpenAlex | CC0 | store metadata, cite freely |
| PubMed / CORE | per-item licences | store metadata + abstract-level facts where permitted; never republish full text |
| IPIP item pool | public domain, commercial use OK | may ship items directly, with attribution |
| PROMIS | free with attribution, health-domain | cite; ship only under the licence's terms |
| ICAR | **academic use only** | cannot ship commercially; may inspire our own items, not copy them |
| Pearson / CANTAB / commercial batteries | paid, restricted | out of scope without a contract; never scrape, never reverse-engineer |

Consequence for the roadmap: our instruments must be **ours or public-domain**, validated by us,
because the good commercial ones are a licensing negotiation, not an implementation task.

---

## 5. How claims reach a decision

`decide` composes recommendations from: learner state (plane 1) × applicable claims (plane 3) ×
profile rules. Every recommendation carries `cites`, and the explanation shown to a learner is
generated **from the claim's `statement` and the learner's own numbers**, not invented — an LLM may
rephrase, and the rephrasing is constrained to those two inputs and checked for the `prohibited_claims`
templates.

If no claim applies, the recommendation is either abstained or marked `basis: "heuristic"` and
excluded from L2 reporting. **A recommendation with no citation never counts as evidence of the
model working.**

---

## 6. What this surface must never become

* A score about a person. No `learner` parameter, anywhere.
* A norm feed. If a licensed instrument's norms are not redistributable, we say "not available",
  we do not approximate them.
* A "brain type" or personality classifier.
* An uncited claim. No DOI, no claim.
* A retraction sink. Retracted papers are excluded at ingestion (`retraction` check via Crossref),
  and a claim whose only sources are retracted is withdrawn, with the withdrawal recorded and the
  decisions that cited it flagged for review. *(We have already been bitten by citing a retracted
  paper during planning — see `docs/v2/NOTES.md`.)*
