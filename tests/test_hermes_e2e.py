#!/usr/bin/env python3
"""End-to-end integration test for complete Hermes task lifecycle.

Tests cover:
1. Create TaskRegistry with temp SQLite DB
2. Create TaskRouter, Outbox, Reconciler, TaskExecutor with mocked runners
3. Mock ClaudeRunner.run() and CodexRunner.run() to return successful results
4. Submit task via executor.submit() and verify it reaches status=done
5. Verify task has: correct agent, branch name, exit_code=0, non-empty result
6. Test failure scenario: mock runner returns exit_code=1, verify retry happens
7. Test permanent failure: mock returns permission denied, verify no retry
8. Test circuit breaker: trigger 3 failures, verify it opens
9. Verify outbox notification was attempted (check DB)
10. Test reconciler: create running task with dead PID, run reconcile(), verify recovered
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_runner import ClaudeRunner
from codex_runner import CodexRunner
from config import REPO_PATH, WORKTREE_BASE
from executor import TaskExecutor
from outbox import Outbox
from reconciler import Reconciler
from retry import CircuitBreaker
from router import TaskRouter
from task_registry import TaskRegistry


class MockClaudeRunner:
    """Mock ClaudeRunner that returns configurable results."""

    def __init__(self, exit_code: int = 0, stderr: str = "", failure_class: str = None,
                 stdout: str = "mock output"):
        self._exit_code = exit_code
        self._stderr = stderr
        self._failure_class = failure_class
        self._stdout = stdout
        self.call_count = 0
        self.last_task_id = None
        self.last_worktree = None

    def run(self, task_id: str, prompt: str, worktree: str, model: str = "claude-sonnet-4-6",
            on_spawn=None) -> dict:
        self.call_count += 1
        self.last_task_id = task_id
        self.last_worktree = worktree
        if on_spawn:
            on_spawn(12345)  # Mock PID
        result = {
            "exit_code": self._exit_code,
            "stdout": self._stdout,
            "stderr": self._stderr,
            "timed_out": False,
            "pid": 12345,
        }
        if self._failure_class:
            result["failure_class"] = self._failure_class
        return result


class MockCodexRunner:
    """Mock CodexRunner that returns configurable results."""

    def __init__(self, exit_code: int = 0, stderr: str = "", failure_class: str = None,
                 stdout: str = "mock codex output"):
        self._exit_code = exit_code
        self._stderr = stderr
        self._failure_class = failure_class
        self._stdout = stdout
        self.call_count = 0
        self.last_task_id = None

    def run(self, task_id: str, prompt: str, worktree: str, model: str = "gpt-5.4",
            reasoning: str = "high", on_spawn=None) -> dict:
        self.call_count += 1
        self.last_task_id = task_id
        if on_spawn:
            on_spawn(12346)  # Mock PID
        result = {
            "exit_code": self._exit_code,
            "stdout": self._stdout,
            "stderr": self._stderr,
            "timed_out": False,
            "pid": 12346,
        }
        if self._failure_class:
            result["failure_class"] = self._failure_class
        return result


class TestHermesE2E(unittest.TestCase):
    """End-to-end integration tests for Hermes task lifecycle."""

    def setUp(self):
        """Set up temporary environment with git repo and database."""
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.repo_path = os.path.join(self.tmpdir, "repo")
        self.worktree_base = os.path.join(self.tmpdir, "worktrees")

        # Initialize git repo
        subprocess.run(["git", "init", self.repo_path], capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo_path, capture_output=True, check=True,
        )
        readme = os.path.join(self.repo_path, "README.md")
        with open(readme, "w") as f:
            f.write("# test repo")
        subprocess.run(["git", "add", "."], cwd=self.repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_path, capture_output=True, check=True,
        )

        # Initialize components
        self.registry = TaskRegistry(self.db_path)
        self.router = TaskRouter()
        self.outbox = Outbox(self.registry)
        self.reconciler = Reconciler(self.registry)
        self.executor = TaskExecutor(self.registry, self.router, self.outbox, self.reconciler)

        # Mock config paths
        self.patcher_repo = patch('config.REPO_PATH', self.repo_path)
        self.patcher_worktree = patch('config.WORKTREE_BASE', Path(self.worktree_base))
        self.patcher_executor_repo = patch('executor.REPO_PATH', self.repo_path)
        self.patcher_executor_worktree = patch('executor.WORKTREE_BASE', Path(self.worktree_base))
        self.patcher_reconciler_repo = patch('reconciler.REPO_PATH', self.repo_path)
        self.patcher_reconciler_worktree = patch('reconciler.WORKTREE_BASE', Path(self.worktree_base))
        self.patcher_reconciler_timeout = patch('reconciler.RECONCILER_TIMEOUT', 600)

        self.patcher_repo.start()
        self.patcher_worktree.start()
        self.patcher_executor_repo.start()
        self.patcher_executor_worktree.start()
        self.patcher_reconciler_repo.start()
        self.patcher_reconciler_worktree.start()
        self.patcher_reconciler_timeout.start()

        # Mock worktree operations
        self.mock_worktrees = {}
        self._setup_worktree_mocks()

        # Mock delay/sleep to speed up tests
        self.patcher_sleep = patch('time.sleep', return_value=None)
        self.patcher_delay = patch('executor.compute_delay', return_value=0.0)
        self.patcher_sleep.start()
        self.patcher_delay.start()

    def tearDown(self):
        """Clean up temporary environment."""
        self.patcher_repo.stop()
        self.patcher_worktree.stop()
        self.patcher_executor_repo.stop()
        self.patcher_executor_worktree.stop()
        self.patcher_reconciler_repo.stop()
        self.patcher_reconciler_worktree.stop()
        self.patcher_reconciler_timeout.stop()
        self.patcher_sleep.stop()
        self.patcher_delay.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_worktree_mocks(self):
        """Set up mocks for worktree operations to avoid actual git worktree calls."""
        def mock_create_worktree(task_id, branch):
            wt_path = os.path.join(self.worktree_base, task_id)
            os.makedirs(wt_path, exist_ok=True)
            self.mock_worktrees[task_id] = wt_path
            return wt_path

        def mock_cleanup_worktree(task_id):
            wt_path = self.mock_worktrees.get(task_id)
            if wt_path and os.path.exists(wt_path):
                shutil.rmtree(wt_path, ignore_errors=True)
                self.mock_worktrees.pop(task_id, None)

        self.executor._create_worktree = mock_create_worktree
        self.executor._cleanup_worktree = mock_cleanup_worktree

    # Test 1: Successful task submission and execution
    def test_1_successful_task_submission(self):
        """Test: Create TaskRegistry with temp SQLite DB and submit successful task."""
        # Verify DB was created
        self.assertTrue(os.path.exists(self.db_path))

        # Mock ClaudeRunner to return success
        mock_runner = MockClaudeRunner(exit_code=0, stdout="Successfully implemented feature")
        self.executor.claude_runner = mock_runner

        # Submit task
        task = self.executor.submit("implement login feature")

        # Verify task reached status=done
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["agent"], "claude-code")
        self.assertEqual(task["exit_code"], 0)
        self.assertIsNotNone(task["result"])
        self.assertNotEqual(task["result"], "")
        self.assertTrue(task["branch"].startswith("hermes/feat-"))
        self.assertEqual(mock_runner.call_count, 1)

        print(f"✓ Test 1 passed: Task {task['id']} completed successfully")

    # Test 2: Verify task has correct properties
    def test_2_task_properties_verification(self):
        """Test: Verify task has correct agent, branch name, exit_code=0, non-empty result."""
        mock_runner = MockClaudeRunner(exit_code=0, stdout="Feature implemented with tests")
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("add user authentication")

        # Verify all expected properties
        self.assertEqual(task["agent"], "claude-code")
        self.assertTrue(task["branch"].startswith("hermes/feat-"))
        self.assertIn("add-user-authenti", task["branch"])
        self.assertEqual(task["exit_code"], 0)
        self.assertIsNotNone(task.get("result"))
        self.assertNotEqual(task.get("result"), "")
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task.get("failure_class"))

        print(f"✓ Test 2 passed: Task properties verified for {task['id']}")

    # Test 3: Failure scenario with retry
    def test_3_failure_with_retry(self):
        """Test: Mock runner returns exit_code=1, verify retry happens."""
        call_count = [0]

        def flaky_runner(task_id, prompt, worktree, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call fails
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "connection timeout",
                    "timed_out": False,
                    "pid": 12345,
                }
            else:
                # Second call succeeds
                return {
                    "exit_code": 0,
                    "stdout": "success on retry",
                    "stderr": "",
                    "timed_out": False,
                    "pid": 12345,
                }

        mock_runner = MagicMock()
        mock_runner.run = flaky_runner
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("implement api endpoint")

        # Verify retry happened and task succeeded
        self.assertEqual(task["status"], "done")
        self.assertEqual(call_count[0], 2)  # Initial attempt + 1 retry
        self.assertEqual(task["attempt"], 1)

        print(f"✓ Test 3 passed: Retry happened, task completed after {call_count[0]} attempts")

    # Test 4: Permanent failure (permission denied) - no retry
    def test_4_permanent_failure_no_retry(self):
        """Test: Mock returns permission denied, verify no retry happens."""
        mock_runner = MockClaudeRunner(
            exit_code=1,
            stderr="permission denied: cannot access repository",
            failure_class="permanent"
        )
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("attempt unauthorized operation")

        # Verify permanent failure
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["failure_class"], "permanent")
        self.assertEqual(mock_runner.call_count, 1)  # Only one attempt, no retries
        self.assertEqual(task["attempt"], 0)

        print(f"✓ Test 4 passed: Permanent failure detected, no retry occurred")

    # Test 5: Circuit breaker opens after 3 failures
    def test_5_circuit_breaker_opens(self):
        """Test: Trigger 3 failures, verify circuit breaker opens."""
        # Reset circuit breaker
        self.executor.circuit_breaker = CircuitBreaker(threshold=3, reset_seconds=300)

        mock_runner = MockClaudeRunner(
            exit_code=1,
            stderr="connection timeout",
        )
        self.executor.claude_runner = mock_runner

        # Submit first failing task - will exhaust all retries (3 attempts)
        task1 = self.executor.submit("failing task 1")
        self.assertEqual(task1["status"], "failed")

        # Submit second failing task
        task2 = self.executor.submit("failing task 2")
        self.assertEqual(task2["status"], "failed")

        # Submit third failing task
        task3 = self.executor.submit("failing task 3")
        self.assertEqual(task3["status"], "failed")

        # Verify circuit breaker is now open
        self.assertTrue(self.executor.circuit_breaker.is_open("claude-code"))

        # Next task should fail immediately without calling the runner
        mock_runner_success = MockClaudeRunner(exit_code=0)
        initial_count = mock_runner_success.call_count
        self.executor.claude_runner = mock_runner_success

        task4 = self.executor.submit("this should not run")
        self.assertEqual(task4["status"], "failed")
        self.assertIn("Circuit breaker", task4.get("stderr_tail", ""))
        self.assertEqual(mock_runner_success.call_count, initial_count)  # Runner not called

        print(f"✓ Test 5 passed: Circuit breaker opened after threshold failures")

    # Test 6: Verify outbox notification was attempted (check DB)
    def test_6_outbox_notification_attempted(self):
        """Test: Verify outbox notification was attempted by checking DB."""
        mock_runner = MockClaudeRunner(exit_code=0, stdout="Task completed successfully")
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("implement feature")

        # Check DB for outbox entry
        with self.registry._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE task_id = ? AND action = 'notify_done';",
                (task["id"],),
            ).fetchall()

        self.assertEqual(len(rows), 1)
        outbox_entry = dict(rows[0])
        self.assertEqual(outbox_entry["task_id"], task["id"])
        self.assertEqual(outbox_entry["action"], "notify_done")
        self.assertIn("completed successfully", outbox_entry["payload"])

        print(f"✓ Test 6 passed: Outbox notification found in DB for {task['id']}")

    # Test 7: Outbox notification on failure
    def test_7_outbox_notification_on_failure(self):
        """Test: Verify outbox notification is sent on task failure."""
        mock_runner = MockClaudeRunner(
            exit_code=1,
            stderr="authentication failed",
            failure_class="permanent"
        )
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("failing task")

        # Check DB for failure notification
        with self.registry._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE task_id = ? AND action = 'notify_failed';",
                (task["id"],),
            ).fetchall()

        self.assertEqual(len(rows), 1)
        outbox_entry = dict(rows[0])
        self.assertEqual(outbox_entry["task_id"], task["id"])
        self.assertEqual(outbox_entry["action"], "notify_failed")
        self.assertIn("failed", outbox_entry["payload"])

        print(f"✓ Test 7 passed: Failure notification found in DB for {task['id']}")

    # Test 8: Reconciler recovers running task with dead PID
    def test_8_reconciler_recovers_dead_pid_task(self):
        """Test: Create running task with dead PID, run reconcile(), verify it is recovered."""
        # Create a task in 'running' state with a non-existent PID
        task_id = "feat-reconciler-test-20240101-120000"
        self.registry.create_task(
            task_id=task_id,
            description="test task for reconciler",
            agent="claude-code",
            status="running",
            branch=f"hermes/{task_id}",
        )
        self.registry.update_task(task_id, pid=999999999)  # Non-existent PID

        # Run reconciler
        result = self.reconciler.reconcile()

        # Verify task was recovered
        self.assertIn(task_id, result["fixed"])
        recovered_task = self.registry.get_task(task_id)
        self.assertEqual(recovered_task["status"], "failed")
        self.assertIn("PID", recovered_task.get("stderr_tail", ""))

        print(f"✓ Test 8 passed: Reconciler recovered task {task_id} with dead PID")

    # Test 9: Reconciler handles timeout-based recovery
    def test_9_reconciler_timeout_recovery(self):
        """Test: Reconciler recovers task that has been running too long."""
        task_id = "feat-timeout-test-20240101-120000"
        self.registry.create_task(
            task_id=task_id,
            description="test task for timeout",
            agent="claude-code",
            status="running",
            branch=f"hermes/{task_id}",
        )

        # Set started_at to 20 minutes ago (exceeds timeout)
        past = datetime.now(timezone.utc) - timedelta(seconds=1200)
        with self.registry._connect() as conn:
            conn.execute(
                "UPDATE tasks SET started_at = ? WHERE id = ?;",
                (past.strftime("%Y-%m-%d %H:%M:%S"), task_id),
            )

        # Run reconciler
        result = self.reconciler.reconcile()

        # Verify task was recovered
        self.assertIn(task_id, result["fixed"])
        recovered_task = self.registry.get_task(task_id)
        self.assertEqual(recovered_task["status"], "failed")
        self.assertIn("Timed out", recovered_task.get("stderr_tail", ""))

        print(f"✓ Test 9 passed: Reconciler recovered timed-out task {task_id}")

    # Test 10: Codex runner execution
    def test_10_codex_runner_execution(self):
        """Test: Verify CodexRunner works correctly with executor."""
        mock_codex = MockCodexRunner(
            exit_code=0,
            stdout="Code review completed"
        )
        self.executor.codex_runner = mock_codex

        # Override to use codex
        task = self.executor.submit("review this code", override="codex")

        self.assertEqual(task["status"], "done")
        self.assertEqual(task["agent"], "codex")
        self.assertEqual(mock_codex.call_count, 1)

        print(f"✓ Test 10 passed: Codex runner executed successfully for {task['id']}")

    # Test 11: Worktree kept on success
    def test_11_worktree_kept_on_success(self):
        """Test: Verify worktree is kept after successful task completion."""
        mock_runner = MockClaudeRunner(exit_code=0)
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("implement feature")

        # Verify worktree still exists
        worktree_path = self.mock_worktrees.get(task["id"])
        self.assertIsNotNone(worktree_path)
        self.assertTrue(os.path.exists(worktree_path))

        print(f"✓ Test 11 passed: Worktree kept for successful task {task['id']}")

    # Test 12: Worktree cleaned up on failure
    def test_12_worktree_cleanup_on_failure(self):
        """Test: Verify worktree is cleaned up after permanent failure."""
        mock_runner = MockClaudeRunner(
            exit_code=1,
            stderr="authentication failed",
            failure_class="permanent"
        )
        self.executor.claude_runner = mock_runner

        task = self.executor.submit("failing task")

        # Verify worktree was cleaned up
        worktree_path = self.mock_worktrees.get(task["id"])
        if worktree_path:
            # Worktree might have been cleaned up
            self.assertFalse(os.path.exists(worktree_path))

        print(f"✓ Test 12 passed: Worktree cleaned up for failed task {task['id']}")


if __name__ == "__main__":
    # Run tests with verbose output
    suite = unittest.TestLoader().loadTestsFromTestCase(TestHermesE2E)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
