"""One way to swallow a model-path failure, so it stays readable.

This app is built so a lesson never 500s: every call to a model, a search provider, or the
accounting around them sits inside ``except Exception``. That is the right product decision
and the one that hides bugs, because "the provider was unreachable" and "we passed a string
where a mapping was expected" arrive at the same line and used to leave the same one-sentence
INFO log with the exception's class name in it -- which is not enough to find the line that
raised.

The distinction worth making is between an *unavailable dependency* and an *unexpected
shape*:

weather
    timeouts, connections, DNS, TLS, a refused socket. Expected, recoverable, and the
    fallback is the designed answer rather than a workaround. Logged at INFO.

bug signs
    ``TypeError``, ``AttributeError``, ``KeyError``, ``IndexError``, ``NameError``,
    ``AssertionError`` -- our code met something it had not planned for. A ValueError can be
    either, so a call site that knows which it is passes ``blame="provider"``. Logging these
    at INFO is how a real defect survives a release, so they get WARNING with a traceback.

:func:`note` is the whole policy in one line per call site, so no author has to re-decide
the level each time and no fallback path grows its own logging.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("adapt.ai.degrade")

#: Exceptions that mean "the thing we asked is not available right now".
WEATHER: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError, OSError)

#: Exceptions that mean "this app met a shape it did not expect". Not the network's fault.
BUG_SIGNS: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    NameError,
    AssertionError,
)


def _is_httpx_error(exc: BaseException) -> bool:
    try:
        import httpx  # local: this module is imported by the client that wraps httpx

        return isinstance(exc, httpx.HTTPError)
    except Exception:  # pragma: no cover - httpx is a hard dependency
        return False


def note(
    where: str,
    exc: BaseException | None,
    *,
    label: str = "",
    blame: str = "auto",
    detail: Any = None,
) -> str:
    """Log one swallowed failure at the level its cause deserves. Returns the reason string.

    ``label`` is the routing-table name of the model task, when there is one -- named
    ``label`` rather than ``purpose`` because this codebase reserves ``purpose`` for the
    keyword argument a call site passes to the client, and an AST test walks the source
    asserting every one of those is in the routing table. A log label is not that.

    ``where`` is the path that caught it ("retention check", "scene narration"); some callers
    put the returned sentence into a learner-visible ``note``, so it names the capability that
    was lost and nothing else -- no provider, no model, no exception text.
    """
    kind = type(exc).__name__ if exc is not None else "unknown"
    message = str(exc).strip() if exc is not None else ""
    what = f"{where} ({label})" if label else where
    ours = _looks_like_our_bug(exc, blame=blame)
    if ours:
        # Say so loudly, and keep the traceback: this is the branch where "we degrade
        # gracefully" is covering for a defect rather than for a bad network.
        log.warning("%s failed inside our own code -- %s: %s", what, kind, message[:200], exc_info=exc)
    else:
        log.info("%s skipped -- %s: %s", what, kind, message[:200])
    if detail is not None:
        return f"{where} skipped: {kind} ({str(detail)[:120]})"
    return f"{where} skipped: {kind}"


def _looks_like_our_bug(exc: BaseException | None, *, blame: str = "auto") -> bool:
    if exc is None:
        return True  # "we got nothing at all" is a question for us, not for the network
    if blame == "provider":
        return False
    if blame == "us":
        return True
    if isinstance(exc, BUG_SIGNS):
        return True
    if _is_httpx_error(exc) or isinstance(exc, WEATHER):
        return False
    # ValueError is the ambiguous one -- a validator raising it on a model's answer is the
    # provider's fault, and raising it on our own argument is not. Unknown causes stay at
    # INFO: an unexpected exception type is not evidence of a bug on its own, and a log full
    # of warnings nobody acts on is how the real ones get missed.
    return False
