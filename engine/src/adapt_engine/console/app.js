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

const PERCENT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export const NO_DATA = "no data";

export function formatPercent(value) {
  return value === null || value === undefined ? NO_DATA : `${PERCENT.format(value * 100)}%`;
}

export function formatNumber(value, digits = 4) {
  return value === null || value === undefined ? NO_DATA : Number(value).toFixed(digits);
}

export function formatInterval(interval) {
  if (!interval || interval.low === null || interval.high === null) {
    return "interval unavailable";
  }
  return `[${formatNumber(interval.low)}, ${formatNumber(interval.high)}]`;
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
        class: m.rate === null ? "null" : "value",
      }),
    ),
    row(
      "abstentions / decisions",
      h(
        "span",
        { class: "value" },
        metric("overview.abstention.numerator", String(m.numerator)),
        " / ",
        metric("overview.abstention.denominator", String(m.denominator)),
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
          ? `no abstentions in ${m.denominator} decisions`
          : Object.entries(m.reasons)
              .map(([reason, count]) => `${reason} ${count}`)
              .join(", "),
      ),
    ),
    row(
      "excluded, and counted",
      metric(
        "overview.abstention.exclusions",
        `${m.exclusions.test_traffic} test, ` +
          `${m.exclusions.paused_or_revoked} paused, ` +
          `${m.exclusions.replays} replays`,
      ),
    ),
    h(
      "p",
      { class: "sentence", "data-metric": "overview.denominator.sentence" },
      empty
        ? "No decisions in this window, so there is no rate to report — not a 0% one."
        : `Rate over ${m.denominator} valid decisions. ` +
          `${m.exclusions.test_traffic} test calls, ${m.exclusions.paused_or_revoked} ` +
          `paused-connection calls and ${m.exclusions.replays} replays are excluded and counted.`,
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
    row("available", metric("overview.credits.available", String(state.credits.available))),
    row("consumed", metric("overview.credits.consumed", String(state.credits.consumed))),
    row("held", metric("overview.credits.held", String(state.credits.held))),
    row("granted", metric("overview.credits.granted", String(state.credits.granted))),
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
          h("td", {}, metric(`overview.connection.${c.id}.quota`, String(c.event_quota_monthly))),
          h(
            "td",
            {},
            metric(
              `overview.connection.${c.id}.decisions`,
              usage ? String(usage.denominator) : "0",
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
          formatNumber(polling.baseline_repeat_ratio, 2),
        ),
      ),
      row(
        "connections alerting",
        metric("connections.polling.alerting", String(polling.alerting.length)),
      ),
      row(
        "alert threshold",
        metric("connections.polling.multiple", `${formatNumber(polling.multiple, 1)}x baseline`),
      ),
      h(
        "p",
        { class: "sentence", "data-metric": "connections.polling.sentence" },
        polling.alerting.length === 0
          ? `No connection is repeating decide on the same learner above ${formatNumber(
              polling.multiple,
              1,
            )}x the baseline, across ${polling.by_connection.length} connections with ` +
              "decisions in this window and a " +
              `${polling.min_repeats}-repeat floor. A host retrying for a non-abstention ` +
              "shows up here, not in the bill."
          : `${polling.alerting.length} of ${polling.by_connection.length} connections are ` +
              `repeating decide on the same learner above ${formatNumber(polling.multiple, 1)}x ` +
              "the baseline — usually a host retrying for a non-abstention.",
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
              state.metrics.by_connection.map((c) =>
                h(
                  "tr",
                  {},
                  h("td", {}, metric(`connections.row.${c.connection_id}.id`, String(c.connection_id))),
                  h("td", {}, metric(`connections.row.${c.connection_id}.n`, String(c.denominator))),
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
                ),
              ),
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
                        : metric(`learner.${s.skill}.value`, formatNumber(s.value)),
                    ),
                    h(
                      "td",
                      {},
                      s.abstain
                        ? metric(
                            `learner.${s.skill}.needs`,
                            `needs ${s.needs.trials} more trials`,
                          )
                        : metric(`learner.${s.skill}.interval`, formatInterval(s.interval)),
                    ),
                    h("td", {}, metric(`learner.${s.skill}.n`, String(s.n))),
                    h("td", {}, metric(`learner.${s.skill}.served`, String(s.served))),
                    h(
                      "td",
                      {},
                      metric(`learner.${s.skill}.validation_level`, String(s.validation_level)),
                    ),
                  ),
                ),
              ),
            ),
      mastery !== null && skills.length > 0
        ? h(
            "p",
            { class: "sentence", "data-metric": "learner.level.sentence" },
            `Entitled to ${skills[0].entitlement_level}; serving ${skills[0].served} where ` +
              `validation has not passed (capability ${skills[0].capability_level}, ` +
              `validated ${skills[0].validation_level}). limited_by is ` +
              `"${skills[0].limited_by === null ? "nothing" : skills[0].limited_by}": a tenant ` +
              "served at its entitlement is not being withheld from, and at M1 every construct " +
              "is L1 — the column is shown rather than hidden.",
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
