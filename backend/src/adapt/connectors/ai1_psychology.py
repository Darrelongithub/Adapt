"""Slot 1 (AI 1) — Psychology & Learning-Science API connector.

AI 1's job is to understand *how* the learner is studying (cognitive load,
memory decay, retrieval practice, worked-example scaffolding, achievement
emotions) and ground its hypotheses and proposals in real psychological
findings rather than bare templates alone.

Two ways to connect Slot 1 (both CLI / ``.env`` only, never browser-exposed):

1. **Custom Psychology Site API** (``ADAPT_AI1_API_URL`` + ``ADAPT_AI1_API_KEY``):
   Connect any HTTPS endpoint that serves psychological or learning-science
   facts/evidence. ADAPT sends only generic construct/strategy query terms
   (never learner names, answers, notes, or IDs), caches responses in the
   encrypted ``http_cache`` table, and sanitizes every returned string.
2. **Built-in Psychology & Cognitive-Science APIs**
   (enabled via ``ADAPT_AI1_PSYCHOLOGY_ENABLED=true`` or ``ADAPT_PUBMED_API_KEY``):
   * **NCBI PubMed E-utilities** (``eutils.ncbi.nlm.nih.gov``) — peer-reviewed
     cognitive & educational psychology studies;
   * **OpenAlex** (``api.openalex.org``) — open-access empirical findings on
     cognitive load, retrieval practice, spacing, and metacognition;
   * **Psychologie et Sérénité** (``psychologieetserenite.com``) — CC BY 4.0
     coping/study-stress reading when frustration or load is high.

Every failure degrades silently to AI 1's built-in literature citations so an
offline or rate-limited API never blocks analysis or check-ins.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from . import psychologie as WELLBEING
from .http import Transport, auth_headers, guarded_get_json

log = logging.getLogger("adapt.connectors.ai1_psychology")

PUBMED_HOST = "eutils.ncbi.nlm.nih.gov"
PUBMED_BASE = f"https://{PUBMED_HOST}/entrez/eutils"
OPENALEX_HOST = "api.openalex.org"
OPENALEX_BASE = f"https://{OPENALEX_HOST}/works"

_HTML_TAG = re.compile(r"<[^>]+>")
_MARKUP = re.compile(r"```|`|\*\*|__")

#: Construct queries mapped from ADAPT's internal strategy & detector IDs so
#: outbound requests carry domain psychology terms, never learner text.
STRATEGY_QUERIES: dict[str, str] = {
    "worked_examples": "cognitive load worked examples novice problem solving",
    "retrieval_practice": "retrieval practice testing effect long-term memory",
    "spaced_practice": "spaced repetition forgetting curve memory consolidation",
    "difficulty_recalibration": "zone of proximal development desirable difficulty",
    "hint_fading": "scaffolding fading cognitive load self-regulated learning",
    "teach_back": "self-explanation generation effect conceptual understanding",
    "teachback": "self-explanation generation effect conceptual understanding",
    "interleaving": "interleaved practice discriminative contrast learning",
    "segmenting": "multimedia learning segmenting working memory capacity",
    "prerequisite_review": "prior knowledge prerequisite activation schema acquisition",
    "fatigue": "mental fatigue cognitive overload rest break learning",
    "frustration": "achievement emotions frustration control value theory learning",
}


@dataclass(frozen=True)
class PsychologyFact:
    """One sanitized, attributed psychological finding from an external API."""

    id: str
    claim: str
    ref: str
    source: str  # custom_ai1 | pubmed | openalex | wellbeing
    attribution: str
    url: str | None = None

    @property
    def finding(self) -> str:
        return self.claim

    @property
    def citation(self) -> str:
        return self.ref

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "claim": self.claim,
            "finding": self.claim,
            "ref": self.ref,
            "citation": self.ref,
            "source": self.source,
            "attribution": self.attribution,
        }
        if self.url:
            out["url"] = self.url
        return out


@dataclass
class PsychologyBundle:
    """What Slot 1 returned for a psychological construct lookup."""

    query: str
    facts: list[PsychologyFact] = field(default_factory=list)
    wellbeing_links: list[dict[str, Any]] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    enabled: bool = False

    def citations(self) -> list[dict[str, str]]:
        return [{"ref": f.ref, "claim": f.claim} for f in self.facts]

    def for_prompt(self, *, limit: int = 3) -> list[dict[str, str]]:
        return [
            {
                "finding": f.claim,
                "citation": f.ref,
                "source": f.source,
            }
            for f in self.facts[: max(1, limit)]
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "facts": [f.as_dict() for f in self.facts],
            "wellbeing_links": list(self.wellbeing_links),
            "sources_used": list(self.sources_used),
            "enabled": self.enabled,
        }


def _clean_text(value: Any, *, maximum: int = 360) -> str:
    raw = _HTML_TAG.sub(" ", str(value or ""))
    raw = _MARKUP.sub("", raw)
    text = " ".join(raw.split()).strip()
    return text[:maximum].strip()


def _safe_https_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return url[:400]


def _fact_id(source: str, claim: str, ref: str) -> str:
    digest = hashlib.sha256(f"{source}:{ref}:{claim}".encode()).hexdigest()[:12]
    return f"psy_{digest}"


def slot_enabled(settings: Any | None) -> bool:
    """Whether AI 1's external Psychology API layer is active on this install."""
    if settings is None:
        return False
    if str(getattr(settings, "ai1_api_url", "") or "").strip():
        return True
    if str(getattr(settings, "pubmed_api_key", "") or "").strip():
        return True
    return bool(getattr(settings, "ai1_psychology_enabled", False))


