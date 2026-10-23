# Round 6 crash log

Crawl of 14 routes × 2 viewports × 3 sessions (signed-out, guest, signed-in), plus the rapid tab-switch test.
Generated 153.0.8010.0 · 2026-10-22T17:41:31.373Z

**4 crash(es) found:**

| session | viewport | route | kind | detail |
| --- | --- | --- | --- | --- |
| signed-out | desktop | / | gate-missing | no account gate on protected route |
| signed-out | desktop | /journey | gate-missing | no account gate on protected route |
| signed-out | mobile | / | gate-missing | no account gate on protected route |
| signed-out | mobile | /journey | gate-missing | no account gate on protected route |

## SessionGate / redirect loop

Fixed in `components/chrome.tsx`: the landing gate that rendered “Opening your path…” over every
screen until the URL reached /journey is gone. If the server says who this browser is, the children
render; navigation is navigation's job. The crawl asserts /journey renders without bouncing.

## Rapid tab switch

10 routes × 3 laps at <300ms apart: passed (no freeze, no loop, no error screen).
