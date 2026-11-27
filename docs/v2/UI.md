# UI — the operator product, and the two surfaces behind it

Adapt 2.0's visible product is a **company-side console**: who is connected, what they are spending,
how Adapt is configured, what it decided and why. The learner-facing app is the customer's, and we
ship one reference implementation so the loop can be validated and demonstrated.

| Surface | Who | Role | Built in |
|---|---|---|---|
| **Console** | the customer's team, and us | the product: connections, credits, configuration, evidence, replay | M1 shell → M3 full |
| **Reference host** | learners, in demos and validation studies | proves the loop end to end; disposable by design | M2.5, M3.5 |
| **Render specs** | the customer's own app | the engine's real interface: a contract the host draws | M2.5 |

Rules of thumb:

* **The console is the commercial face.** A buyer judges the engine in the first ninety seconds of
  the console. It gets the design budget.
* **The reference host never gets house branding.** It is a harness, and it must look like one.
* **Both must look considered.** Sparse is not the same as unfinished; a backend tool with no craft
  looks like a prototype, and prototype-looking infrastructure does not get renewed.

---

## 1. Visual language: blue and cream, pressed into service

We keep Adapt's identity — **ink blue on warm cream** — because it is recognisable, it is not the
default SaaS grey, and it photographs well in a pitch. What changes is the finish: v1 was a warm
consumer surface with hand-drawn art, 20px radii and serif hero type. v2 is an **instrument panel with
a paper stock**: the same palette, higher density, harder edges, real numbers.

```
                            v1                  v2
  --paper      #f4eee0  page          #FBF7EE   page
  --surface    #fbf7ee  cards         #FFFFFF   panels, tables (cream page, white data)
  --ink        #123a8f  headings      #123A8F   headings, selected text
  --accent     #2456c8  primary       #2456C8   primary buttons, links, focus ring
  --body       #3c4a68  body          #3C4A68   body copy
  --muted      #66739a  metadata      #6B7794   metadata, captions
  --line       #e2d9c6  hairlines     #E4DCCB   1px hairline (unchanged idea)
  --measure    —                      #0F5B72   numbers, intervals, chart marks
  --good       —                      #1F6B4A   healthy / within plan
  --warn       —                      #8A5A00   limited evidence, near quota
  --bad        —                      #8C2F1E   breach, blocked, revoked
  --tint-blue  #eaf0fb                #EDF2FB   selection, tags, chips
  --tint-sand  #f7f0df                #F7F0DF   neutral fills, empty states
```

Type — one accent, not two systems:

```
  UI        Inter / system-ui throughout tables, forms, nav. 13 · 14 · 16 · 20px.
  Display   the v1 serif survives, but only on page titles, model cards and empty states —
            never inside a table, never a number. 28/34.
  Data      tabular numerals (font-variant-numeric: tabular-nums), mono for ids and keys.
            An estimate and an interval are always the same width in the same column.
  Labels    sentence case, 13px, --muted. Uppercase mono only for status chips (3–4 chars).
```

Shape and space — the difference between "consumer" and "operator" is mostly here:

```
  radii     8px panels · 6px inputs and buttons · 4px chips   (v1: 20 / 14 / 999)
  borders   1px --line. No drop shadows. Depth comes from the surface/paper contrast.
  grid      8pt. Table rows 40px with 44px headers. Page gutters 48px (24 on narrow).
  content   max 1440px for data pages; a table may go edge to edge with a 48px inset.
  motion    state changes only, ≤120ms, honour prefers-reduced-motion. No page transitions.
  icons     16px, 1.5px stroke, one set. Never emoji.
```

### What makes this read as expensive

Craft in operator tools is not decoration, it is **alignment, restraint and copy**:

1. **One accent colour.** Blue means interactive. Green/amber/red mean state. Nothing else is coloured.
2. **Every number aligned.** Tabular figures, right-aligned numerics, decimals to a fixed place, and
   intervals in brackets of consistent width — so a column of estimates scans vertically.
