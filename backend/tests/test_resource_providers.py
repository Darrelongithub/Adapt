"""The four resource APIs, exercised across the failure matrix the brief asks for.

Why this file exists: the adapters under ``adapt/resources`` are the only part of the
app that talks to a third party we do not control, and nothing tested them. The route
calling them had a real 500 (``search(limit=...)``) that survived the whole build
because no test ever executed ``ResourceService.search``.

What is real here and what is not:

* the provider classes, HTTP client, parameter building, JSON/Atom parsing, cache
  writes, ranking, rate-limit gate and every degrade path are the shipping code;
* only the *transport* is substituted, with ``httpx.MockTransport`` -- the documented
  way to test an httpx client -- so no test here touches the internet;
* the payloads are trimmed but shape-faithful copies of what these four APIs return.

That split matters for the report: it proves the app handles valid / empty /
unavailable / rejected-key / timeout / throttled answers correctly, and it does **not**
prove the live hosts answer, because this sandbox has no route to them. The live
check is the one the CLI demo prints ("unreachable ..."), and it is stated as such.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from adapt.config import Settings
from adapt.resources.base import ProviderResult, cache_key
from adapt.resources.providers import ArXiv, OpenAlex, OpenLibrary, YouTube
from adapt.resources.service import ResourceService

# --------------------------------------------------------------------------- #
# shape-faithful fixtures (trimmed to the fields the adapters read)
# --------------------------------------------------------------------------- #

OPENALEX_BODY = {
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1000/xyz123",
            "display_name": "Adding fractions: a review of the part-whole model",
            "publication_year": 2019,
            "authorships": [{"author": {"display_name": "A. Researcher"}}],
            "primary_location": {
                "landing_page_url": "https://example.org/paper",
                "source": {"display_name": "Journal of Maths Education"},
            },
            "open_access": {"is_oa": True, "oa_url": "https://example.org/paper.pdf"},
            "cited_by_count": 42,
            "type": "article",
            "abstract_inverted_index": {"Fractions": [0], "compare": [1], "denominators": [2]},
        }
    ]
}

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Fractions,  the part-whole model,  and misconceptions</title>
    <summary>  A study of denominator misconceptions.  </summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>B. Preprinter</name></author>
    <link title="doi" href="https://doi.org/10.48550/arXiv.2401.00001"/>
  </entry>
</feed>
"""

OPENLIBRARY_BODY = {
    "docs": [
        {
            "key": "/works/OL12345W",
            "title": "Fractions for beginners",
            "author_name": ["C. Author"],
            "first_publish_year": 2005,
            "cover_i": 9876,
            "edition_count": 3,
            "subject": ["Fractions", "Arithmetic"],
            "ia": ["fractionsforbegi00auth"],
        }
    ]
}

YOUTUBE_BODY = {
    "items": [
        {
            "id": {"videoId": "abc123XYZ_0"},
            "snippet": {
                "title": "Adding fractions step by step",
                "channelTitle": "Maths Channel",
                "publishedAt": "2021-05-02T10:00:00Z",
                "description": "A worked walkthrough.",
                "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abc123XYZ_0/hq.jpg"}},
            },
        }
    ]
}


