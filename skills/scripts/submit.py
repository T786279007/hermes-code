#!/usr/bin/env python3
"""Submit a task to Hermes and optionally wait for completion.

Usage:
    python3 submit.py "Create a Python module that..." [--agent claude-code] [--model claude-sonnet-4-6] [--watch]
"""
import argparse
import json
import sys
import time
import logging

sys.path.insert(0, "/home/txs")

from hermes.executor import TaskExecutor
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.config import DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)


def main():
    parser = argparse.ArgumentParser(description="Submit a task to Hermes")
    parser.add_argument("description", help="Task description")
    parser.add_argument("--agent", default=None, help="Force agent (claude-code/codex)")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--watch", action="store_true", help="Wait for completion")
    parser.add_argument("--timeout", type=int, default=600, help="Max wait seconds (default: 600)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    registry = TaskRegistry(DB_PATH)
    router = TaskRouter()
    outbox = Outbox(registry)
    reconciler = Reconciler(registry)
    executor = TaskExecutor(registry, router, outbox, reconciler)

    print(f"Submitting task...", file=sys.stderr)
    task = executor.submit(args.description, override=args.agent)

    task_id = task["id"]
    status = task["status"]

    if not args.watch:
        result = {"id": task_id, "status": status}
        print(json.dumps(result, indent=2) if args.json else f"Task {task_id}: {status}")
        return

    # Watch mode
    print(f"Watching task {task_id}...", file=sys.stderr)
    start = time.time()
    while time.time() - start < args.timeout:
        task = registry.get_task(task_id)
        status = task["status"]
        if status in ("done", "failed"):
            break
        time.sleep(5)

    task = registry.get_task(task_id)
    elapsed = time.time() - start
    exit_code = task.get("exit_code", "N/A")
    result_tail = (task.get("result") or "")[:300]

    if args.json:
        print(json.dumps({
            "id": task_id,
            "status": status,
            "exit_code": exit_code,
            "elapsed_s": round(elapsed),
            "result": result_tail,
        }, indent=2))
    else:
        icon = "✅" if status == "done" else "❌"
        print(f"\n{icon} Task {task_id}")
        print(f"   Status:    {status}")
        print(f"   Exit code: {exit_code}")
        print(f"   Time:      {elapsed:.0f}s")
        print(f"   Result:    {result_tail}")

    sys.exit(0 if status == "done" else 1)


if __name__ == "__main__":
    main()
