#!/usr/bin/env python3
"""Tests for pr_manager module using unittest.mock."""

import unittest
from unittest.mock import patch, Mock
import json
from pr_manager import (
    create_pr,
    check_ci,
    list_prs,
    merge_pr,
    PRManagerError,
    CLIError,
    ValidationError
)


class TestRunGHCommand(unittest.TestCase):
    """Tests for _run_gh_command internal function."""

    @patch('pr_manager.subprocess.run')
    def test_run_gh_command_success(self, mock_run):
        """Test successful gh command execution."""
        mock_run.return_value = Mock(
            stdout='{"key": "value"}',
            stderr="",
            returncode=0
        )

        from pr_manager import _run_gh_command
        result = _run_gh_command(["pr", "list"])

        self.assertEqual(result, {"key": "value"})
        mock_run.assert_called_once()

    @patch('pr_manager.subprocess.run')
    def test_run_gh_command_cli_error(self, mock_run):
        """Test gh CLI error handling."""
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "gh", stderr="gh: command not found"
        )

        from pr_manager import _run_gh_command

        with self.assertRaises(CLIError):
            _run_gh_command(["pr", "list"])

    @patch('pr_manager.subprocess.run')
    def test_run_gh_command_json_error(self, mock_run):
        """Test JSON parsing error handling."""
        mock_run.return_value = Mock(
            stdout="invalid json",
            stderr="",
            returncode=0
        )

        from pr_manager import _run_gh_command

        with self.assertRaises(CLIError):
            _run_gh_command(["pr", "list"])


class TestCreatePR(unittest.TestCase):
    """Tests for create_pr function."""

    @patch('pr_manager._run_gh_command')
    def test_create_pr_success(self, mock_run):
        """Test successful PR creation."""
        mock_run.return_value = {
            "number": 123,
            "title": "Test PR",
            "state": "open",
            "url": "https://github.com/test/repo/pull/123"
        }

        result = create_pr("Test PR", "This is a test", "main")

        self.assertEqual(result["number"], 123)
        self.assertEqual(result["title"], "Test PR")
        mock_run.assert_called_once()

    @patch('pr_manager._run_gh_command')
    def test_create_pr_with_options(self, mock_run):
        """Test PR creation with optional parameters."""
        mock_run.return_value = {"number": 456, "title": "Draft PR"}

        result = create_pr(
            title="Draft PR",
            body="Draft content",
            base="main",
            head="feature-branch",
            draft=True,
            repo="owner/repo"
        )

        self.assertEqual(result["number"], 456)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--draft", call_args)
        self.assertIn("feature-branch", call_args)

    def test_create_pr_empty_title(self):
        """Test PR creation with empty title."""
        with self.assertRaises(ValidationError) as context:
            create_pr("", "Body", "main")

        self.assertIn("title", str(context.exception).lower())

    def test_create_pr_empty_body(self):
        """Test PR creation with empty body."""
        with self.assertRaises(ValidationError) as context:
            create_pr("Title", "", "main")

        self.assertIn("body", str(context.exception).lower())

    def test_create_pr_empty_base(self):
        """Test PR creation with empty base branch."""
        with self.assertRaises(ValidationError) as context:
            create_pr("Title", "Body", "")

        self.assertIn("base", str(context.exception).lower())


class TestCheckCI(unittest.TestCase):
    """Tests for check_ci function."""

    @patch('pr_manager._run_gh_command')
    def test_check_ci_success(self, mock_run):
        """Test successful CI status check."""
        mock_run.return_value = [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
            {"name": "build", "status": "completed", "conclusion": "success"}
        ]

        result = check_ci(pr_number=123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_checks"], 3)
        self.assertEqual(result["completed_checks"], 3)
        self.assertEqual(result["failed_checks"], 0)

    @patch('pr_manager._run_gh_command')
    def test_check_ci_pending(self, mock_run):
        """Test CI check with pending status."""
        mock_run.return_value = [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "build", "status": "in_progress", "conclusion": None}
        ]

        result = check_ci(pr_number=456)

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["completed_checks"], 1)

    @patch('pr_manager._run_gh_command')
    def test_check_ci_failure(self, mock_run):
        """Test CI check with failed status."""
        mock_run.return_value = [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "failure"}
        ]

        result = check_ci()

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failed_checks"], 1)

    @patch('pr_manager._run_gh_command')
    def test_check_ci_no_checks(self, mock_run):
        """Test CI check with no checks."""
        mock_run.return_value = []

        result = check_ci(pr_number=789, repo="owner/repo")

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["total_checks"], 0)


