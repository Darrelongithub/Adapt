/* The console shell: three screens, real data only.
 *
 * The renderer is a pure function of a state object, and every value it shows
 * carries `data-metric="<id>"` where `<id>` is a key in `data-map.json`. That is
 * the traceability contract: `tests/test_console.py` asserts that every id the
 * renderer can emit is in the map, that the map's JSON path exists in a live
 * response, and — the part that actually catches a hardcoded number — that every
 * rendered value *changes* when the state changes.
 *
 * No framework and no build step: the file a browser runs is the file the test
 * runs, so "it passed in the test" and "it works on screen" are the same claim.
 */

/* Numbers are formatted by type, never printed raw (CONSOLE_STYLE §4).
 *
 * JSON floats do not belong on a screen. Four types, four formatters, and no
 * `toFixed(n)` anywhere else in this file — a fixed width is how `0.5400` happens,
 * and `0.5400` claims two digits of precision the estimator does not have. The
 * test in `tests/test_console.py` greps every rendered run for a third decimal
 * or a trailing zero, so a new cell cannot bring the old habit back with it.
 */

const PERCENT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const TWO = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export const NO_DATA = "no data";

/** A rate: one decimal and a percent sign — `0.0%`, `88.0%`. */
export function formatPercent(value) {
  return value === null || value === undefined ? NO_DATA : `${PERCENT.format(value * 100)}%`;
}

/** An estimate or a probability: two decimals — `0.54`, `0.36`. */
export function formatEstimate(value) {
  return value === null || value === undefined ? NO_DATA : TWO.format(value);
}

/** An interval: two decimals, in brackets — `[0.33, 0.49]`. */
export function formatInterval(interval) {
  if (!interval || interval.low === null || interval.high === null) {
    return "interval unavailable";
  }
  return `[${formatEstimate(interval.low)}, ${formatEstimate(interval.high)}]`;
}

/** A count: an integer, with separators only once they earn their width. */
export function formatCount(value) {
  if (value === null || value === undefined) return NO_DATA;
  const number = Number(value);
  return Math.abs(number) < 10_000 ? String(number) : number.toLocaleString("en-US");
}

/** A multiplier of a baseline: integral when it is, one decimal when it is not. */
export function formatMultiple(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isInteger(number) ? `${number}\u00d7` : `${number.toFixed(1)}\u00d7`;
}

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat().filter(Boolean) };
}

function metric(id, text, attrs) {
  return h("span", { "data-metric": id, ...(attrs || {}) }, text);
}

function row(label, valueNode) {
  return h("div", { class: "row" }, h("span", { class: "label" }, label), valueNode);
}

function card(title, ...children) {
  const id = title.toLowerCase().replace(/[^a-z]+/g, "-");
  return h(
    "section",
    { class: "card", "aria-labelledby": `${id}-title` },
    h("h2", { id: `${id}-title` }, title),
    ...children,
  );
}

