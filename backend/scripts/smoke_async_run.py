from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def wait_for_completion(base_url: str, run_id: str, timeout_seconds: int = 30) -> dict:
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        response = httpx.get(f"{base_url}/api/v1/test-runs/{run_id}", timeout=5.0)
        response.raise_for_status()
        payload = response.json()
        last = payload
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"run {run_id} did not finish within {timeout_seconds}s; last={last}")


def main() -> int:
    base_url = "http://127.0.0.1:8000"
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "async_demo_project"
    suffix = uuid4().hex[:8]

    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        health = client.get("/healthz")
        health.raise_for_status()

        project = client.post(
            "/api/v1/projects",
            json={"name": f"async-smoke-project-{suffix}", "local_path": str(root)},
        )
        project.raise_for_status()
        project_data = project.json()

        snapshot = client.post(f"/api/v1/projects/{project_data['id']}/snapshots", json={})
        snapshot.raise_for_status()
        snapshot_data = snapshot.json()

        strategies = client.get("/api/v1/strategy-versions")
        strategies.raise_for_status()
        direct = next(item for item in strategies.json() if item["id"] == "sv-direct-v1")

        plan = client.post(
            "/api/v1/test-plans",
            json={
                "project_id": project_data["id"],
                "name": f"async-smoke-plan-{suffix}",
                "target_scope": [],
                "goal": "测试 calc 模块",
                "budget": {"timeout_seconds": 60, "allow_reflection": False},
                "default_strategy_version_id": direct["id"],
            },
        )
        plan.raise_for_status()
        plan_data = plan.json()

        run = client.post(
            f"/api/v1/test-plans/{plan_data['id']}/runs",
            json={"snapshot_id": snapshot_data["id"]},
        )
        run.raise_for_status()
        run_data = run.json()
        print(f"queued run: {run_data['id']}")

        final_run = wait_for_completion(base_url, run_data["id"])
        events = client.get(f"/api/v1/test-runs/{run_data['id']}/events")
        events.raise_for_status()
        report = client.get(f"/api/v1/test-runs/{run_data['id']}/report")
        report.raise_for_status()

    summary = {
        "run_id": final_run["id"],
        "status": final_run["status"],
        "tool_call_count": final_run["tool_call_count"],
        "events": [event["event_type"] for event in events.json()],
        "report_metrics": report.json()["metrics"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if final_run["status"] == "completed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - smoke script
        print(f"smoke async run failed: {exc}", file=sys.stderr)
        raise
