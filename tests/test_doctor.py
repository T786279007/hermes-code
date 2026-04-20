#!/usr/bin/env python3
"""Tests for doctor.py health check CLI tool."""

import json
import os
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# Import from parent directory
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
import doctor


class TestCheckClaudeCode(unittest.TestCase):
    """Tests for check_claude_code function."""

    @patch("doctor.shutil.which")
    def test_claude_found(self, mock_which):
        """Test when claude CLI is found."""
        mock_which.return_value = "/usr/bin/claude"
        result = doctor.check_claude_code()
        self.assertEqual(result.name, "claude_code")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("/usr/bin/claude", result.detail)

    @patch("doctor.shutil.which")
    def test_claude_not_found(self, mock_which):
        """Test when claude CLI is not found."""
        mock_which.return_value = None
        result = doctor.check_claude_code()
        self.assertEqual(result.name, "claude_code")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("not found", result.detail)


class TestCheckCodex(unittest.TestCase):
    """Tests for check_codex function."""

    @patch("doctor.shutil.which")
    def test_codex_found(self, mock_which):
        """Test when codex CLI is found."""
        mock_which.return_value = "/usr/local/bin/codex"
        result = doctor.check_codex()
        self.assertEqual(result.name, "codex")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("/usr/local/bin/codex", result.detail)

    @patch("doctor.shutil.which")
    def test_codex_not_found(self, mock_which):
        """Test when codex CLI is not found."""
        mock_which.return_value = None
        result = doctor.check_codex()
        self.assertEqual(result.name, "codex")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("not found", result.detail)


class TestCheckGit(unittest.TestCase):
    """Tests for check_git function."""

    @patch("doctor.shutil.which")
    @patch("doctor.subprocess.run")
    def test_git_found_and_configured(self, mock_run, mock_which):
        """Test when git is found and user.name is configured."""
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = Mock(stdout="John Doe\n", returncode=0)
        result = doctor.check_git()
        self.assertEqual(result.name, "git")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("John Doe", result.detail)

    @patch("doctor.shutil.which")
    def test_git_not_found(self, mock_which):
        """Test when git is not found."""
        mock_which.return_value = None
        result = doctor.check_git()
        self.assertEqual(result.name, "git")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("not found", result.detail)

    @patch("doctor.shutil.which")
    @patch("doctor.subprocess.run")
    def test_git_not_configured(self, mock_run, mock_which):
        """Test when git is found but user.name is not configured."""
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = Mock(stdout="\n", returncode=0)
        result = doctor.check_git()
        self.assertEqual(result.name, "git")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("not configured", result.detail)

    @patch("doctor.shutil.which")
    @patch("doctor.subprocess.run")
    def test_git_timeout(self, mock_run, mock_which):
        """Test when git config times out."""
        mock_which.return_value = "/usr/bin/git"
        mock_run.side_effect = subprocess.TimeoutExpired("git", 5)
        result = doctor.check_git()
        self.assertEqual(result.name, "git")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("timed out", result.detail)


class TestCheckDatabase(unittest.TestCase):
    """Tests for check_database function."""

    @patch("doctor.sqlite3.connect")
    def test_database_ok(self, mock_connect):
        """Test when database is accessible and valid."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ["ok"]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        result = doctor.check_database()
        self.assertEqual(result.name, "database")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("OK", result.detail)

    @patch("doctor.sqlite3.connect")
    def test_database_integrity_fail(self, mock_connect):
        """Test when database integrity check fails."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ["database disk image is malformed"]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        result = doctor.check_database()
        self.assertEqual(result.name, "database")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("Integrity check failed", result.detail)

    @patch("doctor.sqlite3.connect")
    def test_database_not_accessible(self, mock_connect):
        """Test when database cannot be opened."""
        mock_connect.side_effect = sqlite3.OperationalError("unable to open database file")
        result = doctor.check_database()
        self.assertEqual(result.name, "database")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("Cannot open database", result.detail)


class TestCheckProxy(unittest.TestCase):
    """Tests for check_proxy function."""

    @patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.example.com:8080"})
    @patch("doctor.urllib.request.build_opener")
    def test_proxy_ok(self, mock_build_opener):
        """Test when proxy is configured and works."""
        mock_response = MagicMock()
        mock_build_opener.return_value.open.return_value = mock_response
        result = doctor.check_proxy()
        self.assertEqual(result.name, "proxy")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("OK", result.detail)

    @patch.dict(os.environ, {}, clear=True)
    def test_no_proxy(self):
        """Test when no proxy is configured."""
        result = doctor.check_proxy()
        self.assertEqual(result.name, "proxy")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("No proxy configured", result.detail)

    @patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.example.com:8080"})
    @patch("doctor.urllib.request.build_opener")
    def test_proxy_connection_failed(self, mock_build_opener):
        """Test when proxy connection fails."""
        from urllib.error import URLError

        mock_build_opener.return_value.open.side_effect = URLError("Connection refused")
        result = doctor.check_proxy()
        self.assertEqual(result.name, "proxy")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("failed", result.detail)


