"""Slot 2 (AI 2) — Education Content API connector.

AI 2's job is to supply verified, structured **educational subject matter**
(explanations, key facts, definitions, misconceptions, and practice questions)
for AI 3 to adapt to the learner.

Two ways to connect Slot 2 (both CLI / ``.env`` only, never browser-exposed):

1. **Custom Education Site API** (``ADAPT_AI2_API_URL`` + ``ADAPT_AI2_API_KEY``):
   Connect any HTTPS endpoint that serves educational topic material. ADAPT
   queries it with ``{topic, level, curriculum, grade}``, strips markup,
   validates plain-text bounds, caches responses in ``http_cache``, and feeds
   the returned facts/misconceptions directly into AI 2's ``ContentPack`` and
   live tutor context.
2. **Built-in Education Content APIs**
   (enabled via ``ADAPT_AI2_EDUCATION_ENABLED=true`` or ``ADAPT_OAK_API_KEY``):
   * **Oak National Academy Open API** (``open-api.thenational.academy``) —
     full lesson summaries: ``pupilLessonOutcome``, ``keyLearningPoints``,
     ``lessonKeywords`` (definitions), and ``misconceptionsAndCommonMistakes``
     under the Open Government Licence v3.0;
   * **Wikipedia & Wikibooks Plain-Text Extracts** (``en.wikipedia.org`` /
     ``en.wikibooks.org``) — introductory factual extracts (``explaintext=1``)
     under CC BY-SA 4.0;
   * **OpenLibrary** (``openlibrary.org``) — textbook/reference metadata and
     first sentences.

Zero learner PII ever leaves ADAPT on these requests: only the sanitized topic
title and optional curriculum/grade scope are sent.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..content.skeleton.types import (
    Concept,
    Item,
    KeyFact,
    Misconception,
    Option,
    Pack,
)
from . import oak as OAK
from .http import Transport, auth_headers, guarded_get_json

log = logging.getLogger("adapt.connectors.ai2_education")

WIKIPEDIA_HOST = "en.wikipedia.org"
WIKIPEDIA_BASE = f"https://{WIKIPEDIA_HOST}/w/api.php"
WIKIBOOKS_HOST = "en.wikibooks.org"
WIKIBOOKS_BASE = f"https://{WIKIBOOKS_HOST}/w/api.php"
OPENLIBRARY_HOST = "openlibrary.org"
OPENLIBRARY_BASE = f"https://{OPENLIBRARY_HOST}/search.json"

_HTML_TAG = re.compile(r"<[^>]+>")
_MARKUP = re.compile(r"```|`|\*\*|__|\[\d+\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class EducationalFact:
    id: str
    statement: str
    source: str
    attribution: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "statement": self.statement,
            "source": self.source,
            "attribution": self.attribution,
        }


@dataclass(frozen=True)
class EducationalMisconception:
    id: str
    wrong_belief: str
    why_tempting: str
    correction: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "wrong_belief": self.wrong_belief,
            "why_tempting": self.why_tempting,
            "correction": self.correction,
            "source": self.source,
        }


@dataclass
class EducationalMaterial:
    """Structured subject content pulled by Slot 2 for one topic."""

    topic: str
    title: str
    summary: str = ""
    facts: list[EducationalFact] = field(default_factory=list)
    misconceptions: list[EducationalMisconception] = field(default_factory=list)
    worked_steps: list[dict[str, str]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    url: str | None = None
    enabled: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.summary or self.facts)

    def plain_passage(self) -> str:
        """Combined plain-text passage for AI 2's local BM25 & tutor grounding."""
        parts: list[str] = []
        if self.summary:
            parts.append(self.summary)
        for fact in self.facts[:8]:
            if fact.statement not in self.summary:
                parts.append(fact.statement)
        for misc in self.misconceptions[:3]:
            parts.append(
                f"Common mix-up: {misc.wrong_belief}. Correction: {misc.correction}"
            )
        return " ".join(parts).strip()[:3200]

    def grounding_dict(self) -> dict[str, Any]:
        """Compact JSON-safe reference injected into AI 2's skeleton author prompt."""
        return {
            "title": self.title,
            "summary": self.summary[:600],
            "verified_facts": [f.statement for f in self.facts[:10]],
            "known_misconceptions": [
                {
                    "wrong_belief": m.wrong_belief,
                    "correction": m.correction,
                }
                for m in self.misconceptions[:5]
            ],
            "sources": list(self.attributions[:4]),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "title": self.title,
            "summary": self.summary,
            "facts": [f.as_dict() for f in self.facts],
            "misconceptions": [m.as_dict() for m in self.misconceptions],
            "worked_steps": list(self.worked_steps),
            "questions": list(self.questions),
            "sources_used": list(self.sources_used),
            "attributions": list(self.attributions),
            "url": self.url,
            "enabled": self.enabled,
        }