3. **Hairlines, not boxes.** One 1px divider between regions. Cards inside cards are banned.
4. **Two type sizes per region.** A section title and its body. Anything else is emphasis via weight.
5. **Real empty states**, written as sentences with the next action: "No events in the last 24 hours.
   Check the host's `/v1/observe` call and this key's scopes."
6. **Whitespace at the edges, density in the middle.** Generous page margins make a dense table look
   deliberate rather than cramped.
7. **Keyboard-first.** `⌘K` palette, `j/k` row movement in tables, `R` to replay a decision. Shown in
   the UI as a hint, not a help page.

Things that make dashboards look cheap, all banned: gradients and glows, emoji as icons, more than
one accent hue, centered numeric columns, drop shadows on cards, animated counters, 12-colour charts,
"hero" panels that hold one number, and mock data in screenshots that a buyer can spot (no
"SaaS Inc.", no "John Doe", no 100% uptime).

The **palette, type scale and component grammar are normative in `CONSOLE_STYLE.md`** — beige ground,
deep-blue structure, terracotta confined to the thing you want clicked, with every permitted colour
pair carrying a measured contrast ratio. Where this file describes *what* the console shows,
`CONSOLE_STYLE.md` decides *how it looks*, and a stylesheet that disagrees with it is a bug.

---

## 2. Console information architecture

```
  Overview            credits, connection health, abstention headline, alerts
  Abstentions         rate + counts + reasons, by construct, tier and period
  Connections         who is connected, to what, with which key, doing what volume
  Usage & credits     consumption by plane, connection and period; projection; invoices
  Configuration       profile, tier entitlement, evidence policy, consent, retention, budgets
  Learners            search → one learner → model state per construct
  Evidence            decisions, replays, claim citations, audit trail
  Model cards         public: what each construct means, and what it does not
  Coverage            construct × entitlement × capability × validated → what is served
  Settings            team, roles, environments, webhooks
```

The first three screens answer the questions the buyer actually asks — *who is connected, what are
they spending, and is the engine honest* — so abstention sits at the top level of the navigation, not
buried in a report. Definitions live in `METRICS.md`.

### 2.1 Overview

