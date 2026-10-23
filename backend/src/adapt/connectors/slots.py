"""Unified 3-slot API overview and health probes for operator console tooling.

Each of ADAPT's three AI roles has its own dedicated external API slot:
  * **Slot 1 (`ai1`) — Psychology & Learning-Science API**: retrieves empirical
    cognitive-science / educational-psychology findings (custom HTTPS site API,
    NCBI PubMed E-utilities, OpenAlex, Psychologie et Sérénité) for learner
    hypotheses, strategies, and check-ins.
  * **Slot 2 (`ai2`) — Education Content API**: retrieves verified curriculum
    explanations, key facts, definitions, and misconceptions (custom HTTPS site
    API, Oak National Academy lesson summaries, Wikipedia/Wikibooks plain-text
    extracts) to ground ContentPacks and tutoring.
  * **Slot 3 (`ai3`) — Adaptation API**: adapts AI 1's learner state + AI 2's
    educational material into step-sized, whole-plan-validated lessons (custom
    HTTPS adaptation endpoint, OpenAI-compatible chat completions, or dedicated
    Gemini key/model).

This module exposes non-secret status rows and live health probes for the
``adapt config`` CLI. Raw keys and full URLs are never included in returned
dicts.
"""

from __future__ import annotations

from typing import Any

from . import ai1_psychology, ai2_education, ai3_adaptation
from .http import Transport

SLOT_IDS: tuple[str, str, str] = ("ai1", "ai2", "ai3")


def slot_status_rows(settings: Any) -> list[dict[str, Any]]:
    """Return non-secret status metadata for all three AI API slots."""
    ai1_url = bool(str(getattr(settings, "ai1_api_url", "") or "").strip())
    ai1_key = bool(str(getattr(settings, "ai1_api_key", "") or "").strip())
    pubmed_key = bool(str(getattr(settings, "pubmed_api_key", "") or "").strip())
    wellbeing_on = bool(getattr(settings, "connector_wellbeing_enabled", False))
    psych_on = bool(getattr(settings, "ai1_psychology_enabled", False))
    ai1_providers: list[str] = []
    if ai1_url:
        ai1_providers.append("custom_site")
    if pubmed_key or psych_on:
        ai1_providers.append("pubmed")
    if psych_on:
        ai1_providers.append("openalex")
    if wellbeing_on or psych_on:
        ai1_providers.append("psychologie")
    shared_gemini_set = bool(str(getattr(settings, "gemini_api_key", "") or "").strip())
    if not ai1_providers and (shared_gemini_set or ai1_key):
        ai1_providers.append("local_rules+llm")

    ai2_url = bool(str(getattr(settings, "ai2_api_url", "") or "").strip())
    ai2_key = bool(str(getattr(settings, "ai2_api_key", "") or "").strip())
    oak_key = bool(str(getattr(settings, "oak_api_key", "") or "").strip())
    edu_on = bool(getattr(settings, "ai2_education_enabled", False))
    ai2_providers: list[str] = []
    if ai2_url:
        ai2_providers.append("custom_site")
    if oak_key:
        ai2_providers.append("oak_lessons")
    if edu_on:
        ai2_providers.extend(["wikipedia", "wikibooks"])

    ai3_url_raw = str(getattr(settings, "ai3_api_url", "") or "").strip()
    ai3_url = bool(ai3_url_raw)
    ai3_key = bool(str(getattr(settings, "ai3_api_key", "") or "").strip())
    shared_gemini = bool(str(getattr(settings, "gemini_api_key", "") or "").strip())
    ai3_providers: list[str] = []
    if ai3_url:
        ai3_providers.append(
            "openai_compatible"
            if ai3_adaptation.is_openai_compatible_url(ai3_url_raw)
            else "custom_site"
        )
    elif ai3_key:
        ai3_providers.append("gemini_dedicated")
    elif shared_gemini:
        ai3_providers.append("gemini_shared")

    return [
        {
            "slot": "ai1",
            "function": "Psychology & Learning-Science API",
            "enabled": ai1_psychology.slot_enabled(settings) or ai1_key,
            "custom_url_set": ai1_url,
            "key_set": ai1_key or pubmed_key,
            "providers": ai1_providers or ["deterministic_only"],
        },
        {
            "slot": "ai2",
            "function": "Education Content API",
            "enabled": ai2_education.slot_enabled(settings) or ai2_key,
            "custom_url_set": ai2_url,
            "key_set": ai2_key or oak_key,
            "providers": ai2_providers or ["bundled_packs_only"],
        },
        {
            "slot": "ai3",
            "function": "Lesson Adaptation API",
            "enabled": bool(ai3_url or ai3_key or shared_gemini),
            "custom_url_set": ai3_url,
            "key_set": ai3_key or shared_gemini,
            "providers": ai3_providers or ["deterministic_adapter"],
        },
    ]


def check_slot(
    slot: str,
    settings: Any,
    *,
    llm: Any = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Run one minimal live probe for ``ai1``, ``ai2``, or ``ai3``."""
    key = slot.strip().lower()
    if key == "ai1":
        res = ai1_psychology.health(settings, transport=transport)
        return {"slot": "ai1", "function": "Psychology & Learning-Science API", **res}
    if key == "ai2":
        res = ai2_education.health(settings, transport=transport)
        return {"slot": "ai2", "function": "Education Content API", **res}
    if key == "ai3":
        res = ai3_adaptation.health(settings, llm=llm, transport=transport)
        return {"slot": "ai3", "function": "Lesson Adaptation API", **res}
    raise ValueError(f"Unknown slot '{slot}'. Expected one of: {', '.join(SLOT_IDS)}")