def _clean_text(value: Any, *, maximum: int = 320) -> str:
    raw = _HTML_TAG.sub(" ", str(value or ""))
    raw = _MARKUP.sub("", raw)
    # Strip URLs so learner-facing facts stay self-contained plain text.
    raw = re.sub(r"https?://\S+", "", raw)
    text = " ".join(raw.split()).strip()
    return text[:maximum].strip()


def _ensure_period(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if text[-1] not in ".!?":
        return text + "."
    return text


def _short_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def slot_enabled(settings: Any | None) -> bool:
    """Whether AI 2's external Education Content API layer is active."""
    if settings is None:
        return False
    if str(getattr(settings, "ai2_api_url", "") or "").strip():
        return True
    if str(getattr(settings, "oak_api_key", "") or "").strip():
        return True
    return bool(getattr(settings, "ai2_education_enabled", False))


def _sentences_to_facts(
    text: str,
    *,
    source: str,
    attribution: str,
    limit: int = 6,
) -> list[EducationalFact]:
    out: list[EducationalFact] = []
    for raw_sentence in _SENTENCE_SPLIT.split(text):
        cleaned = _ensure_period(_clean_text(raw_sentence, maximum=240))
        words = cleaned.split()
        if len(words) < 5 or len(cleaned) < 24:
            continue
        out.append(
            EducationalFact(
                id=_short_id("f", f"{source}:{cleaned}"),
                statement=cleaned,
                source=source,
                attribution=attribution,
            )
        )
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# 1. Custom Site Education API
# --------------------------------------------------------------------------- #


def fetch_from_custom_site(
    topic: str,
    *,
    api_url: str,
    api_key: str | None = None,
    api_header: str = "Authorization",
    level: int | str | None = None,
    curriculum: str | None = None,
    grade: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 8,
) -> EducationalMaterial | None:
    """Pull structured educational content from an operator-connected HTTPS site."""
    parsed = urlparse(str(api_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    params: dict[str, Any] = {"topic": topic[:160], "limit": max(1, min(20, limit))}
    if level is not None:
        params["level"] = str(level)[:24]
    if curriculum:
        params["curriculum"] = str(curriculum)[:32]
    if grade:
        params["grade"] = str(grade)[:32]

    payload, _src = guarded_get_json(
        connector="ai2_custom",
        allowed_host=parsed.hostname,
        url=api_url.strip(),
        headers=auth_headers(api_key, api_header),
        params=params,
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    if payload is None:
        return None

    host = parsed.hostname
    default_attr = f"Connected education API ({host})"
    if isinstance(payload, list):
        payload = {"items": payload}
    if not isinstance(payload, Mapping):
        return None

    # Unwrap nested `data` / `lesson` / `result` envelopes when present.
    root: Mapping[str, Any] = payload
    for envelope in ("lesson", "content", "result", "data"):
        inner = payload.get(envelope)
        if isinstance(inner, Mapping):
            root = inner
            break

    attribution = _clean_text(
        root.get("attribution")
        or payload.get("attribution")
        or root.get("licence")
        or root.get("license")
        or default_attr,
        maximum=160,
    )
    title = _clean_text(root.get("title") or root.get("topic_title") or topic, maximum=140) or topic
    summary = _clean_text(
        root.get("summary")
        or root.get("explanation")
        or root.get("extract")
        or root.get("overview")
        or root.get("description")
        or "",
        maximum=800,
    )

    facts: list[EducationalFact] = []
    raw_facts = (
        root.get("facts")
        or root.get("key_facts")
        or root.get("keyLearningPoints")
        or root.get("points")
        or []
    )
    if isinstance(raw_facts, list):
        for entry in raw_facts[:limit]:
            stmt = ""
            if isinstance(entry, str):
                stmt = _ensure_period(_clean_text(entry, maximum=240))
            elif isinstance(entry, Mapping):
                stmt = _ensure_period(
                    _clean_text(
                        entry.get("statement")
                        or entry.get("fact")
                        or entry.get("text")
                        or entry.get("keyLearningPoint")
                        or entry.get("summary"),
                        maximum=240,
                    )
                )
            if len(stmt) >= 12:
                facts.append(
                    EducationalFact(
                        id=_short_id("f", f"custom:{stmt}"),
                        statement=stmt,
                        source="custom_ai2",
                        attribution=attribution,
                    )
                )

    # Also harvest facts from `concepts` or `results` / `articles` arrays if supplied.
    for list_key in ("concepts", "lessons", "articles", "results", "items"):
        entries = root.get(list_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if list_key == "items" and entry.get("stem"):
                continue  # question item, handled below
            concept_idea = _ensure_period(
                _clean_text(
                    entry.get("key_idea")
                    or entry.get("summary")
                    or entry.get("explanation")
                    or entry.get("extract")
                    or entry.get("description"),
                    maximum=240,
                )
            )
            if len(concept_idea) >= 16 and len(facts) < limit:
                facts.append(
                    EducationalFact(
                        id=_short_id("f", f"custom:{concept_idea}"),
                        statement=concept_idea,
                        source="custom_ai2",
                        attribution=attribution,
                    )
                )
            nested_facts = entry.get("key_facts") or entry.get("facts") or []
            if isinstance(nested_facts, list):
                for nf in nested_facts:
                    raw_nf = (
                        nf
                        if isinstance(nf, str)
                        else (nf.get("statement") or nf.get("text"))
                        if isinstance(nf, Mapping)
                        else ""
                    )
                    stmt = _ensure_period(_clean_text(raw_nf, maximum=240))
                    if len(stmt) >= 12 and len(facts) < limit:
                        facts.append(
                            EducationalFact(
                                id=_short_id("f", f"custom:{stmt}"),
                                statement=stmt,
                                source="custom_ai2",
                                attribution=attribution,
                            )
                        )

    if not facts and summary:
        facts = _sentences_to_facts(
            summary, source="custom_ai2", attribution=attribution, limit=limit
        )
    if not summary and facts:
        summary = " ".join(f.statement for f in facts[:3])[:800]

    misconceptions: list[EducationalMisconception] = []
    raw_misc = root.get("misconceptions") or root.get("common_mistakes") or []
    if isinstance(raw_misc, list):
        for entry in raw_misc[:6]:
            if not isinstance(entry, Mapping):
                continue
            wrong = _clean_text(
                entry.get("wrong_belief") or entry.get("misconception") or entry.get("mistake"),
                maximum=180,
            )
            corr = _ensure_period(
                _clean_text(
                    entry.get("correction") or entry.get("response") or entry.get("fix"),
                    maximum=220,
                )
            )
            tempting = _clean_text(
                entry.get("why_tempting") or entry.get("reason") or "A common surface shortcut.",
                maximum=180,
            )
            if wrong and corr:
                misconceptions.append(
                    EducationalMisconception(
                        id=_short_id("m", f"custom:{wrong}"),
                        wrong_belief=wrong,
                        why_tempting=tempting,
                        correction=corr,
                        source="custom_ai2",
                    )
                )

    worked_steps: list[dict[str, str]] = []
    raw_example = root.get("worked_example") or root.get("example") or root.get("steps")
    step_rows = (
        raw_example.get("steps")
        if isinstance(raw_example, Mapping)
        else raw_example
        if isinstance(raw_example, list)
        else []
    )
    if isinstance(step_rows, list):
        for step in step_rows[:6]:
            if isinstance(step, str):
                txt = _clean_text(step, maximum=200)
                if txt:
                    worked_steps.append({"text": txt, "why": "Step from connected lesson material"})
            elif isinstance(step, Mapping):
                txt = _clean_text(step.get("text") or step.get("step"), maximum=200)
                why = _clean_text(
                    step.get("why") or step.get("reason") or "Follows from the rule",
                    maximum=140,
                )
                if txt:
                    worked_steps.append({"text": txt, "why": why})

    questions: list[dict[str, Any]] = []
    raw_questions = root.get("questions") or root.get("items") or root.get("quiz") or []
    if isinstance(raw_questions, list):
        for q in raw_questions[:6]:
            if not isinstance(q, Mapping):
                continue
            stem = _clean_text(q.get("stem") or q.get("question"), maximum=220)
            if not stem:
                continue
            options = q.get("options") or q.get("choices") or []
            answer = _clean_text(q.get("answer") or q.get("key") or "", maximum=120)
            questions.append({"stem": stem, "options": options, "key": answer})

    if not summary and not facts:
        return None

    url_val = root.get("url") or payload.get("url")
    safe_url = None
    if isinstance(url_val, str) and url_val.startswith("https://"):
        safe_url = url_val[:400]

    return EducationalMaterial(
        topic=topic,
        title=title,
        summary=summary,
        facts=facts[:limit],
        misconceptions=misconceptions,
        worked_steps=worked_steps,
        questions=questions,
        sources_used=["custom_ai2"],
        attributions=[attribution],
        url=safe_url,
        enabled=True,
    )


# --------------------------------------------------------------------------- #
# 2. Built-in Education Content APIs (Oak full summaries, Wikipedia/Wikibooks)
# --------------------------------------------------------------------------- #


def fetch_from_oak_lessons(
    topic: str,
    *,
    api_key: str,
    grade: str | None = None,
    curriculum: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 6,
) -> EducationalMaterial | None:
    """Fetch full lesson learning points, keywords, and misconceptions from Oak Open API."""
    if not api_key:
        return None
    headers = {"authorization": f"Bearer {api_key}"}

    # 1. Search lessons matching the topic.
    search_payload, _src = guarded_get_json(
        connector="ai2_oak",
        allowed_host=OAK.HOST,
        url=f"{OAK.BASE}/search/lessons",
        headers=headers,
        params={"q": topic[:100], "size": 3},
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    lesson_slug = ""
    lesson_title = ""
    rows: list[Any] = []
    if isinstance(search_payload, list):
        rows = search_payload
    elif isinstance(search_payload, Mapping):
        for key in ("lessons", "results", "data", "items"):
            if isinstance(search_payload.get(key), list):
                rows = search_payload[key]  # type: ignore[assignment]
                break
    for row in rows:
        if isinstance(row, Mapping):
            slug = str(row.get("lessonSlug") or row.get("slug") or "").strip()
            if slug and re.fullmatch(r"[a-zA-Z0-9_-]{2,120}", slug):
                lesson_slug = slug
                lesson_title = _clean_text(
                    row.get("lessonTitle") or row.get("title") or topic, maximum=140
                )
                break

    if not lesson_slug:
        return None

    # 2. Fetch the lesson summary containing keyLearningPoints, keywords, and misconceptions.
    summary_payload, _src2 = guarded_get_json(
        connector="ai2_oak",
        allowed_host=OAK.HOST,
        url=f"{OAK.BASE}/lessons/{lesson_slug}/summary",
        headers=headers,
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    if not isinstance(summary_payload, Mapping):
        return None

    title = _clean_text(summary_payload.get("lessonTitle") or lesson_title or topic, maximum=140)
    outcome = _ensure_period(
        _clean_text(summary_payload.get("pupilLessonOutcome") or "", maximum=320)
    )
    facts: list[EducationalFact] = []
    for kp in (summary_payload.get("keyLearningPoints") or [])[:limit]:
        raw_kp = kp.get("keyLearningPoint") if isinstance(kp, Mapping) else kp
        stmt = _ensure_period(_clean_text(raw_kp, maximum=240))
        if len(stmt) >= 12:
            facts.append(
                EducationalFact(
                    id=_short_id("f", f"oak:{stmt}"),
                    statement=stmt,
                    source="oak",
                    attribution=OAK.ATTRIBUTION,
                )
            )
    for kw in (summary_payload.get("lessonKeywords") or [])[:4]:
        if not isinstance(kw, Mapping):
            continue
        term = _clean_text(kw.get("keyword"), maximum=60)
        desc = _clean_text(kw.get("description"), maximum=180)
        if term and desc and len(facts) < limit:
            stmt = _ensure_period(f"{term}: {desc}")
            facts.append(
                EducationalFact(
                    id=_short_id("f", f"oak:{stmt}"),
                    statement=stmt,
                    source="oak",
                    attribution=OAK.ATTRIBUTION,
                )
            )

    misconceptions: list[EducationalMisconception] = []
    for m in (summary_payload.get("misconceptionsAndCommonMistakes") or [])[:5]:
        if not isinstance(m, Mapping):
            continue
        wrong = _clean_text(m.get("misconception"), maximum=180)
        corr = _ensure_period(_clean_text(m.get("response"), maximum=220))
        if wrong and corr:
            misconceptions.append(
                EducationalMisconception(
                    id=_short_id("m", f"oak:{wrong}"),
                    wrong_belief=wrong,
                    why_tempting="Common classroom mix-up identified in curriculum materials.",
                    correction=corr,
                    source="oak",
                )
            )

    summary = outcome or (" ".join(f.statement for f in facts[:3])[:600])
    if not summary and not facts:
        return None
    return EducationalMaterial(
        topic=topic,
        title=title or topic,
        summary=summary,
        facts=facts[:limit],
        misconceptions=misconceptions,
        sources_used=["oak"],
        attributions=[OAK.ATTRIBUTION],
        enabled=True,
    )


def _fetch_mediawiki_extract(
    topic: str,
    *,
    host: str,
    base_url: str,
    connector_name: str,
    source_label: str,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 6,
) -> tuple[str, str, list[EducationalFact], str] | None:
    """Fetch plain-text introductory educational extract from MediaWiki."""
    clean_topic = " ".join(str(topic or "").split()).strip()[:100]
    if not clean_topic:
        return None
    params = {
        "action": "query",
        "prop": "extracts",
        "exintro": "1",
        "explaintext": "1",
        "redirects": "1",
        "titles": clean_topic,
        "format": "json",
        "formatversion": "2",
    }
    payload, _src = guarded_get_json(
        connector=connector_name,
        allowed_host=host,
        url=base_url,
        params=params,
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    pages = (
        ((payload or {}).get("query") or {}).get("pages")
        if isinstance(payload, Mapping)
        else None
    )
    extract = ""
    page_title = clean_topic
    if (
        isinstance(pages, list)
        and pages
        and isinstance(pages[0], Mapping)
        and not pages[0].get("missing")
    ):
        page_title = str(pages[0].get("title") or clean_topic)
        extract = str(pages[0].get("extract") or "").strip()

    # Fallback: search for closest article title if exact title missed.
    if not extract:
        search_payload, _src2 = guarded_get_json(
            connector=connector_name,
            allowed_host=host,
            url=base_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": clean_topic,
                "srlimit": 1,
                "format": "json",
                "formatversion": "2",
            },
            timeout_s=timeout_s,
            cache_session=cache_session,
            transport=transport,
        )
        hits = (
            ((search_payload or {}).get("query") or {}).get("search")
            if isinstance(search_payload, Mapping)
            else None
        )
        if (
            isinstance(hits, list)
            and hits
            and isinstance(hits[0], Mapping)
            and hits[0].get("title")
        ):
            found_title = str(hits[0]["title"])
            payload2, _src3 = guarded_get_json(
                connector=connector_name,
                allowed_host=host,
                url=base_url,
                params={**params, "titles": found_title},
                timeout_s=timeout_s,
                cache_session=cache_session,
                transport=transport,
            )
            pages2 = (
                ((payload2 or {}).get("query") or {}).get("pages")
                if isinstance(payload2, Mapping)
                else None
            )
            if isinstance(pages2, list) and pages2 and isinstance(pages2[0], Mapping):
                page_title = str(pages2[0].get("title") or found_title)
                extract = str(pages2[0].get("extract") or "").strip()

    if not extract or "may refer to:" in extract[:200]:
        return None
    attribution = f'{source_label} contributors, "{page_title}" (CC BY-SA 4.0)'
    facts = _sentences_to_facts(
        extract, source=connector_name, attribution=attribution, limit=limit
    )
    summary = _clean_text(extract, maximum=700)
    if not facts and not summary:
        return None
    url = f"https://{host}/wiki/{page_title.replace(' ', '_')}"[:400]
    return page_title, summary, facts, url


def fetch_educational_material(
    topic: str,
    *,
    level: int | str | None = None,
    curriculum: str | None = None,
    grade: str | None = None,
    settings: Any | None = None,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 8,
) -> EducationalMaterial:
    """Pull educational content for ``topic`` from Slot 2's connected APIs.

    Priority order:
    1. Operator's custom education site API (``ADAPT_AI2_API_URL``)
    2. Oak National Academy full lesson summary (when ``ADAPT_OAK_API_KEY`` is set)
    3. Wikipedia & Wikibooks plain-text extracts (when ``ADAPT_AI2_EDUCATION_ENABLED``)
    """
    clean_topic = " ".join(str(topic or "").split()).strip()[:160]
    if not clean_topic or not slot_enabled(settings):
        return EducationalMaterial(topic=clean_topic, title=clean_topic or "Topic", enabled=False)

    timeout = float(getattr(settings, "connector_timeout_s", 8.0) or 8.0)
    combined = EducationalMaterial(topic=clean_topic, title=clean_topic, enabled=True)

    custom_url = str(getattr(settings, "ai2_api_url", "") or "").strip()
    if custom_url:
        custom = fetch_from_custom_site(
            clean_topic,
            api_url=custom_url,
            api_key=getattr(settings, "ai2_api_key", None),
            api_header=str(getattr(settings, "ai2_api_header", "Authorization") or "Authorization"),
            level=level,
            curriculum=curriculum,
            grade=grade,
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=limit,
        )
        if custom is not None and custom.has_content:
            combined.title = custom.title
            combined.summary = custom.summary
            combined.facts.extend(custom.facts)
            combined.misconceptions.extend(custom.misconceptions)
            combined.worked_steps.extend(custom.worked_steps)
            combined.questions.extend(custom.questions)
            combined.sources_used.extend(custom.sources_used)
            combined.attributions.extend(custom.attributions)
            combined.url = custom.url

    oak_key = str(getattr(settings, "oak_api_key", "") or "").strip()
    if oak_key and len(combined.facts) < limit:
        oak_mat = fetch_from_oak_lessons(
            clean_topic,
            api_key=oak_key,
            grade=grade,
            curriculum=curriculum,
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=limit - len(combined.facts),
        )
        if oak_mat is not None and oak_mat.has_content:
            if not combined.summary:
                combined.title = oak_mat.title
                combined.summary = oak_mat.summary
            combined.facts.extend(oak_mat.facts)
            combined.misconceptions.extend(oak_mat.misconceptions)
            combined.sources_used.extend(oak_mat.sources_used)
            combined.attributions.extend(oak_mat.attributions)

    if getattr(settings, "ai2_education_enabled", False) and len(combined.facts) < limit:
        wiki = _fetch_mediawiki_extract(
            clean_topic,
            host=WIKIPEDIA_HOST,
            base_url=WIKIPEDIA_BASE,
            connector_name="wikipedia",
            source_label="Wikipedia",
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=limit - len(combined.facts),
        )
        if wiki is not None:
            w_title, w_summary, w_facts, w_url = wiki
            if not combined.summary:
                combined.title = w_title
                combined.summary = w_summary
                combined.url = w_url
            combined.facts.extend(w_facts)
            combined.sources_used.append("wikipedia")
            if w_facts:
                combined.attributions.append(w_facts[0].attribution)

    if getattr(settings, "ai2_education_enabled", False) and len(combined.facts) < 4:
        wikibooks = _fetch_mediawiki_extract(
            clean_topic,
            host=WIKIBOOKS_HOST,
            base_url=WIKIBOOKS_BASE,
            connector_name="wikibooks",
            source_label="Wikibooks",
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=limit - len(combined.facts),
        )
        if wikibooks is not None:
            wb_title, wb_summary, wb_facts, wb_url = wikibooks
            if not combined.summary:
                combined.title = wb_title
                combined.summary = wb_summary
                combined.url = wb_url
            combined.facts.extend(wb_facts)
            combined.sources_used.append("wikibooks")
            if wb_facts:
                combined.attributions.append(wb_facts[0].attribution)

    # Deduplicate facts by normalized statement.
    unique_facts: list[EducationalFact] = []
    seen: set[str] = set()
    for fact in combined.facts:
        norm = fact.statement.casefold()
        if norm in seen:
            continue
        seen.add(norm)
        unique_facts.append(fact)
        if len(unique_facts) >= limit:
            break
    combined.facts = unique_facts
    combined.attributions = list(dict.fromkeys(combined.attributions))
    combined.sources_used = list(dict.fromkeys(combined.sources_used))
    return combined


def build_pack_from_material(
    material: EducationalMaterial,
    *,
    topic_key: str,
    subject_family: str,
) -> Pack | None:
    """Convert rich ``EducationalMaterial`` directly into a validated ``Pack``.

    Used when Slot 2 returned verified educational facts for a topic and no
    LLM is configured (or the LLM call was skipped/unavailable), so AI 3 still
    receives a real ``Pack`` grounded in the connected Education API rather than
    an empty ``simple`` placeholder.
    """
    if not material.has_content or len(material.facts) < 2:
        return None

    # Group facts into 1-4 progressive concepts (2-3 facts per concept).
    facts = material.facts[:8]
    chunk_size = 2 if len(facts) <= 4 else 3
    groups: list[Sequence[EducationalFact]] = [
        facts[i : i + chunk_size] for i in range(0, len(facts), chunk_size)
    ][:4]

    concepts: list[Concept] = []
    edges: list[tuple[str, str]] = []
    items: list[Item] = []

    for idx, group in enumerate(groups, start=1):
        cid = f"c-{idx}"
        prereqs = (f"c-{idx - 1}",) if idx > 1 else ()
        if idx > 1:
            edges.append((f"c-{idx - 1}", cid))

        key_facts = tuple(
            KeyFact(id=f"f-{idx}-{j}", statement=f.statement[:240])
            for j, f in enumerate(group, start=1)
        )
        lead_statement = key_facts[0].statement
        second_statement = key_facts[1].statement if len(key_facts) > 1 else lead_statement

        if idx - 1 < len(material.misconceptions):
            em = material.misconceptions[idx - 1]
            misc = Misconception(
                id=f"m-{idx}-1",
                wrong_belief=em.wrong_belief[:160],
                why_tempting=em.why_tempting[:160],
                correction=lead_statement,
            )
        else:
            misc = Misconception(
                id=f"m-{idx}-1",
                wrong_belief=f"Treating only part of {material.title} as the whole rule",
                why_tempting="Surface features are easier to recall than the underlying condition.",
                correction=lead_statement,
            )

        concept_name = (
            material.title[:80]
            if len(groups) == 1
            else f"{material.title[:60]} — part {idx}"
        )
        concept = Concept(
            id=cid,
            name=concept_name,
            key_idea=lead_statement,
            why_it_works=second_statement,
            prerequisites=prereqs,
            rung=min(4, idx),
            key_facts=key_facts,
            misconceptions=(misc,),
        )
        concepts.append(concept)

        # Build a matched A/B check pair grounded in the concept's key facts.
        pair_id = _short_id("pair", f"{topic_key}:{cid}")
        for leg_idx, leg_fact in enumerate((key_facts[0], key_facts[-1]), start=1):
            suffix = "a" if leg_idx == 1 else "b"
            item_id = _short_id(
                f"item-{suffix}", f"{topic_key}:{cid}:{leg_idx}:{leg_fact.statement}"
            )
            stem = (
                f"Which statement accurately describes {concept_name}?"
                if leg_idx == 1
                else f"In {material.title}, which of the following holds true?"
            )
            options = (
                Option(id="opt-correct", text=leg_fact.statement[:180], misconception_id=None),
                Option(id="opt-misc", text=misc.wrong_belief[:180], misconception_id=misc.id),
            )
            items.append(
                Item(
                    id=item_id,
                    concept_id=cid,
                    kind="mcq",
                    stem=stem,
                    options=options,
                    key="opt-correct",
                    pair_id=pair_id,
                    hints=(
                        f"Focus on the core rule of {concept_name}.",
                        "Rule out the common shortcut that ignores the condition.",
                        f"Recall the key idea: {leg_fact.statement[:90]}",
                    ),
                    teachback=(
                        f"State the main rule of {concept_name} in your own words.",
                        "Explain why the common mix-up fails.",
                        "Give one case where this applies.",
                    ),
                    fact_ids=(leg_fact.id,),
                    # Items auto-built without computational/verifier check stay unverified
                    # so scoring honesty (`item.scorable`) is preserved.
                    verification="unverified",
                )
            )

    return Pack(
        topic_key=topic_key,
        topic_title=material.title[:160] or topic_key,
        subject_family=subject_family,
        source="authored",
        concepts=tuple(concepts),
        prereq_edges=tuple(edges),
        items=tuple(items),
    )


def health(settings: Any, *, transport: Transport | None = None) -> dict[str, Any]:
    """One minimal live probe of AI 2's configured Education API."""
    if not slot_enabled(settings):
        return {
            "ok": False,
            "detail": (
                "Slot ai2 is off (set ADAPT_AI2_API_URL with `adapt config set-url --slot ai2`, "
                "`adapt config set-key --service oak`, or ADAPT_AI2_EDUCATION_ENABLED=true)"
            ),
        }
    custom_url = str(getattr(settings, "ai2_api_url", "") or "").strip()
    oak_key = str(getattr(settings, "oak_api_key", "") or "").strip()
    timeout = float(getattr(settings, "connector_timeout_s", 8.0) or 8.0)

    # When only Oak is configured, use Oak's zero-quota `/rate-limit` probe first.
    if oak_key and not custom_url and not getattr(settings, "ai2_education_enabled", False):
        probe = OAK.health(api_key=oak_key, timeout_s=timeout, transport=transport)
        if probe.get("ok"):
            return {
                "ok": True,
                "detail": "Slot ai2 answered (oak rate-limit probe ok)",
                "sources": ["oak"],
            }

    mat = fetch_educational_material(
        "fractions",
        settings=settings,
        transport=transport,
        limit=2,
    )
    if mat.has_content:
        joined = ", ".join(mat.sources_used)
        return {
            "ok": True,
            "detail": f"Slot ai2 answered ({joined}; {len(mat.facts)} fact(s))",
            "sources": mat.sources_used,
        }
    return {
        "ok": False,
        "detail": "Slot ai2 is enabled, but the upstream education API returned no content",
    }
