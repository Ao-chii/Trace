from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def spawn(script_name: str, stdout_name: str, stderr_name: str) -> int:
    root = Path(__file__).resolve().parents[1]
    log_dir = root / ".tmp-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / stdout_name
    stderr_path = log_dir / stderr_name
    stdout_handle = stdout_path.open("ab")
    stderr_handle = stderr_path.open("ab")
    env = os.environ.copy()
    env.setdefault("TRACE_REDIS_URL", "redis://127.0.0.1:6379/0")
    process = subprocess.Popen(
        [sys.executable, str(root / "scripts" / script_name)],
        cwd=root,
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return process.pid


def main() -> None:
    api_pid = spawn("run_api.py", "api.out.log", "api.err.log")
    worker_pid = spawn("run_worker.py", "worker.out.log", "worker.err.log")
    print(f"api_pid={api_pid}")
    print(f"worker_pid={worker_pid}")
    print(f"log_dir={(Path(__file__).resolve().parents[1] / '.tmp-logs')}")


if __name__ == "__main__":
    main()
