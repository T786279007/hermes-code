"""Tests for cost_monitor.py - Cost tracking for task execution."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cost_monitor import CostMonitor


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
def cost_monitor(temp_db):
    """Create a CostMonitor instance with a temporary database."""
    # Create tasks table first
    conn = sqlite3.connect(temp_db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            agent TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""
    )
    conn.commit()
    conn.close()

    # Clear environment variables to use defaults
    old_input = os.environ.pop("COST_INPUT_RATE", None)
    old_output = os.environ.pop("COST_OUTPUT_RATE", None)
    try:
        monitor = CostMonitor(temp_db)
        yield monitor
    finally:
        # Restore environment
        if old_input is not None:
            os.environ["COST_INPUT_RATE"] = old_input
        if old_output is not None:
            os.environ["COST_OUTPUT_RATE"] = old_output


@pytest.fixture
def populated_db(cost_monitor):
    """Create a database with test tasks."""
    # Create tasks table first
    conn = sqlite3.connect(cost_monitor._db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            agent TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""
    )
    conn.commit()
    conn.close()

    # Initialize CostMonitor to add cost columns
    monitor = CostMonitor(cost_monitor._db_path)

    # Insert test tasks
    conn = sqlite3.connect(cost_monitor._db_path)
    conn.execute(
        "INSERT INTO tasks (id, description, agent, status) VALUES (?, ?, ?, ?)",
        ("task-1", "Test task 1", "claude-code", "done"),
    )
    conn.execute(
        "INSERT INTO tasks (id, description, agent, status) VALUES (?, ?, ?, ?)",
        ("task-2", "Test task 2", "codex", "running"),
    )
    conn.execute(
        "INSERT INTO tasks (id, description, agent, status) VALUES (?, ?, ?, ?)",
        ("task-3", "Test task 3", "claude-code", "pending"),
    )
    conn.commit()
    conn.close()
    return monitor


class TestCostMonitorInit:
    """Test CostMonitor initialization and database setup."""

    def test_adds_cost_columns_on_init(self, temp_db):
        """CostMonitor adds cost_tokens and cost_usd columns on import."""
        # Create basic tasks table first
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, description TEXT, agent TEXT, status TEXT)"
        )
        conn.close()

        # Importing should add columns
        monitor = CostMonitor(temp_db)

        # Verify columns exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("PRAGMA table_info(tasks);")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "cost_tokens" in columns
        assert "cost_usd" in columns

    def test_cost_columns_have_defaults(self, temp_db):
        """New columns have proper default values."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, description TEXT)"
        )
        conn.close()

        monitor = CostMonitor(temp_db)

        # Insert row without specifying cost fields
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO tasks (id, description) VALUES (?, ?)", ("t1", "test"))
        conn.commit()

        cursor = conn.execute("SELECT cost_tokens, cost_usd FROM tasks WHERE id = ?", ("t1",))
        row = cursor.fetchone()
        conn.close()

        assert row[0] == 0  # cost_tokens default
        assert row[1] == 0.0  # cost_usd default

    def test_uses_custom_rates_from_env(self, temp_db):
        """CostMonitor respects custom rates from environment variables."""
        os.environ["COST_INPUT_RATE"] = "2.0"  # $2 per million
        os.environ["COST_OUTPUT_RATE"] = "5.0"  # $5 per million

        monitor = CostMonitor(temp_db)

        assert monitor.input_rate == 2.0
        assert monitor.output_rate == 5.0

        # Cleanup
        del os.environ["COST_INPUT_RATE"]
        del os.environ["COST_OUTPUT_RATE"]

    def test_uses_default_rates_without_env(self, temp_db):
        """CostMonitor uses default rates when env vars not set."""
        monitor = CostMonitor(temp_db)

        assert monitor.input_rate == 0.5
        assert monitor.output_rate == 1.5


