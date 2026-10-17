"use client";

/**
 * Dev test lab (Round 6, Track B). Renders every registered item type once, with
 * minimal legal parameters, so the Playwright click-through in
 * `tests-e2e/round6-items.mjs` can exercise each interaction in isolation.
 *
 * This page carries the answer keys in `data-answer` attributes — acceptable
 * only because it is a developer page under /dev and never linked from the app.
 * Real test items (components/tests/items.tsx) never expose answers to the DOM.
 */

import { useState } from "react";
import { PageHeader } from "@/components/ui";
import { TestItemCard } from "@/components/tests/items";
import { ITEM_TYPES, type ItemTypeName, type PlanItem } from "@/lib/tests/registry";
import "@/components/tests/tests.css";

/** One legal parameter set per type, with the answer Playwright should click. */
const LAB: Array<{ item: PlanItem; answer: string }> = [
  {
    item: { id: "lab-pattern", item_type: "pattern_pick", skill: "patterns.sequence", difficulty: 2,
      parameters: { sequence: "circle,square,circle", options: "square,circle,line,pentagon", answer_index: 0 } },
    answer: "0",
  },
  {
    item: { id: "lab-order", item_type: "drag_order", skill: "ordering", difficulty: 2,
      parameters: { items: "Mix,Shape,Bake", answer: "Shape,Mix,Bake", prompt: "Make a cake" } },
    answer: "Shape>Mix>Bake",
  },
  {
    item: { id: "lab-match", item_type: "drag_match", skill: "matching", difficulty: 2,
      parameters: { left: "Cat,Dog", right: "Meow,Woof", pairs: "Cat:Meow,Dog:Woof" } },
    answer: "Cat=Meow,Dog=Woof",
  },
  {
    item: { id: "lab-odd", item_type: "odd_one_out", skill: "classification", difficulty: 2,
      parameters: { options: "square,circle,triangle,line", answer_index: 3, why: "Only the line has no inside." } },
    answer: "3",
  },
  {
    item: { id: "lab-truth", item_type: "truth_table", skill: "logic", difficulty: 3,
      parameters: { expression: "A and B", columns: 2, rows: 4, target_cells: "0,0,0,1" } },
    answer: "Check",
  },
  {
    item: { id: "lab-venn", item_type: "venn_region", skill: "sets", difficulty: 3,
      parameters: { sets: "A,B", question: "Where do A and B overlap?", answer_region: "AB" } },
    answer: "AB",
  },
  {
    item: { id: "lab-angle", item_type: "geometry_angle", skill: "geometry.spatial", difficulty: 2,
      parameters: { angle: 45, kind: "pick", options: "15°,45°,90°,135°", answer_index: 1 } },
    answer: "1",
  },
  {
    item: { id: "lab-congruent", item_type: "geometry_congruent", skill: "geometry.spatial", difficulty: 3,
      parameters: { target_sides: "30,50,40", options: "20,25,30|30,40,50|30,50,80|10,20,30", answer_index: 1 } },
    answer: "1",
  },
  {
    item: { id: "lab-line", item_type: "number_line", skill: "number.sense", difficulty: 2,
      parameters: { value: 50, max: 100, tolerance: 10 } },
    answer: "line",
  },
  {
    item: { id: "lab-fraction", item_type: "fraction_bar", skill: "number.fractions", difficulty: 2,
      parameters: { numerator: 1, denominator: 4, options: "1/2,1/3,1/4,1/5", answer_index: 2 } },
    answer: "2",
  },
  {
    item: { id: "lab-cloze", item_type: "cloze_word_bank", skill: "vocabulary", difficulty: 2,
      parameters: { sentence: "She ___ to the market.", bank: "go,goes,went,gone", answer_index: 2 } },
    answer: "2",
  },
  {
    item: { id: "lab-recall", item_type: "quick_recall", skill: "memory", difficulty: 2,
      parameters: { shown: "red,green,blue", choices: "red,green,blue,yellow", answer_indices: "0,1,2", seconds: 2 } },
    answer: "0,1,2",
  },
];

export default function TestLab() {
  const [index, setIndex] = useState(0);
  const [results, setResults] = useState<Array<{ type: string; correct: boolean; answer: string; ms: number; hints: number }>>([]);
  const entry = LAB[index];
  const done = index >= LAB.length;

  return (
    <div className="page-pad" data-test-lab>
      <PageHeader
        title="Test lab"
        subtitle="Every registered item type, once. Developer page — not linked from the app."
      />
      <div className="test-progress" aria-label="lab progress">
        {LAB.map((e, i) => (
          <span key={e.item.id} className={i < index ? (results[i]?.correct ? "done" : "miss") : i === index ? "now" : ""} />
        ))}
      </div>
      {!done && entry ? (
        <div data-answer={entry.answer} data-lab-type={entry.item.item_type} style={{ marginTop: "var(--s3)" }}>
          <TestItemCard
            key={entry.item.id}
            item={entry.item}
            position={index + 1}
            total={LAB.length}
            onDone={(r) => {
              setResults((prev) => [...prev, { type: entry.item.item_type, correct: r.correct, answer: r.answer, ms: r.ms, hints: r.hints }]);
              setIndex((i) => i + 1);
            }}
          />
        </div>
      ) : (
        <div data-lab-complete>
          <h2 className="t-h2">All {LAB.length} types answered.</h2>
          <ul className="stack-2" data-lab-results>
            {results.map((r) => (
              <li key={r.type} className="t-small" data-type={r.type} data-correct={String(r.correct)}>
                {r.type}: {r.correct ? "correct" : "wrong"} · {r.ms} ms · {r.hints} hints
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="t-small" style={{ marginTop: "var(--s4)" }}>
        Registered types: {ITEM_TYPES.join(", ")}.
      </p>
    </div>
  );
}

const LAB_TYPES: ItemTypeName[] = LAB.map((e) => e.item.item_type);
