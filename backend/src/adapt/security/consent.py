"""The learner's sharing choice, and the two names it goes by.

Three identifiers point at one boolean:

``consent_share_with_llm``
    the column on ``Student`` and the field every *write* body uses. It names the provider
    class, which is what the schema and the migration call it, and what an operator
    reading ``llm_calls`` expects to see.
``consent_share_with_ai``
    the field every learner-facing *read* returns. §3 of the brief keeps the provider's
    identity out of the learner's screens, and "LLM" is a word from our architecture, not
    from a learner's.
``share_summary_with_ai``
    the key inside the onboarding response's ``consent`` object, phrased as what is shared
    (a summary) rather than as a permission.

The split is deliberate and it is also a trap: a new endpoint that reads the column and
returns it under the storage name leaks provider vocabulary into learner copy, and one that
writes the learner-facing name into a body silently does nothing, because the request model
has no such field -- which is the worse direction to fail in, since "I said no" and "the
field was ignored" look identical to the person who was ignored.

So the names are written down once, here, and the request bodies accept both spellings.
:func:`learner_view` is the only way a read path should put the choice on the wire, and
``tests/test_consent_field_naming.py`` walks the source to make sure nothing else does.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: The name in the database column and in every API body that *sets* the choice.
STORED_FIELD = "consent_share_with_llm"
#: The name a learner's own profile is answered under.
LEARNER_FIELD = "consent_share_with_ai"
#: The name inside the onboarding payload's ``consent`` object.
ONBOARDING_FIELD = "share_summary_with_ai"

#: Every spelling a client is allowed to use when writing the choice. The learner-facing name
#: is accepted on the way in so a client built against a documented read cannot quietly
#: no-op its own consent update.
WRITE_FIELDS: tuple[str, ...] = (STORED_FIELD, LEARNER_FIELD)


def learner_view(value: Any) -> dict[str, bool]:
    """The one key a learner-visible payload may carry for this choice.

    ``{"consent_share_with_ai": bool(...)}`` -- a dict rather than a bare bool so a route
    that spreads a profile cannot accidentally spread the storage name alongside it.
    """
    return {LEARNER_FIELD: bool(value)}


def onboarding_echo(value: Any) -> dict[str, bool]:
    """The onboarding response's line about the choice, in the onboarding phrasing."""
    return {ONBOARDING_FIELD: bool(value)}


def read_body(body: Mapping[str, Any] | None) -> bool | None:
    """The choice a request body is making, whichever spelling it used. ``None`` for "silent".

    Precedence is the storage name first: a body carrying both and disagreeing is a client
    bug, and resolving it the same way every time is better than resolving it differently
    in two places. A caller that treats ``None`` as "do not change anything" is what makes
    an omitted field mean *unchanged* rather than mean *enabled*.
    """
    if not body:
        return None
    for field in WRITE_FIELDS:
        if field in body and body[field] is not None:
            return bool(body[field])
    return None


def accepted_fields(*, exclude: Iterable[str] = ()) -> tuple[str, ...]:
    """Every name this module owns, for a test or a scrubber that needs the list."""
    skip = set(exclude)
    return tuple(n for n in (STORED_FIELD, LEARNER_FIELD, ONBOARDING_FIELD) if n not in skip)
