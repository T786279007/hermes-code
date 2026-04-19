"""Execution log storage — structured logs for task execution."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class ExecutionLog:
    """Structured execution log backed by SQLite.

    Provides append-only log storage with query and stream capabilities.
    """

    def __init__(self, registry: TaskRegistry):
        """Initialize with shared registry.

        Args:
            registry: TaskRegistry instance (shares DB connection).
        """
        self._registry = registry

    def append(
        self,
        task_id: str,
        message: str,
        level: str = "info",
        source: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Append a log entry.

        Args:
            task_id: Task identifier.
            message: Log message.
            level: Log level ('info', 'warn', 'error', 'debug').
            source: Source of the log ('agent', 'system', 'user').
            metadata: Optional dict of additional metadata.

        Returns:
            Log entry ID.
        """
        metadata_json = json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None
        with self._registry._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO execution_logs (task_id, level, source, message, metadata)
                VALUES (?, ?, ?, ?, ?);
                """,
                (task_id, level, source, message, metadata_json),
            )
            return cursor.lastrowid

    def list_logs(
        self,
        task_id: str,
        since_id: int | None = None,
        limit: int = 100,
        level: str | None = None,
    ) -> list[dict]:
        """List log entries for a task.

        Args:
            task_id: Task identifier.
            since_id: Only return entries after this ID (for polling).
            limit: Maximum entries to return.
            level: Filter by level.

        Returns:
            List of log entry dicts.
        """
        with self._registry._connect() as conn:
            if level and since_id:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_logs
                    WHERE task_id = ? AND level = ? AND id > ?
                    ORDER BY id ASC LIMIT ?;
                    """,
                    (task_id, level, since_id, limit),
                ).fetchall()
            elif since_id:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_logs
                    WHERE task_id = ? AND id > ?
                    ORDER BY id ASC LIMIT ?;
                    """,
                    (task_id, since_id, limit),
                ).fetchall()
            elif level:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_logs
                    WHERE task_id = ? AND level = ?
                    ORDER BY id ASC LIMIT ?;
                    """,
                    (task_id, level, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_logs
                    WHERE task_id = ?
                    ORDER BY id ASC LIMIT ?;
                    """,
                    (task_id, limit),
                ).fetchall()

        return [dict(r) for r in rows]

    def get_latest_id(self, task_id: str) -> int | None:
        """Get the latest log ID for a task (for SSE polling).

        Args:
            task_id: Task identifier.

        Returns:
            Latest log ID or None.
        """
        with self._registry._connect() as conn:
            row = conn.execute(
                "SELECT MAX(id) as max_id FROM execution_logs WHERE task_id = ?;",
                (task_id,),
            ).fetchone()
            return row["max_id"] if row else None

    def count_by_task(self, task_id: str) -> int:
        """Count total log entries for a task.

        Args:
            task_id: Task identifier.

        Returns:
            Log entry count.
        """
        with self._registry._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM execution_logs WHERE task_id = ?;",
                (task_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def delete_by_task(self, task_id: str) -> int:
        """Delete all log entries for a task.

        Args:
            task_id: Task identifier.

        Returns:
            Number of deleted entries.
        """
        with self._registry._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM execution_logs WHERE task_id = ?;",
                (task_id,),
            )
            return cursor.rowcount
