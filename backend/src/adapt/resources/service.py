"""Resource service: fan out to providers, cache, rank, persist, degrade politely.

Used by AI 2 (recommendations inside explanations) and by the Resources screen.
The contract callers can rely on: the response always has a per-provider status
list, so the UI can say "arXiv is unreachable, showing results from 3 days ago"
instead of pretending the result set is complete.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db.models import HttpCacheEntry, ResourceReference
from .base import CachePort, Provider, ProviderResult, Resource, keywords, rank
from .providers import PROVIDERS

log = logging.getLogger("adapt.resources")


class DBCache(CachePort):
    """SQLAlchemy-backed cache so repeated searches cost nothing (and work offline)."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def get(self, key: str) -> dict[str, Any] | None:
        def _read() -> dict[str, Any] | None:
            with self._sf() as s:
                row = s.get(HttpCacheEntry, key)
                if row is None:
                    return None
                row.hit_count = int(row.hit_count or 0) + 1
                payload = dict(row.payload or {})
                payload.setdefault("fetched_at", row.fetched_at.replace(tzinfo=timezone.utc).isoformat())
                if row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                    payload["expired"] = True
                return payload

        return await asyncio.to_thread(_read)

    async def put(self, key: str, provider: str, url: str, payload: dict[str, Any], ttl: timedelta) -> None:
        def _write() -> None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self._sf() as s:
                row = s.get(HttpCacheEntry, key)
                if row is None:
                    s.add(
                        HttpCacheEntry(
                            key=key,
                            provider=provider,
                            request_url=url[:1000],
                            payload=payload,
                            fetched_at=now,
                            expires_at=now + ttl,
                        )
                    )
                else:
                    row.payload = payload
                    row.fetched_at = now
                    row.expires_at = now + ttl
                    row.request_url = url[:1000]

        await asyncio.to_thread(_write)


@dataclass
class SearchResponse:
    query: str
    providers: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    any_live: bool = False
    any_cached: bool = False
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": self.results,
            "providers": self.providers,
            "any_live": self.any_live,
            "any_cached": self.any_cached,
            "note": self.note,
            "count": len(self.results),
            "disclaimer": (
                "Relevance here means 'matches the topic words and is the kind of source we "
                "trust for that topic'. It is not a check that the content is correct or "
                "matches your syllabus — open the source before relying on it."
            ),
        }


class ResourceService:
    def __init__(self, settings: Settings | None = None, *, limiter: Any = None, session_factory: Any = None) -> None:
        self.settings = settings or get_settings()
        self.cache = DBCache(session_factory) if session_factory else None
        self.providers: dict[str, Provider] = {
            name: cls(self.settings, limiter=limiter) for name, cls in PROVIDERS.items()
        }

    def status(self) -> list[dict[str, Any]]:
        out = []
        for p in self.providers.values():
            d = p.status()
            d["cache_ttl_hours"] = p.default_ttl_hours
            out.append(d)
        return out

    async def aclose(self) -> None:
        for p in self.providers.values():
            client = getattr(p, "_client", None)
            if client is not None and not client.is_closed:
                await client.aclose()

    async def search(
        self,
        query: str,
        *,
        providers: Sequence[str] | None = None,
        limit_per_provider: int | None = None,
        topic_keywords: Sequence[str] = (),
    ) -> SearchResponse:
        wanted = [p for p in (providers or list(self.providers)) if p in self.providers]
        jobs = []
        for name in wanted:
            prov = self.providers[name]
            if not prov.enabled():
                jobs.append(
                    asyncio.ensure_future(
                        _resolved(
                            ProviderResult(
                                name,
                                False,
                                source="skipped",
                                note=f"{name} disabled"
                                + ("" if prov.enabled() else " (no key or ADAPT_{}_ENABLED=false)".format(name.upper())),
                            )
                        )
                    )
                )
                continue
            jobs.append(
                asyncio.ensure_future(
                    prov.search(query, limit=limit_per_provider or self.settings.resource_max_results, cache=self.cache)
                )
            )
        raw: list[ProviderResult] = await asyncio.gather(*jobs, return_exceptions=False)  # each provider traps its own errors

        all_res: list[Resource] = []
        for r in raw:
            all_res.extend(r.resources)
        ranked = rank(all_res, query=query, topic_keywords=topic_keywords)[
            : max(6, (limit_per_provider or self.settings.resource_max_results) * 2)
        ]
        # de-duplicate across providers by title-ish key
        seen: set[str] = set()
        deduped: list[Resource] = []
        for r in ranked:
            key = "".join(w.lower() for w in keywords(r.title, limit=6))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        providers_meta = [
            {"provider": r.provider, "ok": r.ok, "source": r.source, "note": r.note, "count": len(r.resources),
             "status_code": r.status_code, "latency_ms": r.latency_ms}
            for r in raw
        ]
        live = any(r.source == "live" and r.ok for r in raw)
        cached = any(r.source in ("cache", "stale-cache") and r.ok for r in raw)
        note = None
        if not deduped:
            note = (
                "No resources available right now. Search results are cached for "
                f"{self.settings.resource_cache_ttl_hours // 24} days, so this clears as soon as one provider is "
                "reachable — teaching and assessment continue to work without them."
            )
        return SearchResponse(
            query=query,
            providers=providers_meta,
            results=[r.as_dict() for r in deduped],
            any_live=live,
            any_cached=cached,
            note=note,
        )

    async def recommend_for_topic(
        self,
        *,
        title: str,
        note: str = "",
        level_hint: str = "",
        limit: int = 6,
    ) -> SearchResponse:
        """Query built from the topic's own vocabulary, not the learner's words.

        Keeping the query curriculum-side (never "what the student typed") means a
        resource search does not leak a learner's phrasing to four external hosts.
        """
        bits = [title, *keywords(note, limit=4)[:4]]
        if level_hint:
            bits.append(level_hint)
        query = " ".join(bits)[:180]
        resp = await self.search(query, limit_per_provider=max(2, limit // 2), topic_keywords=keywords(title))
        resp.results = resp.results[:limit]
        return resp

    async def persist(
        self,
        *,
        student_id: str | None,
        topic_id: str | None,
        response: SearchResponse,
        why: str,
    ) -> list[str]:
        """Store the shown cards so the journey view can cite what was recommended."""

        def _write() -> list[str]:
            ids: list[str] = []
            with self.cache._sf() as s:  # type: ignore[union-attr]
                for r in response.results:
                    existing = None
                    if r.get("provider") and r.get("external_id"):
                        existing = s.execute(
                            select(ResourceReference).where(
                                ResourceReference.provider == r["provider"],
                                ResourceReference.external_id == r["external_id"],
                            )
                        ).scalar_one_or_none()
                    row = existing or ResourceReference(
                        student_id=student_id,
                        topic_id=topic_id,
                        provider=str(r.get("provider")),
                        external_id=str(r.get("external_id")),
                    )
                    row.kind = str(r.get("kind"))
                    row.title = str(r.get("title"))[:1000]
                    row.authors = r.get("authors")
                    row.year = r.get("year")
                    row.url = str(r.get("url"))[:1000]
                    row.doi = r.get("doi")
                    row.snippet = (r.get("snippet") or "")[:1200] or None
                    row.relevance = float(r.get("relevance") or 0)
                    row.why = why[:600]
                    row.license = r.get("license")
                    row.flags = r.get("flags")
                    if existing is None:
                        s.add(row)
                    s.flush()
                    ids.append(str(row.id))
                s.commit()
            return ids

        if self.cache is None:
            return []
        return await asyncio.to_thread(_write)


async def _resolved(result: ProviderResult) -> ProviderResult:
    return result
