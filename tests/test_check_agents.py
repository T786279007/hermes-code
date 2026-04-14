#!/usr/bin/env python3
"""Comprehensive tests for check_agents.py — HealthChecker."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from check_agents import HealthChecker
from task_registry import TaskRegistry


class BaseHealthCheckTest(unittest.TestCase):
    """Base with temp DB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.registry = TaskRegistry(self.db_path)
        self.checker = HealthChecker(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestCheckDisk(BaseHealthCheckTest):
    """Disk usage check tests."""

    def test_returns_dict(self):
        result = self.checker._check_disk()
        self.assertIsInstance(result, dict)

    def test_has_required_keys(self):
        result = self.checker._check_disk()
        self.assertIn("disk_total_gb", result)
        self.assertIn("disk_used_gb", result)
        self.assertIn("disk_percent", result)

    def test_positive_values(self):
        result = self.checker._check_disk()
        self.assertGreater(result["disk_total_gb"], 0)
        self.assertGreater(result["disk_used_gb"], 0)
        self.assertGreater(result["disk_percent"], 0)
        self.assertLessEqual(result["disk_percent"], 100)

    def test_rounded_values(self):
        result = self.checker._check_disk()
        # Should be rounded to reasonable precision
        self.assertEqual(result["disk_total_gb"], round(result["disk_total_gb"], 2))


class TestCheckTasks(BaseHealthCheckTest):
    """Task count check tests."""

    def test_empty_registry(self):
        result = self.checker._check_tasks()
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["running"], 0)
        self.assertEqual(result["done"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["retrying"], 0)

    def test_counts_match(self):
        self.registry.create_task("t1", "d1", "claude-code")
        self.registry.create_task("t2", "d2", "claude-code")
        self.registry.create_task("t3", "d3", "codex")
        self.registry.transition_status("t1", "running", "pending")
        self.registry.finish_task("t2", "done", exit_code=0)
        self.registry.finish_task("t3", "failed", exit_code=1)

        result = self.checker._check_tasks()
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["running"], 1)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["failed"], 1)

    def test_all_statuses_present(self):
        result = self.checker._check_tasks()
        for status in ("pending", "running", "done", "failed", "retrying"):
            self.assertIn(status, result)


class TestCheckAgents(BaseHealthCheckTest):
    """Running agent check tests."""

    def test_no_running_tasks(self):
        result = self.checker._check_agents()
        self.assertEqual(result, {})

    def test_running_task_with_dead_pid(self):
        self.registry.create_task("t1", "d1", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        self.registry.update_task("t1", pid=999999999)  # Definitely dead PID

        result = self.checker._check_agents()
        self.assertIn("t1", result)
        self.assertEqual(result["t1"]["pid"], 999999999)
        self.assertFalse(result["t1"]["alive"])

    def test_running_task_with_started_at(self):
        self.registry.create_task("t1", "d1", "claude-code")
        self.registry.transition_status("t1", "running", "pending")

        result = self.checker._check_agents()
        self.assertIn("t1", result)
        self.assertIn("elapsed_sec", result["t1"])
        self.assertGreaterEqual(result["t1"]["elapsed_sec"], 0)

    def test_running_task_without_started_at(self):
        self.registry.create_task("t1", "d1", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        # Manually clear started_at
        with self.registry._connect() as conn:
            conn.execute("UPDATE tasks SET started_at = NULL WHERE id = 't1';")

        result = self.checker._check_agents()
        self.assertIn("t1", result)
        self.assertEqual(result["t1"]["elapsed_sec"], 0)

    def test_multiple_running_tasks(self):
        for i in range(3):
            self.registry.create_task(f"t{i}", f"d{i}", "claude-code")
            self.registry.transition_status(f"t{i}", "running", "pending")
            self.registry.update_task(f"t{i}", pid=99999000 + i)

        result = self.checker._check_agents()
        self.assertEqual(len(result), 3)


class TestCheckDatabase(BaseHealthCheckTest):
    """Database health check tests."""

    def test_returns_integrity(self):
        result = self.checker._check_database()
        self.assertIn("integrity", result)
        self.assertEqual(result["integrity"], "ok")

    def test_returns_wal_checkpoint(self):
        result = self.checker._check_database()
        self.assertIn("wal_checkpoint", result)


class TestCheckAll(BaseHealthCheckTest):
    """Top-level check() integration test."""

    def test_check_returns_all_sections(self):
        result = self.checker.check()
        self.assertIn("system", result)
        self.assertIn("tasks", result)
        self.assertIn("agents", result)
        self.assertIn("database", result)

    def test_check_system_is_dict(self):
        result = self.checker.check()
        self.assertIsInstance(result["system"], dict)

    def test_check_tasks_is_dict(self):
        result = self.checker.check()
        self.assertIsInstance(result["tasks"], dict)

    def test_check_agents_is_dict(self):
        result = self.checker.check()
        self.assertIsInstance(result["agents"], dict)

    def test_check_database_is_dict(self):
        result = self.checker.check()
        self.assertIsInstance(result["database"], dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
