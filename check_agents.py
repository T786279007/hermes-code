"""Health checker for Hermes Agent Cluster v2."""

from __future__ import annotations

import calendar
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class HealthChecker:
    """Checks system health: disk, tasks, running agents, database."""

    def __init__(self, registry: TaskRegistry) -> None:
        """Initialize health checker with shared registry.

        Args:
            registry: TaskRegistry instance.
        """
        self._registry = registry

    def check(self) -> dict:
        """Run all health checks and return a summary dict.

        Returns:
            Dict with keys: system, tasks, agents, database, stale, pr_status, needs_attention.
        """
        stale = self._check_stale()
        pr_status = self._check_pr_status()
        needs_attention = bool(stale.get("stale_tasks")) or pr_status.get("has_failed_ci", False)

        return {
            "system": self._check_disk(),
            "tasks": self._check_tasks(),
            "agents": self._check_agents(),
            "database": self._check_database(),
            "stale": stale,
            "pr_status": pr_status,
            "needs_attention": needs_attention,
        }

    def _check_disk(self) -> dict:
        """Check disk usage on the root filesystem.

        Returns:
            Dict with disk_total_gb, disk_used_gb, disk_percent.
        """
        usage = shutil.disk_usage(Path("/").resolve())
        return {
            "disk_total_gb": round(usage.total / (1024**3), 2),
            "disk_used_gb": round(usage.used / (1024**3), 2),
            "disk_percent": round(usage.used / usage.total * 100, 1),
        }

    def _check_tasks(self) -> dict:
        """Count tasks grouped by status.

        Returns:
            Dict mapping status names to counts.
        """
        counts: dict[str, int] = {}
        for status in ("pending", "running", "done", "failed", "retrying"):
            tasks = self._registry.list_tasks(status=status)
            counts[status] = len(tasks)
        logger.info("Task counts: %s", counts)
        return counts

    def _check_agents(self) -> dict:
        """Check running agents: PID alive and elapsed time.

        Returns:
            Dict keyed by task_id with pid, alive, elapsed_sec.
        """
        running = self._registry.list_tasks(status="running")
        result: dict[str, dict] = {}
        now = time.time()

        for task in running:
            pid = task.get("pid")
            started_at = task.get("started_at")
            alive = False
            elapsed = 0.0

            if pid is not None:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except OSError:
                    alive = False

            if started_at:
                try:
                    if isinstance(started_at, str):
                        # SQLite CURRENT_TIMESTAMP is UTC; parse as UTC (B2 fix)
                        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                        started_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                    else:
                        started_ts = float(started_at)
                    elapsed = round(now - started_ts, 1)
                except (ValueError, TypeError):
                    elapsed = -1.0

            result[task["id"]] = {
                "pid": pid,
                "alive": alive,
                "elapsed_sec": elapsed,
            }

        logger.info("Agent check: %d running tasks", len(result))
        return result

    def _check_database(self) -> dict:
        """Check database integrity and WAL checkpoint.

        Returns:
            Dict with integrity and wal_checkpoint results.
        """
        return self._registry.health_check()

    def _check_stale(self) -> dict:
        """Detect stale/timeout running tasks (reuses reconciler timeout logic).

        Returns:
            Dict with stale_tasks list.
        """
        from config import RECONCILER_TIMEOUT

        running = self._registry.list_tasks(status="running")
        now = time.time()
        stale_tasks: list[dict] = []

        for task in running:
            started_at = task.get("started_at")
            if not started_at:
                continue
            try:
                if isinstance(started_at, str):
                    dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                    started_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                else:
                    started_ts = float(started_at)
                elapsed = now - started_ts
                if elapsed > RECONCILER_TIMEOUT:
                    stale_tasks.append({
                        "task_id": task["id"],
                        "elapsed_sec": round(elapsed, 1),
                        "pid": task.get("pid"),
                    })
            except (ValueError, TypeError):
                pass

        logger.info("Stale check: %d stale tasks", len(stale_tasks))
        return {"stale_tasks": stale_tasks}

    def _check_pr_status(self) -> dict:
        """Check PR status using gh CLI.

        Returns:
            Dict with open_prs list and has_failed_ci flag.
        """
        import subprocess

        open_prs: list[dict] = []
        has_failed_ci = False
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--state", "open", "--json",
                 "number,title,statusCheckRollup,headRefName"],
                capture_output=True, timeout=15, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json as _json
                prs = _json.loads(result.stdout)
                for pr in prs:
                    checks = pr.get("statusCheckRollup") or []
                    failed = any(
                        c.get("status", "") in ("FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED")
                        for c in checks
                    )
                    if failed:
                        has_failed_ci = True
                    open_prs.append({
                        "number": pr["number"],
                        "title": pr["title"],
                        "branch": pr.get("headRefName"),
                        "ci_failed": failed,
                    })
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            logger.warning("PR status check failed: %s", e)

        logger.info("PR check: %d open PRs, CI failed=%s", len(open_prs), has_failed_ci)
        return {"open_prs": open_prs, "has_failed_ci": has_failed_ci}
