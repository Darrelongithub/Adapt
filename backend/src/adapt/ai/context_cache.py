"""Caching the *static head* of a prompt, so it is paid for once instead of per call.

Every teaching prompt in this app has the same shape:

    [ instructions + rubric + subject catalogue ]  <- identical for every learner
    [ this learner's material, this item, this answer ]  <- different every time

The first part is the expensive one. ``AI2_SYSTEM`` plus the guardrails is a page of
prose, the teach-back judge adds the checklist contract, and the subject-placement
prompt carries the whole skill taxonomy with a sentence of meaning per skill. Sending
that again on every call is what turns "a tutor that answers a question" into a tokens
per minute problem -- and it is pure repetition, because none of it changed.

Three mechanisms, in order of how much they are relied on:

1. **Explicit context caching** (this module). For providers that support it, the static
   head is registered once with a TTL, and each call references it instead of resending
   it. That is what :class:`ContextCache` does, and everything about it is optional: a
   provider that refuses, a model below the minimum cacheable size, a deployment with
   caching switched off -- all of them end in the same place, a normal call with the
   full prompt, and a counter that says so.
2. **Implicit prefix caching.** Models that do this natively only benefit if the shared
   part is a genuine *prefix*. So the way prompts are built here is a contract, not a
   style: static head first, dynamic tail last, and :func:`assert_static` refuses a
   prefix that looks like it came from a learner.
3. **The in-process guard.** The cache is consulted by content hash, bounded in size and
   time, and a prefix that failed once is remembered as failed so a broken cache does
   not add a failed round-trip to every single call.

What must never happen, and is therefore enforced rather than intended:

* **No learner text in a cached head.** A cache entry is shared by every learner on the
  instance, so a prefix containing somebody's answer would be a data leak across
  learners dressed up as an optimisation. :func:`assert_static` rejects prefixes that
  carry the markers this app puts around learner text (``<student_text>``,
  ``<topic_name>``, ``<learner_text>``) and any call site that tries is refused in tests.
* **No answer depends on the cache being up.** Every failure path returns "no cache",
  never an exception and never a degraded answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

log = logging.getLogger("adapt.ai.cache")

#: Tag names this codebase wraps learner-written text in before a model sees it. A *pair*
#: of these tags with something between them is learner text, and a cache entry is shared
#: by every learner on the instance.
_LEARNER_TAGS = ("student_text", "topic_name", "learner_text", "learner_answer", "question")

#: Only a *populated* wrapper counts. An instruction may legitimately say "the learner's
#: text arrives between <student_text> tags" -- that sentence is about the protocol and
#: carries nothing -- while ``<student_text>…their answer…</student_text>`` carries
#: somebody's words. Matching the populated form is the difference between a check that
#: can be reasoned about and one the prompt text has to work around.
_WRAPPED_LEARNER_TEXT = re.compile(
    r"<(?P<tag>" + "|".join(_LEARNER_TAGS) + r")>(?:(?!</).)+</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


class StaticPrefixError(ValueError):
    """Raised when something that is not static tries to become a cache prefix."""


def assert_static(text: str) -> str:
    """Refuse a cache prefix that carries learner text. Returns the text when it is clean.

    Tests call this on every registered block, and :func:`ContextCache.key_for` calls it
    on every lookup, so a call site cannot opt out by forgetting.
    """
    body = str(text or "")
    match = _WRAPPED_LEARNER_TEXT.search(body)
    if match is not None:
        raise StaticPrefixError(
            f"a cache prefix may not contain learner text (<{match.group('tag')}> … </>); the "
            "cache is shared by every learner on this instance"
        )
    return body


def prefix_key(model: str, prefix: str) -> str:
    """A stable id for one (model, prefix) pair. Content-addressed, so it cannot drift.

    The model is part of the key because a cache is only valid for the model that made
    it, and the hash is of the *exact* text, so changing one word of the instructions
    produces a different entry rather than silently serving the old one.
    """
    digest = hashlib.sha256(f"{model}\x00{prefix}".encode()).hexdigest()
    return digest[:32]


@dataclass
class CacheEntry:
    """One registered static prefix, and what it has saved."""

    key: str
    model: str
    name: str | None  # provider-side handle, e.g. "cachedContents/abc123"
    tokens: int
    created_at: float
    expires_at: float
    uses: int = 0
    #: Input tokens the provider reported as served from the cache on the calls that used
    #: this entry. Kept per entry so the saving can be attributed to a prompt rather than
    #: to "the cache" in aggregate.
    reused_input_tokens: int = 0

    @property
    def live(self) -> bool:
        return bool(self.name) and time.monotonic() < self.expires_at


@dataclass
class CacheStats:
    """What the cache has done, in the only units that matter: avoided input tokens."""

    hits: int = 0
    misses: int = 0
    created: int = 0
    failed: int = 0
    evicted: int = 0
    skipped_small: int = 0
    skipped_disabled: int = 0
    reused_input_tokens: int = 0
    #: Prefixes the provider refused, by key, so they are not retried on every call.
    refused: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "created": self.created,
            "failed": self.failed,
            "evicted": self.evicted,
            "skipped_small": self.skipped_small,
            "skipped_disabled": self.skipped_disabled,
            "reused_input_tokens": self.reused_input_tokens,
            "refused_prefixes": len(self.refused),
        }


class ContextCache:
    """The book-keeping half: what to cache, when to give up, what it saved.

    The provider-specific half lives in :mod:`adapt.ai.llm` (``GeminiLLM``), because that
    is where the HTTP client is. This class is deliberately transport-free so its rules
    -- eligibility, TTL, bounded size, no learner text -- can be tested without a network
    and reused if the transport changes.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_s: int = 1800,
        min_tokens: int = 1024,
        max_tokens: int = 32_000,
        max_entries: int = 8,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl_s = max(0, int(ttl_s))
        self.min_tokens = max(1, int(min_tokens))
        self.max_tokens = max(self.min_tokens, int(max_tokens))
        self.max_entries = max(1, int(max_entries))
        self.entries: dict[str, CacheEntry] = {}
        self.stats = CacheStats()

    # -- eligibility ---------------------------------------------------- #

    def eligible(self, prefix: str) -> tuple[bool, str]:
        """Can this prefix be cached at all? Returns ``(ok, reason)``.

        The reasons are the honest ones: too small to be worth a round trip (and below
        the documented floor in most providers), too big to trust, not static, or the
        feature is switched off. ``reason`` is recorded, not shown to a learner.
        """
        if not self.enabled:
            self.stats.skipped_disabled += 1
            return False, "context caching is off on this deployment"
        try:
            assert_static(prefix)
        except StaticPrefixError as exc:
            self.stats.failed += 1
            return False, str(exc)
        from .llm import estimate_tokens  # local import: llm imports this module

        tokens = estimate_tokens([prefix])
        if tokens < self.min_tokens:
            self.stats.skipped_small += 1
            return False, f"prefix is {tokens} tokens, under the {self.min_tokens} floor"
        if tokens > self.max_tokens:
            self.stats.failed += 1
            return False, f"prefix is {tokens} tokens, over the {self.max_tokens} ceiling"
        return True, ""

    def key_for(self, model: str, prefix: str) -> str:
        return prefix_key(model, assert_static(prefix))

    def lookup(self, key: str) -> CacheEntry | None:
        """A live entry, or None. Evicts expired entries as it goes."""
        self._sweep()
        entry = self.entries.get(key)
        if entry is None or not entry.live:
            return None
        entry.uses += 1
        self.stats.hits += 1
        return entry

    def remember(self, key: str, *, model: str, name: str, tokens: int) -> CacheEntry:
        """Record a provider-side cache that was just created."""
        while len(self.entries) >= self.max_entries:
            self._evict_oldest()
        now = time.monotonic()
        entry = CacheEntry(
            key=key,
            model=model,
            name=name,
            tokens=int(tokens),
            created_at=now,
            expires_at=now + self.ttl_s if self.ttl_s else now,
        )
        self.entries[key] = entry
        self.stats.created += 1
        log.info(
            "static context registered: %d tokens reused across calls for the next %ds",
            entry.tokens,
            self.ttl_s,
        )
        return entry

    def note_reuse(self, key: str, tokens: int) -> None:
        """Attribute the provider's ``cachedContentTokenCount`` to the entry that earned it."""
        entry = self.entries.get(key)
        if entry is None or tokens <= 0:
            return
        entry.reused_input_tokens += int(tokens)
        self.stats.reused_input_tokens += int(tokens)

    def refuse(self, key: str, *, why: str) -> None:
        """Remember a prefix the provider would not cache, and stop asking.

        This is the difference between "caching is unavailable here" and "every call
        pays an extra failing round trip". One is a degradation; the other is a bug.
        """
        self.stats.failed += 1
        self.stats.refused.add(key)
        log.info("static context not cacheable here (%s); prompts are sent in full", why[:120])

    def refused(self, key: str) -> bool:
        return key in self.stats.refused

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self.entries.clear()
        else:
            self.entries.pop(key, None)

    # -- internals ------------------------------------------------------- #

    def _sweep(self) -> None:
        dead = [k for k, e in self.entries.items() if not e.live]
        for key in dead:
            self.entries.pop(key, None)
            self.stats.evicted += 1

    def _evict_oldest(self) -> None:
        if not self.entries:
            return
        oldest = min(self.entries.values(), key=lambda e: (e.uses, e.created_at))
        self.entries.pop(oldest.key, None)
        self.stats.evicted += 1

    def report(self) -> dict[str, Any]:
        """Operator-facing summary. Provider-neutral: no model names, no provider names."""
        self._sweep()
        return {
            "enabled": self.enabled,
            "entries": len(self.entries),
            "prefixes_held": sorted(e.key[:8] for e in self.entries.values()),
            "ttl_s": self.ttl_s,
            "floor": self.min_tokens,
            **self.stats.as_dict(),
        }


def json_prefix(payload: Mapping[str, Any]) -> str:
    """A canonical rendering of a static mapping, so two builds hash the same.

    Used where the static head is data rather than prose (a catalogue, a scale, a
    taxonomy): sorted keys and no whitespace means the key does not change because a
    dictionary was built in a different order in a different process.
    """
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "CacheEntry",
    "CacheStats",
    "ContextCache",
    "StaticPrefixError",
    "assert_static",
    "json_prefix",
    "prefix_key",
]
