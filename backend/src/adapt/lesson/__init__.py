"""The lesson layer: visual templates an adaptation can actually put on screen.

Two modules live here:

* :mod:`adapt.lesson.templates` — the seven schema-validated visual templates
  (flow, comparison table, timeline, labelled diagram, number line/graph,
  set/Venn, bar/strip model). A spec that fails validation is rejected and the
  caller falls back to words, with the reason kept attached.
* (the adaptation that fills them is :mod:`adapt.ai.adapter` — AI 3)

This package deliberately does not import any AI module. Templates are code:
they validate, they escape, they draw. What goes *in* them is decided elsewhere.
"""

from .templates import (
    VISUAL_KINDS,
    VisualSpecError,
    derive_spec,
    fallback_text,
    render_svg,
    schema_summary,
    validate_visual_spec,
)

__all__ = [
    "VISUAL_KINDS",
    "VisualSpecError",
    "derive_spec",
    "fallback_text",
    "render_svg",
    "schema_summary",
    "validate_visual_spec",
]