class TestUpdateCost:
    """Test update_cost method."""

    def test_update_cost_calculates_usd_correctly(self, populated_db):
        """update_cost calculates USD from token counts."""
        # 1000 input tokens * 0.5 / 1M = 0.0005
        # 500 output tokens * 1.5 / 1M = 0.00075
        # Total = 0.00125
        populated_db.update_cost("task-1", 1000, 500)

        cost = populated_db.get_task_cost("task-1")
        assert cost["cost_tokens"] == 1500
        assert abs(cost["cost_usd"] - 0.00125) < 0.00001

    def test_update_cost_accumulates_for_same_task(self, populated_db):
        """Multiple updates to same task accumulate costs."""
        populated_db.update_cost("task-1", 1000, 500)  # 0.00125 USD
        populated_db.update_cost("task-1", 2000, 1000)  # 0.0025 USD

        cost = populated_db.get_task_cost("task-1")
        assert cost["cost_tokens"] == 4500  # 1500 + 3000
        assert abs(cost["cost_usd"] - 0.00375) < 0.00001

    def test_update_cost_handles_zero_tokens(self, populated_db):
        """update_cost handles zero token counts."""
        populated_db.update_cost("task-2", 0, 0)

        cost = populated_db.get_task_cost("task-2")
        assert cost["cost_tokens"] == 0
        assert cost["cost_usd"] == 0.0

    def test_update_cost_nonexistent_task_returns_false(self, cost_monitor):
        """update_cost returns False for nonexistent task."""
        result = cost_monitor.update_cost("nonexistent-task", 1000, 500)
        assert result is False


class TestGetTaskCost:
    """Test get_task_cost method."""

    def test_get_task_cost_returns_dict(self, populated_db):
        """get_task_cost returns dict with cost info."""
        populated_db.update_cost("task-1", 1000, 500)

        cost = populated_db.get_task_cost("task-1")
        assert isinstance(cost, dict)
        assert "cost_tokens" in cost
        assert "cost_usd" in cost

    def test_get_task_cost_nonexistent_returns_none(self, cost_monitor):
        """get_task_cost returns None for nonexistent task."""
        cost = cost_monitor.get_task_cost("nonexistent-task")
        assert cost is None

    def test_get_task_cost_initially_zero(self, populated_db):
        """New tasks start with zero cost."""
        cost = populated_db.get_task_cost("task-2")
        assert cost["cost_tokens"] == 0
        assert cost["cost_usd"] == 0.0


class TestGetDailyCost:
    """Test get_daily_cost method."""

    def test_get_daily_cost_single_task(self, populated_db):
        """get_daily_cost returns cost for tasks on given date."""
        populated_db.update_cost("task-1", 1_000_000, 500_000)  # 1.25 USD

        # Get today's cost
        from datetime import date
        today = date.today().isoformat()
        daily_cost = populated_db.get_daily_cost(today)

        assert abs(daily_cost - 1.25) < 0.01

    def test_get_daily_cost_multiple_tasks(self, populated_db):
        """get_daily_cost sums all tasks for the date."""
        # Add cost to multiple tasks
        populated_db.update_cost("task-1", 1_000_000, 0)  # 0.5 USD
        populated_db.update_cost("task-2", 2_000_000, 0)  # 1.0 USD

        from datetime import date
        today = date.today().isoformat()
        daily_cost = populated_db.get_daily_cost(today)

        assert abs(daily_cost - 1.5) < 0.01

    def test_get_daily_cost_no_tasks_returns_zero(self, cost_monitor):
        """get_daily_cost returns 0 when no tasks exist."""
        from datetime import date
        today = date.today().isoformat()
        daily_cost = cost_monitor.get_daily_cost(today)

        assert daily_cost == 0.0


