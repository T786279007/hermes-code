"""Crash recovery — detect and fix orphaned tasks from previous runs."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from config import CLAUDE_TIMEOUT, CODEX_TIMEOUT, REPO_PATH, WORKTREE_BASE

if TYPE_CHECKING:
    from task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class Reconciler:
    """Detects and recovers from crashed/interrupted tasks."""

    def __init__(self, registry: TaskRegistry):
        """Initialize reconciler with shared registry.

        Args:
            registry: TaskRegistry instance.
        """
        self._registry = registry

    def reconcile(self) -> dict:
        """Scan running tasks and recover from crashes.

        Checks:
        1. PID liveness (os.kill(pid, 0))
        2. Worktree directory existence
        3. Branch existence in repo
        4. Timeout based on started_at (fix B2)

        Returns:
            Dict with 'fixed' (list of recovered task IDs) and
            'orphaned' (list of orphaned worktree paths).
        """
        fixed: list[str] = []
        orphaned: list[str] = []
        now = time.time()

        running_tasks = self._registry.list_tasks(status="running")
        logger.info("Reconciler: %d running tasks to check", len(running_tasks))

        for task in running_tasks:
            task_id = task["id"]
            worktree = task.get("worktree")
            branch = task.get("branch")
            pid = task.get("pid")
            started_at = task.get("started_at")

            is_dead = False
            reason = ""

            # Check PID liveness
            if pid is not None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    is_dead = True
                    reason = f"PID {pid} not found"
                except PermissionError:
                    # Process exists but we can't signal it
                    logger.warning("Cannot signal PID %d for task %s", pid, task_id)
                except OSError as e:
                    logger.warning("Error checking PID %d: %s", pid, e)

            # Check timeout via started_at
            if not is_dead and started_at:
                try:
                    if isinstance(started_at, str):
                        # SQLite CURRENT_TIMESTAMP is UTC; parse as UTC (B2 fix)
                        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                        started_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                    else:
                        started_ts = float(started_at)
                    elapsed = now - started_ts
                    # Reconciliation timeout must exceed max runner timeout (W1)
                    max_runner_timeout = max(CLAUDE_TIMEOUT, CODEX_TIMEOUT) * 2
                    if elapsed > max_runner_timeout:
                        is_dead = True
                        reason = f"Timed out ({elapsed:.0f}s > {max_runner_timeout}s)"
                        if pid is not None:
                            try:
                                os.kill(pid, 9)
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning("Error parsing started_at for task %s: %s", task_id, e)

            # Check worktree existence
            worktree_missing = False
            if worktree:
                if not Path(worktree).exists():
                    worktree_missing = True
                    if not is_dead:
                        is_dead = True
                        reason = "Worktree directory missing"

            # Check branch existence
            branch_missing = False
            if branch and not is_dead:
                try:
                    result = subprocess.run(
                        ["git", "rev-parse", "--verify", branch],
                        cwd=REPO_PATH,
                        capture_output=True,
                        timeout=10,
                    )
                    if result.returncode != 0:
                        branch_missing = True
                        if not is_dead:
                            is_dead = True
                            reason = f"Branch {branch} not found"
                except Exception as e:
                    logger.warning("Error checking branch %s: %s", branch, e)

            if is_dead:
                # B3 fix: use finish_task for atomic status+reason update
                try:
                    self._registry.finish_task(task_id, "failed", stderr_tail=reason)
                except Exception:
                    # Fallback: separate calls if finish_task not available
                    try:
                        self._registry.transition_status(task_id, "failed", "running")
                    except Exception:
                        self._registry.update_task(task_id, status="failed")
                    self._registry.update_task(task_id, stderr_tail=reason)
                fixed.append(task_id)
                logger.info("Reconciled task %s: %s", task_id, reason)

                # Clean up worktree immediately (W9 fix)
                if worktree:
                    self._cleanup_worktree(worktree)

            # Clean up orphaned worktrees
            if worktree and worktree_missing:
                orphaned.append(worktree)
                self._cleanup_worktree(worktree)

        # Also scan for orphaned worktrees not tracked by any task
        if WORKTREE_BASE.exists():
            for entry in WORKTREE_BASE.iterdir():
                if entry.is_dir():
                    tracked = any(
                        t.get("worktree") == str(entry)
                        for t in running_tasks
                    )
                    if not tracked:
                        orphaned.append(str(entry))
                        self._cleanup_worktree(str(entry))
                        logger.info("Orphaned worktree: %s", entry)

        logger.info("Reconciliation complete: fixed=%d orphaned=%d", len(fixed), len(orphaned))
        return {"fixed": fixed, "orphaned": orphaned}

    def _cleanup_worktree(self, worktree: str) -> None:
        """Remove a worktree directory.

        Args:
            worktree: Absolute path to the worktree.
        """
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                cwd=REPO_PATH,
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass
        try:
            from pathlib import Path
            import shutil
            p = Path(worktree)
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass
