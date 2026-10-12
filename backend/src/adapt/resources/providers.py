"""The four resource integrations.

All four are free-to-use and reachable without an account except YouTube (which
needs a Data API v3 key and has a 10,000 unit/day quota, search costing 100).
Endpoints and response shapes below were written against the public API
documentation; :mod:`scripts.verify_resources` re-checks live field names against
the real services, and each parser is defensive about missing keys because an API
change must degrade one provider, never the whole feature.

Deliberate limits, so "we searched the literature" stays an honest claim:
* results are capped (``ADAPT_RESOURCE_MAX_RESULTS``, default 6 per provider);
* snippets are truncated;
* we never import full text, and we never present a snippet as verified content --
  every card carries its provider, licence and paywall flag.
"""

from __future__ import annotations

from typing import Any

from .base import Provider, ProviderResult, Resource, cache_key, keywords


class OpenAlex(Provider):
    """OpenAlex -- open scholarly graph (papers, authors, concepts). No key needed."""

    name = "openalex"
    auth_note = "optional ?mailto= for the polite pool"
    quota_note = "free: 100,000 credits/day, 10 req/s documented; we stay far below"
    BASE = "https://api.openalex.org/works"

    def configured(self) -> bool:
        return True

    async def search(
        self, query: str, *, limit: int | None = None, cache=None, filters: dict[str, Any] | None = None
    ) -> ProviderResult:
        n = limit or self.settings.resource_max_results
        params: dict[str, Any] = {
            "search": query,
            "per-page": min(25, max(3, n * 2)),
            "select": "id,doi,title,display_name,publication_year,authorships,primary_location,open_access,cited_by_count,type",
        }
        if self.settings.openalex_mailto:
            params["mailto"] = self.settings.openalex_mailto
        params.update(filters or {})
        payload, failure = await self.guarded_get(
            self.BASE,
            params=params,
            cache=cache,
            ckey=cache_key(self.name, query, {"v": 1, **(filters or {})}),
        )
        if payload is None:
            return failure or ProviderResult(self.name, False, source="none", note="no response")
        out: list[Resource] = []
        for w in (payload.get("results") or [])[: max(n * 2, n)]:
            oa = w.get("open_access") or {}
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            res = Resource(
                provider=self.name,
                external_id=str(w.get("id", "")).rsplit("/", 1)[-1] or w.get("doi", "")[:64],
                kind="paper",
                title=str(w.get("display_name") or w.get("title") or "(untitled)")[:400],
                url=str(w.get("doi") or loc.get("landing_page_url") or w.get("id") or "")[:400],
                authors=[
                    str((a.get("author") or {}).get("display_name", ""))
                    for a in (w.get("authorships") or [])[:6]
                    if (a.get("author") or {}).get("display_name")
                ],
                year=w.get("publication_year"),
                snippet=_abstract_snippet(w),
                doi=(str(w.get("doi")).replace("https://doi.org/", "") if w.get("doi") else None),
                license=("open access" if oa.get("is_oa") else None),
                flags={
                    "cited_by": w.get("cited_by_count"),
                    "type": w.get("type"),
                    "source": src.get("display_name"),
                    "paywalled": not bool(oa.get("is_oa")),
                    "pdf": oa.get("oa_url"),
                },
            )
            out.append(res)
        result = ProviderResult(self.name, True, resources=out, source=(failure.source if failure else "live"),
                                note=failure.note if failure else None,
                                status_code=failure.status_code if failure else 200)
        return result


