"""Command queue — send commands to running tasks via web UI."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_registry import TaskRegistry

logger = logging.getLogger(__name__)

# Commands that can be sent to running tasks
VALID_COMMANDS = {"cancel", "inject", "priority", "retry", "pause", "resume"}


class CommandQueue:
    """Queue for sending commands to running tasks.

    Supports commands like cancel, inject (send new instructions),
    and priority changes from the web UI.
    """

    def __init__(self, registry: TaskRegistry):
        """Initialize with shared registry.

        Args:
            registry: TaskRegistry instance (shares DB connection).
        """
        self._registry = registry

    def enqueue(
        self,
        task_id: str,
        command: str,
        payload: dict | None = None,
    ) -> int:
        """Enqueue a command for a task.

        Args:
            task_id: Target task identifier.
            command: Command type (must be in VALID_COMMANDS).
            payload: Optional command payload.

        Returns:
            Command queue entry ID.

        Raises:
            ValueError: If command is invalid.
        """
        if command not in VALID_COMMANDS:
            raise ValueError(
                f"Invalid command '{command}'. Must be one of: {', '.join(sorted(VALID_COMMANDS))}"
            )

        payload_json = json.dumps(payload, ensure_ascii=False, default=str) if payload else None
        with self._registry._transaction() as conn:
            # Verify task exists
            task = conn.execute("SELECT id, status FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if not task:
                raise ValueError(f"Task '{task_id}' not found")

            cursor = conn.execute(
                """
                INSERT INTO command_queue (task_id, command, payload)
                VALUES (?, ?, ?);
                """,
                (task_id, command, payload_json),
            )

            # Increment cmd_count on task
            conn.execute(
                "UPDATE tasks SET cmd_count = COALESCE(cmd_count, 0) + 1 WHERE id = ?;",
                (task_id,),
            )

            cmd_id = cursor.lastrowid
            logger.info("Command #%d enqueued: task=%s cmd=%s", cmd_id, task_id, command)
            return cmd_id

    def consume(self, task_id: str) -> list[dict]:
        """Consume pending commands for a task (mark as delivered).

        Args:
            task_id: Task identifier.

        Returns:
            List of command dicts that were pending.
        """
        with self._registry._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM command_queue
                WHERE task_id = ? AND status = 'pending'
                ORDER BY id ASC;
                """,
                (task_id,),
            ).fetchall()

            if rows:
                conn.execute(
                    """
                    UPDATE command_queue
                    SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND status = 'pending';
                    """,
                    (task_id,),
                )

        commands = [dict(r) for r in rows]
        if commands:
            logger.info("Consumed %d commands for task %s", len(commands), task_id)
        return commands

    def mark_executed(self, cmd_id: int, result: str | None = None) -> bool:
        """Mark a command as executed.

        Args:
            cmd_id: Command ID.
            result: Optional result message.

        Returns:
            True if updated.
        """
        with self._registry._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE command_queue
                SET status = 'executed', executed_at = CURRENT_TIMESTAMP, result = ?
                WHERE id = ? AND status = 'delivered';
                """,
                (result, cmd_id),
            )
            return cursor.rowcount > 0

    def list_commands(
        self,
        task_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List commands for a task.

        Args:
            task_id: Task identifier.
            status: Filter by status.
            limit: Maximum entries.

        Returns:
            List of command dicts.
        """
        with self._registry._connect() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT * FROM command_queue
                    WHERE task_id = ? AND status = ?
                    ORDER BY created_at DESC LIMIT ?;
                    """,
                    (task_id, status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM command_queue
                    WHERE task_id = ?
                    ORDER BY created_at DESC LIMIT ?;
                    """,
                    (task_id, limit),
                ).fetchall()

        return [dict(r) for r in rows]

    def has_pending(self, task_id: str) -> bool:
        """Check if a task has pending commands.

        Args:
            task_id: Task identifier.

        Returns:
            True if pending commands exist.
        """
        with self._registry._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM command_queue WHERE task_id = ? AND status = 'pending';",
                (task_id,),
            ).fetchone()
            return row["cnt"] > 0 if row else False

    def expire_old(self, max_age_hours: int = 24) -> int:
        """Expire old pending commands.

        Args:
            max_age_hours: Maximum age in hours.

        Returns:
            Number of expired commands.
        """
        with self._registry._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE command_queue
                SET status = 'expired'
                WHERE status = 'pending'
                AND created_at < datetime('now', ? || ' hours');
                """,
                (f"-{max_age_hours}",),
            )
            count = cursor.rowcount
            if count:
                logger.info("Expired %d old commands", count)
            return count
