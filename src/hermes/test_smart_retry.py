"""Tests for smart_retry module."""

import unittest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, "src")

from hermes.smart_retry import analyze_failure, generate_retry_prompt, get_partial_progress


class TestAnalyzeFailure(unittest.TestCase):
    """Tests for failure analysis."""

    def test_timeout_detection(self):
        """Detects timeout failures."""
        task = {"exit_code": -1, "stderr_tail": "operation timed out after 300s"}
        result = analyze_failure(task)
        self.assertEqual(result["category"], "timeout")

    def test_timeout_by_exit_code(self):
        """Detects timeout by exit code."""
        task = {"exit_code": -1, "stderr_tail": "", "result": ""}
        result = analyze_failure(task)
        self.assertEqual(result["category"], "timeout")

    def test_test_failure(self):
        """Detects test failures."""
        task = {
            "exit_code": 1,
            "stderr_tail": "FAILED test_example.py::test_add - AssertionError: 1 != 2",
            "result": "pytest failed 3 tests",
        }
        result = analyze_failure(task)
        self.assertEqual(result["category"], "test_failure")
        self.assertIn("测试失败", result["cause"])

    def test_import_error(self):
        """Detects missing module."""
        task = {
            "exit_code": 1,
            "stderr_tail": "ModuleNotFoundError: No module named 'requests'",
            "result": "",
        }
        result = analyze_failure(task)
        self.assertEqual(result["category"], "import_error")
        self.assertIn("requests", result["cause"])

    def test_syntax_error(self):
        """Detects syntax errors."""
        task = {
            "exit_code": 1,
            "stderr_tail": "SyntaxError: invalid syntax on line 5",
            "result": "",
        }
        result = analyze_failure(task)
        self.assertEqual(result["category"], "syntax_error")

    def test_permission_error(self):
        """Detects permission errors."""
        task = {
            "exit_code": 1,
            "stderr_tail": "Permission denied: /etc/something",
            "result": "",
        }
        result = analyze_failure(task)
        self.assertEqual(result["category"], "permission_error")
        self.assertEqual(result["severity"], "high")

    def test_unknown_failure(self):
        """Handles unknown failures."""
        task = {
            "exit_code": 137,
            "stderr_tail": "some random error",
            "result": "",
        }
        result = analyze_failure(task)
        self.assertEqual(result["category"], "unknown")


class TestGetPartialProgress(unittest.TestCase):
    """Tests for partial progress detection."""

    def test_no_worktree(self):
        """Returns empty lists when no worktree."""
        result = get_partial_progress(None)
        self.assertEqual(result["files_written"], [])
        self.assertEqual(result["files_test"], [])

    def test_nonexistent_worktree(self):
        """Returns empty lists for nonexistent worktree."""
        result = get_partial_progress("/nonexistent/path")
        self.assertEqual(result["files_written"], [])
        self.assertEqual(result["files_test"], [])

    @patch("subprocess.run")
    def test_git_diff_detected(self, mock_run):
        """Detects files from git diff."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="src/module.py\ntest/test_module.py\nREADME.md\n",
            stderr="",
        )

        with patch("pathlib.Path.exists", return_value=True):
            result = get_partial_progress("/tmp/wt")
            self.assertIn("src/module.py", result["files_written"])
            self.assertIn("test/test_module.py", result["files_test"])
            self.assertIn("README.md", result["files_written"])


class TestGenerateRetryPrompt(unittest.TestCase):
    """Tests for retry prompt generation."""

    def test_timeout_retry_prompt(self):
        """Generates timeout-specific retry prompt."""
        task = {
            "exit_code": -1,
            "stderr_tail": "timed out",
            "result": "",
            "worktree": None,
        }
        prompt = generate_retry_prompt("Create a parser module", task, 1)
        self.assertIn("重试任务", prompt)
        self.assertIn("原始需求", prompt)
        self.assertIn("Create a parser module", prompt)
        self.assertIn("超时", prompt)
        self.assertIn("减少不必要的操作", prompt)

    def test_test_failure_retry_prompt(self):
        """Generates test-failure-specific retry prompt."""
        task = {
            "exit_code": 1,
            "stderr_tail": "FAILED test_calc.py::test_add",
            "result": "3 failed, 12 passed",
            "worktree": None,
        }
        prompt = generate_retry_prompt("Create calc.py", task, 2)
        self.assertIn("测试失败", prompt)
        self.assertIn("修复代码逻辑", prompt)

    def test_import_error_retry_prompt(self):
        """Generates import-error-specific retry prompt."""
        task = {
            "exit_code": 1,
            "stderr_tail": "No module named 'requests'",
            "result": "",
            "worktree": None,
        }
        prompt = generate_retry_prompt("Create http module", task, 0)
        self.assertIn("requests", prompt)
        self.assertIn("标准库", prompt)

    def test_unknown_failure_retry_prompt(self):
        """Generates generic retry prompt for unknown failures."""
        task = {
            "exit_code": 137,
            "stderr_tail": "",
            "result": "",
            "worktree": None,
        }
        prompt = generate_retry_prompt("Build something", task, 0)
        self.assertIn("原因不明", prompt)
        self.assertIn("重新理解需求", prompt)

    @patch("hermes.smart_retry.get_partial_progress")
    def test_includes_partial_progress(self, mock_progress):
        """Includes partial file progress in retry prompt."""
        mock_progress.return_value = {
            "files_written": ["module.py", "utils.py"],
            "files_test": ["test_module.py"],
        }
        task = {"exit_code": 1, "stderr_tail": "test failed", "result": "", "worktree": "/tmp/wt"}
        prompt = generate_retry_prompt("Create module", task, 1)
        self.assertIn("module.py", prompt)
        self.assertIn("test_module.py", prompt)


if __name__ == "__main__":
    unittest.main()
