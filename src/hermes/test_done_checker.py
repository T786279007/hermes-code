"""Tests for done_checker module."""

import json
import subprocess
import unittest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, "src")

from hermes.done_checker import run_done_checks


class TestRunDoneChecks(unittest.TestCase):
    """Tests for the run_done_checks function."""

    def test_basic_task_with_no_worktree(self):
        """Task with no worktree returns commit=False."""
        task = {
            "id": "test-1",
            "status": "done",
            "worktree": "",
            "branch": "",
        }
        result = run_done_checks(task)
        self.assertFalse(result["commit"])
        self.assertTrue(result["tests_passed"])
        self.assertFalse(result["pr_created"])
        # No worktree = commit missing, but PR not created = all_passed
        # commit is required, so this should be False
        self.assertFalse(result["all_passed"])

    @patch("subprocess.run")
    def test_commit_exists(self, mock_run):
        """Detects existing commit in worktree."""
        task = {
            "id": "test-2",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "",
        }

        # git log returns a commit
        mock_run.return_value = MagicMock(
            returncode=0, stdout="abc123 feat: something\n", stderr=""
        )

        result = run_done_checks(task)
        self.assertTrue(result["commit"])
        self.assertIn("Commit exists", result["details"][0])

    @patch("subprocess.run")
    def test_no_commit(self, mock_run):
        """Detects missing commit."""
        task = {
            "id": "test-3",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "",
        }

        mock_run.return_value = MagicMock(
            returncode=0, stdout="\n", stderr=""
        )

        result = run_done_checks(task)
        self.assertFalse(result["commit"])

    @patch("subprocess.run")
    def test_pr_created(self, mock_run):
        """Detects PR creation."""
        task = {
            "id": "test-4",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "hermes/feat-test-123",
        }

        pr_response = json.dumps([{
            "number": 42,
            "title": "Add feature",
            "state": "OPEN",
            "url": "https://github.com/owner/repo/pull/42"
        }])

        # First call: git log, Second: gh pr list
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123 commit\n", stderr=""),
            MagicMock(returncode=0, stdout=pr_response, stderr=""),
        ]

        result = run_done_checks(task)
        self.assertTrue(result["pr_created"])
        self.assertEqual(result["pr_number"], 42)

    @patch("subprocess.run")
    def test_no_pr(self, mock_run):
        """Handles no PR found."""
        task = {
            "id": "test-5",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "hermes/feat-test-123",
        }

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123 commit\n", stderr=""),
            MagicMock(returncode=0, stdout="[]", stderr=""),
        ]

        result = run_done_checks(task)
        self.assertFalse(result["pr_created"])
        self.assertTrue(result["all_passed"])  # PR is optional

    @patch("subprocess.run")
    def test_ci_passed(self, mock_run):
        """Detects CI passing."""
        task = {
            "id": "test-6",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "hermes/feat-test-123",
        }

        pr_response = json.dumps([{"number": 42, "title": "Add feature", "state": "OPEN"}])
        ci_response = json.dumps([
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ])
        view_response = json.dumps({"body": "Some changes\n![screenshot](url)"})

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
            MagicMock(returncode=0, stdout=pr_response, stderr=""),
            MagicMock(returncode=0, stdout=ci_response, stderr=""),
            MagicMock(returncode=0, stdout=view_response, stderr=""),
        ]

        result = run_done_checks(task)
        self.assertTrue(result["ci_passed"])
        self.assertTrue(result["screenshot_included"])

    @patch("subprocess.run")
    def test_ci_failed(self, mock_run):
        """Detects CI failure."""
        task = {
            "id": "test-7",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "hermes/feat-test-123",
        }

        pr_response = json.dumps([{"number": 42, "title": "Add feature", "state": "OPEN"}])
        ci_response = json.dumps([
            {"name": "test", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ])
        view_response = json.dumps({"body": "Changes without any visual proof"})

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
            MagicMock(returncode=0, stdout=pr_response, stderr=""),
            MagicMock(returncode=0, stdout=ci_response, stderr=""),
            MagicMock(returncode=0, stdout=view_response, stderr=""),
        ]

        result = run_done_checks(task)
        self.assertFalse(result["ci_passed"])
        self.assertFalse(result["screenshot_included"])  # No screenshot keywords
        self.assertFalse(result["all_passed"])

    @patch("subprocess.run")
    def test_ci_not_configured(self, mock_run):
        """Handles CI not being configured (no checks)."""
        task = {
            "id": "test-8",
            "status": "done",
            "worktree": "/tmp/test-wt",
            "branch": "hermes/feat-test-123",
        }

        pr_response = json.dumps([{"number": 42, "title": "Add feature", "state": "OPEN"}])
        ci_response = json.dumps([])

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
            MagicMock(returncode=0, stdout=pr_response, stderr=""),
            MagicMock(returncode=0, stdout=ci_response, stderr=""),
        ]

        result = run_done_checks(task)
        self.assertIsNone(result["ci_passed"])
        self.assertTrue(result["all_passed"])  # CI not configured = pass


if __name__ == "__main__":
    unittest.main()