/** Overview: the abstention/served pair from one query, credits, connections. */
function overview(state) {
  const m = state.metrics;
  const empty = m.denominator === 0;

  const abstention = card(
    "Abstention",
    row(
      "rate",
      metric("overview.abstention.rate", formatPercent(m.rate), {
        // The accent is earned by a value you would act on (CONSOLE_STYLE §1).
        // A rate of zero is the number that says "nothing has happened yet", and
        // marking it terracotta argues with the sentence underneath. A null keeps
        // the mustard italic: "no data" is never decorated either.
        class: m.rate === null ? "null" : m.rate > 0 ? "mark" : "value",
      }),
    ),
    row(
      "abstentions / decisions",
      h(
        "span",
        { class: "value" },
        metric("overview.abstention.numerator", formatCount(m.numerator)),
        " / ",
        metric("overview.abstention.denominator", formatCount(m.denominator)),
      ),
    ),
    row("95% interval", metric("overview.abstention.interval", formatInterval(m.interval))),
    row(
      "served, same denominator",
      metric("overview.served.rate", formatPercent(m.served.rate), {
        class: m.served.rate === null ? "null" : "value",
      }),
    ),
    row(
      "reasons",
      metric(
        "overview.abstention.reasons",
        Object.keys(m.reasons).length === 0
          ? `no abstentions in ${formatCount(m.denominator)} decisions`
          : Object.entries(m.reasons)
              .map(([reason, count]) => `${reason} ${formatCount(count)}`)
              .join(", "),
      ),
    ),
    row(
      "excluded, and counted",
      metric(
        "overview.abstention.exclusions",
        `${formatCount(m.exclusions.test_traffic)} test, ` +
          `${formatCount(m.exclusions.paused_or_revoked)} paused, ` +
          `${formatCount(m.exclusions.replays)} replays`,
      ),
    ),
    h(
      "p",
      { class: "sentence", "data-metric": "overview.denominator.sentence" },
      empty
        ? "No decisions in this window, so there is no rate to report — not a 0% one."
        : `Rate over ${formatCount(m.denominator)} valid decisions. ` +
          `${formatCount(m.exclusions.test_traffic)} test calls, ` +
          `${formatCount(m.exclusions.paused_or_revoked)} paused-connection calls and ` +
          `${formatCount(m.exclusions.replays)} replays are excluded and counted.`,
    ),
    h(
      "p",
      { class: "sentence" },
      "0% abstention on a quiet tenant is not success: it means nothing was asked. " +
        "The pair is always shown together because either number alone can be gamed.",
    ),
  );

  const credits = card(
    "Credits",
    row("available", metric("overview.credits.available", formatCount(state.credits.available))),
    row("consumed", metric("overview.credits.consumed", formatCount(state.credits.consumed))),
    row("held", metric("overview.credits.held", formatCount(state.credits.held))),
    row("granted", metric("overview.credits.granted", formatCount(state.credits.granted))),
    h(
      "p",
      { class: "sentence" },
      "granted = available + held + consumed + forfeited; consumption reads settles only, " +
        "so an abstention costs nothing.",
    ),
  );

  const connections = card(
    "Connections",
    state.connections.length === 0
      ? h(
          "p",
          { class: "sentence", "data-metric": "overview.connections.empty" },
          "No connections yet. Create one with POST /v1/admin/connections, then send a batch.",
        )
      : connectionTable(state),
  );

  return h("div", { class: "screen", id: "overview" }, abstention, credits, connections);
}

function connectionTable(state) {
  const byId = new Map(state.metrics.by_connection.map((c) => [c.connection_id, c]));
  return h(
    "table",
    {},
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", {}, "host"),
        h("th", {}, "environment"),
        h("th", {}, "status"),
        h("th", {}, "monthly quota"),
        h("th", {}, "decisions"),
        h("th", {}, "abstention"),
      ),
    ),
    h(
      "tbody",
      {},
      state.connections.map((c) => {
        const usage = byId.get(c.id);
        return h(
          "tr",
          {},
          h("td", {}, metric(`overview.connection.${c.id}.host`, c.host_name)),
          h("td", {}, metric(`overview.connection.${c.id}.environment`, c.environment)),
          h("td", {}, metric(`overview.connection.${c.id}.status`, c.status)),
          h("td", {}, metric(`overview.connection.${c.id}.quota`, formatCount(c.event_quota_monthly))),
          h(
            "td",
            {},
            metric(
              `overview.connection.${c.id}.decisions`,
              usage ? formatCount(usage.denominator) : formatCount(0),
            ),
          ),
          h(
            "td",
            {},
            metric(
              `overview.connection.${c.id}.abstention`,
              usage ? formatPercent(usage.rate) : NO_DATA,
            ),
          ),
        );
      }),
    ),
  );
}

/** True when entitlement, service and validation do not agree. */
function hasGap(skill) {
  const limitedBy = skill.limited_by;
  return (
    (limitedBy !== null && limitedBy !== undefined && limitedBy !== "nothing") ||
    skill.served !== skill.entitlement_level ||
    skill.validation_level !== skill.served
  );
}