class TestListPRs(unittest.TestCase):
    """Tests for list_prs function."""

    @patch('pr_manager._run_gh_command')
    def test_list_prs_success(self, mock_run):
        """Test successful PR listing."""
        mock_run.return_value = [
            {
                "number": 1,
                "title": "First PR",
                "state": "open",
                "author": {"login": "user1"}
            },
            {
                "number": 2,
                "title": "Second PR",
                "state": "open",
                "author": {"login": "user2"}
            }
        ]

        result = list_prs()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["number"], 1)
        self.assertEqual(result[1]["title"], "Second PR")

    @patch('pr_manager._run_gh_command')
    def test_list_prs_with_filters(self, mock_run):
        """Test PR listing with filters."""
        mock_run.return_value = [
            {"number": 100, "title": "Feature PR", "state": "open"}
        ]

        result = list_prs(
            state="closed",
            limit=10,
            head="feature",
            base="main",
            repo="owner/repo"
        )

        self.assertEqual(len(result), 1)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--state", call_args)
        self.assertIn("closed", call_args)

    def test_list_prs_invalid_state(self):
        """Test PR listing with invalid state."""
        with self.assertRaises(ValidationError) as context:
            list_prs(state="invalid")

        self.assertIn("invalid state", str(context.exception).lower())

    @patch('pr_manager._run_gh_command')
    def test_list_prs_all_states(self, mock_run):
        """Test PR listing with all valid states."""
        mock_run.return_value = []

        for state in ["open", "closed", "merged", "all"]:
            result = list_prs(state=state)
            self.assertIsInstance(result, list)


class TestMergePR(unittest.TestCase):
    """Tests for merge_pr function."""

    @patch('pr_manager._run_gh_command')
    def test_merge_pr_success(self, mock_run):
        """Test successful PR merge."""
        mock_run.return_value = {
            "merged": True,
            "mergedAt": "2024-01-01T00:00:00Z",
            "mergedBy": {"login": "user1"}
        }

        result = merge_pr(pr_number=123)

        self.assertTrue(result["merged"])
        mock_run.assert_called_once()

    @patch('pr_manager._run_gh_command')
    def test_merge_pr_with_options(self, mock_run):
        """Test PR merge with optional parameters."""
        mock_run.return_value = {"merged": True}

        result = merge_pr(
            pr_number=456,
            merge_method="squash",
            delete_branch=True,
            subject="Custom subject",
            body="Custom body",
            repo="owner/repo"
        )

        self.assertTrue(result["merged"])
        call_args = mock_run.call_args[0][0]
        self.assertIn("--delete-branch", call_args)
        self.assertIn("--subject", call_args)

    @patch('pr_manager._run_gh_command')
    def test_merge_pr_different_methods(self, mock_run):
        """Test PR merge with different merge methods."""
        mock_run.return_value = {"merged": True}

        for method in ["merge", "squash", "rebase"]:
            result = merge_pr(pr_number=1, merge_method=method)
            self.assertTrue(result["merged"])

    def test_merge_pr_invalid_method(self):
        """Test PR merge with invalid merge method."""
        with self.assertRaises(ValidationError) as context:
            merge_pr(pr_number=123, merge_method="invalid")

        self.assertIn("merge method", str(context.exception).lower())

    def test_merge_pr_invalid_number(self):
        """Test PR merge with invalid PR number."""
        with self.assertRaises(ValidationError) as context:
            merge_pr(pr_number=0)

        self.assertIn("must be positive", str(context.exception).lower())

        with self.assertRaises(ValidationError):
            merge_pr(pr_number=-1)


class TestExceptions(unittest.TestCase):
    """Tests for custom exceptions."""

    def test_pr_manager_error_hierarchy(self):
        """Test exception class hierarchy."""
        error = PRManagerError("test")
        self.assertIsInstance(error, Exception)

        cli_error = CLIError("cli error")
        self.assertIsInstance(cli_error, PRManagerError)

        validation_error = ValidationError("validation error")
        self.assertIsInstance(validation_error, PRManagerError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