class ArXiv(Provider):
    """arXiv API -- preprints (Atom XML parsed by us; no key, 3s courtesy spacing)."""

    name = "arxiv"
    auth_note = "none"
    quota_note = "free; documented courtesy limit ~1 request / 3 s"
    BASE = "http://export.arxiv.org/api/query"

    async def search(self, query: str, *, limit: int | None = None, cache=None) -> ProviderResult:
        n = limit or self.settings.resource_max_results
        params = {
            "search_query": _arxiv_query(query),
            "start": 0,
            "max_results": min(20, max(3, n * 2)),
            "sortBy": "relevance",
        }
        payload, failure = await self.guarded_get(
            self.BASE, params=params, cache=cache, ckey=cache_key(self.name, query, {"v": 1})
        )
        if payload is None:
            return failure or ProviderResult(self.name, False, source="none", note="no response")
        entries = _atom_entries(payload) if isinstance(payload, dict) else []
        out: list[Resource] = []
        for e in entries[: n * 2]:
            link = ""
            for l in e.get("link", []) if isinstance(e.get("link"), list) else []:
                if isinstance(l, dict) and l.get("title") == "doi":
                    link = l.get("href", "")
            out.append(
                Resource(
                    provider=self.name,
                    external_id=str(e.get("id", "")).rsplit("/", 1)[-1][:64],
                    kind="preprint",
                    title=_clean(e.get("title")),
                    url=str(e.get("id", ""))[:400] or link,
                    authors=[_clean(a.get("name")) for a in (e.get("author") or []) if isinstance(a, dict)][:6],
                    year=_year(e.get("published")),
                    snippet=_clean(e.get("summary"))[:600],
                    license="arXiv open access (per-article licence)",
                    flags={"note": "preprint: not peer reviewed"},
                )
            )
        return ProviderResult(self.name, True, resources=out, source=(failure.source if failure else "live"),
                              note=failure.note if failure else None)


class OpenLibrary(Provider):
    """Open Library -- books and covers, free JSON, no key."""

    name = "openlibrary"
    auth_note = "none"
    quota_note = "free public API; be gentle, we cache for 7 days"

    async def search(self, query: str, *, limit: int | None = None, cache=None) -> ProviderResult:
        n = limit or self.settings.resource_max_results
        params = {
            "q": query,
            "fields": "key,title,author_name,first_publish_year,cover_i,edition_count,subject,ia",
            "limit": min(20, max(3, n * 2)),
        }
        payload, failure = await self.guarded_get(
            "https://openlibrary.org/search.json",
            params=params,
            cache=cache,
            ckey=cache_key(self.name, query, {"v": 1}),
        )
        if payload is None:
            return failure or ProviderResult(self.name, False, source="none", note="no response")
        out: list[Resource] = []
        for doc in (payload.get("docs") or [])[: n * 2]:
            key = str(doc.get("key", ""))
            ia = (doc.get("ia") or [None])[0]
            out.append(
                Resource(
                    provider=self.name,
                    external_id=key.rsplit("/", 1)[-1] or key,
                    kind="book",
                    title=str(doc.get("title") or "(untitled)")[:300],
                    url=f"https://openlibrary.org{key}" if key else "https://openlibrary.org",
                    authors=[str(a) for a in (doc.get("author_name") or [])][:4],
                    year=doc.get("first_publish_year"),
                    snippet=(", ".join(str(s) for s in (doc.get("subject") or [])[:5]) or None),
                    license="library lending",
                    flags={
                        "cover": f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg" if doc.get("cover_i") else None,
                        "editions": doc.get("edition_count"),
                        "read_online": bool(ia),
                        "lending_restricted": True,
                    },
                )
            )
        return ProviderResult(self.name, True, resources=out, source=(failure.source if failure else "live"),
                              note=failure.note if failure else None)