/** The level sentence: a function of the three axes, with a stated grammar.
 *
 * The sentence used to read "serving L1 where validation has not passed
 * (capability L1, validated L1)" — a gap asserted in the first clause and denied
 * in the parenthetical, on the line most likely to be quoted. It is now built
 * from entitlement (E), served (S), validated (V) and limited_by (L), and each
 * branch says one thing:
 *
 *   L set        → limited by a tenant setting, not by the evidence
 *   S = V = E    → no gap between entitlement, capability and validation
 *   V ≠ S        → the served claim has not passed validation
 *   S ≠ E        → the entitled claim has not passed, so serving stops at S
 *
 * `tests/test_console.py` renders all four (L1/L1, L1/L0, L2/L1, limited_by set)
 * and asserts the clause, so the sentence cannot drift back into contradiction.
 */
function levelSentence(skill) {
  const entitled = skill.entitlement_level;
  const served = skill.served;
  const validated = skill.validation_level;
  const head = `Entitled to ${entitled}; serving ${served}, validated at ${validated}.`;
  const limitedBy = skill.limited_by;

  if (limitedBy !== null && limitedBy !== undefined && limitedBy !== "nothing") {
    return `${head} Limited by "${limitedBy}", a tenant setting rather than the evidence.`;
  }
  if (served === entitled && validated === served) {
    return `${head} No gap between entitlement, capability and validation.`;
  }
  if (validated !== served) {
    return validated === "L0"
      ? `${head} The ${served} claim has not passed validation at any level.`
      : `${head} The ${served} claim has not passed validation; ${validated} has.`;
  }
  return `${head} The ${entitled} claim has not passed validation, so serving stops at ${served}.`;
}

/** The same object wears the same name on every screen (CONSOLE_STYLE §5).
 *
 * `/v1/admin/connections` has a host name; `/v1/admin/metrics/abstention` only
 * has an id. Printing the UUID in one table and the host name in the other made
 * two screens describe one connection two ways. A connection with no host name —
 * or one the console has not loaded — gets a short monospace id, because a
 * 36-character UUID in a 13px column is not an identity, it is a wrapping bug.
 */
function identify(connections, connectionId) {
  const match = connections.find((c) => c.id === connectionId);
  if (match && match.host_name) return { name: match.host_name, mono: false };
  return { name: `${String(connectionId).slice(0, 8)}…`, mono: true };
}

/** The polling sentence: what the check compares against, and why. */
function pollingSentence(polling) {
  const baseline = polling.baseline_repeat_ratio;
  const counted = polling.by_connection.length;
  const repeats = polling.by_connection.reduce((total, row) => total + row.repeats, 0);
  const floor = `${formatCount(polling.min_repeats)}-repeat floor`;
  const tail = "A host retrying for a non-abstention shows up here, not in the bill.";

  if (baseline === 0) {
    const measure =
      `with the baseline at zero the check uses the raw ratio: ` +
      `${formatCount(repeats)} repeat${repeats === 1 ? "" : "s"} across ` +
      `${formatCount(counted)} connection${counted === 1 ? "" : "s"} with decisions in ` +
      `this window, ${floor}`;
    return polling.alerting.length === 0
      ? `No connection is repeating decide on the same learner — ${measure}. ${tail}`
      : `${alerted(polling)} repeating decide on the same learner — ${measure}. ${tail}`;
  }

  const threshold = `${formatMultiple(polling.multiple)} the baseline`;
  return polling.alerting.length === 0
    ? `No connection is repeating decide on the same learner above ${threshold}, across ` +
        `${formatCount(counted)} connection${counted === 1 ? "" : "s"} with decisions in ` +
        `this window and a ${floor}. ${tail}`
    : `${alerted(polling)} repeating decide on the same learner above ${threshold} — ` +
        "usually a host retrying for a non-abstention.";
}

/** "1 of 2 connections is" — the verb agrees with the count that leads the clause. */
function alerted(polling) {
  const firing = polling.alerting.length;
  const counted = polling.by_connection.length;
  return (
    `${formatCount(firing)} of ${formatCount(counted)} connection${counted === 1 ? "" : "s"} ` +
    `${firing === 1 ? "is" : "are"}`
  );
}

