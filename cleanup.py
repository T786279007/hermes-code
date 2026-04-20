#!/usr/bin/env python3
"""Cleanup utility for Hermes Agent Cluster.

Cleans up old worktrees, completed tasks, logs, and zombie tasks.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import DB_PATH, LOG_DIR, WORKTREE_BASE

logger = logging.getLogger(__name__)


def cleanup_worktrees(max_age_hours: int = 24, dry_run: bool = False) -> list[dict[str, Any]]:
    """Remove worktree directories for done/failed tasks older than max_age_hours.

    Args:
        max_age_hours: Maximum age in hours for worktrees to keep.
        dry_run: If True, print what would be deleted without deleting.

    Returns:
        List of cleaned items with path, age_hours, and task_id.
    """
    cleaned: list[dict[str, Any]] = []

    worktree_base = Path(WORKTREE_BASE)
    db_path = Path(DB_PATH)

    if not worktree_base.exists():
        logger.info("Worktree base %s does not exist", worktree_base)
        return cleaned

    if not db_path.exists():
        logger.info("Database %s does not exist", db_path)
        return cleaned

    # Get tasks with done/failed status and their worktrees
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, worktree, status, updated_at
            FROM tasks
            WHERE status IN ('done', 'failed') AND worktree IS NOT NULL
            """
        )
        tasks = cursor.fetchall()
        conn.close()
    except Exception as e:
        logger.error("Error querying tasks: %s", e)
        return cleaned

    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

    for task in tasks:
        task_id = task["id"]
        worktree_path = Path(task["worktree"])
        updated_at_str = task["updated_at"]

        if not worktree_path.exists():
            continue

        # Parse updated_at
        try:
            if isinstance(updated_at_str, str):
                updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S")
            else:
                updated_at = datetime.fromisoformat(str(updated_at_str))
        except (ValueError, TypeError):
            logger.warning("Could not parse updated_at for task %s: %s", task_id, updated_at_str)
            continue

        if updated_at < cutoff_time:
            age_hours = (datetime.now() - updated_at).total_seconds() / 3600
            item = {
                "path": str(worktree_path),
                "age_hours": round(age_hours, 2),
                "task_id": task_id,
                "type": "worktree",
            }

            if dry_run:
                logger.info("DRY RUN: Would remove worktree %s (age: %.2fh, task: %s)", worktree_path, age_hours, task_id)
            else:
                try:
                    # Try git worktree remove first
                    try:
                        result = subprocess.run(
                            ["git", "worktree", "remove", "--force", str(worktree_path)],
                            capture_output=True,
                            timeout=15,
                            check=False,
                        )
                        # If git worktree remove failed, use shutil.rmtree as fallback
                        if result.returncode != 0:
                            if worktree_path.exists():
                                shutil.rmtree(worktree_path, ignore_errors=True)
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        # Fallback to shutil.rmtree on timeout or git not found
                        if worktree_path.exists():
                            shutil.rmtree(worktree_path, ignore_errors=True)
                    logger.info("Removed worktree %s (age: %.2fh, task: %s)", worktree_path, age_hours, task_id)
                except Exception as e:
                    logger.error("Failed to remove worktree %s: %s", worktree_path, e)
                    continue

            cleaned.append(item)

    return cleaned


def cleanup_old_tasks(max_age_days: int = 7, dry_run: bool = False) -> list[dict[str, Any]]:
    """Delete tasks with done/failed status older than max_age_days.

    Args:
        max_age_days: Maximum age in days for tasks to keep.
        dry_run: If True, print what would be deleted without deleting.

    Returns:
        List of cleaned items with task_id, status, and age_days.
    """
    cleaned: list[dict[str, Any]] = []

    db_path = Path(DB_PATH)

    if not db_path.exists():
        logger.info("Database %s does not exist", db_path)
        return cleaned

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find tasks to delete
        cursor.execute(
            """
            SELECT id, status, updated_at
            FROM tasks
            WHERE status IN ('done', 'failed')
              AND updated_at < datetime('now', '-' || ? || ' days')
            """,
            (max_age_days,),
        )
        tasks_to_delete = cursor.fetchall()

        for task in tasks_to_delete:
            task_id = task["id"]
            status = task["status"]
            updated_at_str = task["updated_at"]

            # Parse updated_at to calculate age
            try:
                if isinstance(updated_at_str, str):
                    updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S")
                else:
                    updated_at = datetime.fromisoformat(str(updated_at_str))
                age_days = (datetime.now() - updated_at).total_seconds() / 86400
            except (ValueError, TypeError):
                age_days = max_age_days

            item = {
                "task_id": task_id,
                "status": status,
                "age_days": round(age_days, 2),
                "type": "task",
            }

            if dry_run:
                logger.info("DRY RUN: Would delete task %s (status: %s, age: %.2fd)", task_id, status, age_days)
            else:
                cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                logger.info("Deleted task %s (status: %s, age: %.2fd)", task_id, status, age_days)

            cleaned.append(item)

        if not dry_run and cleaned:
            conn.commit()

        conn.close()
    except Exception as e:
        logger.error("Error cleaning old tasks: %s", e)

    return cleaned