```
┌ Adapt ─────────────────────────────────────────────────── acme-tutoring ▾  ⌘K  ? ┐
│ Overview   Connections   Usage   Configuration   Learners   Evidence   Model cards │
├───────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│  Active learners        Credits this cycle        Decisions / day    Uptime (30d) │
│  412   ↑ 8%             8,240 / 10,000            3,118   ↑ 12%      99.94%       │
│  of 500 plan            ▇▇▇▇▇▇▇▇▁▁  82%           ╱╲╱╲╱╱╲╱             ● SLO met    │
│                                                                                   │
│  Abstentions (7d)          1,247 of 4,365 decisions · 28.6%  [26.9–30.4%]         │
│  Served (7d)               3,118 of 4,365 decisions · 71.4%                       │
│  ● within band   ▲ +3.1pp vs previous 7d                                          │
│  insufficient evidence 71% · low confidence 14% · conflicting 8% ·                │
│  consent withheld 4% · data quality 2% · unsupported 1%                           │
│  412 entitlement limits · 87 not validated · 0 not capable — outside the rate      │
│  [ investigate ]                                                                  │
│                                                                                   │
│  Connections                                            7 days ▾                 │
│  ────────────────────────────────────────────────────────────────────────────────  │
│  ● production   acme-web          last event  4s ago    2,904 /day   p95  82ms    │
│  ● staging      acme-web-stg      last event  2d ago    0            —            │
│  ○ sandbox      local dev         never                  —            —            │
│                                                                                   │
│  Needs attention                                                                  │
│  ▲ Abstentions rose to 11% on "spatial" for one host — 3 candidates.  [ open ]     │
│  ▲ Credit projection: plan exhausted in 6 days at current rate.       [ open ]     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* Four counters, each with a comparison and a micro-chart. No fifth, no "hero".
* **Abstention gets its own block, not a counter tile.** It shows the rate *and* the counts
  (`1,247 of 4,365 decisions`), the interval, the delta, the reason mix, and the entitlement limits
  that are deliberately outside the rate. A rate without a denominator is a rumour; a rate without
  reasons is a scolding.
* The denominator sentence is always visible, never a tooltip: *"decisions that requested a
  recommendation; replays, paused connections, test traffic, refusals and 5xx excluded."*
* Alerts are sentences with a cause and one link, never a red dot.

### 2.2 Abstentions

The screen a sceptical buyer opens second. It must survive being read closely.

```
┌ Abstentions            7 days ▾   all connections ▾   all tiers ▾   [ export ]     │
│                                                                                    │
│  28.6%   [26.9–30.4%]      1,247 abstentions   4,365 valid decide requests          │
│  71.4%   served            3,118 recommendations served                            │
│  ▲ +3.1pp vs previous 7d   ● within band                                           │
│  ╱╲╱╱╲╱╲╱╱╲  with band edges and any floor change annotated as a vertical marker    │
│                                                                                    │
│  By reason                     count     share    ▲ vs prev                        │
│  insufficient evidence           885      71%      +2.4pp                          │
│  low confidence                  175      14%      +0.6pp                          │
│  conflicting signals             100       8%      −0.2pp                          │
│  consent withheld                 50       4%      +0.9pp   ← host scopes          │
│  data quality                     25       2%      −0.4pp   ← host instrumentation │
│  unsupported request              12       1%      —                               │
│  ── not abstentions: the three axes, kept apart ───────────────────────────────    │
│  not entitled    (contract)      412   [ coverage ]                                │
│  not validated   (measurement)    87   [ coverage ]  ← our gate programme           │
│  not capable     (engineering)     0   [ internal ]                                │
│  policy refusals                   3   [ refused requests ]                        │
│  errors (5xx)                      1   [ reliability ]                             │
│                                                                                    │
│  By construct        rate      decisions   abstentions   needs (median)            │
│  skill.mastery        4.1%      3,980        163         —                         │
│  strategy             31.8%     1,204        383         +9 trials                 │
│  calibration          26.4%       512        135         +4 judgements             │
│  spatial              62.0%       141         87         +6 trials, 1 session        │
└────────────────────────────────────────────────────────────────────────────────────┘
```

* **Rate, counts and interval in one row**, so the number can be checked without export.
* **Served rate sits directly under the abstention rate**, because either alone is a half-truth: a
  tenant with no validated constructs can show 0% abstention while serving nothing (`METRICS.md` §1.8).
  The pair is the metric; the single number is the misreading.
* **Reasons are a column, not a chart.** Six rows reads faster than a pie and cannot be misread.
* The two customer-side reasons are flagged inline, because "consent withheld" is a setting in *their*
  app, not a weakness of ours.
* **`needs (median)`** turns the number into an instruction: six more trials, one more session.
* A floor change is drawn on the trend as an annotated marker, so a definition or threshold change can
  never read as a behaviour change.

### 2.3 Connections — "who is connected to what"

```
┌ Connections                                          [+ new connection]  search  │
│  ● production   acme-web   ak_live_7f3a…   4s ago                               │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  Environment   production                    Key      ak_live_7f3a… (observe,    │
│  Host          acme-web  (Next.js, EU-West)            decide, read_model)      │
│  Webhook       https://acme.app/hooks/adapt    ● 200 last delivery 4s ago        │
│  Learners      412 active · 3,904 total        Created 2026-09-14 by R. Otieno    │
│  Events 7d     20,331  (observe 19,880 · decide 3,118 · probe 233)               │
│  Latency       p50 41ms · p95 82ms · errors 0.02%                                │
│  Credits       8,240 used of 10,000 this cycle                                   │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  [ rotate key ]  [ pause ingestion ]  [ revoke ]        revoking is immediate    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

A connection is **host app × environment × key × webhook**, with the four facts that matter on the
row: last event, daily volume, p95 latency, credits. Keys are shown as prefix only — the full key
exists once, at creation, and the UI says so in those words.

### 2.4 Usage & credits

