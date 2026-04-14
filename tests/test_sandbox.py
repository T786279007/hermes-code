#!/usr/bin/env python3
"""Comprehensive tests for sandbox.py — prepare_runner_env, cleanup, isolation."""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import prepare_runner_env, cleanup_runner_env


class TestPrepareRunnerEnv(unittest.TestCase):
    """Tests for prepare_runner_env()."""

    def setUp(self):
        # Create a temp runner home to avoid polluting real env
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_creates_home_directory(self):
        env = prepare_runner_env("claude-code", "test-1")
        home = env["HOME"]
        self.assertTrue(os.path.isdir(home))

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_home_is_agent_task_specific(self):
        env = prepare_runner_env("claude-code", "task-abc")
        home = env["HOME"]
        self.assertIn("claude-code", home)
        self.assertIn("task-abc", home)

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_gitconfig_created(self):
        env = prepare_runner_env("claude-code", "test-git")
        gitconfig = env["GIT_CONFIG_GLOBAL"]
        self.assertTrue(os.path.isfile(gitconfig))
        content = Path(gitconfig).read_text()
        self.assertIn("Hermes Agent", content)
        self.assertIn("hermes@localhost", content)

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    @patch.dict(os.environ, {"HERMES_GITHUB_TOKEN": "test-token-123"})
    def test_git_askpass_set(self):
        env = prepare_runner_env("codex", "test-askpass")
        self.assertIn("GIT_ASKPASS", env)
        self.assertTrue(os.path.isfile(env["GIT_ASKPASS"]))

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_git_terminal_prompt_disabled(self):
        env = prepare_runner_env("claude-code", "test-prompt")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_home_env_var_is_set(self):
        env = prepare_runner_env("claude-code", "test-home")
        self.assertEqual(env["HOME"], env["GIT_CONFIG_GLOBAL"].rsplit("/", 1)[0])

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    @patch.dict(os.environ, {"HERMES_GITHUB_TOKEN": "test-token-123"})
    def test_token_file_created_with_token(self):
        env = prepare_runner_env("claude-code", "test-token")
        self.assertIn("HERMES_GITHUB_TOKEN_FILE", env)
        token_file = env["HERMES_GITHUB_TOKEN_FILE"]
        self.assertTrue(os.path.isfile(token_file))
        content = Path(token_file).read_text()
        self.assertEqual(content, "test-token-123")

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    @patch.dict(os.environ, {"HERMES_GITHUB_TOKEN": "test-token-123"})
    def test_token_file_permissions(self):
        """Token file should be 0600."""
        env = prepare_runner_env("claude-code", "test-perm")
        token_file = env["HERMES_GITHUB_TOKEN_FILE"]
        st = os.stat(token_file)
        perms = stat.S_IMODE(st.st_mode)
        self.assertEqual(perms, stat.S_IRUSR | stat.S_IWUSR)

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    @patch.dict(os.environ, {}, clear=False)
    def test_no_token_when_env_empty(self):
        """If HERMES_GITHUB_TOKEN is not set, should still work."""
        if "HERMES_GITHUB_TOKEN" in os.environ:
            del os.environ["HERMES_GITHUB_TOKEN"]
        env = prepare_runner_env("codex", "test-notoken")
        self.assertIn("HOME", env)

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    @patch.dict(os.environ, {"HERMES_GITHUB_TOKEN": "test-token-123"})
    def test_askpass_script_executable(self):
        env = prepare_runner_env("claude-code", "test-exec")
        askpass = env["GIT_ASKPASS"]
        self.assertTrue(os.access(askpass, os.X_OK))

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_reentrant_call(self):
        """Calling twice with same task_id should produce same path."""
        env1 = prepare_runner_env("claude-code", "test-re1")
        env2 = prepare_runner_env("claude-code", "test-re1")
        self.assertEqual(env1["HOME"], env2["HOME"])


class TestCleanupRunnerEnv(unittest.TestCase):
    """Tests for cleanup_runner_env()."""

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_cleanup_removes_directory(self):
        prepare_runner_env("claude-code", "cleanup-test")
        cleanup_runner_env("claude-code", "cleanup-test")
        # Verify directory is gone
        runner_home = Path(os.environ.get("SANDBOX_HOME", "/tmp")) / "claude-code" / "cleanup-test"
        # We patched RUNNER_HOME, so use the patch value
        import sandbox
        expected = sandbox.RUNNER_HOME / "claude-code" / "cleanup-test"
        self.assertFalse(expected.exists())

    @patch("sandbox.RUNNER_HOME", Path(tempfile.mkdtemp()))
    def test_cleanup_nonexistent_does_not_error(self):
        """Cleaning up a non-existent env should not raise."""
        cleanup_runner_env("claude-code", "nonexistent-task")


if __name__ == "__main__":
    unittest.main(verbosity=2)
