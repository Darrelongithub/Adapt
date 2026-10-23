"""Single-process LAN demo admission control for SQLite's single writer.

Queue asynchronously BEFORE FastAPI acquires request sessions or worker threads.
Holding many synchronous handlers in SQLite's busy wait can exhaust both the
connection pool and the worker pool needed to commit their yielded dependencies.
Pure ASGI is intentional: release only after response AND dependency cleanup.
Do not use multiple uvicorn workers; this lock is process-local, as are services.
"""

from __future__ import annotations

import asyncio


class DemoCapacityMiddleware:
    def __init__(self, app):
        self.app = app
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/"):
            async with self._lock:
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