def _client(handler) -> httpx.AsyncClient:
    """A real httpx client whose transport is a scripted function."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _ok(body, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def _by_host(routes: dict[str, object]):
    """One client, one handler per host, so a whole fan-out can be scripted."""

    def handler(request: httpx.Request) -> httpx.Response:
        for host, route in routes.items():
            if host in request.url.host:
                if isinstance(route, httpx.Response):
                    return route
                if callable(route):
                    return route(request)
                raise AssertionError(f"unscripted route for {host}")
        return httpx.Response(404, json={"error": "no route scripted for " + request.url.host})

    return handler


def _settings(**over) -> Settings:
    base = {"resource_timeout_s": 1.0, "resource_max_results": 4}
    base.update(over)
    return Settings(**base)


# --------------------------------------------------------------------------- #
# valid answers
# --------------------------------------------------------------------------- #


def test_openalex_parses_a_paper_into_a_cited_resource():
    async def run() -> ProviderResult:
        p = OpenAlex(_settings())
        p._client = _client(_ok(OPENALEX_BODY))
        return await p.search("adding fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok and result.source == "live"
    assert len(result.resources) == 1
    r = result.resources[0]
    assert r.title.startswith("Adding fractions")
    assert r.doi == "10.1000/xyz123"
    assert r.year == 2019
    assert r.authors == ["A. Researcher"]
    assert r.flags["cited_by"] == 42 and r.flags["paywalled"] is False
    assert r.flags["pdf"] == "https://example.org/paper.pdf"


def test_arxiv_parses_atom_xml_and_marks_the_preprint():
    async def run() -> ProviderResult:
        p = ArXiv(_settings())
        p._client = _client(lambda request: httpx.Response(200, text=ARXIV_XML))
        return await p.search("fractions misconceptions", limit=4)

    result = asyncio.run(run())
    assert result.ok
    assert len(result.resources) == 1
    r = result.resources[0]
    assert r.kind == "preprint"
    assert r.title == "Fractions, the part-whole model, and misconceptions", "whitespace is collapsed"
    assert r.year == 2024 and r.authors == ["B. Preprinter"]
    assert "not peer reviewed" in json.dumps(r.flags), "a preprint must say what it is"


def test_openlibrary_parses_a_book_with_its_lending_flags():
    async def run() -> ProviderResult:
        p = OpenLibrary(_settings())
        p._client = _client(_ok(OPENLIBRARY_BODY))
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    r = result.resources[0]
    assert r.kind == "book" and r.url == "https://openlibrary.org/works/OL12345W"
    assert r.flags["read_online"] is True and r.flags["lending_restricted"] is True
    assert r.flags["cover"] == "https://covers.openlibrary.org/b/id/9876-M.jpg"


def test_youtube_parses_a_video_and_gets_its_duration_from_the_cheap_endpoint():
    async def run() -> ProviderResult:
        p = YouTube(_settings(youtube_api_key="test-key"))
        p._client = _client(
            _by_host(
                {
                    "googleapis": _ok(YOUTUBE_BODY),
                }
            )
        )
        found = await p.search("adding fractions", limit=4)
        return found

    result = asyncio.run(run())
    r = result.resources[0]
    assert r.kind == "video" and r.url == "https://www.youtube.com/watch?v=abc123XYZ_0"
    assert r.authors == ["Maths Channel"] and r.year == 2021

    async def with_duration() -> dict[str, int]:
        p = YouTube(_settings(youtube_api_key="test-key"))
        p._client = _client(
            _by_host(
                {
                    "googleapis": _ok({"items": [{"id": "abc123XYZ_0", "contentDetails": {"duration": "PT3M20S"}}]}),
                }
            )
        )
        return await p.durations(["abc123XYZ_0"])

    assert asyncio.run(with_duration()) == {"abc123XYZ_0": 200}


# --------------------------------------------------------------------------- #
# empty, unavailable, refused, slow, throttled
# --------------------------------------------------------------------------- #


def test_an_empty_result_set_is_an_answer_not_a_failure():
    """Zero results from a healthy API is not the same as an API we could not reach."""

    async def run() -> ProviderResult:
        p = OpenAlex(_settings())
        p._client = _client(_ok({"results": []}))
        return await p.search("zzzz nothing at all", limit=4)

    result = asyncio.run(run())
    assert result.ok is True and result.source == "live" and result.resources == []


@pytest.mark.parametrize(
    "provider_cls",
    [OpenAlex, ArXiv, OpenLibrary],
)
def test_an_unreachable_host_degrades_with_a_note_and_never_raises(provider_cls):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nodename nor servname provided, or not known")

    async def run() -> ProviderResult:
        p = provider_cls(_settings())
        p._client = _client(boom)
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False and result.resources == []
    assert result.source == "none"
    assert "unreachable" in (result.note or ""), "the note must say what went wrong"
    assert "ConnectError" in (result.note or ""), "and which failure it was"


def test_a_timeout_degrades_the_same_way_as_a_refused_connection():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    async def run() -> ProviderResult:
        p = OpenLibrary(_settings())
        p._client = _client(slow)
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False and "ReadTimeout" in (result.note or "")


def test_an_invalid_key_is_reported_as_the_http_failure_it_is():
    """A rejected YouTube key must read as 403, not as 'no results found'."""

    async def run() -> ProviderResult:
        p = YouTube(_settings(youtube_api_key="obviously-wrong"))
        p._client = _client(
            _by_host(
                {
                    "googleapis": httpx.Response(
                        403, json={"error": {"code": 403, "message": "API key not valid. Please pass a valid API key."}}
                    ),
                }
            )
        )
        return await p.search("adding fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False
    assert result.source == "error" and result.status_code == 403
    assert "HTTP 403" in (result.note or "")
    assert "obviously-wrong" not in (result.note or ""), "the note must never echo the key"


def test_upstream_throttling_is_named_as_quota_not_as_an_empty_search():
    async def run() -> ProviderResult:
        p = OpenAlex(_settings())
        p._client = _client(_by_host({"openalex": httpx.Response(429, json={"error": "rate limited"})}))
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False and result.status_code == 429
    assert "quota/throttled" in (result.note or "")
    assert result.source == "error"


def test_a_throttled_provider_with_a_cached_answer_serves_the_cache_and_says_so():
    """The blueprint's promise: a broken provider narrows the search, it does not empty it."""
    from adapt.resources.base import Provider  # noqa: F401  (documents what is being exercised)

    class MemoryCache:
        def __init__(self) -> None:
            self.rows: dict[str, dict] = {}

        async def get(self, key: str):
            return self.rows.get(key)

        async def put(self, key, provider, url, payload, ttl):
            self.rows[key] = payload

    cache = MemoryCache()
    ckey = cache_key("openalex", "adding fractions", {"v": 1})
    cache.rows[ckey] = {
        "payload": OPENALEX_BODY,
        "fetched_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),  # stale
    }

    async def run() -> ProviderResult:
        p = OpenAlex(_settings())
        p._client = _client(_by_host({"openalex": httpx.Response(503, json={"error": "unavailable"})}))
        return await p.search("adding fractions", limit=4, cache=cache)

    result = asyncio.run(run())
    assert result.ok is True
    assert result.source == "stale-cache", "the caller can then tell the learner how old the results are"
    assert "throttling" in (result.note or "")
    assert len(result.resources) == 1


