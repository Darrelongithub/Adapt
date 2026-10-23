#!/usr/bin/env python3
"""50 real HTTP clients, one shared IP, isolated cookies; no retries mask errors.

Run against a disposable server with ADAPT_DEMO_MODE=true. Writes learners to it.
Uses only public task data, not answer keys. Timings exclude human think time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter

import httpx


async def run(base_url: str, users: int) -> dict:
    latencies: list[float] = []
    statuses: Counter = Counter()
    errors: list[str] = []
    durations: list[float] = []
    ready = asyncio.Event()

    async def learner(index: int):
        async with httpx.AsyncClient(base_url=base_url, timeout=90) as client:
            await ready.wait()
            started = time.perf_counter()

            async def request(method: str, path: str, **kwargs):
                tick = time.perf_counter()
                response = await client.request(method, path, **kwargs)
                latencies.append(time.perf_counter() - tick)
                statuses[response.status_code] += 1
                if response.status_code != 200:
                    raise RuntimeError(
                        f"{method} {path}: {response.status_code} {response.text[:300]}"
                    )
                return response.json()

            try:
                session = await request(
                    "POST",
                    "/api/v1/demo/session",
                    json={
                        "consent_analytics": True,
                        "consent_share_with_llm": False,
                        "display_name": f"Load guest {index}",
                    },
                )
                client.headers["x-csrf-token"] = session["csrf_token"]
                view = await request("POST", "/api/v1/baseline")
                run_id = view["run_id"]
                games = set()
                for _ in range(20):
                    if view["kind"] == "snapshot":
                        break
                    assert view["kind"] == "task", view["kind"]
                    task = view["task"]
                    games.add(task["game"])
                    if task["game"] == "vault":
                        taps = task["sequence"]
                        answer = {
                            "taps": taps[::-1]
                            if task.get("answer_direction") == "backward"
                            else taps
                        }
                    else:
                        answer = {
                            "value": task["options"][index % len(task["options"])]
                        }
                    view = await request(
                        "POST",
                        f"/api/v1/baseline/{run_id}/trial",
                        json={
                            "task_id": task["id"],
                            "response": answer,
                            "latency_ms": 1200,
                            "wager": 2,
                        },
                    )
                assert view["kind"] == "snapshot", "flow did not end"
                assert games == {"quick", "vault", "trap"}, games
                snapshot = await request("POST", f"/api/v1/baseline/{run_id}/finish")
                assert snapshot["state"] == "sealed" and snapshot["factors"], snapshot
                fetched = await request(
                    "GET", f"/api/v1/baseline/snapshot?run_id={run_id}"
                )
                assert fetched["snapshot_id"] == snapshot["snapshot_id"]
                durations.append(time.perf_counter() - started)
            except Exception as exc:  # noqa: BLE001 - report every failed simulated user
                errors.append(f"user {index}: {type(exc).__name__}: {exc}")

    started = time.perf_counter()
    tasks = [asyncio.create_task(learner(i)) for i in range(users)]
    ready.set()
    await asyncio.gather(*tasks)

    def summary(values):
        if not values:
            return {}
        ordered = sorted(values)
        return {
            "min_s": round(ordered[0], 3),
            "median_s": round(ordered[len(ordered) // 2], 3),
            "p95_s": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
            "max_s": round(ordered[-1], 3),
        }

    return {
        "users": users,
        "completed": len(durations),
        "wall_s": round(time.perf_counter() - started, 3),
        "requests": len(latencies),
        "http_statuses": dict(statuses),
        "request_latency": summary(latencies),
        "flow_latency": summary(durations),
        "errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=50)
    args = parser.parse_args()
    result = asyncio.run(run(args.base_url, args.users))
    print(json.dumps(result, indent=2))
    raise SystemExit(
        0 if result["completed"] == args.users and not result["errors"] else 1
    )
