"""The connectors registry: what exists, whether it is on, and its licence.

One table an operator can read (``adapt config status`` prints it), so
"which outside services can this install talk to?" has a single answer.
Defaults are the privacy posture of the whole feature:

* **Oak** — on when a key exists (console-only key path); no key, no calls.
* **Wikimedia outlines** — off until ``ADAPT_CONNECTOR_WIKIMEDIA_ENABLED``.
* **Wellbeing reading links** — off until ``ADAPT_CONNECTOR_WELLBEING_ENABLED``.
* **OpenStax** — *not wired*: verified 2026-10-23 that it publishes no
  documented outline/TOC API (its CMS catalog lists books only), so it is
  recorded here as considered-and-skipped rather than scraped. See
  ``docs/CONNECTORS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import oak as OAK
from . import psychologie as WELLBEING
from . import wikimedia as WIKIMEDIA


@dataclass(frozen=True)
class ConnectorSpec:
    id: str
    job: str  # scanner | wellbeing
    host: str
    licence: str
    attribution: str
    needs_key: bool
    wired: bool = True
    note: str = ""


SPECS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        id="oak",
        job="scanner",
        host=OAK.HOST,
        licence=OAK.LICENCE,
        attribution=OAK.ATTRIBUTION,
        needs_key=True,
        note="UK key-stage unit titles; enabled when a key is configured",
    ),
    ConnectorSpec(
        id="wikimedia",
        job="scanner",
        host=WIKIMEDIA.HOST,
        licence=WIKIMEDIA.LICENCE,
        attribution="Wikipedia contributors (CC BY-SA 4.0)",
        needs_key=False,
        note='"Outline of …" section headings; off unless enabled',
    ),
    ConnectorSpec(
        id="wellbeing",
        job="wellbeing",
        host=WELLBEING.HOST,
        licence=WELLBEING.LICENCE,
        attribution=WELLBEING.ATTRIBUTION,
        needs_key=False,
        note="general wellbeing reading links after a hard check-in; never tests or scores; off unless enabled",
    ),
    ConnectorSpec(
        id="openstax",
        job="scanner",
        host="openstax.org",
        licence="CC BY-NC-SA 4.0",
        attribution="OpenStax (CC BY-NC-SA 4.0)",
        needs_key=False,
        wired=False,
        note="skipped: no documented public outline API (verified 2026-10-23)",
    ),
)


def _enabled(spec: ConnectorSpec, settings: Any) -> tuple[bool, str]:
    if not spec.wired:
        return False, "not wired"
    if spec.id == "oak":
        key = str(getattr(settings, "oak_api_key", "") or "").strip()
        return (bool(key), "key configured" if key else "no key (adapt config set-key --service oak)")
    if spec.id == "wikimedia":
        return bool(getattr(settings, "connector_wikimedia_enabled", False)), "enabled by operator" if getattr(settings, "connector_wikimedia_enabled", False) else "off (ADAPT_CONNECTOR_WIKIMEDIA_ENABLED)"
    if spec.id == "wellbeing":
        return bool(getattr(settings, "connector_wellbeing_enabled", False)), "enabled by operator" if getattr(settings, "connector_wellbeing_enabled", False) else "off (ADAPT_CONNECTOR_WELLBEING_ENABLED)"
    return False, "off"


def connector_status(settings: Any) -> list[dict[str, Any]]:
    """Console-safe status rows: state and licence, never a secret."""
    rows: list[dict[str, Any]] = []
    for spec in SPECS:
        enabled, why = _enabled(spec, settings)
        rows.append(
            {
                "id": spec.id,
                "job": spec.job,
                "host": spec.host,
                "enabled": enabled,
                "state": why,
                "licence": spec.licence,
                "attribution": spec.attribution,
                "note": spec.note,
            }
        )
    return rows


def health_check(settings: Any, connector_id: str, *, transport: Any = None) -> dict[str, Any]:
    """One live probe for an enabled connector. Refuses when off.

    Health means "the documented probe endpoint answered", nothing more:
    it sends no learner data (there is none to send) and never runs at
    startup — only when an operator asks from the console.
    """
    spec = next((s for s in SPECS if s.id == connector_id), None)
    if spec is None:
        return {"ok": False, "detail": f"unknown connector {connector_id!r}"}
    enabled, why = _enabled(spec, settings)
    if not enabled:
        return {"ok": False, "detail": f"connector is off: {why}"}
    timeout = float(getattr(settings, "connector_timeout_s", 8.0) or 8.0)
    if spec.id == "oak":
        return OAK.health(api_key=str(getattr(settings, "oak_api_key", "") or ""), timeout_s=timeout, transport=transport)
    if spec.id == "wikimedia":
        return WIKIMEDIA.health(timeout_s=timeout, transport=transport)
    if spec.id == "wellbeing":
        return WELLBEING.health(transport=transport)
    return {"ok": False, "detail": "not wired"}