def test_a_local_rate_limit_is_respected_before_the_quota_is_spent():
    class RefusingLimiter:
        def allow(self, scope: str, key: str = "shared"):
            return False, 7.5

    async def run() -> ProviderResult:
        p = OpenAlex(_settings(), limiter=RefusingLimiter())
        p._client = _client(_ok(OPENALEX_BODY))
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False and result.source == "skipped"
    assert "retry in 8s" in (result.note or "") or "retry in 7s" in (result.note or ""), result.note


def test_youtube_without_a_key_is_skipped_and_never_faked():
    async def run() -> ProviderResult:
        p = YouTube(_settings())
        p._client = _client(_ok(YOUTUBE_BODY))
        return await p.search("fractions", limit=4)

    result = asyncio.run(run())
    assert result.ok is False and result.source == "skipped"
    assert "ADAPT_YOUTUBE_API_KEY" in (result.note or "")
    assert result.resources == [], "no key means no videos, not invented videos"


# --------------------------------------------------------------------------- #
# the service: fan-out, cache-first, and the two mixed cases
# --------------------------------------------------------------------------- #


def _service(handler) -> ResourceService:
    svc = ResourceService(_settings())
    client = _client(handler)
    for _name, provider in svc.providers.items():
        provider._client = client
    return svc


def test_a_search_where_one_provider_dies_still_answers_with_the_others():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "openalex" in host:
            return httpx.Response(200, json=OPENALEX_BODY)
        if "arxiv" in host:
            raise httpx.ConnectError("offline")
        if "openlibrary" in host:
            return httpx.Response(200, json=OPENLIBRARY_BODY)
        return httpx.Response(404, json={})

    async def run():
        svc = _service(handler)
        try:
            return await svc.search("adding fractions", limit_per_provider=4)
        finally:
            await svc.aclose()

    resp = asyncio.run(run())
    titles = [r["title"] for r in resp.results]
    assert any("Adding fractions" in t for t in titles), "the working providers still contribute"
    assert any("Fractions for beginners" in t for t in titles)
    by_name = {p["provider"]: p for p in resp.providers}
    assert by_name["arxiv"]["ok"] is False and "unreachable" in by_name["arxiv"]["note"]
    assert by_name["youtube"]["source"] == "skipped"
    assert resp.any_live is True
    assert resp.note is None, "a partial answer still carrying results needs no empty-state note"


def test_a_search_with_everything_down_still_answers_with_the_reason():
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async def run():
        svc = _service(dead)
        try:
            return await svc.search("adding fractions", limit_per_provider=4)
        finally:
            await svc.aclose()

    resp = asyncio.run(run())
    assert resp.results == []
    assert resp.any_live is False and resp.any_cached is False
    assert resp.note and "teaching and assessment continue to work without them" in resp.note
    assert all(p["ok"] is False for p in resp.providers)


def test_an_unknown_provider_name_is_dropped_rather_than_asked():
    async def run():
        svc = _service(_ok(OPENALEX_BODY))
        try:
            return await svc.search("fractions", providers=["openalex", "bing"], limit_per_provider=2)
        finally:
            await svc.aclose()

    resp = asyncio.run(run())
    assert [p["provider"] for p in resp.providers] == ["openalex"]


def test_the_same_search_twice_costs_one_network_call():
    """Cache-first: the second identical search must not leave the process."""
    from adapt.resources.service import DBCache

    calls = {"n": 0}
    seen: dict[str, dict] = {}

    class CountingCache:
        async def get(self, key: str):
            return seen.get(key)

        async def put(self, key, provider, url, payload, ttl):
            seen[key] = payload

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=OPENALEX_BODY)

    async def run():
        svc = _service(handler)
        svc.cache = CountingCache()
        try:
            first = await svc.search("adding fractions", providers=["openalex"], limit_per_provider=4)
            second = await svc.search("adding fractions", providers=["openalex"], limit_per_provider=4)
            return first, second
        finally:
            await svc.aclose()

    first, second = asyncio.run(run())
    assert calls["n"] == 1, "the second search was served without a second request"
    assert [r["title"] for r in first.results] == [r["title"] for r in second.results]
    assert DBCache is not None


def test_the_service_never_reads_a_key_from_the_environment_into_a_note():
    """A failure note goes to the learner; it must not carry the credential."""
    secret = "AIzaSyTHIS-IS-A-FAKE-KEY-0000000000000000"

    async def run():
        svc = _service(_by_host({"googleapis": httpx.Response(400, json={"error": "bad key " + secret})}))
        svc.settings.youtube_api_key = secret
        try:
            return await svc.search("fractions", providers=["youtube"], limit_per_provider=2)
        finally:
            await svc.aclose()

    resp = asyncio.run(run())
    assert secret not in json.dumps(resp.as_dict() if hasattr(resp, "as_dict") else resp.__dict__, default=str)
