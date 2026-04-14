"""SQLite WAL task registry — single source of truth for all tasks."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    branch TEXT,
    worktree TEXT,
    prompt TEXT,
    result TEXT,
    model TEXT,
    exit_code INTEGER,
    stderr_tail TEXT,
    failure_class TEXT,
    attempt INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    pid INTEGER,
    done_checks_json TEXT
);
"""

_CREATE_OUTBOX_TABLE = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,
    external_id TEXT,
    payload TEXT,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    last_error TEXT,
    UNIQUE(task_id, action)
);
"""


class TaskRegistry:
    """Thread-safe SQLite task registry with WAL mode."""

    def __init__(self, db_path: str | Path):
        """Initialize registry, create DB directory and schema.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._lock = threading.Lock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.executescript(_CREATE_TASKS_TABLE)
            conn.executescript(_CREATE_OUTBOX_TABLE)
            conn.execute("PRAGMA journal_mode=WAL;")
            self._ensure_done_checks_column(conn)
        logger.info("TaskRegistry initialized at %s", self._db_path)

    def _connect(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL mode and Row factory.

        Returns:
            Configured sqlite3.Connection.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,
            timeout=10,
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_done_checks_column(self, conn: sqlite3.Connection) -> None:
        """Add done_checks_json column for legacy task tables if missing."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks);").fetchall()}
        if "done_checks_json" in cols:
            return
        conn.execute("ALTER TABLE tasks ADD COLUMN done_checks_json TEXT;")
        logger.info("Added missing done_checks_json column to tasks table")

    @contextmanager
    def _transaction(self):
        """Context manager for exclusive transactions with thread lock.

        Yields:
            sqlite3.Connection within an exclusive transaction.
        """
        conn = self._connect()
        try:
            with self._lock:
                conn.execute("BEGIN EXCLUSIVE;")
                yield conn
                conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def create_task(self, task_id: str, description: str, agent: str, **kwargs) -> dict:
        """Insert a new task into the registry.

        Args:
            task_id: Unique task identifier (e.g. 'feat-xxx-20240101-120000').
            description: Natural-language task description.
            agent: Agent name ('claude-code' or 'codex').
            **kwargs: Additional fields (branch, worktree, prompt, model, etc.).

        Returns:
            Dict representation of the created task row.
        """
        fields = {
            "id": task_id,
            "description": description,
            "agent": agent,
            "status": kwargs.get("status", "pending"),
            "branch": kwargs.get("branch"),
            "worktree": kwargs.get("worktree"),
            "prompt": kwargs.get("prompt"),
            "model": kwargs.get("model"),
            "max_attempts": kwargs.get("max_attempts", 3),
        }
        # Remove None values so DEFAULT clauses apply
        fields = {k: v for k, v in fields.items() if v is not None}

        columns = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        values = list(fields.values())

        with self._transaction() as conn:
            row = conn.execute(
                f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) RETURNING *;",
                values,
            ).fetchone()

        result = dict(row)
        logger.info("Created task %s (agent=%s)", task_id, agent)
        return result

    def get_task(self, task_id: str) -> dict | None:
        """Fetch a task by ID.

        Args:
            task_id: Unique task identifier.

        Returns:
            Task dict or None if not found.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()

        if row is None:
            return None
        return dict(row)

    def update_task(self, task_id: str, **fields) -> bool:
        """Update arbitrary fields on a task.

        Args:
            task_id: Unique task identifier.
            **fields: Column names and values to update.
                     Special handling: 'updated_at' is set via SQL CURRENT_TIMESTAMP.

        Returns:
            True if exactly one row was updated.
        """
        if not fields:
            return False

        # Always set updated_at via SQL function, not as a literal value
        set_parts = ["updated_at = CURRENT_TIMESTAMP"]
        values: list = []

        for k, v in fields.items():
            if k == "updated_at":
                continue  # Already handled above
            set_parts.append(f"{k} = ?")
            values.append(v)

        values.append(task_id)

        set_clause = ", ".join(set_parts)

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?;",
                values,
            )
            updated = cursor.rowcount > 0

        if updated:
            logger.debug("Updated task %s: %s", task_id, list(fields.keys()))
        return updated

    def transition_status(self, task_id: str, new_status: str,
                          expected_current: str | None = None) -> bool:
        """Atomically transition task status with optimistic locking.

        When transitioning to 'running', also sets started_at timestamp.

        Args:
            task_id: Unique task identifier.
            new_status: Target status.
            expected_current: If set, only transition if current status matches.

        Returns:
            True if the transition succeeded.
        """
        with self._transaction() as conn:
            if expected_current:
                row = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?;",
                    (task_id,),
                ).fetchone()
                if row is None or row["status"] != expected_current:
                    logger.warning(
                        "Status transition failed: task=%s expected=%s got=%s",
                        task_id, expected_current, row["status"] if row else "not found",
                    )
                    return False

            extra = ""
            params: list = [new_status, task_id]
            if new_status == "running":
                extra = ", started_at = CURRENT_TIMESTAMP"
                params = [new_status, task_id]

            conn.execute(
                f"UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP{extra} WHERE id = ?;",
                params,
            )

        logger.info("Task %s transitioned to %s", task_id, new_status)
        return True

    def finish_task(self, task_id: str, new_status: str, **fields) -> bool:
        """Atomically write result fields and transition status in one transaction.

        Prevents partial states where result is written but status is not
        (e.g. if the executor process crashes between the two operations).

        Args:
            task_id: Unique task identifier.
            new_status: Target status ('done' or 'failed').
            **fields: Additional fields to update (exit_code, stderr_tail, etc.).

        Returns:
            True if the transition succeeded.
        """
        set_parts = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        values: list = [new_status]

        if new_status == "done":
            set_parts.append("started_at = NULL")
            set_parts.append("pid = NULL")

        for k, v in fields.items():
            if k in ("status", "updated_at"):
                continue
            set_parts.append(f"{k} = ?")
            values.append(v)

        values.append(task_id)
        set_clause = ", ".join(set_parts)

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?;",
                values,
            )
            updated = cursor.rowcount > 0

        if updated:
            logger.info("Task %s finished: status=%s fields=%s", task_id, new_status, list(fields.keys()))
        return updated

    def update_outbox_status(self, task_id: str, action: str, status: str) -> None:
        """Update outbox entry status (e.g. 'logged' vs 'sent').

        Args:
            task_id: Task ID.
            action: Notification action name.
            status: New status string.
        """
        with self._transaction() as conn:
            conn.execute(
                "UPDATE outbox SET status = ?, last_error = ? WHERE task_id = ? AND action = ?;",
                (status, f"status updated to {status}", task_id, action),
            )

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        """List tasks, optionally filtered by status.

        Args:
            status: Filter by status value, or None for all.
            limit: Maximum number of tasks to return.

        Returns:
            List of task dicts ordered by created_at DESC.
        """
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?;",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?;",
                    (limit,),
                ).fetchall()

        return [dict(row) for row in rows]

    def health_check(self) -> dict:
        """Run database integrity check and WAL checkpoint.

        Returns:
            Dict with 'integrity' and 'wal_checkpoint' results.
        """
        with self._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()

        result = {
            "integrity": integrity,
            "wal_checkpoint": {
                "busy": checkpoint[0],
                "log": checkpoint[1],
                "checkpointed": checkpoint[2],
            },
        }
        logger.info("DB health check: integrity=%s", integrity)
        return result
