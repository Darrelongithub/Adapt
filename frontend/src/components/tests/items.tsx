"use client";

/**
 * Real test items (Round 6, Track B). Every item here is an interaction scored by
 * code — a tap, a drag, a grid — never a self-report. Each renderer receives the
 * plan's parameters and reports `{correct, answer, ms, hints}` through `onDone`,
 * and the runner stores that as an evidence row.
 *
 * Instruction copy is capped at ~12 words per screen (registry INSTRUCTIONS).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import {
  HINTS,
  INSTRUCTIONS,
  asInt,
  splitList,
  type ItemResult,
  type ItemTypeName,
  type PlanItem,
} from "@/lib/tests/registry";

/* ------------------------------------------------------------------ */
/* shared chrome                                                       */
/* ------------------------------------------------------------------ */

export function TestItemCard({
  item,
  position,
  total,
  onDone,
}: {
  item: PlanItem;
  position: number;
  total: number;
  onDone: (result: ItemResult) => void;
}) {
  const start = useRef<number>(Date.now());
  const [hints, setHints] = useState(0);
  const [showHint, setShowHint] = useState(false);
  const [result, setResult] = useState<ItemResult | null>(null);

  useEffect(() => {
    start.current = Date.now();
    setHints(0);
    setShowHint(false);
    setResult(null);
  }, [item.id]);

  const finish = useCallback(
    (correct: boolean, answer: string) => {
      const ms = Date.now() - start.current;
      const r: ItemResult = { correct, answer, ms, hints };
      setResult(r);
      // The feedback beat is short: the next item is the learner's to choose.
      window.setTimeout(() => onDone(r), correct ? 650 : 1400);
    },
    [hints, onDone],
  );

  return (
    <section className="test-item card" data-tone="plain" data-item-type={item.item_type} data-item-id={item.id} data-answered={result ? "true" : "false"}>
      <div className="test-item-meta">
        <span className="status-chip">
          {position} of {total}
        </span>
        <span className="status-chip">{item.skill}</span>
      </div>
      <h2 className="t-h2">{INSTRUCTIONS[item.item_type]}</h2>
      <div className="test-item-body">
        <ItemRenderer type={item.item_type} parameters={item.parameters} onAnswer={finish} answered={result !== null} />
      </div>
      {result ? (
        <p className={`test-verdict ${result.correct ? "is-correct" : "is-wrong"}`} role="status">
          {result.correct ? "Correct." : "Not this time. Next one."}
        </p>
      ) : (
        <div className="wrap-2">
          {showHint ? (
            <p className="t-small">{HINTS[item.item_type]}</p>
          ) : (
            <button
              type="button"
              className="btn btn-ctl btn-ghost"
              onClick={() => {
                setHints((h) => h + 1);
                setShowHint(true);
              }}
            >
              <Icon name="search" /> Hint
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function ItemRenderer({
  type,
  parameters,
  onAnswer,
  answered,
}: {
  type: ItemTypeName;
  parameters: Record<string, unknown>;
  onAnswer: (correct: boolean, answer: string) => void;
  answered: boolean;
}) {
  switch (type) {
    case "pattern_pick":
      return <PatternPicker parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "drag_order":
      return <DragOrder parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "drag_match":
      return <DragMatch parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "odd_one_out":
      return <OddOneOut parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "truth_table":
      return <TruthTableGrid parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "venn_region":
      return <VennRegion parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "geometry_angle":
      return <GeometryAngle parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "geometry_congruent":
      return <GeometryCongruent parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "number_line":
      return <NumberLine parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "fraction_bar":
      return <FractionBar parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "cloze_word_bank":
      return <ClozeWordBank parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    case "quick_recall":
      return <QuickRecall parameters={parameters} onAnswer={onAnswer} answered={answered} />;
    default:
      return <p>This check cannot show here.</p>;
  }
}

/* ------------------------------------------------------------------ */
/* shape glyphs — small SVG diagrams so options are pictures, not prose */
/* ------------------------------------------------------------------ */

function glyphFor(word: string) {
  const w = word.toLowerCase();
  const fill = "var(--accent)";
  if (w.includes("circle")) return <circle cx="24" cy="24" r="16" fill="none" stroke={fill} strokeWidth="3" />;
  if (w.includes("square") || w.includes("rectangle")) return <rect x="8" y="12" width="32" height="24" fill="none" stroke={fill} strokeWidth="3" rx="2" />;
  if (w.includes("triangle")) return <polygon points="24,8 40,40 8,40" fill="none" stroke={fill} strokeWidth="3" />;
  if (w.includes("pentagon")) return <polygon points="24,6 42,20 35,42 13,42 6,20" fill="none" stroke={fill} strokeWidth="3" />;
  if (w.includes("hexagon")) return <polygon points="14,10 34,10 44,24 34,38 14,38 4,24" fill="none" stroke={fill} strokeWidth="3" />;
  if (w.includes("line") || w.includes("ray")) return <line x1="6" y1="36" x2="42" y2="12" stroke={fill} strokeWidth="3" />;
  if (w.includes("star")) return <polygon points="24,6 29,18 42,18 31,26 35,40 24,32 13,40 17,26 6,18 19,18" fill="none" stroke={fill} strokeWidth="2.5" />;
  return null;
}

function OptionTile({
  word,
  selected,
  correct,
  wrong,
  onClick,
  disabled,
}: {
  word: string;
  selected?: boolean;
  correct?: boolean;
  wrong?: boolean;
  onClick?: () => void;
  disabled?: boolean;
}) {
  const glyph = glyphFor(word);
  return (
    <button
      type="button"
      className={`option-tile${selected ? " is-selected" : ""}${correct ? " is-correct" : ""}${wrong ? " is-wrong" : ""}`}
      aria-pressed={selected ?? false}
      onClick={onClick}
      disabled={disabled}
    >
      {glyph ? (
        <svg width="48" height="48" viewBox="0 0 48 48" aria-hidden="true">
          {glyph}
        </svg>
      ) : null}
      <span className="option-tile-label">{word}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* 1 · pattern / sequence picker — tap the next shape                   */
/* ------------------------------------------------------------------ */

function PatternPicker({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const sequence = splitList(parameters.sequence);
  const options = splitList(parameters.options);
  const answerIndex = asInt(parameters.answer_index, 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="stack-3">
      <div className="sequence-row" aria-label="the sequence so far">
        {sequence.map((word, i) => (
          <span key={i} className="sequence-step">
            <OptionTile word={word} />
            <span aria-hidden="true" className="sequence-arrow">→</span>
          </span>
        ))}
        <span className="sequence-step"><span className="sequence-q">?</span></span>
      </div>
      <div className="option-grid" role="group" aria-label="choices">
        {options.map((word, i) => (
          <OptionTile
            key={word}
            word={word}
            selected={picked === i}
            correct={answered && i === answerIndex}
            wrong={answered && picked === i && i !== answerIndex}
            disabled={answered}
            onClick={() => {
              if (answered) return;
              setPicked(i);
              onAnswer(i === answerIndex, word);
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 2 · drag-to-order                                                   */
/* ------------------------------------------------------------------ */

function DragOrder({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const items = splitList(parameters.items);
  const answer = splitList(parameters.answer);
  const prompt = String(parameters.prompt ?? "");
  // Deterministic shuffle from the answer order, so a refresh shows the same start.
  const initial = useMemo(() => {
    const arr = items.map((_, i) => i);
    if (arr.length >= 4) {
      [arr[0], arr[arr.length - 1]] = [arr[arr.length - 1], arr[0]];
    } else if (arr.length >= 2) {
      [arr[0], arr[1]] = [arr[1], arr[0]];
    }
    return arr;
  }, [items]);
  const [order, setOrder] = useState<number[]>(initial);
  const dragIndex = useRef<number | null>(null);

  useEffect(() => setOrder(initial), [initial]);

  const move = (from: number, to: number) => {
    if (answered || from === to || to < 0 || to >= order.length) return;
    const next = [...order];
    const [x] = next.splice(from, 1);
    next.splice(to, 0, x);
    setOrder(next);
  };

  const check = () => {
    const current = order.map((i) => items[i]);
    const correct = current.length === answer.length && current.every((w, i) => w === answer[i]);
    onAnswer(correct, current.join(">"));
  };

  return (
    <div className="stack-3">
      {prompt ? <p className="t-small">{prompt}</p> : null}
      <ul className="order-list">
        {order.map((itemIndex, pos) => (
          <li
            key={itemIndex}
            className="order-row"
            draggable={!answered}
            onDragStart={() => (dragIndex.current = pos)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (dragIndex.current !== null) move(dragIndex.current, pos);
              dragIndex.current = null;
            }}
          >
            <span className="order-grip" aria-hidden="true">⋮⋮</span>
            <span className="order-text">{items[itemIndex]}</span>
            <span className="order-movers">
              <button type="button" className="btn btn-ctl btn-ghost" aria-label="move up" disabled={answered || pos === 0} onClick={() => move(pos, pos - 1)}>↑</button>
              <button type="button" className="btn btn-ctl btn-ghost" aria-label="move down" disabled={answered || pos === order.length - 1} onClick={() => move(pos, pos + 1)}>↓</button>
            </span>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-ctl btn-primary" disabled={answered} onClick={check}>Check</button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 3 · drag-to-match                                                   */
/* ------------------------------------------------------------------ */

function DragMatch({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const left = splitList(parameters.left);
  const right = splitList(parameters.right);
  const pairs = useMemo(() => {
    const map = new Map<string, string>();
    for (const pair of splitList(parameters.pairs)) {
      const [l, r] = pair.split(":").map((s) => s.trim());
      if (l && r) map.set(l, r);
    }
    return map;
  }, [parameters.pairs]);
  const [selectedLeft, setSelectedLeft] = useState<string | null>(null);
  const [matches, setMatches] = useState<Record<string, string>>({});

  const assign = (r: string) => {
    if (answered || !selectedLeft) return;
    setMatches((m) => ({ ...m, [selectedLeft]: r }));
    setSelectedLeft(null);
  };

  const check = () => {
    const correct = left.length > 0 && left.every((l) => matches[l] === pairs.get(l));
    onAnswer(correct, left.map((l) => `${l}=${matches[l] ?? ""}`).join(","));
  };

  return (
    <div className="stack-3">
      <div className="match-grid">
        <div className="stack-2" role="group" aria-label="left side">
          {left.map((l) => (
            <button
              key={l}
              type="button"
              className={`tap-chip match-chip${selectedLeft === l ? " is-selected" : ""}`}
              aria-pressed={selectedLeft === l}
              disabled={answered}
              onClick={() => setSelectedLeft(l)}
            >
              {l}
            </button>
          ))}
        </div>
        <div className="stack-2" role="group" aria-label="right side">
          {right.map((r) => {
            const usedBy = Object.entries(matches).find(([, v]) => v === r)?.[0];
            return (
              <button
                key={r}
                type="button"
                className={`tap-chip match-chip${usedBy ? " is-used" : ""}`}
                disabled={answered || !selectedLeft}
                onClick={() => assign(r)}
              >
                {r}
                {usedBy ? <small>← {usedBy}</small> : null}
              </button>
            );
          })}
        </div>
      </div>
      <button type="button" className="btn btn-ctl btn-primary" disabled={answered || Object.keys(matches).length < left.length} onClick={check}>Check</button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 4 · odd one out (with diagrams)                                     */
/* ------------------------------------------------------------------ */

function OddOneOut({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const options = splitList(parameters.options);
  const answerIndex = asInt(parameters.answer_index, 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="option-grid" role="group" aria-label="choices">
      {options.map((word, i) => (
        <OptionTile
          key={word}
          word={word}
          selected={picked === i}
          correct={answered && i === answerIndex}
          wrong={answered && picked === i && i !== answerIndex}
          disabled={answered}
          onClick={() => {
            if (answered) return;
            setPicked(i);
            onAnswer(i === answerIndex, word);
          }}
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 5 · truth-table tap grid                                            */
/* ------------------------------------------------------------------ */

function TruthTableGrid({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const expression = String(parameters.expression ?? "A and B");
  const cols = Math.max(2, Math.min(4, asInt(parameters.columns, 2)));
  const rows = Math.max(2, Math.min(8, asInt(parameters.rows, 4)));
  const target = splitList(parameters.target_cells).map((s) => (s === "1" ? true : false));
  const rowValues = useMemo(() => {
    const out: Array<[boolean, boolean]> = [];
    for (let i = 0; i < rows; i++) {
      out.push([i % 2 === 0, i < rows / 2]);
    }
    return out;
  }, [rows]);
  const [cells, setCells] = useState<boolean[]>(() => Array(rows).fill(false));

  const check = () => {
    const correct = cells.length === target.length && cells.every((v, i) => v === target[i]);
    onAnswer(correct, cells.map((v) => (v ? "1" : "0")).join(","));
  };

  return (
    <div className="stack-3">
      <p className="t-body">
        <span className="strong">{expression}</span> — set the last column.
      </p>
      <table className="truth-grid">
        <thead>
          <tr>
            <th scope="col">A</th>
            {cols >= 2 ? <th scope="col">B</th> : null}
            <th scope="col">{expression}</th>
          </tr>
        </thead>
        <tbody>
          {rowValues.map(([a, b], i) => (
            <tr key={i}>
              <td>{a ? "T" : "F"}</td>
              {cols >= 2 ? <td>{b ? "T" : "F"}</td> : null}
              <td>
                <button
                  type="button"
                  className={`truth-cell${cells[i] ? " is-true" : ""}`}
                  disabled={answered}
                  aria-label={`row ${i + 1} result`}
                  onClick={() => setCells((c) => c.map((v, j) => (j === i ? !v : v)))}
                >
                  {cells[i] ? "T" : "F"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="btn btn-ctl btn-primary" disabled={answered} onClick={check}>Check</button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 6 · Venn / set region tap                                           */
/* ------------------------------------------------------------------ */

function VennRegion({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const sets = splitList(parameters.sets);
  const a = sets[0] ?? "A";
  const b = sets[1] ?? "B";
  const question = String(parameters.question ?? "Tap the region.");
  const answerRegion = String(parameters.answer_region ?? "AB");
  const [picked, setPicked] = useState<string | null>(null);
  const regions: Array<{ id: string; label: string; cx: number; cy: number }> = [
    { id: a, label: `${a} only`, cx: 130, cy: 140 },
    { id: b, label: `${b} only`, cx: 270, cy: 140 },
    { id: "AB", label: `${a} and ${b}`, cx: 200, cy: 140 },
    { id: "none", label: "outside", cx: 60, cy: 240 },
  ];
  return (
    <div className="stack-3">
      <p className="t-body">{question}</p>
      <svg viewBox="0 0 400 280" className="venn-svg" role="group" aria-label="set regions">
        <rect x="4" y="4" width="392" height="272" rx="14" fill="var(--panel)" stroke="var(--line)" />
        <circle cx="160" cy="140" r="95" fill="var(--accent-soft)" fillOpacity="0.7" stroke="var(--accent)" strokeWidth="2.5" />
        <circle cx="240" cy="140" r="95" fill="var(--tint-mint)" fillOpacity="0.7" stroke="var(--good)" strokeWidth="2.5" />
        <text x="110" y="55" textAnchor="middle" fontSize="20" fontWeight="650" fill="var(--ink)">{a}</text>
        <text x="290" y="55" textAnchor="middle" fontSize="20" fontWeight="650" fill="var(--ink)">{b}</text>
        {regions.map((r) => (
          <g key={r.id} className={`venn-hit${picked === r.id ? " is-selected" : ""}${answered && r.id === answerRegion ? " is-correct" : ""}`}>
            <circle
              cx={r.cx} cy={r.cy} r="26"
              fill={picked === r.id ? "var(--accent)" : "var(--white)"}
              fillOpacity="0.9" stroke="var(--accent)" strokeWidth="2" style={{ cursor: answered ? "default" : "pointer" }}
              onClick={() => {
                if (answered) return;
                setPicked(r.id);
                onAnswer(r.id === answerRegion, r.id);
              }}
            />
            <text x={r.cx} y={r.cy + 5} textAnchor="middle" fontSize="14" fontWeight="600" fill={picked === r.id ? "var(--white)" : "var(--ink)"} style={{ pointerEvents: "none" }}>
              {r.label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 7 · geometry: tap the angle                                         */
/* ------------------------------------------------------------------ */

function GeometryAngle({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const angle = Math.max(15, Math.min(165, asInt(parameters.angle, 45)));
  const options = splitList(parameters.options);
  const answerIndex = asInt(parameters.answer_index, Math.min(options.length - 1, Math.round((angle - 15) / 40)));
  const [picked, setPicked] = useState<number | null>(null);
  // Two rays from a vertex at the given angle, opening up-right.
  const rad = (angle * Math.PI) / 180;
  const x2 = 40 + 120;
  const y2 = 140;
  const x3 = 40 + 120 * Math.cos(rad);
  const y3 = 140 - 120 * Math.sin(rad);
  return (
    <div className="stack-3">
      <svg viewBox="0 0 220 170" className="angle-svg" role="img" aria-label="an angle to measure">
        <rect x="2" y="2" width="216" height="166" rx="12" fill="var(--panel)" stroke="var(--line)" />
        <line x1="40" y1="140" x2={x2} y2={y2} stroke="var(--accent)" strokeWidth="3" />
        <line x1="40" y1="140" x2={x3} y2={y3} stroke="var(--accent)" strokeWidth="3" />
        <path d={`M ${40 + 34} 140 A 34 34 0 0 0 ${40 + 34 * Math.cos(rad)} ${140 - 34 * Math.sin(rad)}`} fill="none" stroke="var(--think)" strokeWidth="2.5" />
        <circle cx="40" cy="140" r="4" fill="var(--accent)" />
      </svg>
      <div className="option-grid" role="group" aria-label="choices">
        {options.map((word, i) => (
          <OptionTile
            key={word}
            word={word}
            selected={picked === i}
            correct={answered && i === answerIndex}
            wrong={answered && picked === i && i !== answerIndex}
            disabled={answered}
            onClick={() => {
              if (answered) return;
              setPicked(i);
              onAnswer(i === answerIndex, word);
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 8 · geometry: pick the congruent triangle                           */
/* ------------------------------------------------------------------ */

function GeometryCongruent({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const target = splitList(parameters.target_sides);
  const options = splitList(parameters.options).map((row) => splitList(row));
  const answerIndex = asInt(parameters.answer_index, 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="stack-3">
      <p className="t-body">
        Target sides: <span className="strong">{target.join(", ")}</span>
      </p>
      <div className="option-grid" role="group" aria-label="choices">
        {options.map((sides, i) => (
          <button
            key={i}
            type="button"
            className={`option-tile tri-tile${picked === i ? " is-selected" : ""}${answered && i === answerIndex ? " is-correct" : ""}${answered && picked === i && i !== answerIndex ? " is-wrong" : ""}`}
            aria-pressed={picked === i}
            disabled={answered}
            onClick={() => {
              if (answered) return;
              setPicked(i);
              onAnswer(i === answerIndex, sides.join(","));
            }}
          >
            <svg width="64" height="52" viewBox="0 0 64 52" aria-hidden="true">
              <polygon points="32,6 58,46 6,46" fill="var(--tint-blue)" stroke="var(--accent)" strokeWidth="2.5" />
            </svg>
            <span className="option-tile-label">{sides.join(" · ")}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 9 · number line estimate (tap / drag)                               */
/* ------------------------------------------------------------------ */

function NumberLine({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const max = Math.max(2, asInt(parameters.max, 100));
  const target = Math.max(0, Math.min(max, asInt(parameters.value, 50)));
  const tolerance = Math.max(1, asInt(parameters.tolerance, 8));
  const [mark, setMark] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const valueAt = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return 0;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left - 24) / (rect.width - 48)));
    return Math.round(ratio * max);
  };

  return (
    <div className="stack-3">
      <p className="t-body">
        Where is <span className="strong">{target}</span>?
      </p>
      <svg
        ref={svgRef}
        viewBox="0 0 400 90"
        className="numberline-svg"
        role="group"
        aria-label="number line"
        onClick={(e) => {
          if (answered) return;
          const v = valueAt(e.clientX);
          setMark(v);
          onAnswer(Math.abs(v - target) <= tolerance, String(v));
        }}
      >
        <line x1="24" y1="45" x2="376" y2="45" stroke="var(--accent)" strokeWidth="3" />
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={24 + t * 352} y1="36" x2={24 + t * 352} y2="54" stroke="var(--accent)" strokeWidth="2.5" />
            <text x={24 + t * 352} y="76" textAnchor="middle" fontSize="13" fill="var(--muted)">{Math.round(t * max)}</text>
          </g>
        ))}
        {mark !== null ? (
          <circle cx={24 + (mark / max) * 352} cy="45" r="11" fill="var(--think)" stroke="var(--white)" strokeWidth="2.5" />
        ) : null}
      </svg>
      <p className="t-small">Tap the line.</p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 10 · fraction bar                                                   */
/* ------------------------------------------------------------------ */

function FractionBar({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const numerator = Math.max(1, asInt(parameters.numerator, 1));
  const denominator = Math.max(2, asInt(parameters.denominator, 4));
  const options = splitList(parameters.options);
  const answerIndex = asInt(parameters.answer_index, 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="stack-3">
      <div className="fraction-bar" role="img" aria-label={`${numerator} of ${denominator} parts shaded`}>
        {Array.from({ length: denominator }, (_, i) => (
          <span key={i} className={`fraction-part${i < numerator ? " is-filled" : ""}`} />
        ))}
      </div>
      <div className="option-grid" role="group" aria-label="choices">
        {options.map((word, i) => (
          <OptionTile
            key={word}
            word={word}
            selected={picked === i}
            correct={answered && i === answerIndex}
            wrong={answered && picked === i && i !== answerIndex}
            disabled={answered}
            onClick={() => {
              if (answered) return;
              setPicked(i);
              onAnswer(i === answerIndex, word);
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 11 · word-bank cloze                                                */
/* ------------------------------------------------------------------ */

function ClozeWordBank({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const sentence = String(parameters.sentence ?? "");
  const bank = splitList(parameters.bank);
  const answerIndex = asInt(parameters.answer_index, 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="stack-3">
      <p className="cloze-sentence">{sentence}</p>
      <div className="option-grid" role="group" aria-label="word bank">
        {bank.map((word, i) => (
          <OptionTile
            key={word}
            word={word}
            selected={picked === i}
            correct={answered && i === answerIndex}
            wrong={answered && picked === i && i !== answerIndex}
            disabled={answered}
            onClick={() => {
              if (answered) return;
              setPicked(i);
              onAnswer(i === answerIndex, word);
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 12 · quick-recall flip (memory)                                     */
/* ------------------------------------------------------------------ */

function QuickRecall({ parameters, onAnswer, answered }: { parameters: Record<string, unknown>; onAnswer: (c: boolean, a: string) => void; answered: boolean }) {
  const shown = splitList(parameters.shown);
  const choices = splitList(parameters.choices);
  const answers = useMemo(
    () =>
      new Set(
        splitList(parameters.answer_indices)
          .map((s) => asInt(s, -1))
          .filter((i) => i >= 0 && i < choices.length)
          .map((i) => choices[i]),
      ),
    [parameters.answer_indices, choices],
  );
  const seconds = Math.max(2, Math.min(6, asInt(parameters.seconds, 3)));
  const [phase, setPhase] = useState<"show" | "pick">("show");
  const [picked, setPicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    setPhase("show");
    setPicked(new Set());
    const t = window.setTimeout(() => setPhase("pick"), seconds * 1000);
    return () => window.clearTimeout(t);
  }, [seconds, parameters.shown]);

  const check = () => {
    const correct = picked.size === answers.size && [...picked].every((w) => answers.has(w));
    onAnswer(correct, [...picked].join(","));
  };

  if (phase === "show") {
    return (
      <div className="stack-3">
        <p className="t-small">Memorise these — {seconds} seconds.</p>
        <div className="option-grid">
          {shown.map((word) => (
            <OptionTile key={word} word={word} />
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="stack-3">
      <div className="option-grid" role="group" aria-label="choices">
        {choices.map((word) => {
          const on = picked.has(word);
          return (
            <OptionTile
              key={word}
              word={word}
              selected={on}
              disabled={answered}
              onClick={() => {
                if (answered) return;
                setPicked((s) => {
                  const next = new Set(s);
                  if (next.has(word)) next.delete(word);
                  else next.add(word);
                  return next;
                });
              }}
            />
          );
        })}
      </div>
      <button type="button" className="btn btn-ctl btn-primary" disabled={answered || picked.size !== shown.length} onClick={check}>
        Check
      </button>
    </div>
  );
}