```
┌ Usage            this cycle · 1–30 Nov            [ export CSV ]  [ invoices ]   │
│  8,240 of 10,000 credits            82%  ▇▇▇▇▇▇▇▇▁▁     runs out ~22 Nov          │
│                                                                                   │
│  by plane         observe 61%   decide 31%   probes 8%                            │
│  by learner       top 10 accounts for 34% of spend      [ see all 412 ]           │
│  by day           ╱╲╱╲╱╱╲╱╱╲╱╲   with the plan line                               │
│                                                                                   │
│  Metering is append-only and auditable. Every line has a reason and a reference.   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* Credits are shown as **consumed / plan** with a projection date, because "will I run out" is the
  question an operator actually asks.
* Per-learner spend is available but sits behind a click: it is the fastest way to discover a host
  that is polling `decide` in a loop.

### 2.5 Configuration

Everything the engine does is visible and changeable, grouped by the thing it affects. Every change is
audited with actor, before/after and timestamp.

```
┌ Configuration                                            [ save ]  [ history ]   │
│  Profile            adult_general ▾        (switching profile is an audited event)│
│  Evidence policy    floors: standard ▾   abstain below floor: on   [ explain ]    │
│  Consent scopes     interaction log ✓  probes ✓  model summary ✓  research ✗     │
│  Retention          interaction events 24 months ▾   probe trials 36 months ▾    │
│  Accessibility      required: audio, 44px targets, no-scroll     [ host contract ]│
│  Provider budget    summarisation 12k tokens/day · TTS off                        │
│  Webhooks           decision.created ▾  consent.changed ▾  quota.warning ▾       │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* The **evidence policy** is not a slider: it is `standard | strict | research`, each with a written
  definition, because loosening it changes what the engine is allowed to claim.
* Retention and consent scopes are shown as a matrix of *what is stored* × *who may read it*, in the
  same words the learner sees.

### 2.6 Learners and the inspector

Search by id or your own external id. The inspector is the same shape as the decision replay, because
they are the same question: *what does it think, and why*.

```
┌ Learner lrn_8f2c…      adult_general   tier: Deep   first seen 12 Sep   4s ago   │
│  Construct        serving  validated  estimate [interval]     evidence   bound by │
│  skill.mastery       L1        L1     0.41  [0.33–0.49]       212 trials   —      │
│  strategy            L2        L2     0.68  [0.51–0.82]        34 trials   —      │
│  calibration         L2        L2     0.71  Brier [0.63–0.79]  28 judges   —      │
│  retention           L1        L1     —                        —           validation │
│  spatial             —         —      insufficient evidence    6 of 12     —      │
│                                                                                   │
│  Entitled to L3; serving L1 where validation has not passed.  [ see coverage ]     │
│                                                                                   │
│  Recent decisions  [ replay ]                                                     │
│  14:02  worked_example_then_fade  ⟨accepted⟩  cites clm_0142 v3                   │
│  13:58  extra_practice            ⟨rejected: "too easy">⟩                         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* `—` and "insufficient evidence" are rendered as ordinary states, not blanks.
* **The badge is the validated level, never the entitlement.** A Deep tenant sees `L1` here for a
  construct that has not passed its gates, plus the `bound by validation` note. Showing an entitled
  level would be the single most dishonest pixel in the product.
* `serving` and `validated` are separate columns because they can differ on the other axis too: a
  capability that exists and is validated but sits above the contract is shown as `serving L1 · bound by
  entitlement`, which is a fair commercial conversation rather than a hidden downgrade.
* Rejection reasons are shown verbatim. They are the best diagnostic data in the system.

### 2.7 Model cards — public

Publishable, linkable, and the honest answer to "how do you know?":

```
  Construct      Confidence calibration
  What it is     agreement between stated confidence and observed accuracy, same task
  What it is not self-esteem, humility, honesty, "self-awareness"
  How measured   confidence tap 0–100 per item; Brier score and ECE, within-person
  Current status L2 on 2 of 3 validation gates · fairness audit pending
  Cannot do      compare learners, produce a trait, be used in selection
  Version        cmp_calibration v2 · reviewed 2026-10-01 · history