class YouTube(Provider):
    """YouTube Data API v3 -- needs a free key; skipped entirely when absent."""

    name = "youtube"
    auth_note = "ADAPT_YOUTUBE_API_KEY required (free tier)"
    quota_note = "10,000 units/day; search=100 units, so ~100 searches/day -- cached aggressively"

    def configured(self) -> bool:
        return bool(self.settings.youtube_api_key)

    async def search(self, query: str, *, limit: int | None = None, cache=None) -> ProviderResult:
        if not self.configured():
            return ProviderResult(
                self.name,
                False,
                source="skipped",
                note="no ADAPT_YOUTUBE_API_KEY set: video results are skipped (OpenAlex/arXiv/Open Library still run)",
            )
        n = limit or self.settings.resource_max_results
        params = {
            "part": "snippet,contentDetails",
            "q": query,
            "type": "video",
            "videoDuration": "medium",
            "safeSearch": "strict",
            "maxResults": min(15, max(3, n * 2)),
            "key": self.settings.youtube_api_key,
        }
        payload, failure = await self.guarded_get(
            "https://www.googleapis.com/youtube/v3/search",
            params=params,
            cache=cache,
            ckey=cache_key(self.name, query, {"v": 1}),
        )
        if payload is None:
            return failure or ProviderResult(self.name, False, source="none", note="no response")
        out: list[Resource] = []
        for item in payload.get("items") or []:
            sn = item.get("snippet") or {}
            vid = ((item.get("id") or {}).get("videoId")) or ""
            if not vid:
                continue
            out.append(
                Resource(
                    provider=self.name,
                    external_id=vid,
                    kind="video",
                    title=str(sn.get("title") or "")[:300],
                    url=f"https://www.youtube.com/watch?v={vid}",
                    authors=[str(sn.get("channelTitle"))] if sn.get("channelTitle") else [],
                    year=_year(sn.get("publishedAt")),
                    snippet=str(sn.get("description") or "")[:400],
                    flags={
                        "thumbnail": (sn.get("thumbnails") or {}).get("high", {}).get("url"),
                        "official_channel": False,
                        "note": "duration/labels require the videos endpoint (1 unit), fetched on open",
                    },
                )
            )
        return ProviderResult(self.name, True, resources=out, source=(failure.source if failure else "live"),
                              note=failure.note if failure else None)

    async def durations(self, ids: list[str]) -> dict[str, int]:
        """Cheap (1 unit/video) follow-up so the 45-second filter actually bites."""
        if not self.configured() or not ids:
            return {}
        payload, failure = await self.guarded_get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "contentDetails,statistics", "id": ",".join(ids[:25]), "key": self.settings.youtube_api_key},
        )
        if payload is None:
            return {}
        out: dict[str, int] = {}
        for item in payload.get("items") or []:
            out[str(item.get("id"))] = _iso_duration_s(str((item.get("contentDetails") or {}).get("duration", "")))
        return out


# --------------------------------------------------------------------------- #
# parsing helpers (tolerant by design)
# --------------------------------------------------------------------------- #


def _arxiv_query(query: str) -> str:
    """arXiv's ``all:`` field treats spaces as OR, which drowns a short topic
    phrase in noise. Joining the first few keywords with AND is the documented
    way to narrow it; quotes are dropped because the field parser is inconsistent
    about them."""
    kw = keywords(query, limit=4)
    return f"all:({' AND '.join(kw)})" if kw else f"all:{query}"


def _abstract_snippet(work: dict[str, Any]) -> str | None:
    for field_name in ("abstract_inverted_index",):
        inv = work.get(field_name)
        if isinstance(inv, dict) and inv:
            positions: list[tuple[int, str]] = []
            for word, idxs in inv.items():
                for i in idxs[:1]:
                    positions.append((int(i), str(word)))
            positions.sort()
            return " ".join(w for _, w in positions[:110])[:600]
    return None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    import re as _re

    return _re.sub(r"\s+", " ", str(value)).strip()


def _year(value: Any) -> int | None:
    text = str(value or "")
    for i in range(len(text) - 3):
        if text[i : i + 4].isdigit() and 1900 <= int(text[i : i + 4]) <= 2100:
            return int(text[i : i + 4])
    return None


def _iso_duration_s(iso: str) -> int:
    import re as _re

    m = _re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _atom_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The arXiv Atom feed is fetched as text and parsed to dicts by ``_xml_to_dicts``.

    ``guarded_get`` cannot JSON-decode XML, so it stashes ``{"_text": ...}``; we
    convert here. Keeping the XML walk in one tiny function means a namespace change
    does not need a new dependency.
    """
    import xml.etree.ElementTree as ET

    text = payload.get("_text") if isinstance(payload, dict) else None
    if not text:
        return payload.get("entry", []) if isinstance(payload, dict) else []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    ns = "{http://www.w3.org/2005/Atom}"
    entries: list[dict[str, Any]] = []
    for e in root.findall(f"{ns}entry"):
        def _t(tag: str) -> str | None:
            node = e.find(f"{ns}{tag}")
            return node.text if node is not None else None

        links = [{"href": l.get("href"), "title": l.get("title"), "rel": l.get("rel")} for l in e.findall(f"{ns}link")]
        entries.append(
            {
                "id": _t("id"),
                "title": _t("title"),
                "summary": _t("summary"),
                "published": _t("published") or _t("updated"),
                "author": [{"name": (a.findtext(f"{ns}name") or "").strip()} for a in e.findall(f"{ns}author")],
                "link": links,
            }
        )
    return entries


PROVIDERS: dict[str, type[Provider]] = {
    "openalex": OpenAlex,
    "arxiv": ArXiv,
    "openlibrary": OpenLibrary,
    "youtube": YouTube,
}
