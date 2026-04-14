#!/usr/bin/env python3
"""Comprehensive tests for reconciler.py — crash recovery, timeout, orphan cleanup."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reconciler import Reconciler
from task_registry import TaskRegistry


class BaseReconcilerTest(unittest.TestCase):
    """Base with temp DB + git repo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.repo_path = os.path.join(self.tmpdir, "repo")
        self.worktree_base = os.path.join(self.tmpdir, "worktrees")

        # Init git repo
        subprocess.run(["git", "init", self.repo_path], capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.repo_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo_path, capture_output=True,
        )
        readme = os.path.join(self.repo_path, "README.md")
        with open(readme, "w") as f:
            f.write("# test repo")
        subprocess.run(["git", "add", "."], cwd=self.repo_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_path, capture_output=True,
        )

        self.registry = TaskRegistry(self.db_path)
        self.reconciler = Reconciler(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestReconcileDeadPid(BaseReconcilerTest):
    """PID liveness detection tests."""

    @patch("reconciler.REPO_PATH")
    @patch("reconciler.WORKTREE_BASE")
    def test_dead_pid_marked_failed(self, mock_wt_base, mock_repo):
        """Task with dead PID should be marked as failed."""
        mock_repo = self.repo_path
        mock_wt_base = Path(self.worktree_base)
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", mock_wt_base):
            self.registry.create_task("t1", "desc", "claude-code")
            self.registry.transition_status("t1", "running", "pending")
            self.registry.update_task("t1", pid=999999999)

            result = self.reconciler.reconcile()
            self.assertIn("t1", result["fixed"])
            task = self.registry.get_task("t1")
            self.assertEqual(task["status"], "failed")
            self.assertIn("PID", task["stderr_tail"])

    @patch("reconciler.REPO_PATH")
    @patch("reconciler.WORKTREE_BASE")
    def test_no_running_tasks(self, mock_wt_base, mock_repo):
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            result = self.reconciler.reconcile()
            self.assertEqual(result["fixed"], [])
            self.assertEqual(result["orphaned"], [])


class TestReconcileTimeout(BaseReconcilerTest):
    """Timeout-based recovery tests."""

    @patch("reconciler.CLAUDE_TIMEOUT", 300)
    @patch("reconciler.CODEX_TIMEOUT", 180)
    def test_timed_out_task_recovered(self):
        """Task with started_at too long ago should be recovered."""
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            self.registry.create_task("t1", "desc", "claude-code")
            self.registry.transition_status("t1", "running", "pending")

            past = datetime.now(timezone.utc) - timedelta(seconds=1200)
            with self.registry._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET started_at = ? WHERE id = ?;",
                    (past.strftime("%Y-%m-%d %H:%M:%S"), "t1"),
                )

            result = self.reconciler.reconcile()
            self.assertIn("t1", result["fixed"])
            task = self.registry.get_task("t1")
            self.assertEqual(task["status"], "failed")
            self.assertIn("Timed out", task["stderr_tail"])


class TestReconcileWorktree(BaseReconcilerTest):
    """Worktree-related recovery tests."""

    def test_missing_worktree_marked_failed(self):
        """Task whose worktree dir is missing should be marked failed."""
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            self.registry.create_task("t1", "desc", "claude-code")
            self.registry.transition_status("t1", "running", "pending")
            self.registry.update_task("t1", worktree="/nonexistent/path", pid=None)

            result = self.reconciler.reconcile()

        self.assertIn("t1", result["fixed"])
        task = self.registry.get_task("t1")
        self.assertEqual(task["status"], "failed")
        self.assertIn("Worktree", task["stderr_tail"])


class TestReconcileOrphanWorktrees(BaseReconcilerTest):
    """Orphan worktree cleanup tests."""

    def test_orphan_worktree_detected(self):
        """Worktree directory not tracked by any task should be cleaned up."""
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            os.makedirs(self.worktree_base, exist_ok=True)
            orphan = os.path.join(self.worktree_base, "orphan-task")
            os.makedirs(orphan)

            result = self.reconciler.reconcile()

        self.assertIn(orphan, result["orphaned"])


class TestReconcileNoSelfPid(BaseReconcilerTest):
    """Ensure reconciler doesn't kill its own PID."""

    def test_own_pid_not_killed(self):
        """PID matching current process should not be marked dead."""
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            self.registry.create_task("t1", "desc", "claude-code")
            self.registry.transition_status("t1", "running", "pending")
            self.registry.update_task("t1", pid=os.getpid())

            result = self.reconciler.reconcile()

        self.assertNotIn("t1", result["fixed"])
        task = self.registry.get_task("t1")
        self.assertEqual(task["status"], "running")


class TestReconcileMultiple(BaseReconcilerTest):
    """Multiple tasks recovery."""

    def test_mixed_states(self):
        """Reconciler should handle mixed task states."""
        with patch("reconciler.REPO_PATH", self.repo_path), \
             patch("reconciler.WORKTREE_BASE", Path(self.worktree_base)):
            self.registry.create_task("t1", "desc1", "claude-code")
            self.registry.transition_status("t1", "running", "pending")
            self.registry.update_task("t1", pid=999999999)

            self.registry.create_task("t2", "desc2", "claude-code")
            self.registry.finish_task("t2", "done", exit_code=0)

            self.registry.create_task("t3", "desc3", "codex")
            self.registry.transition_status("t3", "running", "pending")
            self.registry.update_task("t3", pid=888888888)

            result = self.reconciler.reconcile()
            self.assertEqual(len(result["fixed"]), 2)
            self.assertIn("t1", result["fixed"])
            self.assertIn("t3", result["fixed"])

        self.assertEqual(self.registry.get_task("t2")["status"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