def construct_query_for(
    *,
    strategy: str | None = None,
    detector: str | None = None,
    emotion: str | None = None,
    fallback_query: str = "cognitive load memory learning strategies",
) -> str:
    """Map an internal detector/strategy/emotion to a clean, PII-free query string."""
    for key in (strategy, detector, emotion):
        token = str(key or "").strip().lower()
        if token in STRATEGY_QUERIES:
            return STRATEGY_QUERIES[token]
    cleaned = re.sub(r"[^a-zA-Z0-9 _-]+", " ", str(fallback_query or "")).strip()
    words = [w for w in cleaned.split() if 2 <= len(w) <= 32][:8]
    return " ".join(words) or "cognitive load learning science"


def _parse_custom_payload(payload: Any, *, host: str, limit: int) -> list[PsychologyFact]:
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        for key in ("facts", "results", "findings", "articles", "items", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
        if not rows and any(
            payload.get(k) for k in ("claim", "summary", "fact", "statement", "title")
        ):
            rows = [payload]

    out: list[PsychologyFact] = []
    for row in rows:
        if isinstance(row, str):
            claim = _clean_text(row, maximum=320)
            if len(claim) >= 12:
                ref = f"Connected psychology source ({host})"
                out.append(
                    PsychologyFact(
                        id=_fact_id("custom_ai1", claim, ref),
                        claim=claim,
                        ref=ref,
                        source="custom_ai1",
                        attribution=ref,
                    )
                )
        elif isinstance(row, Mapping):
            claim = _clean_text(
                row.get("claim")
                or row.get("finding")
                or row.get("fact")
                or row.get("statement")
                or row.get("text")
                or row.get("summary")
                or row.get("description")
                or row.get("abstract")
                or row.get("title"),
                maximum=320,
            )
            if len(claim) < 12:
                continue
            ref = _clean_text(
                row.get("ref")
                or row.get("citation")
                or row.get("author")
                or row.get("title")
                or f"Connected psychology API ({host})",
                maximum=120,
            )
            attribution = _clean_text(
                row.get("attribution") or row.get("licence") or row.get("license") or ref,
                maximum=140,
            )
            url = _safe_https_url(row.get("url") or row.get("link"))
            out.append(
                PsychologyFact(
                    id=_fact_id("custom_ai1", claim, ref),
                    claim=claim,
                    ref=ref,
                    source="custom_ai1",
                    attribution=attribution,
                    url=url,
                )
            )
        if len(out) >= limit:
            break
    return out


def fetch_from_custom_site(
    query: str,
    *,
    api_url: str,
    api_key: str | None = None,
    api_header: str = "Authorization",
    strategy: str | None = None,
    emotion: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 4,
) -> list[PsychologyFact]:
    """Pull psychological facts from an operator-configured HTTPS site API."""
    parsed = urlparse(str(api_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return []
    params: dict[str, Any] = {
        "q": query[:160],
        "domain": "psychology",
        "limit": max(1, min(10, limit)),
    }
    if strategy:
        params["strategy"] = str(strategy)[:64]
    if emotion:
        params["emotion"] = str(emotion)[:32]
    payload, _src = guarded_get_json(
        connector="ai1_custom",
        allowed_host=parsed.hostname,
        url=api_url.strip(),
        headers=auth_headers(api_key, api_header),
        params=params,
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    if payload is None:
        return []
    return _parse_custom_payload(payload, host=parsed.hostname, limit=limit)


def _reconstruct_openalex_abstract(work: Mapping[str, Any]) -> str:
    inv = work.get("abstract_inverted_index")
    if not isinstance(inv, Mapping) or not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        if isinstance(idxs, list):
            for idx in idxs[:4]:
                if isinstance(idx, int):
                    positions.append((idx, str(word)))
    positions.sort(key=lambda item: item[0])
    text = " ".join(w for _, w in positions[:70])
    # Keep first 1-2 sentences so it reads as a crisp psychological finding.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return _clean_text(" ".join(sentences[:2]) or text, maximum=320)


def fetch_from_openalex_psychology(
    query: str,
    *,
    mailto: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 2,
) -> list[PsychologyFact]:
    """Query OpenAlex for empirical cognitive & educational psychology works."""
    params: dict[str, Any] = {
        "search": f"{query} psychology learning",
        "per-page": max(2, min(8, limit * 2)),
        "select": (
            "id,doi,display_name,publication_year,authorships,abstract_inverted_index,open_access"
        ),
    }
    if mailto:
        params["mailto"] = str(mailto).strip()
    payload, _src = guarded_get_json(
        connector="ai1_openalex",
        allowed_host=OPENALEX_HOST,
        url=OPENALEX_BASE,
        params=params,
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    if not isinstance(payload, Mapping):
        return []
    out: list[PsychologyFact] = []
    for work in (payload.get("results") or []):
        if not isinstance(work, Mapping):
            continue
        title = _clean_text(work.get("display_name"), maximum=200)
        abstract = _reconstruct_openalex_abstract(work)
        claim = abstract if len(abstract) >= 24 else title
        if len(claim) < 16:
            continue
        authors = [
            str((a.get("author") or {}).get("display_name") or "").strip()
            for a in (work.get("authorships") or [])[:2]
            if isinstance(a, Mapping) and (a.get("author") or {}).get("display_name")
        ]
        year = work.get("publication_year")
        author_label = authors[0].split()[-1] if authors else "OpenAlex"
        if len(authors) > 1:
            author_label += " et al."
        ref = f"{author_label} {year}" if year else author_label
        url = _safe_https_url(work.get("doi") or work.get("id"))
        out.append(
            PsychologyFact(
                id=_fact_id("openalex", claim, ref),
                claim=claim,
                ref=ref,
                source="openalex",
                attribution="OpenAlex scholarly index (CC0 metadata)",
                url=url,
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch_from_pubmed(
    query: str,
    *,
    api_key: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 2,
) -> list[PsychologyFact]:
    """Query NCBI PubMed E-utilities for cognitive/learning psychology articles."""
    search_params: dict[str, Any] = {
        "db": "pubmed",
        "term": f"({query}) AND (learning OR memory OR cognition)",
        "retmode": "json",
        "retmax": max(1, min(5, limit)),
        "sort": "relevance",
    }
    # If an NCBI API key is set, avoid caching the key in the shared cache table.
    active_cache = None if api_key else cache_session
    if api_key:
        search_params["api_key"] = str(api_key).strip()
    search_payload, _src = guarded_get_json(
        connector="ai1_pubmed",
        allowed_host=PUBMED_HOST,
        url=f"{PUBMED_BASE}/esearch.fcgi",
        params=search_params,
        timeout_s=timeout_s,
        cache_session=active_cache,
        transport=transport,
    )
    id_list = (
        ((search_payload or {}).get("esearchresult") or {}).get("idlist")
        if isinstance(search_payload, Mapping)
        else None
    )
    if not isinstance(id_list, list) or not id_list:
        return []
    ids = [str(x).strip() for x in id_list[:limit] if str(x).strip().isdigit()]
    if not ids:
        return []

    summary_params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
    }
    if api_key:
        summary_params["api_key"] = str(api_key).strip()
    summary_payload, _src2 = guarded_get_json(
        connector="ai1_pubmed",
        allowed_host=PUBMED_HOST,
        url=f"{PUBMED_BASE}/esummary.fcgi",
        params=summary_params,
        timeout_s=timeout_s,
        cache_session=active_cache,
        transport=transport,
    )
    result_map = (
        (summary_payload or {}).get("result") if isinstance(summary_payload, Mapping) else None
    )
    if not isinstance(result_map, Mapping):
        return []

    out: list[PsychologyFact] = []
    for pmid in ids:
        doc = result_map.get(pmid)
        if not isinstance(doc, Mapping):
            continue
        title = _clean_text(doc.get("title"), maximum=280).rstrip(".")
        if len(title) < 16:
            continue
        authors = doc.get("authors") or []
        first_author = ""
        if isinstance(authors, list) and authors and isinstance(authors[0], Mapping):
            first_author = _clean_text(authors[0].get("name"), maximum=48)
        pubdate = str(doc.get("pubdate") or "")[:4]
        year = pubdate if pubdate.isdigit() else ""
        journal = _clean_text(doc.get("fulljournalname") or doc.get("source"), maximum=80)
        ref_parts = [p for p in (first_author or "PubMed", year) if p]
        ref = " ".join(ref_parts) + (f" ({journal})" if journal else "")
        out.append(
            PsychologyFact(
                id=_fact_id("pubmed", title, ref),
                claim=f"{title}.",
                ref=ref[:120],
                source="pubmed",
                attribution="NCBI PubMed (U.S. National Library of Medicine)",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch_psychology_facts(
    query: str | None = None,
    *,
    strategy: str | None = None,
    detector: str | None = None,
    emotion: str | None = None,
    settings: Any | None = None,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 4,
) -> PsychologyBundle:
    """Fetch psychological findings for AI 1 from custom and/or built-in APIs.

    Returns an empty ``PsychologyBundle`` when Slot 1 is not enabled so offline
    installs and existing unit tests make zero outbound calls.
    """
    resolved_query = construct_query_for(
        strategy=strategy,
        detector=detector,
        emotion=emotion,
        fallback_query=query or "cognitive load learning strategies",
    )
    if not slot_enabled(settings):
        return PsychologyBundle(query=resolved_query, enabled=False)

    timeout = float(getattr(settings, "connector_timeout_s", 8.0) or 8.0)
    facts: list[PsychologyFact] = []
    sources: list[str] = []

    custom_url = str(getattr(settings, "ai1_api_url", "") or "").strip()
    if custom_url:
        custom_facts = fetch_from_custom_site(
            resolved_query,
            api_url=custom_url,
            api_key=getattr(settings, "ai1_api_key", None),
            api_header=str(getattr(settings, "ai1_api_header", "Authorization") or "Authorization"),
            strategy=strategy,
            emotion=emotion,
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=limit,
        )
        if custom_facts:
            facts.extend(custom_facts)
            sources.append("custom_ai1")

    built_in_on = bool(
        getattr(settings, "ai1_psychology_enabled", False)
        or str(getattr(settings, "pubmed_api_key", "") or "").strip()
    )
    if len(facts) < limit and built_in_on:
        remaining = limit - len(facts)
        pubmed_facts = fetch_from_pubmed(
            resolved_query,
            api_key=getattr(settings, "pubmed_api_key", None),
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=max(1, remaining),
        )
        if pubmed_facts:
            facts.extend(pubmed_facts)
            sources.append("pubmed")

    if len(facts) < limit and built_in_on and getattr(settings, "openalex_enabled", True):
        remaining = limit - len(facts)
        oa_facts = fetch_from_openalex_psychology(
            resolved_query,
            mailto=getattr(settings, "openalex_mailto", None),
            timeout_s=timeout,
            cache_session=cache_session,
            transport=transport,
            limit=max(1, remaining),
        )
        if oa_facts:
            facts.extend(oa_facts)
            sources.append("openalex")

    wellbeing: list[dict[str, Any]] = []
    if (
        emotion in {"frustration", "anxiety"}
        and (
            getattr(settings, "connector_wellbeing_enabled", False)
            or getattr(settings, "ai1_psychology_enabled", False)
        )
    ):
        wellbeing = WELLBEING.wellbeing_links(
            limit=2, cache_session=cache_session, transport=transport
        )
        if wellbeing:
            sources.append("wellbeing")

    # Deduplicate by claim text while preserving priority order.
    deduped: list[PsychologyFact] = []
    seen_claims: set[str] = set()
    for item in facts:
        norm = item.claim.casefold()
        if norm in seen_claims:
            continue
        seen_claims.add(norm)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    return PsychologyBundle(
        query=resolved_query,
        facts=deduped,
        wellbeing_links=wellbeing,
        sources_used=sources,
        enabled=True,
    )


def health(settings: Any, *, transport: Transport | None = None) -> dict[str, Any]:
    """One minimal live probe of AI 1's configured Psychology API."""
    if not slot_enabled(settings):
        return {
            "ok": False,
            "detail": (
                "Slot ai1 is off (set ADAPT_AI1_API_URL with `adapt config set-url --slot ai1`, "
                "`adapt config set-key --service pubmed`, or ADAPT_AI1_PSYCHOLOGY_ENABLED=true)"
            ),
        }
    bundle = fetch_psychology_facts(
        strategy="worked_examples",
        settings=settings,
        transport=transport,
        limit=1,
    )
    if bundle.facts:
        joined = ", ".join(bundle.sources_used)
        return {
            "ok": True,
            "detail": f"Slot ai1 answered ({joined}; {len(bundle.facts)} finding)",
            "sources": bundle.sources_used,
        }
    return {
        "ok": False,
        "detail": "Slot ai1 is enabled, but the upstream psychology API did not return findings",
    }