def cleanup_old_logs(max_age_days: int = 30, dry_run: bool = False) -> list[dict[str, Any]]:
    """Remove .log files in LOG_DIR older than max_age_days.

    Args:
        max_age_days: Maximum age in days for log files to keep.
        dry_run: If True, print what would be deleted without deleting.

    Returns:
        List of cleaned items with path and size_bytes.
    """
    cleaned: list[dict[str, Any]] = []

    log_dir = Path(LOG_DIR)

    if not log_dir.exists():
        logger.info("Log directory %s does not exist", log_dir)
        return cleaned

    cutoff_time = datetime.now() - timedelta(days=max_age_days)

    for log_file in log_dir.glob("*.log"):
        if not log_file.is_file():
            continue

        # Get file modification time
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)

        if mtime < cutoff_time:
            age_days = (datetime.now() - mtime).total_seconds() / 86400
            size_bytes = log_file.stat().st_size
            item = {
                "path": str(log_file),
                "age_days": round(age_days, 2),
                "size_bytes": size_bytes,
                "type": "log",
            }

            if dry_run:
                logger.info("DRY RUN: Would remove log %s (age: %.2fd, size: %d bytes)", log_file, age_days, size_bytes)
            else:
                try:
                    log_file.unlink()
                    logger.info("Removed log %s (age: %.2fd, size: %d bytes)", log_file, age_days, size_bytes)
                except Exception as e:
                    logger.error("Failed to remove log %s: %s", log_file, e)
                    continue

            cleaned.append(item)

    return cleaned


def cleanup_zombie_tasks(dry_run: bool = False) -> list[dict[str, Any]]:
    """Find tasks with status='running' but PID not alive, mark as 'failed'.

    Args:
        dry_run: If True, print what would be updated without updating.

    Returns:
        List of cleaned items with task_id and pid.
    """
    cleaned: list[dict[str, Any]] = []

    db_path = Path(DB_PATH)

    if not db_path.exists():
        logger.info("Database %s does not exist", db_path)
        return cleaned

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, pid
            FROM tasks
            WHERE status = 'running' AND pid IS NOT NULL
            """
        )
        running_tasks = cursor.fetchall()

        for task in running_tasks:
            task_id = task["id"]
            pid = task["pid"]

            if pid is None:
                continue

            # Check if PID is alive
            is_alive = False
            try:
                os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
                is_alive = True
            except ProcessLookupError:
                is_alive = False
            except PermissionError:
                # Process exists but we can't signal it - consider it alive
                is_alive = True
            except OSError:
                is_alive = False

            if not is_alive:
                item = {
                    "task_id": task_id,
                    "pid": pid,
                    "type": "zombie",
                }

                if dry_run:
                    logger.info("DRY RUN: Would mark task %s (PID %d) as failed (zombie)", task_id, pid)
                else:
                    cursor.execute(
                        "UPDATE tasks SET status = 'failed', stderr_tail = ? WHERE id = ?",
                        (f"Zombie task: PID {pid} not found", task_id),
                    )
                    logger.info("Marked task %s (PID %d) as failed (zombie)", task_id, pid)

                cleaned.append(item)

        if not dry_run and cleaned:
            conn.commit()

        conn.close()
    except Exception as e:
        logger.error("Error cleaning zombie tasks: %s", e)

    return cleaned


def print_summary_table(all_cleaned: dict[str, list[dict[str, Any]]]) -> None:
    """Print a summary table of cleanup results.

    Args:
        all_cleaned: Dict mapping function names to their cleaned items lists.
    """
    total_items = sum(len(items) for items in all_cleaned.values())

    if total_items == 0:
        print("\nNo items to clean.")
        return

    print(f"\nCleanup Summary ({total_items} items total):")
    print("-" * 80)

    for func_name, items in all_cleaned.items():
        if not items:
            print(f"{func_name}: No items cleaned")
            continue

        print(f"\n{func_name}: {len(items)} items")

        for item in items:
            if item["type"] == "worktree":
                print(f"  - {item['path']}")
                print(f"    Task: {item['task_id']}, Age: {item['age_hours']}h")
            elif item["type"] == "task":
                print(f"  - Task {item['task_id']}")
                print(f"    Status: {item['status']}, Age: {item['age_days']}d")
            elif item["type"] == "log":
                print(f"  - {item['path']}")
                print(f"    Age: {item['age_days']}d, Size: {item['size_bytes']} bytes")
            elif item["type"] == "zombie":
                print(f"  - Task {item['task_id']}")
                print(f"    PID {item['pid']} not found, marked as failed")

    print("-" * 80)


def main() -> int:
    """Main entry point for cleanup CLI.

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    parser = argparse.ArgumentParser(
        description="Cleanup utility for Hermes Agent Cluster",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted but don't delete",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        help="Maximum age in days for tasks to keep (default: 7)",
    )
    parser.add_argument(
        "--worktree-age-hours",
        type=int,
        default=24,
        help="Maximum age in hours for worktrees to keep (default: 24)",
    )
    parser.add_argument(
        "--log-age-days",
        type=int,
        default=30,
        help="Maximum age in days for logs to keep (default: 30)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if args.dry_run:
        print("DRY RUN MODE - No actual deletions will be performed\n")

    # Run all cleanup functions
    all_cleaned: dict[str, list[dict[str, Any]]] = {}

    print("Running cleanup...")
    print(f"  Worktrees older than {args.worktree_age_hours}h")
    print(f"  Tasks older than {args.max_age_days}d")
    print(f"  Logs older than {args.log_age_days}d")
    print("  Zombie task detection")

    all_cleaned["cleanup_worktrees"] = cleanup_worktrees(args.worktree_age_hours, args.dry_run)
    all_cleaned["cleanup_old_tasks"] = cleanup_old_tasks(args.max_age_days, args.dry_run)
    all_cleaned["cleanup_old_logs"] = cleanup_old_logs(args.log_age_days, args.dry_run)
    all_cleaned["cleanup_zombie_tasks"] = cleanup_zombie_tasks(args.dry_run)

    # Print summary
    print_summary_table(all_cleaned)

    total_cleaned = sum(len(items) for items in all_cleaned.values())
    if args.dry_run:
        print(f"\nDRY RUN: Would clean {total_cleaned} items total")
    else:
        print(f"\nCleaned {total_cleaned} items total")

    return 0


if __name__ == "__main__":
    sys.exit(main())
