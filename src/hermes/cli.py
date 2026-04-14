#!/usr/bin/env python3
"""CLI entry point for Hermes Agent Cluster v2."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# Ensure parent of hermes package is on sys.path
_hermes_root = Path(__file__).resolve().parent.parent  # ~/hermes
_pkg_parent = _hermes_root.parent  # ~/ (or /home/txs)
if str(_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_pkg_parent))

from hermes.config import DB_PATH
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.check_agents import HealthChecker

logger = logging.getLogger(__name__)

# ANSI color helpers
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"

STATUS_COLORS = {
    "pending": _YELLOW,
    "running": _CYAN,
    "done": _GREEN,
    "failed": _RED,
    "retrying": _MAGENTA,
}


def _color(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def cmd_status(args: argparse.Namespace) -> None:
    """List all tasks from the registry."""
    registry = TaskRegistry(DB_PATH)
    tasks = registry.list_tasks(limit=args.limit)

    if not tasks:
        print("No tasks found.")
        return

    use_color = _supports_color()
    header = f"{'ID':<40} {'AGENT':<12} {'STATUS':<10} {'CREATED':<20}"
    print(header)
    print("-" * 82)

    for t in tasks:
        tid = t["id"][:38]
        agent = t["agent"]
        status = t["status"]
        created = str(t.get("created_at", ""))[:19]

        if use_color:
            status_str = _color(status.ljust(10), STATUS_COLORS.get(status, ""))
        else:
            status_str = status.ljust(10)

        print(f"{tid:<40} {agent:<12} {status_str} {created:<20}")

    logger.info("Listed %d tasks", len(tasks))


def cmd_check(args: argparse.Namespace) -> None:
    """Run health checker and print results."""
    registry = TaskRegistry(DB_PATH)
    checker = HealthChecker(registry)
    result = checker.check()

    use_color = _supports_color()

    print("=== System ===")
    sys_info = result["system"]
    print(f"  Disk: {sys_info['disk_used_gb']}/{sys_info['disk_total_gb']} GB "
          f"({sys_info['disk_percent']}%)")

    print("\n=== Tasks ===")
    for status, count in result["tasks"].items():
        if count > 0:
            if use_color:
                status_str = _color(status, STATUS_COLORS.get(status, ""))
            else:
                status_str = status
            print(f"  {status_str}: {count}")

    print("\n=== Running Agents ===")
    agents = result["agents"]
    if agents:
        for tid, info in agents.items():
            if use_color:
                alive_str = _color("ALIVE", _GREEN) if info["alive"] else _color("DEAD", _RED)
            else:
                alive_str = "ALIVE" if info["alive"] else "DEAD"
            print(f"  {tid}: PID={info['pid']} {alive_str} ({info['elapsed_sec']}s)")
    else:
        print("  No running agents.")

    print("\n=== Database ===")
    db = result["database"]
    if use_color:
        integrity_str = _color(db["integrity"], _GREEN if db["integrity"] == "ok" else _RED)
    else:
        integrity_str = db["integrity"]
    print(f"  Integrity: {integrity_str}")
    wc = db["wal_checkpoint"]
    print(f"  WAL checkpoint: log={wc['log']} checkpointed={wc['checkpointed']}")


def cmd_reconcile(args: argparse.Namespace) -> None:
    """Run crash recovery and print results."""
    registry = TaskRegistry(DB_PATH)
    reconciler = Reconciler(registry)
    result = reconciler.reconcile()

    print(f"Fixed tasks: {len(result['fixed'])}")
    for tid in result["fixed"]:
        print(f"  - {tid}")

    print(f"Orphaned worktrees: {len(result['orphaned'])}")
    for wt in result["orphaned"]:
        print(f"  - {wt}")


def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a new task and execute it (blocking)."""
    registry = TaskRegistry(DB_PATH)
    router = TaskRouter()
    outbox = Outbox(registry)
    reconciler = Reconciler(registry)
    from executor import TaskExecutor
    executor = TaskExecutor(registry, router, outbox, reconciler)

    description = args.description

    print(f"Submitting and executing: {description}")
    logger.info("Submitting task: %s", description)

    task = executor.submit(description, override=args.agent)

    status_color = STATUS_COLORS.get(task["status"], "")
    if _supports_color():
        status_str = _color(task["status"], status_color)
    else:
        status_str = task["status"]

    print(f"\nTask {task['id']}: {status_str}")
    if task.get("failure_class"):
        print(f"  Failure: {task['failure_class']}")
    if task.get("stderr_tail"):
        print(f"  Error: {task['stderr_tail'][:200]}")
    logger.info("Task %s finished: %s", task["id"], task["status"])


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(prog="hermes", description="Hermes Agent Cluster v2")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="List all tasks")
    p_status.add_argument("--limit", type=int, default=50)

    sub.add_parser("check", help="Health check")

    sub.add_parser("reconcile", help="Crash recovery")

    p_submit = sub.add_parser("submit", help="Submit and execute a new task")
    p_submit.add_argument("description", help="Task description")
    p_submit.add_argument("--agent", choices=["claude-code", "codex"], default=None,
                          help="Override agent selection")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "status": cmd_status,
        "check": cmd_check,
        "reconcile": cmd_reconcile,
        "submit": cmd_submit,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