class TestCheckDisk(unittest.TestCase):
    """Tests for check_disk function."""

    @patch("doctor.shutil.disk_usage")
    def test_disk_enough_space(self, mock_disk_usage):
        """Test when there's enough disk space."""
        mock_disk_usage.return_value = Mock(free=2 * 1024**3)  # 2 GB
        result = doctor.check_disk()
        self.assertEqual(result.name, "disk")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("2.00", result.detail)

    @patch("doctor.shutil.disk_usage")
    def test_disk_low_space(self, mock_disk_usage):
        """Test when disk space is low."""
        mock_disk_usage.return_value = Mock(free=500 * 1024**2)  # 500 MB
        result = doctor.check_disk()
        self.assertEqual(result.name, "disk")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("GB free", result.detail)
        self.assertIn("(< 1 GB)", result.detail)

    @patch("doctor.shutil.disk_usage")
    def test_disk_check_error(self, mock_disk_usage):
        """Test when disk usage check fails."""
        mock_disk_usage.side_effect = OSError("Permission denied")
        result = doctor.check_disk()
        self.assertEqual(result.name, "disk")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("Cannot check disk usage", result.detail)


class TestCheckGithub(unittest.TestCase):
    """Tests for check_github function."""

    @patch("doctor.subprocess.run")
    def test_github_authenticated(self, mock_run):
        """Test when GitHub CLI is authenticated."""
        mock_run.return_value = Mock(returncode=0)
        result = doctor.check_github()
        self.assertEqual(result.name, "github")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("authenticated", result.detail)

    @patch("doctor.subprocess.run")
    def test_github_not_authenticated(self, mock_run):
        """Test when GitHub CLI is not authenticated."""
        mock_run.return_value = Mock(returncode=1)
        result = doctor.check_github()
        self.assertEqual(result.name, "github")
        self.assertEqual(result.status, doctor.STATUS_FAIL)
        self.assertIn("not authenticated", result.detail)

    @patch("doctor.subprocess.run")
    def test_github_not_installed(self, mock_run):
        """Test when GitHub CLI is not installed."""
        mock_run.side_effect = FileNotFoundError()
        result = doctor.check_github()
        self.assertEqual(result.name, "github")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("not installed", result.detail)


class TestCheckFeishu(unittest.TestCase):
    """Tests for check_feishu function."""

    @patch.dict(os.environ, {"FEISHU_APP_ID": "app_id_123", "FEISHU_APP_SECRET": "secret_456"})
    def test_feishu_configured(self):
        """Test when Feishu credentials are configured."""
        result = doctor.check_feishu()
        self.assertEqual(result.name, "feishu")
        self.assertEqual(result.status, doctor.STATUS_PASS)
        self.assertIn("configured", result.detail)

    @patch.dict(os.environ, {"FEISHU_APP_ID": "app_id_123"}, clear=True)
    def test_feishu_partial_config(self):
        """Test when Feishu is partially configured."""
        result = doctor.check_feishu()
        self.assertEqual(result.name, "feishu")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("partially configured", result.detail)

    @patch.dict(os.environ, {}, clear=True)
    def test_feishu_not_configured(self):
        """Test when Feishu credentials are not configured."""
        result = doctor.check_feishu()
        self.assertEqual(result.name, "feishu")
        self.assertEqual(result.status, doctor.STATUS_WARN)
        self.assertIn("not configured", result.detail)


