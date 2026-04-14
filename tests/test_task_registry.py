#!/usr/bin/env python3
"""Comprehensive tests for task_registry.py — all CRUD + transactions + schema."""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from task_registry import TaskRegistry


class BaseRegistryTest(unittest.TestCase):
    """Base class with a temp DB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.registry = TaskRegistry(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestInit(BaseRegistryTest):
    """Schema initialization tests."""

    def test_creates_db_directory(self):
        self.assertTrue(os.path.exists(self.tmpdir))

    def test_db_file_created(self):
        self.assertTrue(os.path.exists(self.db_path))

    def test_wal_mode(self):
        with self.registry._connect() as conn:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            self.assertEqual(row[0], "wal")

    def test_tasks_table_schema(self):
        """Verify all columns from spec exist."""
        with self.registry._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks);").fetchall()}
        expected = {
            "id", "description", "agent", "status", "branch", "worktree",
            "prompt", "result", "model", "exit_code", "stderr_tail",
            "failure_class", "attempt", "max_attempts", "created_at",
            "updated_at", "started_at", "pid",
        }
        self.assertTrue(expected.issubset(cols), f"Missing: {expected - cols}")

    def test_outbox_table_schema(self):
        """Verify outbox columns from spec exist."""
        with self.registry._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(outbox);").fetchall()}
        expected = {
            "id", "task_id", "action", "external_id", "payload",
            "status", "attempts", "created_at", "sent_at", "last_error",
        }
        self.assertTrue(expected.issubset(cols), f"Missing: {expected - cols}")

    def test_outbox_unique_constraint(self):
        """UNIQUE(task_id, action) should prevent duplicates."""
        with self.registry._connect() as conn:
            conn.execute(
                "INSERT INTO outbox (task_id, action, payload) VALUES (?, ?, '{}');",
                ("t1", "notify_done"),
            )
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO outbox (task_id, action, payload) VALUES (?, ?, '{}');",
                    ("t1", "notify_done"),
                )


class TestCreateTask(BaseRegistryTest):
    """create_task() tests."""

    def test_basic_create(self):
        task = self.registry.create_task("t1", "Build API", "claude-code")
        self.assertEqual(task["id"], "t1")
        self.assertEqual(task["description"], "Build API")
        self.assertEqual(task["agent"], "claude-code")
        self.assertEqual(task["status"], "pending")

    def test_default_max_attempts(self):
        task = self.registry.create_task("t1", "desc", "claude-code")
        self.assertEqual(task["max_attempts"], 3)

    def test_custom_fields(self):
        task = self.registry.create_task(
            "t2", "desc", "codex",
            branch="feat/t2", model="gpt-5.4", max_attempts=5,
        )
        self.assertEqual(task["branch"], "feat/t2")
        self.assertEqual(task["model"], "gpt-5.4")
        self.assertEqual(task["max_attempts"], 5)

    def test_created_at_set(self):
        task = self.registry.create_task("t1", "desc", "claude-code")
        self.assertIsNotNone(task["created_at"])

    def test_id_is_primary_key(self):
        self.registry.create_task("t1", "desc", "claude-code")
        with self.assertRaises(Exception):
            self.registry.create_task("t1", "desc2", "codex")


class TestGetTask(BaseRegistryTest):
    """get_task() tests."""

    def test_get_existing(self):
        self.registry.create_task("t1", "desc", "claude-code")
        task = self.registry.get_task("t1")
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "t1")

    def test_get_nonexistent(self):
        task = self.registry.get_task("nonexistent")
        self.assertIsNone(task)

    def test_returns_dict(self):
        self.registry.create_task("t1", "desc", "claude-code")
        task = self.registry.get_task("t1")
        self.assertIsInstance(task, dict)


class TestUpdateTask(BaseRegistryTest):
    """update_task() tests."""

    def test_update_single_field(self):
        self.registry.create_task("t1", "desc", "claude-code")
        result = self.registry.update_task("t1", exit_code=42)
        self.assertTrue(result)
        task = self.registry.get_task("t1")
        self.assertEqual(task["exit_code"], 42)

    def test_update_multiple_fields(self):
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.update_task("t1", exit_code=1, stderr_tail="error msg", pid=12345)
        task = self.registry.get_task("t1")
        self.assertEqual(task["exit_code"], 1)
        self.assertEqual(task["stderr_tail"], "error msg")
        self.assertEqual(task["pid"], 12345)

    def test_update_nonexistent(self):
        result = self.registry.update_task("nonexistent", exit_code=1)
        self.assertFalse(result)

    def test_update_empty_fields(self):
        result = self.registry.update_task("t1", )
        self.assertFalse(result)

    def test_updated_at_auto_set(self):
        self.registry.create_task("t1", "desc", "claude-code")
        original = self.registry.get_task("t1")["updated_at"]
        self.registry.update_task("t1", exit_code=0)
        updated = self.registry.get_task("t1")["updated_at"]
        self.assertIsNotNone(updated)


class TestTransitionStatus(BaseRegistryTest):
    """transition_status() tests — spec: optimistic locking + started_at on running."""

    def test_pending_to_running(self):
        self.registry.create_task("t1", "desc", "claude-code")
        result = self.registry.transition_status("t1", "running", "pending")
        self.assertTrue(result)
        task = self.registry.get_task("t1")
        self.assertEqual(task["status"], "running")
        self.assertIsNotNone(task["started_at"])

    def test_running_to_done(self):
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        result = self.registry.transition_status("t1", "done", "running")
        self.assertTrue(result)

    def test_wrong_expected_status(self):
        self.registry.create_task("t1", "desc", "claude-code")
        # Try pending→done (should fail, expected is pending but target is done without running)
        result = self.registry.transition_status("t1", "done", "running")
        self.assertFalse(result)

    def test_nonexistent_task(self):
        result = self.registry.transition_status("nope", "running", "pending")
        self.assertFalse(result)

    def test_no_expected_succeeds(self):
        """transition without expected_current should always succeed."""
        self.registry.create_task("t1", "desc", "claude-code")
        result = self.registry.transition_status("t1", "failed")
        self.assertTrue(result)

    def test_started_at_not_set_for_non_running(self):
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.transition_status("t1", "failed")
        task = self.registry.get_task("t1")
        self.assertIsNone(task.get("started_at"))


class TestLegacyMigration(unittest.TestCase):
    """Ensure TaskRegistry adds done_checks_json column when missing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "legacy.db")
        self._create_legacy_schema()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_legacy_schema(self):
        legacy_schema = """
        CREATE TABLE tasks (
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
            pid INTEGER
        );
        CREATE TABLE outbox (
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
        conn = sqlite3.connect(self.db_path)
        conn.executescript(legacy_schema)
        conn.close()

    def test_migrates_done_checks_column(self):
        TaskRegistry(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks);").fetchall()}
        self.assertIn("done_checks_json", cols)

    def test_finish_task_accepts_done_checks(self):
        registry = TaskRegistry(self.db_path)
        task = registry.create_task("t-mig", "migration", "claude-code")
        done = registry.finish_task(task["id"], "done", done_checks_json='{"checks": []}')
        self.assertTrue(done)
        stored = registry.get_task(task["id"])
        self.assertEqual(stored["done_checks_json"], '{"checks": []}')


class TestFinishTask(BaseRegistryTest):
    """finish_task() tests — atomic status + fields update."""

    def test_finish_done(self):
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        result = self.registry.finish_task("t1", "done", exit_code=0, result="output")
        self.assertTrue(result)
        task = self.registry.get_task("t1")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["exit_code"], 0)
        self.assertEqual(task["result"], "output")

    def test_finish_failed(self):
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        result = self.registry.finish_task(
            "t1", "failed", exit_code=1, stderr_tail="boom", failure_class="permanent"
        )
        self.assertTrue(result)
        task = self.registry.get_task("t1")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["failure_class"], "permanent")

    def test_finish_clears_started_at_and_pid(self):
        """Spec: done status should clear started_at and pid."""
        self.registry.create_task("t1", "desc", "claude-code")
        self.registry.transition_status("t1", "running", "pending")
        self.registry.update_task("t1", pid=99999)
        self.registry.finish_task("t1", "done")
        task = self.registry.get_task("t1")
        self.assertIsNone(task.get("started_at"))
        self.assertIsNone(task.get("pid"))

    def test_finish_nonexistent(self):
        result = self.registry.finish_task("nope", "done")
        self.assertFalse(result)


class TestListTasks(BaseRegistryTest):
    """list_tasks() tests."""

    def test_list_all(self):
        self.registry.create_task("t1", "desc1", "claude-code")
        self.registry.create_task("t2", "desc2", "codex")
        self.registry.create_task("t3", "desc3", "claude-code")
        tasks = self.registry.list_tasks()
        self.assertEqual(len(tasks), 3)

    def test_list_by_status(self):
        self.registry.create_task("t1", "desc1", "claude-code")
        self.registry.create_task("t2", "desc2", "codex")
        self.registry.transition_status("t1", "running", "pending")
        pending = self.registry.list_tasks(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], "t2")

    def test_list_limit(self):
        for i in range(10):
            self.registry.create_task(f"t{i}", f"desc{i}", "claude-code")
        tasks = self.registry.list_tasks(limit=3)
        self.assertEqual(len(tasks), 3)

    def test_list_order_desc(self):
        for i in range(5):
            self.registry.create_task(f"t{i}", f"desc{i}", "claude-code")
        tasks = self.registry.list_tasks()
        # Verify descending order by created_at (newest first)
        timestamps = [t["created_at"] for t in tasks]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_list_empty_status(self):
        tasks = self.registry.list_tasks(status="done")
        self.assertEqual(len(tasks), 0)


class TestHealthCheck(BaseRegistryTest):
    """health_check() tests."""

    def test_integrity_ok(self):
        result = self.registry.health_check()
        self.assertEqual(result["integrity"], "ok")

    def test_wal_checkpoint(self):
        result = self.registry.health_check()
        self.assertIn("wal_checkpoint", result)
        self.assertIn("busy", result["wal_checkpoint"])
        self.assertIn("log", result["wal_checkpoint"])
        self.assertIn("checkpointed", result["wal_checkpoint"])


class TestTransaction(BaseRegistryTest):
    """Transaction isolation and threading tests."""

    def test_rollback_on_error(self):
        """Verify ROLLBACK on exception within _transaction."""
        self.registry.create_task("t1", "original", "claude-code")
        try:
            with self.registry._transaction() as conn:
                conn.execute("UPDATE tasks SET description = ? WHERE id = ?;", ("modified", "t1"))
                raise ValueError("Simulated error")
        except ValueError:
            pass
        task = self.registry.get_task("t1")
        self.assertEqual(task["description"], "original")


if __name__ == "__main__":
    unittest.main(verbosity=2)