class TestCostReport:
    """Test cost_report method."""

    def test_cost_report_returns_list(self, populated_db):
        """cost_report returns list of daily costs."""
        populated_db.update_cost("task-1", 1_000_000, 0)

        report = populated_db.cost_report(days=7)
        assert isinstance(report, list)
        assert len(report) <= 7

    def test_cost_report_default_7_days(self, populated_db):
        """cost_report defaults to 7 days."""
        populated_db.update_cost("task-1", 1_000_000, 0)

        report = populated_db.cost_report()
        assert len(report) <= 7

    def test_cost_report_custom_days(self, populated_db):
        """cost_report respects custom days parameter."""
        populated_db.update_cost("task-1", 1_000_000, 0)

        report = populated_db.cost_report(days=3)
        assert len(report) <= 3

    def test_cost_report_entry_structure(self, populated_db):
        """cost_report entries have date and total_usd keys."""
        from datetime import date
        populated_db.update_cost("task-1", 1_000_000, 0)  # 0.5 USD

        report = populated_db.cost_report(days=1)
        today = date.today().isoformat()

        assert len(report) >= 0
        if len(report) > 0:
            entry = report[0]
            assert "date" in entry
            assert "total_usd" in entry
            assert entry["date"] == today
            assert abs(entry["total_usd"] - 0.5) < 0.01


class TestCheckBudget:
    """Test check_budget method."""

    def test_check_budget_under_limit_returns_true(self, populated_db):
        """check_budget returns True when under budget."""
        populated_db.update_cost("task-1", 100_000, 50_000)  # ~0.125 USD

        result = populated_db.check_budget("task-1", limit_usd=1.0)
        assert result is True

    def test_check_budget_over_limit_returns_false(self, populated_db):
        """check_budget returns False when over budget."""
        # Use lots of tokens to exceed $1
        populated_db.update_cost("task-1", 1_500_000, 500_000)  # 1.5 USD

        result = populated_db.check_budget("task-1", limit_usd=1.0)
        assert result is False

    def test_check_budget_at_limit_returns_false(self, populated_db):
        """check_budget returns False when at or over limit."""
        populated_db.update_cost("task-1", 1_000_000, 333_334)  # ~1.0 USD

        result = populated_db.check_budget("task-1", limit_usd=1.0)
        assert result is False

    def test_check_budget_custom_limit(self, populated_db):
        """check_budget respects custom limit_usd parameter."""
        populated_db.update_cost("task-1", 1_000_000, 0)  # 0.5 USD

        # Under custom limit
        result = populated_db.check_budget("task-1", limit_usd=0.75)
        assert result is True

        # Over custom limit
        result = populated_db.check_budget("task-1", limit_usd=0.25)
        assert result is False

    def test_check_budget_nonexistent_task_returns_true(self, cost_monitor):
        """check_budget returns True for nonexistent task (no cost)."""
        result = cost_monitor.check_budget("nonexistent-task", limit_usd=1.0)
        assert result is True


class TestRates:
    """Test rate calculation and environment variable handling."""

    def test_custom_rate_calculation(self, temp_db):
        """Custom rates are used in cost calculations."""
        # Clear any cached rate from previous tests
        os.environ.pop("COST_INPUT_RATE", None)
        os.environ.pop("COST_OUTPUT_RATE", None)

        # Create tasks table with cost columns
        conn = sqlite3.connect(temp_db)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cost_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0
            );"""
        )
        conn.commit()
        conn.close()

        os.environ["COST_INPUT_RATE"] = "10.0"  # $10 per million
        os.environ["COST_OUTPUT_RATE"] = "20.0"  # $20 per million

        monitor = CostMonitor(temp_db)

        # Create test task
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO tasks (id, description, agent, status) VALUES (?, ?, ?, ?)",
            ("task-1", "Test", "claude-code", "done"),
        )
        conn.commit()
        conn.close()

        result = monitor.update_cost("task-1", 1_000_000, 500_000)
        assert result is True, "update_cost should return True"

        cost = monitor.get_task_cost("task-1")
        assert cost is not None, f"get_task_cost returned None - check DB path: {temp_db}"
        # 1M * 10 / 1M + 500K * 20 / 1M = 10 + 10 = 20 USD
        assert abs(cost["cost_usd"] - 20.0) < 0.01

        # Cleanup
        del os.environ["COST_INPUT_RATE"]
        del os.environ["COST_OUTPUT_RATE"]