```

Static pages, cache-able, no auth — the same content a customer's procurement reviewer needs.

---

### 2.8 Coverage — the three axes, side by side

The screen that makes the entitlement/capability/validation split visible instead of rhetorical. Public
view for anyone, tenant view adds your entitlement column.

```
┌ Coverage            tier: Deep      [ public view ]   [ export ]   updated 4h ago │
│  Construct        capability  validated  serving   gates passed        debt         │
│  skill.mastery        L3         L3        L3      6/6                 —            │
│  strategy             L3         L2        L2      4/6 incremental validity         │
│  calibration          L3         L2        L2      5/6 fairness audit pending       │
│  retention            L3         L1        L1      2/6 ← you are entitled to L3     │
│  spatial              L2         L1        L1      3/6                              │
│                                                                                     │
│  1 of 5 constructs is entitled above its validated level. Validation debt: 20%.      │
│  You have bought the capacity; we have not yet earned the claim.                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

* Four columns, because collapsing any two of them is the overclaim this exists to prevent.
* **`debt`** names the failing gate in words, not a score: "fairness audit pending" is a commitment,
  a percentage is a shrug.
* The closing sentence is the product's whole commercial honesty in one line.
* Generated from the validation harness, never hand-edited; a mismatch between this screen and the
  harness fails CI.

---

## 3. Signature components

**The Why panel**, console edition — the four lines that everything else supports:

```
  Adapt will    show a worked example, then fade it
  Because       3 consecutive errors after instruction · method effect +0.12 [0.02–0.23], n=34
  How sure      ●●●○  fairly sure        [ what this means ]
  Based on      clm_0142 v3 · clm_0310 v1 · 34 trials · 3 sessions · 4 trials excluded
```

**The three-axis chip.** Wherever a level appears, it carries its provenance:

```
  DEEP · capable L3 · validated L1  →  serving L1        [ bound by validation ]
```

Read left to right: what you bought, what the code does, what we proved, what you get. It is the same
object on the inspector, in the API reference and in the sales deck, because it is rendered from the
same fields. A bare "L3" badge anywhere in the product is a defect.

**The abstention row** is a first-class table state:

```
  spatial          —        insufficient evidence     needs 6 trials, 1 session   [ run probe ]
```

**Interval formatting.** Always `point [low–high]`, always right-aligned, always tabular. The interval
kind (CI, credible interval, bootstrap) is in the column tooltip and in the exported CSV. **Never** a
bare percentage where the interval is wide enough to change the decision.

**Charts** are sparklines and one-line time series. No pie charts, no dual axes, no more than three
series, no chart that exists to fill a card.

---

## 4. Reference host (learner surface)

Deliberately plain, deliberately disposable — a harness that shows the contract.

```
┌──────────────────────────────────────────────────────────────┐
│   Linear equations                                    4 of 7  │
│   ─────────────────────────────────────────────────────────  │
│   Here is one worked out. Watch the steps.                    │
│        2x + 5 = 13   ─5 both sides ─▶  2x = 8   ─÷2 ─▶  x = 4 │
│   [ show me the next step ]   [ I'll try one ]                │
│   ─────────────────────────────────────────────────────────  │
│   Why this?  You asked for the steps; this worked before.     │
│   [ stop for now ]                                            │
└──────────────────────────────────────────────────────────────┘
```

* No score, no XP, no streak, no leaderboard, no percentage-of-mastery.
* One primary action per screen; "stop for now" always visible, one tap.
* "Why this?" expands the Why panel. Never hidden in settings.

### 4.1 The `cognitive_support` face

Hard rules from the design and Easy Read research, not preference:

| Rule | Reason |
|---|---|
| **No scrolling** — page with two large buttons | Scrolling is a persistent failure for this group; it also hides the end of the screen |
| **Buttons look like buttons; icons resemble the real object** | Reducing abstraction improves recognition |
| **Photographs of real people over drawings or line symbols** | 58% preferred real photos vs ~8% drawings, ~11% line symbols |
| **At most 3 options** | Fewer choices, fewer errors |
| **Every instruction available as audio**, large speaker control | Keep reading load out of the task |
| **One idea per screen**, sentences ≤15 words, ≤50–100 words | Easy Read layout, applied to screens |
| **Targets ≥44×44px** (WCAG AA floor is 24) | Motor variability |
| **"Stop" and "I need help" always in the same place** | WCAG 3.2.6, and the duty not to exploit vulnerability |
| **No streaks, no praise for persistence, no return notifications** | AI Act 5(1)(b), Digital Fairness Act Art. 17 |
| **Breaks offered, never scored** | Adherence diagnoses our design, it is not a learner target |
| **Nothing destructive without undo** | Support, not testing |

```
┌──────────────────────────────────────────────────────────────┐
│  [ 🔊 Read this ]                              [ Stop ]       │
│   ┌──────────┐                                                │
│   │  photo   │   Put the cup in the sink.                     │
│   └──────────┘                                                │
│      [ Do it ]        [ Show me ]                             │
│   [ ← Back ]                            [ Next → ]            │
└──────────────────────────────────────────────────────────────┘
```

Absent by design: numeric progress, timers, "great job!", logo animation, mascot.

---

## 5. Uncertainty, by audience

| Audience | Representation |
|---|---|
| Console | `0.68 [0.51–0.82]`, right-aligned, `n` adjacent, interval type in the tooltip |
| Docs / CSV export | interval + estimator name + what would narrow it |
| Learner (`adult_general`) | three bands in words: confident / fairly sure / not sure yet |
| Learner (`cognitive_support`) | no numeric uncertainty; the app changes its support level and shows what it will try next |

Never: a percentage that reads as an accuracy score, a "confidence: 87%" badge, or a dial implying
precision we do not have.

---

## 6. Accessibility

We are B2B: the European Accessibility Act's closed service list (e-commerce, banking, e-books,
transport, telecoms, audiovisual) does not cover an engine API, but our customers' consumer apps may
be covered. So we make their compliance possible and evidenced:

* instruments and decisions carry accessibility contract fields (`ADR-001` §4);
* the engine **refuses to serve an instrument to a surface that declares it cannot meet them**;
* we ship a statement template and a WCAG 2.2 AA conformance report the customer can publish;
* console and reference host meet AA themselves — keyboard-only pass, screen-reader pass, axe in CI.

Symbols: ARASAAC is CC BY-**NC**-SA, so it cannot ship in a paid product; Mulberry is CC BY-SA;
SymbolStix, PCS and Widgit are commercial. **The engine ships no symbol assets** — only a
`symbol_set` field and a licence registry.

---

## 7. Banned patterns

1. Streaks, loss-framed progress, expiring rewards.
2. Confirm-shaming, pre-ticked consent, hidden opt-outs.
3. Stopping or cancelling taking more steps than starting.
4. Countdowns, artificial urgency, scarcity.
5. Celebratory animation on correct answers in `cognitive_support`.
6. Confidence as a score; "IQ"; "brain age"; "learning style".
7. Autoplay audio or motion without a visible stop.
8. Notifications whose purpose is bringing a learner back.
9. Ranking learners against each other, anywhere, including admin tables.
10. Showing a third party a learner's data the learner has not consented to share — including
    "engagement" summaries.

---

## 8. Build order

| Milestone | UI deliverable |
|---|---|
| M0 | none — health endpoint only |
| M1 | console shell: auth, nav, Overview with real connection and credit data, Connections list |
| M2 | Evidence screen: decision list, replay, claim citations |
| M2.5 | reference host, one mechanic, Why panel + abstention panel; axe + screenshot tests |
| M3 | full console: Usage, Configuration, Coverage matrix, Learner inspector (three-axis), model cards public, `⌘K` |
| M3.5 | `cognitive_support` face of the reference host, co-designed, AT-tested |
| M5 | handover kit: render spec, statement template, conformance report, two worked examples |

Testing discipline: axe-core in CI, keyboard-only pass, screen-reader pass (NVDA or TalkBack), visual
regression on every page at 1440/1024/390, and **five usability participants per surface** before
anything is called usable. For `cognitive_support`, co-design with learners and support workers is an
input to the build, not a review at the end.
