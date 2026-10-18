"use client";

/** The progress chart from the design snapshot, drawn from the dashboard's own numbers.
 *
 * The encoding *is* the point, and it is why this is a chart rather than a row of coloured
 * bands:
 *
 * - **position** is the estimate (`p_know`);
 * - **the width of the line through it** is the uncertainty the server computed (`ci`, a 90%
 *   interval from the observation count — 4/5 and 40/50 are not the same claim);
 * - **the size of the circle** is how many answers that estimate rests on.
 *
 * A bigger circle is not a better score. It is a more confident one, and the two are only
 * comparable by position. The shading behind the lanes is the app's own band thresholds and is
 * deliberately neutral: a position on a scale is not a grade, and colouring it red or green is
 * how an estimate becomes a label.
 *
 * `.topo`, `.topo-table` and `.pg-note` shipped in `globals.css` from the snapshot with nothing
 * rendering them — Progress was the one screen the app answered with a list. The exact figures
 * are in the table below the chart, because a chart that cannot be read as numbers is a chart
 * that cannot be checked.
 */

import { EARLY_SIGNAL_N, EvidenceBadge } from "./evidence";
import { friendlyLabel } from "@/lib/labels";

type TopoRow = {
  topic_id: string;
  title?: string;
  p_know: number;
  band?: string;
  observations?: number;
  ci?: [number, number] | null;
  due_at?: string | null;
};

/** The app's own bands (`learning/bkt.py::BktParams.band`), as regions of the same axis. */
const BAND_REGIONS = [
  { from: 0, to: 0.55, label: "new / gapped", opacity: 0.5 },
  { from: 0.55, to: 0.8, label: "developing", opacity: 0.25 },
  { from: 0.8, to: 0.95, label: "proficient", opacity: 0.5 },
  { from: 0.95, to: 1, label: "mastered", opacity: 0.25 },
];

const W = 760;
const X0 = 216;
const X1 = 744;
const LANE_H = 48;
const TOP = 34;
const AXIS = 34;
const NAME_CHARS = 27;

const xOf = (v: number) => X0 + Math.min(1, Math.max(0, v)) * (X1 - X0);
const pct = (v: number) => `${Math.round(v * 100)}%`;

function clip(title: string): string {
  return title.length > NAME_CHARS ? `${title.slice(0, NAME_CHARS - 1).trimEnd()}…` : title;
}

/** Half-width of the interval in percentage points — the "±" a reader can compare. */
function uncertaintyPoints(ci?: [number, number] | null): number | null {
  if (!ci || ci.length < 2) return null;
  const lo = Number(ci[0]);
  const hi = Number(ci[1]);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  return Math.round(((hi - lo) / 2) * 100);
}