class TestRunAllChecks(unittest.TestCase):
    """Tests for run_all_checks function."""

    @patch("doctor.check_feishu")
    @patch("doctor.check_github")
    @patch("doctor.check_disk")
    @patch("doctor.check_proxy")
    @patch("doctor.check_database")
    @patch("doctor.check_git")
    @patch("doctor.check_codex")
    @patch("doctor.check_claude_code")
    def test_all_checks_run(
        self,
        mock_claude,
        mock_codex,
        mock_git,
        mock_db,
        mock_proxy,
        mock_disk,
        mock_github,
        mock_feishu,
    ):
        """Test that all checks are executed."""
        mock_claude.return_value = doctor.CheckResult("claude_code", doctor.STATUS_PASS, "OK")
        mock_codex.return_value = doctor.CheckResult("codex", doctor.STATUS_PASS, "OK")
        mock_git.return_value = doctor.CheckResult("git", doctor.STATUS_PASS, "OK")
        mock_db.return_value = doctor.CheckResult("database", doctor.STATUS_PASS, "OK")
        mock_proxy.return_value = doctor.CheckResult("proxy", doctor.STATUS_PASS, "OK")
        mock_disk.return_value = doctor.CheckResult("disk", doctor.STATUS_PASS, "OK")
        mock_github.return_value = doctor.CheckResult("github", doctor.STATUS_PASS, "OK")
        mock_feishu.return_value = doctor.CheckResult("feishu", doctor.STATUS_PASS, "OK")

        results = doctor.run_all_checks()

        self.assertEqual(len(results), 8)
        mock_claude.assert_called_once()
        mock_codex.assert_called_once()
        mock_git.assert_called_once()
        mock_db.assert_called_once()
        mock_proxy.assert_called_once()
        mock_disk.assert_called_once()
        mock_github.assert_called_once()
        mock_feishu.assert_called_once()


class TestFormatTable(unittest.TestCase):
    """Tests for format_table function."""

    def test_format_table_basic(self):
        """Test basic table formatting."""
        results = [
            doctor.CheckResult("test1", doctor.STATUS_PASS, "Detail 1"),
            doctor.CheckResult("test2", doctor.STATUS_WARN, "Detail 2"),
        ]
        output = doctor.format_table(results)

        self.assertIn("┌", output)
        self.assertIn("│", output)
        self.assertIn("└", output)
        self.assertIn("test1", output)
        self.assertIn("test2", output)
        self.assertIn("PASS", output)
        self.assertIn("WARN", output)


class TestFormatJson(unittest.TestCase):
    """Tests for format_json function."""

    def test_format_json_basic(self):
        """Test basic JSON formatting."""
        results = [
            doctor.CheckResult("test1", doctor.STATUS_PASS, "Detail 1"),
            doctor.CheckResult("test2", doctor.STATUS_WARN, "Detail 2"),
        ]
        output = doctor.format_json(results)
        data = json.loads(output)

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["check"], "test1")
        self.assertEqual(data[0]["status"], "PASS")
        self.assertEqual(data[0]["detail"], "Detail 1")
        self.assertEqual(data[1]["check"], "test2")
        self.assertEqual(data[1]["status"], "WARN")


class TestMainExitCodes(unittest.TestCase):
    """Tests for main function exit codes."""

    @patch("doctor.run_all_checks")
    @patch("doctor.format_table")
    @patch("sys.argv", ["doctor.py"])
    def test_exit_code_all_pass(self, mock_format_table, mock_run_checks):
        """Test exit code 0 when all checks pass."""
        mock_run_checks.return_value = [
            doctor.CheckResult("test", doctor.STATUS_PASS, "OK")
        ]
        with self.assertRaises(SystemExit) as cm:
            doctor.main()
        self.assertEqual(cm.exception.code, 0)

    @patch("doctor.run_all_checks")
    @patch("doctor.format_table")
    @patch("sys.argv", ["doctor.py"])
    def test_exit_code_warnings(self, mock_format_table, mock_run_checks):
        """Test exit code 1 when there are warnings."""
        mock_run_checks.return_value = [
            doctor.CheckResult("test", doctor.STATUS_WARN, "Warning")
        ]
        with self.assertRaises(SystemExit) as cm:
            doctor.main()
        self.assertEqual(cm.exception.code, 1)

    @patch("doctor.run_all_checks")
    @patch("doctor.format_table")
    @patch("sys.argv", ["doctor.py"])
    def test_exit_code_errors(self, mock_format_table, mock_run_checks):
        """Test exit code 2 when there are errors."""
        mock_run_checks.return_value = [
            doctor.CheckResult("test", doctor.STATUS_FAIL, "Error")
        ]
        with self.assertRaises(SystemExit) as cm:
            doctor.main()
        self.assertEqual(cm.exception.code, 2)

    @patch("doctor.run_all_checks")
    @patch("doctor.format_json")
    @patch("sys.argv", ["doctor.py", "--json"])
    @patch("sys.stdout", new_callable=MagicMock)
    def test_json_output_flag(self, mock_stdout, mock_format_json, mock_run_checks):
        """Test --json flag produces JSON output."""
        mock_run_checks.return_value = [doctor.CheckResult("test", doctor.STATUS_PASS, "OK")]
        mock_format_json.return_value = '{"check": "test"}'

        with self.assertRaises(SystemExit):
            doctor.main()

        mock_format_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