/** Connections: the polling check, per connection, with the alert state named. */
function connectionsScreen(state) {
  const polling = state.metrics.abstention_polling;
  return h(
    "div",
    { class: "screen", id: "connections" },
    card(
      "Polling check",
      row(
        "baseline repeat ratio",
        metric(
          "connections.polling.baseline",
          formatEstimate(polling.baseline_repeat_ratio),
        ),
      ),
      row(
        "connections alerting",
        metric("connections.polling.alerting", formatCount(polling.alerting.length), {
          class: polling.alerting.length === 0 ? "value" : "chip",
        }),
      ),
      row(
        "alert threshold",
        // A zero baseline is never a multiple (METRICS.md §1.5): the API sends
        // `multiple: null` there, and a screen that printed "5x baseline" next to
        // a baseline of 0.00 was drawing a threshold that does not exist. The
        // configured constant is still named, as a constant that is not applied —
        // which also keeps this value traceable: it moves when the constant does.
        polling.multiple === null
          ? metric(
              "connections.polling.threshold",
              "baseline is zero — decided on the raw ratio, so the " +
                `${formatMultiple(polling.threshold_multiple)} threshold is not applied`,
            )
          : metric("connections.polling.multiple", `${formatMultiple(polling.multiple)} baseline`),
      ),
      h(
        "p",
        { class: "sentence", "data-metric": "connections.polling.sentence" },
        pollingSentence(polling),
      ),
    ),
    card(
      "By connection",
      state.metrics.by_connection.length === 0
        ? h(
            "p",
            { class: "sentence", "data-metric": "connections.byconnection.empty" },
            "No decisions in this window. Nothing to break down — and nothing to celebrate.",
          )
        : h(
            "table",
            {},
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "connection"),
                h("th", {}, "decisions"),
                h("th", {}, "abstention"),
                h("th", {}, "served"),
              ),
            ),
            h(
              "tbody",
              {},
              state.metrics.by_connection.map((c) => {
                const identity = identify(state.connections, c.connection_id);
                return h(
                  "tr",
                  {},
                  h(
                    "td",
                    {},
                    metric(`connections.row.${c.connection_id}.host`, identity.name, {
                      class: identity.mono ? "mono" : undefined,
                    }),
                  ),
                  h(
                    "td",
                    {},
                    metric(`connections.row.${c.connection_id}.n`, formatCount(c.denominator)),
                  ),
                  h(
                    "td",
                    {},
                    metric(`connections.row.${c.connection_id}.abstention`, formatPercent(c.rate)),
                  ),
                  h(
                    "td",
                    {},
                    metric(`connections.row.${c.connection_id}.served`, formatPercent(c.served.rate)),
                  ),
                );
              }),
            ),
          ),
      h(
        "p",
        { class: "sentence" },
        "Last-event time and event volume per connection are not on any endpoint yet " +
          "(UI.md 2.3 asks for both). The quota and the decision count are; nothing here is invented.",
      ),
    ),
  );
}

