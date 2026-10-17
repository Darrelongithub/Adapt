"use client";

import { SceneStage, TEMPLATE_NAMES, type SceneSpec } from "@/components/animator";

export default function AnimatorDevPage() {
  return <main className="dev-animator-page">
    <header><p className="eyebrow">Developer preview</p><h1>Scene templates</h1><p>Every visual is drawn from the same safe primitives. Use the controls on any card.</p></header>
    <div className="dev-template-grid">{TEMPLATE_NAMES.map((template) => {
      const spec: SceneSpec = { template, captions: [`${template.replaceAll("-", " ")}.`, "Pause and predict the next step.", "Now say what changed."], source: "simple" };
      return <section key={template} className="dev-template-card"><h2>{template.replaceAll("-", " ")}</h2><SceneStage spec={spec} /></section>;
    })}</div>
  </main>;
}