export function TopicTopo({ rows, minN = EARLY_SIGNAL_N }: { rows: TopoRow[]; minN?: number }) {
  rows = rows.filter((r) => friendlyLabel(r.title || r.topic_id) !== null);
  if (!rows.length) return null;
  const height = TOP + rows.length * LANE_H + AXIS;
  const plotTop = TOP - 16;
  const plotBottom = TOP + rows.length * LANE_H + 4;

  return (
    <div className="topo-wrap">
      <svg
        className="topo"
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`Mastery estimates for ${rows.length} topic${rows.length === 1 ? "" : "s"}. Position on the scale is the estimate, the horizontal line through it is the 90% uncertainty interval, and the size of the circle is how many answers the estimate rests on — a larger circle means more evidence, not a better score. The exact figures are in the table below.`}
      >
        {/* band shading: the app's own thresholds, neutral on purpose */}
        {BAND_REGIONS.map((b) => (
          <rect
            key={b.label}
            x={xOf(b.from)}
            y={plotTop}
            width={Math.max(1, xOf(b.to) - xOf(b.from))}
            height={plotBottom - plotTop}
            fill="var(--panel-2)"
            opacity={b.opacity}
          />
        ))}
        <line x1={X0} y1={plotTop} x2={X0} y2={plotBottom} stroke="var(--line)" strokeWidth={1} />

        {rows.map((r, i) => {
          const y = TOP + i * LANE_H + LANE_H / 2;
          const n = Number(r.observations ?? 0);
          const unc = uncertaintyPoints(r.ci);
          const early = n < minN;
          const title = friendlyLabel(r.title || r.topic_id) ?? "";
          return (
            <g key={r.topic_id}>
              <title>{`${title}: ${pct(r.p_know)}${unc === null ? "" : ` ±${unc} points`}, ${n} answers, band ${r.band ?? "unknown"}`}</title>
              <text x={8} y={y - 4} fontFamily="var(--font-mono)" fontSize={11} fill="var(--ink)">
                {clip(title)}
              </text>
              <text x={8} y={y + 13} fontFamily="var(--font-mono)" fontSize={9.5} fill="var(--muted)">
                {`${r.band ?? "unknown"} · ${pct(r.p_know)}${unc === null ? "" : ` ±${unc}`} · n=${n}${early ? " · early signal" : ""}`}
              </text>
              {r.ci && unc !== null ? (
                <>
                  <line x1={xOf(r.ci[0])} y1={y} x2={xOf(r.ci[1])} y2={y} stroke="var(--line)" strokeWidth={2} />
                  <line x1={xOf(r.ci[0])} y1={y - 4} x2={xOf(r.ci[0])} y2={y + 4} stroke="var(--line)" strokeWidth={2} />
                  <line x1={xOf(r.ci[1])} y1={y - 4} x2={xOf(r.ci[1])} y2={y + 4} stroke="var(--line)" strokeWidth={2} />
                </>
              ) : null}
              <line x1={xOf(r.p_know)} y1={y - 8} x2={xOf(r.p_know)} y2={y + 8} stroke="var(--ink)" strokeWidth={2} />
              <circle
                cx={xOf(r.p_know)}
                cy={y}
                r={Math.min(18, 5 + Math.sqrt(Math.max(0, n)) * 1.5)}
                fill="var(--accent)"
                opacity={0.32}
                stroke="var(--accent)"
                strokeWidth={1.5}
              />
            </g>
          );
        })}

        {BAND_REGIONS.map((b) => (
          <text
            key={`label-${b.label}`}
            x={(xOf(b.from) + xOf(b.to)) / 2}
            y={height - 12}
            textAnchor="middle"
            fontFamily="var(--font-mono)"
            fontSize={10}
            fill="var(--muted)"
          >
            {b.label}
          </text>
        ))}
      </svg>

      <div className="topo-legend">
        Shading is the app&rsquo;s own bands and is neutral on purpose: this is a position with an
        interval around it, not a grade. A lane with no line through it is a topic the server sent
        no interval for, and the table says so rather than drawing one up.
      </div>

      <div className="table-scroll">
        <table className="topo-table">
          <caption>Estimates by topic — the numbers behind the chart above</caption>
          <thead>
            <tr>
              <th scope="col">Topic</th>
              <th scope="col">Band</th>
              <th scope="col">Estimate</th>
              <th scope="col">Uncertainty</th>
              <th scope="col">Answers</th>
              <th scope="col">Next review</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const n = Number(r.observations ?? 0);
              const unc = uncertaintyPoints(r.ci);
              return (
                <tr key={r.topic_id}>
                  <td>{friendlyLabel(r.title || r.topic_id)}</td>
                  <td>{r.band ?? "unknown"}</td>
                  <td>{pct(r.p_know)}</td>
                  <td>
                    {unc === null
                      ? "no interval reported"
                      : `± ${unc} pts (${pct(Number(r.ci?.[0] ?? 0))}–${pct(Number(r.ci?.[1] ?? 0))})`}
                  </td>
                  <td>
                    <EvidenceBadge n={n} threshold={minN} />
                  </td>
                  <td>{r.due_at ? new Date(r.due_at).toLocaleDateString() : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The encoding, in the words the snapshot used: what each dimension means and what it does not. */
export function TopoNote() {
  return (
    <p className="pg-note">
      Position is the estimate. The width of the line through it is the uncertainty around that
      estimate — wider means less evidence pins it down. The size of the circle is how many answers
      it rests on. A bigger circle is not a better score, it is a more confident one. Two topics are
      only comparable by position.
    </p>
  );
}
