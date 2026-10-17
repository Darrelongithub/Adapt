"""Public scene-template helpers kept separate from the legacy simulation catalogue."""
from __future__ import annotations
from typing import Any

from .template_library import TEMPLATE_IDS, TEMPLATE_SCHEMAS, dry_run, suggest_template, validate_and_dry_run, validate_template


def render_scene(spec: dict[str, Any]) -> list[dict[str, Any]]:
    clean, errors = validate_template(spec)
    if errors or clean is None:
        raise ValueError("; ".join(errors))
    return dry_run(clean)


def template_catalogue() -> dict[str, Any]:
    return {"templates": [{"id": key, "params": schema} for key, schema in TEMPLATE_SCHEMAS.items()]}


__all__ = ["TEMPLATE_IDS", "TEMPLATE_SCHEMAS", "render_scene", "suggest_template", "template_catalogue", "validate_and_dry_run"]
