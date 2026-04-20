"""Tests for cleanup.py - integration tests with real temp DB and filesystem."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import cleanup


class TestCleanupOldTasks(unittest.TestCase):
    """Test cleanup_old_tasks with real SQLite databases."""

    def _create_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pid INTEGER,
                worktree TEXT,
                stderr_tail TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_no_old_tasks(self):
        """No tasks older than threshold."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            # Insert a recent task
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("recent-task", "desc", "claude-code", "done", datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_old_tasks(7, dry_run=False)
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(db)

    def test_old_tasks_deleted(self):
        """Old done/failed tasks are deleted."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            old_time = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("old-task-1", "desc", "claude-code", "done", old_time),
            )
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("old-task-2", "desc", "codex", "failed", old_time),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_old_tasks(7, dry_run=False)

            task_ids = {r["task_id"] for r in result}
            self.assertEqual(task_ids, {"old-task-1", "old-task-2"})

            # Verify tasks are actually deleted
            conn = sqlite3.connect(db)
            remaining = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
            conn.close()
            self.assertEqual(remaining, 0)
        finally:
            os.unlink(db)

    def test_running_tasks_not_deleted(self):
        """Running tasks are never deleted even if old."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            old_time = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("running-old", "desc", "claude-code", "running", old_time),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_old_tasks(7, dry_run=False)
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(db)

    def test_dry_run_does_not_delete(self):
        """Dry run mode does not actually delete tasks."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            old_time = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("dry-task", "desc", "claude-code", "done", old_time),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_old_tasks(7, dry_run=True)
            self.assertEqual(len(result), 1)

            # Task should still exist
            conn = sqlite3.connect(db)
            remaining = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
            conn.close()
            self.assertEqual(remaining, 1)
        finally:
            os.unlink(db)


class TestCleanupZombieTasks(unittest.TestCase):
    """Test cleanup_zombie_tasks with real SQLite databases."""

    def _create_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pid INTEGER,
                worktree TEXT,
                stderr_tail TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_no_zombie_tasks(self):
        """No running tasks with dead PIDs."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            # No running tasks in DB
            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_zombie_tasks(dry_run=False)
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(db)

    def test_zombie_task_detected(self):
        """Zombie task (dead PID) is marked as failed."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            # Use a PID that definitely doesn't exist
            fake_pid = 99999999
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, pid) VALUES (?, ?, ?, ?, ?)",
                ("zombie-task", "desc", "claude-code", "running", fake_pid),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_zombie_tasks(dry_run=False)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["task_id"], "zombie-task")

            # Verify status updated to failed
            conn = sqlite3.connect(db)
            status = conn.execute("SELECT status FROM tasks WHERE id = ?", ("zombie-task",)).fetchone()[0]
            conn.close()
            self.assertEqual(status, "failed")
        finally:
            os.unlink(db)

    def test_dry_run_does_not_update(self):
        """Dry run does not update task status."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            fake_pid = 99999999
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, pid) VALUES (?, ?, ?, ?, ?)",
                ("zombie-dry", "desc", "claude-code", "running", fake_pid),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_zombie_tasks(dry_run=True)

            self.assertEqual(len(result), 1)
            # Status should still be running
            conn = sqlite3.connect(db)
            status = conn.execute("SELECT status FROM tasks WHERE id = ?", ("zombie-dry",)).fetchone()[0]
            conn.close()
            self.assertEqual(status, "running")
        finally:
            os.unlink(db)

    def test_null_pid_skipped(self):
        """Tasks with null PID are skipped (not treated as zombies)."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO tasks (id, description, agent, status, pid) VALUES (?, ?, ?, ?, ?)",
                ("null-pid", "desc", "claude-code", "running", None),
            )
            conn.commit()
            conn.close()

            with patch("cleanup.DB_PATH", db):
                result = cleanup.cleanup_zombie_tasks(dry_run=False)
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(db)


class TestCleanupOldLogs(unittest.TestCase):
    """Test cleanup_old_logs with real temp directories."""

    def test_old_log_removed(self):
        """Old log files are removed."""
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = Path(tmpdir) / "old.log"
            old_file.write_text("old content")
            old_time = time.time() - 31 * 86400
            os.utime(str(old_file), (old_time, old_time))

            recent_file = Path(tmpdir) / "recent.log"
            recent_file.write_text("recent content")

            with patch("cleanup.LOG_DIR", tmpdir):
                result = cleanup.cleanup_old_logs(30, dry_run=False)

            self.assertEqual(len(result), 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())

    def test_dry_run_does_not_delete(self):
        """Dry run does not delete log files."""
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = Path(tmpdir) / "old.log"
            old_file.write_text("old content")
            old_time = time.time() - 31 * 86400
            os.utime(str(old_file), (old_time, old_time))

            with patch("cleanup.LOG_DIR", tmpdir):
                result = cleanup.cleanup_old_logs(30, dry_run=True)

            self.assertEqual(len(result), 1)
            self.assertTrue(old_file.exists())

    def test_no_log_dir(self):
        """Missing log directory is handled gracefully."""
        with patch("cleanup.LOG_DIR", "/nonexistent/path/that/does/not/exist"):
            result = cleanup.cleanup_old_logs(30, dry_run=False)
        self.assertEqual(len(result), 0)


class TestCleanupWorktrees(unittest.TestCase):
    """Test cleanup_worktrees with real temp directories."""

    def _create_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pid INTEGER,
                worktree TEXT,
                stderr_tail TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_old_worktree_removed(self):
        """Old worktree for done/failed task is removed."""
        import time
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            with tempfile.TemporaryDirectory() as wt_base:
                wt_dir = Path(wt_base) / "old-wt"
                wt_dir.mkdir()
                (wt_dir / "file.py").write_text("content")
                old_time = time.time() - 25 * 3600  # 25 hours ago
                os.utime(str(wt_dir), (old_time, old_time))

                old_ts = "2026-01-01 00:00:00"
                conn = sqlite3.connect(db)
                conn.execute(
                    "INSERT INTO tasks (id, description, agent, status, worktree, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("wt-task", "desc", "claude-code", "done", str(wt_dir), old_ts),
                )
                conn.commit()
                conn.close()

                with patch("cleanup.DB_PATH", db), patch("cleanup.WORKTREE_BASE", Path(wt_base)):
                    result = cleanup.cleanup_worktrees(24, dry_run=False)

                self.assertEqual(len(result), 1)
                self.assertFalse(wt_dir.exists())
                self.assertFalse(wt_dir.exists())
        finally:
            os.unlink(db)

    def test_running_task_worktree_not_removed(self):
        """Worktree for running task is not removed."""
        import time
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            with tempfile.TemporaryDirectory() as wt_base:
                wt_dir = Path(wt_base) / "running-wt"
                wt_dir.mkdir()
                old_time = time.time() - 25 * 3600
                os.utime(str(wt_dir), (old_time, old_time))

                conn = sqlite3.connect(db)
                conn.execute(
                    "INSERT INTO tasks (id, description, agent, status, worktree) VALUES (?, ?, ?, ?, ?)",
                    ("running-wt-task", "desc", "claude-code", "running", str(wt_dir)),
                )
                conn.commit()
                conn.close()

                with patch("cleanup.DB_PATH", db), patch("cleanup.WORKTREE_BASE", Path(wt_base)):
                    result = cleanup.cleanup_worktrees(24, dry_run=False)

                self.assertEqual(len(result), 0)
                self.assertTrue(wt_dir.exists())
        finally:
            os.unlink(db)

    def test_no_worktrees(self):
        """Empty worktree base is handled gracefully."""
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._create_db(db)
            with tempfile.TemporaryDirectory() as wt_base:
                with patch("cleanup.DB_PATH", db), patch("cleanup.WORKTREE_BASE", Path(wt_base)):
                    result = cleanup.cleanup_worktrees(24, dry_run=False)
                self.assertEqual(len(result), 0)
        finally:
            os.unlink(db)


class TestPrintSummaryTable(unittest.TestCase):
    """Test print_summary_table output."""

    def test_print_summary(self):
        """Summary table prints without error."""
        all_cleaned = {
            "cleanup_worktrees": [{"type": "worktree", "path": "/tmp/wt1", "task_id": "task1", "age_hours": 25}],
            "cleanup_old_tasks": [{"type": "task", "task_id": "task2", "status": "done", "age_days": 10}],
        }
        cleanup.print_summary_table(all_cleaned)


class TestMain(unittest.TestCase):
    """Test CLI main entry point."""

    def test_main_default_args(self):
        """main() calls all cleanup functions."""
        with patch("sys.argv", ["cleanup"]), \
             patch("cleanup.cleanup_worktrees", return_value=[]) as mock_wt, \
             patch("cleanup.cleanup_old_tasks", return_value=[]) as mock_ot, \
             patch("cleanup.cleanup_old_logs", return_value=[]) as mock_ol, \
             patch("cleanup.cleanup_zombie_tasks", return_value=[]) as mock_zt, \
             patch("cleanup.print_summary_table") as mock_print:
            cleanup.main()
            mock_wt.assert_called_once()
            mock_ot.assert_called_once()
            mock_ol.assert_called_once()
            mock_zt.assert_called_once()
            mock_print.assert_called_once()

    def test_main_dry_run(self):
        """main() passes dry-run flag."""
        with patch("sys.argv", ["cleanup", "--dry-run"]), \
             patch("cleanup.cleanup_worktrees", return_value=[]) as mock_wt, \
             patch("cleanup.cleanup_old_tasks", return_value=[]) as mock_ot, \
             patch("cleanup.cleanup_old_logs", return_value=[]) as mock_ol, \
             patch("cleanup.cleanup_zombie_tasks", return_value=[]) as mock_zt, \
             patch("cleanup.print_summary_table") as mock_print:
            cleanup.main()
            mock_wt.assert_called_once()
            mock_ot.assert_called_once()
            mock_ol.assert_called_once()
            mock_zt.assert_called_once()
            mock_print.assert_called_once()
