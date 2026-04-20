#!/usr/bin/env python3
"""Comprehensive tests for cleanup.py."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add repo root to sys.path for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes import cleanup


class TestCleanupWorktrees(unittest.TestCase):
    """Test cleanup_worktrees function."""

    def setUp(self):
        """Create temporary worktree directory and mock DB."""
        self.temp_dir = tempfile.mkdtemp()
        self.worktree_base = Path(self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("hermes.cleanup.WORKTREE_BASE")
    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.REPO_PATH")
    def test_no_worktrees(self, mock_repo, mock_db, mock_base):
        """Test with no worktrees present."""
        mock_base.exists.return_value = False
        mock_base.iterdir.return_value = []

        result = cleanup.cleanup_worktrees(24, dry_run=False)

        self.assertEqual(result, [])

    @patch("hermes.cleanup.WORKTREE_BASE")
    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.REPO_PATH")
    def test_worktree_too_recent(self, mock_repo, mock_db, mock_base):
        """Test that recent worktrees are not removed."""
        # Create a recent worktree
        recent_dir = self.worktree_base / "recent-worktree"
        recent_dir.mkdir(parents=True)

        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [recent_dir]

        # Mock DB query - return dict-like objects
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Create mock Row objects
        class MockRow(dict):
            def __init__(self, worktree):
                super().__init__(worktree=worktree)

        mock_cursor.fetchall.return_value = [MockRow(str(recent_dir))]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.row_factory = sqlite3.Row

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_worktrees(24, dry_run=False)

        self.assertEqual(result, [])

    @patch("hermes.cleanup.WORKTREE_BASE")
    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.REPO_PATH")
    @patch("hermes.cleanup.subprocess.run")
    def test_old_worktree_removed(self, mock_run, mock_repo, mock_db, mock_base):
        """Test that old worktrees are removed."""
        # Create an old worktree
        old_dir = self.worktree_base / "old-worktree"
        old_dir.mkdir(parents=True)

        # Set modification time to 48 hours ago
        old_time = time.time() - (48 * 3600)
        os.utime(old_dir, (old_time, old_time))

        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [old_dir]

        # Mock DB query
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Create mock Row objects
        class MockRow(dict):
            def __init__(self, worktree):
                super().__init__(worktree=worktree)

        mock_cursor.fetchall.return_value = [MockRow(str(old_dir))]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.row_factory = sqlite3.Row

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_worktrees(24, dry_run=False)

        self.assertEqual(len(result), 1)
        self.assertIn("old-worktree", result[0])
        mock_run.assert_called_once()

    @patch("hermes.cleanup.WORKTREE_BASE")
    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.REPO_PATH")
    def test_dry_run_does_not_delete(self, mock_repo, mock_db, mock_base):
        """Test that dry-run does not delete worktrees."""
        old_dir = self.worktree_base / "old-worktree"
        old_dir.mkdir(parents=True)
        old_time = time.time() - (48 * 3600)
        os.utime(old_dir, (old_time, old_time))

        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [old_dir]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Create mock Row objects
        class MockRow(dict):
            def __init__(self, worktree):
                super().__init__(worktree=worktree)

        mock_cursor.fetchall.return_value = [MockRow(str(old_dir))]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.row_factory = sqlite3.Row

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_worktrees(24, dry_run=True)

        # Should report the worktree but not delete it
        self.assertEqual(len(result), 1)
        self.assertTrue(old_dir.exists())


class TestCleanupOldTasks(unittest.TestCase):
    """Test cleanup_old_tasks function."""

    @patch("hermes.cleanup.DB_PATH")
    def test_no_old_tasks(self, mock_db):
        """Test with no old tasks to delete."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_old_tasks(7, dry_run=False)

        self.assertEqual(result, [])
        mock_cursor.execute.assert_called()
        # No DELETE should be called
        self.assertEqual(mock_cursor.execute.call_count, 1)

    @patch("hermes.cleanup.DB_PATH")
    def test_old_tasks_deleted(self, mock_db):
        """Test that old tasks are deleted."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1",), ("task2",)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_old_tasks(7, dry_run=False)

        self.assertEqual(len(result), 2)
        self.assertIn("task1", result)
        self.assertIn("task2", result)
        # Should have 5 execute calls: SELECT + 4 DELETEs
        self.assertEqual(mock_cursor.execute.call_count, 5)
        mock_conn.commit.assert_called_once()

    @patch("hermes.cleanup.DB_PATH")
    def test_dry_run_does_not_delete(self, mock_db):
        """Test that dry-run does not delete tasks."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1",)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_old_tasks(7, dry_run=True)

        self.assertEqual(result, ["task1"])
        # No DELETE or COMMIT should be called in dry-run
        self.assertEqual(mock_cursor.execute.call_count, 1)
        mock_conn.commit.assert_not_called()


