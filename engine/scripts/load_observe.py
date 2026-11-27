"""Load test for ``POST /v1/observe``: 400,000 events, measured.

METERING §1.1 prices an observe event at **3.5 µs of CPU (three BKT updates)**
and **262 bytes** on the wire. This script does not measure BKT updates — there
are none yet (increment 3 adds the models). What it measures is the part that
exists: validation, flags, quota, and a durable row per event. The comparison is
therefore reported with its scope attached, not as a pass/fail.

Two modes, because they answer different questions:

* ``--mode http`` — the real thing: a running server, keep-alive connections,
  several workers, batches of 500. Answers "what does a host see".
* ``--mode store`` — calls ``observe.service.ingest`` in-process, one
  transaction per batch, no HTTP and no server. Answers "what does our storage
  path cost per event", which is the number comparable to §1.1's per-event
  figure.

Both print p50/p95/p99 and the ratio against 3.5 µs/event, and neither rounds
in our favour.

Usage::

    python -m scripts.load_observe --base-url http://127.0.0.1:8000 \\
        --admin-key ak_test_… --events 400000 --workers 4

    python -m scripts.load_observe --mode store --database-url postgresql://… \\
        --tenant-id … --connection-id … --events 400000
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

#: METERING §1.1, per event.
BASELINE_US_PER_EVENT = 3.5
BASELINE_BYTES_PER_EVENT = 262


def _event(n: int, learner: str) -> dict[str, Any]:
    """One representative event, close to 262 bytes on the wire."""
    return {
        "learner_id": learner,
        "skill": "skill.fractions",
        "item_id": f"item-{n % 5000:05d}",
        "correct": n % 3 != 0,
        "ts": datetime.now(UTC).isoformat(),
        "seq": n,
        "session_id": f"sess-{n // 500:06d}",
        "latency_ms": 1200 + n % 800,
        "device": "web",
        "decision_id": f"dcn_load_{n:08d}",
    }


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if not ordered:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

    def at(fraction: float) -> float:
        index = min(round(fraction * (len(ordered) - 1)), len(ordered) - 1)
        return ordered[index]

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99), "max": ordered[-1]}


def _report(
    *,
    label: str,
    events: int,
    batches: int,
    seconds: float,
    latencies_ms: list[float],
    bytes_sent: int,
) -> dict[str, Any]:
    pct = _percentiles(latencies_ms)
    us_per_event = (seconds * 1e6) / events if events else 0.0
    ratio = us_per_event / BASELINE_US_PER_EVENT if BASELINE_US_PER_EVENT else 0.0
    result = {
        "mode": label,
        "events": events,
        "batches": batches,
        "seconds": round(seconds, 3),
        "throughput_events_per_s": round(events / seconds, 1) if seconds else 0.0,
        "batch_latency_ms": {k: round(v, 2) for k, v in pct.items()},
        "us_per_event": round(us_per_event, 2),
        "baseline_us_per_event": BASELINE_US_PER_EVENT,
        "ratio_vs_baseline": round(ratio, 2),
        "bytes_per_event": round(bytes_sent / events, 1) if events else 0.0,
        "baseline_bytes_per_event": BASELINE_BYTES_PER_EVENT,
    }
    print(json.dumps(result, indent=2))
    if ratio > 2.0:
        print(
            f"NOTE: {us_per_event:.2f} µs/event is {ratio:.1f}x the §1.1 baseline of "
            f"{BASELINE_US_PER_EVENT} µs — more than the 2x the review asked to flag. "
            "The baseline prices three BKT updates and no durable write; this measures "
            "validation, flags, quota and a committed row per event.",
            file=sys.stderr,
        )
    return result


# --- http mode ------------------------------------------------------------


def _http_mode(args: argparse.Namespace) -> int:
    import httpx

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.admin_key}"}
    with httpx.Client(base_url=base, timeout=120.0) as client:
        created = client.post(
            "/v1/admin/connections",
            json={
                "host_name": f"load-{uuid.uuid4().hex[:8]}",
                "environment": "test",
                "event_quota_monthly": max(args.events * 2, 1_000_000),
                "scopes": ["observe"],
            },
            headers=headers,
        )
        created.raise_for_status()
        connection = created.json()
        key = connection["key"]["full_key"]

    per_batch = args.batch
    total_batches = args.events // per_batch
    latencies: list[float] = []
    errors: list[str] = []
    sent = 0
    lock = threading.Lock()
    counter = {"next": 0, "bytes": 0}

    def worker(worker_id: int) -> None:
        nonlocal sent
        local_latencies: list[float] = []
        local_bytes = 0
        with httpx.Client(base_url=base, timeout=120.0) as client:
            while True:
                with lock:
                    if counter["next"] >= total_batches:
                        break
                    batch_index = counter["next"]
                    counter["next"] += 1
                start_n = batch_index * per_batch
                events = [
                    _event(start_n + n, f"learn-load-{worker_id:02d}") for n in range(per_batch)
                ]
                body = json.dumps({"events": events}).encode()
                started = time.perf_counter()
                response = client.post(
                    "/v1/observe",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": f"load-{batch_index:08d}-{uuid.uuid4().hex[:8]}",
                    },
                )
                local_latencies.append((time.perf_counter() - started) * 1000)
                if response.status_code != 202:
                    errors.append(f"{response.status_code}: {response.text[:200]}")
                    break
                local_bytes += len(body)
        with lock:
            latencies.extend(local_latencies)
            counter["bytes"] += local_bytes
            sent += len(local_latencies) * per_batch

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(args.workers)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started

    if errors:
        print("FAILED: " + errors[0], file=sys.stderr)
        return 1
    if sent < args.events:
        print(f"only {sent} events were accepted", file=sys.stderr)
    _report(
        label="http",
        events=sent,
        batches=len(latencies),
        seconds=elapsed,
        latencies_ms=latencies,
        bytes_sent=counter["bytes"],
    )
    return 0


# --- store mode -----------------------------------------------------------


def _store_mode(args: argparse.Namespace) -> int:
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
    from sqlalchemy import create_engine

    from adapt_engine.db.tenant import tenant_transaction
    from adapt_engine.observe import service
    from adapt_engine.observe.schema import parse_event

    engine = create_engine(args.database_url, future=True)
    tenant_id = uuid.UUID(args.tenant_id)
    connection_id = uuid.UUID(args.connection_id)
    per_batch = args.batch
    total_batches = args.events // per_batch
    now = datetime.now(UTC)

    parsed_batches = []
    for batch_index in range(total_batches):
        start_n = batch_index * per_batch
        raw = [_event(start_n + n, f"learn-store-{n % 8:02d}") for n in range(per_batch)]
        parsed_batches.append([parse_event(item, index=i, now=now) for i, item in enumerate(raw)])
    sample = json.dumps({"events": [_event(0, "learn-store-00")]}).encode()
    payload_bytes = len(sample) * per_batch

    latencies: list[float] = []
    started = time.perf_counter()
    for batch_index, events in enumerate(parsed_batches):
        began = time.perf_counter()
        with tenant_transaction(engine, tenant_id) as conn:
            service.ingest(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                idempotency_key=f"load-store-{batch_index:08d}",
                body_hash=b"load-test" + batch_index.to_bytes(8, "big"),
                events=events,
                now=now,
            )
        latencies.append((time.perf_counter() - began) * 1000)
    elapsed = time.perf_counter() - started
    engine.dispose()

    _report(
        label="store",
        events=total_batches * per_batch,
        batches=total_batches,
        seconds=elapsed,
        latencies_ms=latencies,
        bytes_sent=payload_bytes * total_batches,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("http", "store"), default="http")
    parser.add_argument("--events", type=int, default=400_000)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-key", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--connection-id", default=None)
    args = parser.parse_args(argv)

    if args.events % args.batch:
        parser.error("--events must be a multiple of --batch")
    if args.mode == "http" and not args.admin_key:
        parser.error("--mode http needs --admin-key")
    if args.mode == "store" and not (args.database_url and args.tenant_id and args.connection_id):
        parser.error("--mode store needs --database-url, --tenant-id and --connection-id")
    runner: Callable[[argparse.Namespace], int] = _http_mode if args.mode == "http" else _store_mode
    return runner(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
