#!/usr/bin/env python3
"""Comprehensive tests for config.py — verify spec constants."""

import unittest
from pathlib import Path
import config


class TestConfigConstants(unittest.TestCase):
    """Verify config values match HERMES_SPEC.md."""

    def test_hermes_home(self):
        self.assertEqual(config.HERMES_HOME, Path("/home/txs/hermes-agent"))

    def test_worktree_base(self):
        self.assertEqual(config.WORKTREE_BASE, config.HERMES_HOME / "worktrees")

    def test_db_path(self):
        self.assertEqual(config.DB_PATH, config.HERMES_HOME / "tasks.db")

    def test_runner_home(self):
        self.assertEqual(config.RUNNER_HOME, config.HERMES_HOME / "runner_home")

    def test_log_dir(self):
        self.assertEqual(config.LOG_DIR, Path("/home/txs/hermes/logs"))

    def test_proxy(self):
        self.assertEqual(config.PROXY, "http://127.0.0.1:7897")

    def test_claude_timeout(self):
        self.assertEqual(config.CLAUDE_TIMEOUT, 300)

    def test_codex_timeout(self):
        self.assertEqual(config.CODEX_TIMEOUT, 180)

    def test_max_retries(self):
        self.assertEqual(config.MAX_RETRIES, 3)

    def test_retry_base_delay(self):
        self.assertEqual(config.RETRY_BASE_DELAY, 10.0)

    def test_retry_max_delay(self):
        self.assertEqual(config.RETRY_MAX_DELAY, 300.0)

    def test_circuit_breaker_threshold(self):
        self.assertEqual(config.CIRCUIT_BREAKER_THRESHOLD, 3)

    def test_circuit_breaker_reset(self):
        self.assertEqual(config.CIRCUIT_BREAKER_RESET, 300)

    def test_repo_path(self):
        self.assertEqual(config.REPO_PATH, "/tmp/hermes-repo")

    def test_worktree_base_is_path_object(self):
        self.assertIsInstance(config.WORKTREE_BASE, Path)

    def test_db_path_is_path_object(self):
        self.assertIsInstance(config.DB_PATH, Path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