/** Learner inspector: one row per construct with evidence, and the level, honestly. */
function inspector(state) {
  const mastery = state.mastery;
  const skills = mastery === null ? [] : mastery.skills;
  return h(
    "div",
    { class: "screen", id: "learner" },
    card(
      "Learner",
      mastery === null
        ? h(
            "p",
            { class: "sentence", "data-metric": "learner.empty" },
            "Enter a learner id above, or run adapt sandbox seed. A learner we have never " +
              "seen is a 404, not a fabricated prior.",
          )
        : skills.length === 0
          ? h(
              "p",
              { class: "sentence", "data-metric": "learner.empty" },
              "No construct has evidence for this learner yet.",
            )
          : h(
              "table",
              {},
              h(
                "thead",
                {},
                h(
                  "tr",
                  {},
                  h("th", {}, "construct"),
                  h("th", {}, "estimate"),
                  h("th", {}, "interval"),
                  h("th", {}, "evidence"),
                  h("th", {}, "serving"),
                  h("th", {}, "validated"),
                ),
              ),
              h(
                "tbody",
                {},
                skills.map((s) =>
                  h(
                    "tr",
                    {},
                    h("td", {}, metric(`learner.${s.skill}.skill`, s.skill)),
                    h(
                      "td",
                      {},
                      s.abstain
                        ? metric(`learner.${s.skill}.abstain`, `abstains: ${s.reason}`)
                        : metric(`learner.${s.skill}.value`, formatEstimate(s.value)),
                    ),
                    h(
                      "td",
                      {},
                      s.abstain
                        ? metric(
                            `learner.${s.skill}.needs`,
                            `needs ${formatCount(s.needs.trials)} more trials`,
                          )
                        : metric(`learner.${s.skill}.interval`, formatInterval(s.interval)),
                    ),
                    h("td", {}, metric(`learner.${s.skill}.n`, formatCount(s.n))),
                    h(
                      "td",
                      {},
                      metric(`learner.${s.skill}.served`, String(s.served), {
                        // §5: a serving-level chip is the screen's single accent
                        // element when it is the point of the screen. It is the
                        // point when the level is below the entitlement — that
                        // is the gap a reader can act on. Otherwise it is blue.
                        class: hasGap(s) ? "chip chip-accent" : "chip",
                      }),
                    ),
                    h(
                      "td",
                      {},
                      h("span", { class: "ok" }, "\u2713 "),
                      metric(`learner.${s.skill}.validation_level`, String(s.validation_level)),
                    ),
                  ),
                ),
              ),
            ),
      mastery !== null && skills.length > 0
        ? h(
            "p",
            { class: "sentence level", "data-metric": "learner.level.sentence" },
            levelSentence(skills[0]),
          )
        : null,
    ),
  );
}

export function renderConsole(state) {
  return h(
    "div",
    { class: "console" },
    overview(state),
    connectionsScreen(state),
    inspector(state),
  );
}

/** Flatten a rendered tree into `{metric, text}` pairs — the traceability surface. */
export function metricNodes(node, out = []) {
  const id = node.attrs && node.attrs["data-metric"];
  if (id) out.push({ metric: id, text: textOf(node) });
  for (const child of node.children || []) metricNodes(child, out);
  return out;
}

export function textOf(node) {
  if (typeof node === "string") return node;
  if (!node) return "";
  return (node.children || []).map(textOf).join("");
}

/** Browser-only: turn the tree into DOM. */
export function toDom(node, doc) {
  if (typeof node === "string") return doc.createTextNode(node);
  const element = doc.createElement(node.tag);
  for (const [name, value] of Object.entries(node.attrs || {})) {
    element.setAttribute(name, value);
  }
  for (const child of node.children || []) element.appendChild(toDom(child, doc));
  return element;
}

async function fetchJson(path, key) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${key}` } });
  const body = await response.json();
  if (!response.ok) {
    const detail = body.error ? `${body.error.code}: ${body.error.message}` : String(response.status);
    throw new Error(`${path}: ${detail}`);
  }
  return body;
}

/** One load = four reads, all of them GET, none of them metered. */
export async function loadState(key, learnerId) {
  const [metrics, credits, connections, mastery] = await Promise.all([
    fetchJson("/v1/admin/metrics/abstention", key),
    fetchJson("/v1/credits/balance", key),
    fetchJson("/v1/admin/connections", key),
    learnerId ? fetchJson(`/v1/mastery?learner_id=${encodeURIComponent(learnerId)}`, key) : null,
  ]);
  return { metrics, credits, connections, mastery, learnerId: learnerId || null };
}

function bootstrap() {
  const form = document.getElementById("auth");
  const app = document.getElementById("app");
  const status = document.getElementById("status");
  const keyInput = document.getElementById("key");
  const learnerInput = document.getElementById("learner");

  const saved = sessionStorage.getItem("adapt-key");
  if (saved) keyInput.value = saved;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = keyInput.value.trim();
    if (!key) return;
    sessionStorage.setItem("adapt-key", key);
    status.textContent = "loading…";
    try {
      const state = await loadState(key, learnerInput.value.trim());
      app.replaceChildren(toDom(renderConsole(state), document));
      status.textContent =
        state.metrics.denominator === 0
          ? "loaded — no decisions in this window"
          : `loaded — ${state.metrics.denominator} valid decisions`;
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  });
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  bootstrap();
}
