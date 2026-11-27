"""CONSOLE_STYLE.md §4's formatting rule, as a function — one copy, two surfaces.

The console and the CLI both turn API numbers into sentences, and until increment 9
each did it its own way: the console had one `formatNumber(…, 4)` doing everything,
and the CLI had `:.1f` for rates and `:.2f` for estimates typed inline. The console
was fixed and gated; the CLI was right by imitation. This module is the rule both
are now checked against, so "right by imitation" becomes "right by test".

Two precisions are *defined* rather than padded, and survive the check: a rate is
one decimal plus `%` (`0.0%`), and an interval is two decimals in brackets
(`[0.30, 0.65]`). Everything else is stripped before the trailing-zero check, which
leaves the sins visible: `0.5400` (a measurement padded to four) and `5.0x` (a
multiplier padded to one decimal).
"""

from __future__ import annotations

import re

#: A float with three or more decimals — `0.5400`, `0.3256`, `[0.0000, 0.1487]`.
TOO_PRECISE = re.compile(r"\d+\.\d{3,}")
#: An instant and a duration are not measurements: a clock may print microseconds
#: and `t+00.10s` may print a trailing zero, because both describe when something
#: happened rather than how much of it there was.
CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:\+\d{2}:\d{2}|Z)?")
SECONDS = re.compile(r"\d+\.\d+s\b")


def numeric_sins(text: str) -> list[str]:
    """Everything wrong with the numbers in one string, labelled."""
    sins: list[str] = []
    stripped = SECONDS.sub(" ", CLOCK.sub(" ", text))
    if TOO_PRECISE.search(stripped):
        sins.append(f"more than two decimals: {text!r}")
    bare = re.sub(r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]", " ", stripped)  # intervals: 2dp
    bare = re.sub(r"\d+(?:\.\d+)?\s*%", " ", bare)  # rates: 1dp
    bare = re.sub(r"\b\d+\.\d{2}\b", " ", bare)  # estimates and ratios: 2dp
    if re.search(r"\d+\.\d*0(?![\d])", bare):
        sins.append(f"trailing zero: {text!r}")
    return sins
