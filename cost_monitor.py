"""Cost monitoring for task execution with token usage and USD tracking."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default rates per million tokens (USD)
DEFAULT_INPUT_RATE = 0.5
DEFAULT_OUTPUT_RATE = 1.5


def _ensure_cost_columns(db_path: str | Path) -> None:
    """Add cost tracking columns to tasks table if they don't exist.

    Args:
        db_path: Path to the SQLite database.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("PRAGMA table_info(tasks);")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if "cost_tokens" not in existing_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN cost_tokens INTEGER DEFAULT 0;")
            logger.info("Added cost_tokens column to tasks table")

        if "cost_usd" not in existing_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN cost_usd REAL DEFAULT 0.0;")
            logger.info("Added cost_usd column to tasks table")

        conn.commit()
    except sqlite3.OperationalError as e:
        logger.warning("Could not add cost columns: %s", e)
    finally:
        conn.close()


# Run on import to add columns
# We'll get DB_PATH from config when imported
try:
    from config import DB_PATH
    _ensure_cost_columns(DB_PATH)
except ImportError:
    # config not available, skip auto-migration on import
    pass


class CostMonitor:
    """Monitor and track costs for task execution."""

    def __init__(self, db_path: str | Path):
        """Initialize CostMonitor with database path.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = str(db_path)
        self.input_rate = float(os.environ.get("COST_INPUT_RATE", DEFAULT_INPUT_RATE))
        self.output_rate = float(os.environ.get("COST_OUTPUT_RATE", DEFAULT_OUTPUT_RATE))

        # Ensure columns exist
        _ensure_cost_columns(self._db_path)

    def update_cost(self, task_id: str, input_tokens: int, output_tokens: int) -> bool:
        """Update cost tracking for a task.

        Calculates USD cost using current rates and accumulates with existing cost.

        Args:
            task_id: Unique task identifier.
            input_tokens: Number of input tokens consumed.
            output_tokens: Number of output tokens generated.

        Returns:
            True if update succeeded, False if task not found.
        """
        # Calculate cost for this update
        input_cost = (input_tokens / 1_000_000) * self.input_rate
        output_cost = (output_tokens / 1_000_000) * self.output_rate
        total_cost = input_cost + output_cost
        total_tokens = input_tokens + output_tokens

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET cost_tokens = COALESCE(cost_tokens, 0) + ?,
                    cost_usd = COALESCE(cost_usd, 0.0) + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (total_tokens, total_cost, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_task_cost(self, task_id: str) -> dict | None:
        """Get cost information for a specific task.

        Args:
            task_id: Unique task identifier.

        Returns:
            Dict with 'cost_tokens' and 'cost_usd' keys, or None if task not found.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT cost_tokens, cost_usd FROM tasks WHERE id = ?;",
                (task_id,),
            ).fetchone()

            if row is None:
                return None

            return {
                "cost_tokens": row[0] or 0,
                "cost_usd": row[1] or 0.0,
            }
        finally:
            conn.close()

    def get_daily_cost(self, date_str: str) -> float:
        """Get total cost for a specific date.

        Args:
            date_str: Date string in ISO format (YYYY-MM-DD).

        Returns:
            Total USD cost for all tasks on that date.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) as total
                FROM tasks
                WHERE DATE(created_at) = ?;
                """,
                (date_str,),
            ).fetchone()
            return row[0] or 0.0
        finally:
            conn.close()

    def cost_report(self, days: int = 7) -> list[dict]:
        """Generate cost report for recent days.

        Args:
            days: Number of days to include in report (default 7).

        Returns:
            List of dicts with 'date' and 'total_usd' keys, ordered by date descending.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT DATE(created_at) as date,
                       COALESCE(SUM(cost_usd), 0.0) as total_usd
                FROM tasks
                WHERE DATE(created_at) >= DATE('now', '-' || ? || ' days')
                GROUP BY DATE(created_at)
                ORDER BY date DESC;
                """,
                (days,),
            ).fetchall()

            return [{"date": row[0], "total_usd": row[1]} for row in rows]
        finally:
            conn.close()

    def check_budget(self, task_id: str, limit_usd: float = 1.0) -> bool:
        """Check if a task is within its budget limit.

        Args:
            task_id: Unique task identifier.
            limit_usd: Budget limit in USD (default 1.0).

        Returns:
            True if task cost is under the limit, False if at or over limit.
        """
        cost = self.get_task_cost(task_id)
        if cost is None:
            # Task doesn't exist, treat as zero cost (under budget)
            return True

        return cost["cost_usd"] < limit_usd
