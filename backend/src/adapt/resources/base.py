"""Shared plumbing for the free learning-resource APIs.

Every provider goes through here so the four guarantees the blueprint asks for hold
uniformly:

* **Cache first.** A keyed row in ``http_cache`` (default TTL 7 days) is served
  without touching the network, which is what keeps the free quotas intact across
  a repeated demo and lets search work with no internet at all.
* **Ask permission before spending quota.** A per-provider :class:`Limiter` bucket
  must grant the call; if it refuses, we serve stale cache instead of hammering.
* **Degrade, never die.** Network error / non-200 / unparseable -> ``ProviderResult(
  ok=False, source="stale-cache"|"none")`` plus a human note. The caller never sees
  an exception and the UI never shows a spinner that never ends.
* **Key-free by default.** Only YouTube needs a key; when it is absent the provider
  reports ``configured=False`` and is *skipped*, not faked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("adapt.resources")

WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
STOP = frozenset(
    "the a an of and or for with how what why is are was be does do did you your that this from into about".split()
)


def keywords(text: str, *, limit: int = 12) -> list[str]:
    out: list[str] = []
    for w in WORD.findall((text or "").lower()):
        if w not in STOP and w not in out:
            out.append(w)
    return out[:limit]


@dataclass
class Resource:
    provider: str
    external_id: str
    kind: str  # paper|preprint|book|video
    title: str
    url: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    snippet: str | None = None
    doi: str | None = None
    license: str | None = None
    duration_s: int | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    relevance: float = 0.0
    why: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["relevance"] = round(self.relevance, 3)
        return d

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.external_id}"


@dataclass
class ProviderResult:
    provider: str
    ok: bool
    resources: list[Resource] = field(default_factory=list)
    source: str = "live"  # live|cache|stale-cache|none|skipped|error
    note: str | None = None
    status_code: int | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "source": self.source,
            "note": self.note,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "count": len(self.resources),
            "results": [r.as_dict() for r in self.resources],
        }


def cache_key(provider: str, query: str, extra: dict[str, Any] | None = None) -> str:
    norm = re.sub(r"\s+", " ", (query or "").strip().lower())
    blob = f"{provider}|{norm}|{json.dumps(extra or {}, sort_keys=True, default=str)}"
    return hashlib.sha256(blob.encode()).hexdigest()[:64]


class Provider:
    """Base class: HTTP + cache + limiter + tolerant parsing."""

    name = "base"
    default_ttl_hours = 24 * 7

    def __init__(self, settings: Settings | None = None, *, limiter: Any = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.limiter = limiter
        self._client = client

    # -- capability ---------------------------------------------------- #

    def configured(self) -> bool:
        return True

    def enabled(self) -> bool:
        return getattr(self.settings, f"{self.name}_enabled", True) and self.configured()

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "enabled": self.enabled(),
            "configured": self.configured(),
            "purpose": self.__doc__.strip().splitlines()[0] if self.__doc__ else "",
            "auth": getattr(self, "auth_note", "none required"),
            "quota_note": getattr(self, "quota_note", ""),
        }

    async def http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.resource_timeout_s, connect=6.0),
                follow_redirects=True,
                headers={"user-agent": "ADAPT-learning-platform/0.1 (educational demo; contact: local)"},
            )
        return self._client

    # -- the one flow every provider uses ------------------------------ #

    async def guarded_get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache: "CachePort | None" = None,
        ckey: str | None = None,
        ttl_hours: float | None = None,
    ) -> tuple[dict[str, Any] | None, ProviderResult | None]:
        """Return ``(payload, failure_result)`` — exactly one of the two is None."""
        ttl = float(ttl_hours if ttl_hours is not None else self.default_ttl_hours)
        now = datetime.now(timezone.utc)
        if cache is not None and ckey:
            hit = await cache.get(ckey)
            if hit is not None:
                fresh = hit.get("fetched_at") and (now - _aware(hit["fetched_at"])) < timedelta(hours=ttl)
                if fresh:
                    return hit.get("payload"), None
                # expired: still usable if the network fails
                stale_payload = hit.get("payload")
            else:
                stale_payload = None
        else:
            stale_payload = None

        if self.limiter is not None:
            ok, retry = self.limiter.allow(self.name, "shared")
            if not ok:
                if stale_payload is not None:
                    return stale_payload, ProviderResult(
                        self.name, True, source="stale-cache",
                        note=f"local rate limit ({retry:.0f}s to next slot); serving cached results",
                    )
                return None, ProviderResult(
                    self.name, False, source="skipped",
                    note=f"rate limit reached; retry in {retry:.0f}s",
                )

        import time

        started = time.monotonic()
        try:
            client = await self.http()
            resp = await client.get(url, params=params, headers=headers or {})
        except Exception as exc:  # DNS, TLS, timeout, proxy, offline
            latency = int((time.monotonic() - started) * 1000)
            if stale_payload is not None:
                return stale_payload, ProviderResult(
                    self.name,
                    True,
                    source="stale-cache",
                    note=f"{type(exc).__name__} contacting {self.name}; using cached results",
                    latency_ms=latency,
                )
            return None, ProviderResult(
                self.name,
                False,
                source="none",
                note=f"unreachable ({type(exc).__name__}: {str(exc)[:90]}); the search will retry later",
                latency_ms=latency,
            )
        latency = int((time.monotonic() - started) * 1000)
        if resp.status_code in (429, 503):
            if stale_payload is not None:
                return stale_payload, ProviderResult(self.name, True, source="stale-cache", status_code=resp.status_code,
                                                       note=f"{self.name} is throttling us; serving cached results", latency_ms=latency)
            return None, ProviderResult(self.name, False, source="error", status_code=resp.status_code,
                                         note=f"{self.name} returned {resp.status_code} (quota/throttled); trying other sources", latency_ms=latency)
        if resp.status_code >= 400:
            return None, ProviderResult(self.name, False, source="error", status_code=resp.status_code,
                                         note=f"{self.name} returned HTTP {resp.status_code}", latency_ms=latency)
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            payload = {"_text": resp.text[:4000]}
        if cache is not None and ckey:
            await cache.put(ckey, self.name, url, {"payload": payload, "fetched_at": now.isoformat()}, timedelta(hours=ttl))
        return payload, ProviderResult(self.name, True, source="live", status_code=resp.status_code, latency_ms=latency)


def _aware(dt: datetime | str) -> datetime:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CachePort:
    """Interface implemented by the SQLAlchemy-backed cache (see service.py)."""

    async def get(self, key: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def put(self, key: str, provider: str, url: str, payload: dict[str, Any], ttl: timedelta) -> None:
        raise NotImplementedError


def rank(
    resources: Sequence[Resource],
    *,
    query: str,
    topic_keywords: Sequence[str] = (),
    prefer: Sequence[str] = ("video", "paper", "preprint", "book"),
) -> list[Resource]:
    """Filter for suitability, then rank. Retrieved != verified — so we say which.

    Signals used: exact keyword overlap in the title (strong), overlap in the
    snippet (weak), recency for papers, a minimum length for videos, and a small
    source prior. Nothing here claims a resource is *correct*; the UI shows the
    source and the flag list instead.
    """
    qterms = set(keywords(query)) | {k.lower() for k in topic_keywords}
    out: list[Resource] = []
    for r in resources:
        flags = dict(r.flags or {})
        title_terms = {w.lower() for w in WORD.findall(r.title or "")}
        overlap = len(qterms & title_terms)
        if r.kind == "video" and r.duration_s and r.duration_s < 45:
            flags["rejected"] = "under_45s"
            continue
        if r.kind in ("paper", "preprint") and r.year and r.year < 1990:
            flags["historical"] = True
        relevance = 0.0
        if qterms:
            relevance += min(0.6, 0.2 * overlap)
            snip_terms = {w.lower() for w in WORD.findall((r.snippet or ""))}
            relevance += min(0.25, 0.05 * len(qterms & snip_terms))
        if r.year:
            relevance += 0.12 if r.year >= datetime.now(timezone.utc).year - 4 else 0.05
        relevance += 0.1 if r.license and ("cc" in r.license.lower() or "open" in r.license.lower()) else 0.0
        relevance += 0.08 * (1 - prefer.index(r.kind) / max(1, len(prefer))) if r.kind in prefer else 0.0
        r.relevance = round(relevance, 4)
        r.flags = flags
        r.why = _why(r, overlap)
        out.append(r)
    return sorted(out, key=lambda r: (r.relevance, r.year or 0), reverse=True)


def _why(r: Resource, overlap: int) -> str:
    bits = [f"{r.kind} from {r.provider}"]
    if r.year:
        bits.append(str(r.year))
    if overlap:
        bits.append(f"{overlap} topic term(s) in the title")
    if r.flags.get("paywalled"):
        bits.append("publisher paywall may apply")
    if r.license:
        bits.append(f"licence {r.license}")
    return "; ".join(bits)
