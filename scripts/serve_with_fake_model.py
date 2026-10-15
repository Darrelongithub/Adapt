#!/usr/bin/env python3
"""Serve the real app with a scripted fake model, for browser integration runs.

Phase G's full-journey e2e drives the real application: real routes, real validators, real
fallbacks, real database. The only thing replaced is the provider client — the fake in
``backend/tests/fixtures/model/e2e_model.py`` answers Phase B's content purposes and Phase
C's lesson purposes with replies those validators accept. No network call is made and no
real credential exists; the environment key is an obvious placeholder so the app's
"model configured" paths run, and the secrecy layer treats it like any other key.

Run:
    backend/.venv/bin/python scripts/serve_with_fake_model.py

Environment:
    PORT             port to serve on (default 8000)
    ADAPT_DATA_DIR   data directory (use a fresh one for a clean journey)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND / "src"))

# The placeholder must look like nothing real; the canary tests treat any configured key
# as a secret to redact, which is exactly the behaviour under test.
os.environ.setdefault("ADAPT_GEMINI_API_KEY", "e2e-fake-key-not-a-real-key")
os.environ.setdefault("ADAPT_LOG_LEVEL", os.environ.get("ADAPT_LOG_LEVEL", "INFO"))

_spec = importlib.util.spec_from_file_location(
    "adapt_e2e_model", BACKEND / "tests" / "fixtures" / "model" / "e2e_model.py"
)
assert _spec is not None and _spec.loader is not None
_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2e)


class _FakeClient(_e2e.FakeAsyncLLM):  # type: ignore[misc, valid-type]
    """Same shape the services construct: GeminiLLM(settings, limiter=...)."""

    def __init__(self, settings=None, *, limiter=None, **_kwargs) -> None:
        super().__init__()

    def available(self) -> bool:
        return True


import adapt.services as services  # noqa: E402

services.GeminiLLM = _FakeClient  # type: ignore[assignment]

if __name__ == "__main__":
    import uvicorn

    print(
        "serving the real app with a SCRIPTED FAKE MODEL — browser integration runs only; "
        "no provider is contacted",
        flush=True,
    )
    uvicorn.run("adapt.api.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