class TestCleanupOldLogs(unittest.TestCase):
    """Test cleanup_old_logs function."""

    def setUp(self):
        """Create temporary log directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_dir = Path(self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("hermes.cleanup.LOG_DIR")
    def test_no_logs_directory(self, mock_log_dir):
        """Test when logs directory does not exist."""
        mock_log_dir.exists.return_value = False
        result = cleanup.cleanup_old_logs(30, dry_run=False)
        self.assertEqual(result, [])

    @patch("hermes.cleanup.LOG_DIR")
    def test_recent_log_not_removed(self, mock_log_dir):
        """Test that recent log files are not removed."""
        recent_log = self.log_dir / "recent.log"
        recent_log.write_text("recent log content")

        mock_log_dir.exists.return_value = True
        mock_log_dir.glob.return_value = [recent_log]

        result = cleanup.cleanup_old_logs(30, dry_run=False)

        self.assertEqual(result, [])
        self.assertTrue(recent_log.exists())

    @patch("hermes.cleanup.LOG_DIR")
    def test_old_log_removed(self, mock_log_dir):
        """Test that old log files are removed."""
        old_log = self.log_dir / "old.log"
        old_log.write_text("old log content")

        # Set modification time to 45 days ago
        old_time = time.time() - (45 * 86400)
        os.utime(old_log, (old_time, old_time))

        mock_log_dir.exists.return_value = True
        mock_log_dir.glob.return_value = [old_log]

        result = cleanup.cleanup_old_logs(30, dry_run=False)

        self.assertEqual(len(result), 1)
        self.assertIn("old.log", result[0])
        self.assertFalse(old_log.exists())

    @patch("hermes.cleanup.LOG_DIR")
    def test_dry_run_does_not_delete(self, mock_log_dir):
        """Test that dry-run does not delete log files."""
        old_log = self.log_dir / "old.log"
        old_log.write_text("old log content")
        old_time = time.time() - (45 * 86400)
        os.utime(old_log, (old_time, old_time))

        mock_log_dir.exists.return_value = True
        mock_log_dir.glob.return_value = [old_log]

        result = cleanup.cleanup_old_logs(30, dry_run=True)

        self.assertEqual(len(result), 1)
        self.assertTrue(old_log.exists())


class TestCleanupZombieTasks(unittest.TestCase):
    """Test cleanup_zombie_tasks function."""

    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.os.kill")
    def test_no_zombie_tasks(self, mock_kill, mock_db):
        """Test when all running tasks have valid PIDs."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1", 1234), ("task2", 5678)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_zombie_tasks(dry_run=False)

        self.assertEqual(result, [])
        # All PIDs exist, so no UPDATE should be called
        self.assertEqual(mock_cursor.execute.call_count, 1)
        mock_conn.commit.assert_not_called()

    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.os.kill")
    def test_zombie_task_detected(self, mock_kill, mock_db):
        """Test that zombie tasks are marked as failed."""
        # Simulate PID not found
        mock_kill.side_effect = ProcessLookupError()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1", 1234)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_zombie_tasks(dry_run=False)

        self.assertEqual(len(result), 1)
        self.assertIn("task1", result)
        self.assertEqual(mock_cursor.execute.call_count, 2)  # SELECT + UPDATE
        mock_conn.commit.assert_called_once()

    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.os.kill")
    def test_dry_run_does_not_update(self, mock_kill, mock_db):
        """Test that dry-run does not update task status."""
        mock_kill.side_effect = ProcessLookupError()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1", 1234)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_zombie_tasks(dry_run=True)

        self.assertEqual(result, ["task1"])
        # No UPDATE or COMMIT in dry-run
        self.assertEqual(mock_cursor.execute.call_count, 1)
        mock_conn.commit.assert_not_called()

    @patch("hermes.cleanup.DB_PATH")
    @patch("hermes.cleanup.os.kill")
    def test_permission_error_ignored(self, mock_kill, mock_db):
        """Test that PermissionError is handled gracefully."""
        mock_kill.side_effect = PermissionError()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("task1", 1234)]
        mock_conn.cursor.return_value = mock_cursor

        def mock_connect(*args, **kwargs):
            return mock_conn

        with patch("sqlite3.connect", mock_connect):
            result = cleanup.cleanup_zombie_tasks(dry_run=False)

        # PermissionError means process exists, so no zombie
        self.assertEqual(result, [])


class TestPrintSummary(unittest.TestCase):
    """Test print_summary function."""

    @patch("hermes.cleanup.print")
    def test_print_summary(self, mock_print):
        """Test that summary is printed correctly."""
        cleanup.print_summary(["/path/1"], ["task1"], ["/log/1"], ["task2"])

        self.assertEqual(mock_print.call_count, 8)  # separator + title + 4 lines + separator


class TestMain(unittest.TestCase):
    """Test main entry point."""

    @patch("hermes.cleanup.cleanup_zombie_tasks")
    @patch("hermes.cleanup.cleanup_old_logs")
    @patch("hermes.cleanup.cleanup_old_tasks")
    @patch("hermes.cleanup.cleanup_worktrees")
    @patch("hermes.cleanup.print_summary")
    @patch("sys.argv", ["cleanup.py"])
    def test_main_default_args(self, mock_print, mock_wt, mock_tasks, mock_logs, mock_zombies):
        """Test main with default arguments."""
        mock_wt.return_value = []
        mock_tasks.return_value = []
        mock_logs.return_value = []
        mock_zombies.return_value = []

        exit_code = cleanup.main()

        self.assertEqual(exit_code, 0)
        mock_wt.assert_called_once_with(24, False)
        mock_tasks.assert_called_once_with(7, False)
        mock_logs.assert_called_once_with(30, False)
        mock_zombies.assert_called_once_with(False)

    @patch("hermes.cleanup.cleanup_zombie_tasks")
    @patch("hermes.cleanup.cleanup_old_logs")
    @patch("hermes.cleanup.cleanup_old_tasks")
    @patch("hermes.cleanup.cleanup_worktrees")
    @patch("hermes.cleanup.print_summary")
    @patch("sys.argv", ["cleanup.py", "--dry-run", "--max-age-days", "14"])
    def test_main_dry_run(self, mock_print, mock_wt, mock_tasks, mock_logs, mock_zombies):
        """Test main with --dry-run flag."""
        mock_wt.return_value = []
        mock_tasks.return_value = []
        mock_logs.return_value = []
        mock_zombies.return_value = []

        exit_code = cleanup.main()

        self.assertEqual(exit_code, 0)
        mock_wt.assert_called_once_with(24, True)
        mock_tasks.assert_called_once_with(14, True)
        mock_logs.assert_called_once_with(30, True)
        mock_zombies.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
