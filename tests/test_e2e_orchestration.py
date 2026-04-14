"""End-to-end orchestration tests for Hermes v2 QA Agent.

Tests cover:
- Task submission, execution, and completion
- Retry logic with failure handling
- Circuit breaker functionality
- Outbox notifications
- Reconciler crash recovery
- Worktree management
- Auto-commit functionality
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure PYTHONPATH includes /home/txs for imports
import sys
sys.path.insert(0, '/home/txs')

from hermes.executor import TaskExecutor
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.retry import CircuitBreaker, classify_failure
from hermes.router import TaskRouter
from hermes.task_registry import TaskRegistry


# ---------------------------------------------------------------------------
# Mock Claude Runner (following pattern from test_full_lifecycle.py)
# ---------------------------------------------------------------------------

class MockClaudeRunner:
    """Stub ClaudeRunner that returns configurable results."""

    def __init__(self, exit_code: int = 0, stderr: str = "", failure_class: str = None):
        self._exit_code = exit_code
        self._stderr = stderr
        self._failure_class = failure_class
        self.call_count = 0
        self.last_task_id = None
        self.last_worktree = None

    def run(self, task_id: str, prompt: str, worktree: str, model: str = "claude-sonnet-4-6", on_spawn=None) -> dict:
        self.call_count += 1
        self.last_task_id = task_id
        self.last_worktree = worktree
        return {
            "exit_code": self._exit_code,
            "stdout": "mock output",
            "stderr": self._stderr,
            "timed_out": False,
        }


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True, check=True)

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)

    return repo_path


@pytest.fixture
def worktree_base(tmp_path):
    """Create a temporary worktree base directory."""
    worktree_base = tmp_path / "worktrees"
    worktree_base.mkdir()
    return worktree_base


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary SQLite database path."""
    return tmp_path / "test.db"


@pytest.fixture
def registry(db_path):
    """Provide a TaskRegistry backed by a temporary DB."""
    return TaskRegistry(db_path)


@pytest.fixture
def router():
    """Provide a TaskRouter."""
    return TaskRouter()


@pytest.fixture
def outbox(registry):
    """Provide an Outbox (feishu sending will be mocked)."""
    return Outbox(registry)


@pytest.fixture
def reconciler(registry):
    """Provide a Reconciler."""
    return Reconciler(registry)


@pytest.fixture
def executor(registry, router, outbox, reconciler, temp_git_repo, worktree_base, monkeypatch):
    """Provide a TaskExecutor with real components but mockable runners."""
    # Mock the config paths to use temp directories
    import hermes.config
    import hermes.executor

    monkeypatch.setattr(hermes.config, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.config, "WORKTREE_BASE", worktree_base)
    monkeypatch.setattr(hermes.executor, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.executor, "WORKTREE_BASE", worktree_base)

    executor_instance = TaskExecutor(registry, router, outbox, reconciler)
    return executor_instance


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _mock_worktree(monkeypatch, tmp_path, executor):
    """Patch _create_worktree and _cleanup_worktree to use tmp_path."""
    worktrees = tmp_path / "mock_worktrees"
    worktrees.mkdir(exist_ok=True)

    def fake_create(task_id, branch):
        wt = worktrees / task_id
        wt.mkdir(exist_ok=True)
        return str(wt)

    def fake_cleanup(task_id):
        wt = worktrees / task_id
        if wt.exists():
            wt.rmdir()

    monkeypatch.setattr(executor, "_create_worktree", fake_create)
    monkeypatch.setattr(executor, "_cleanup_worktree", fake_cleanup)


def _mock_outbox_send(monkeypatch, outbox):
    """Patch outbox to track sends without calling openclaw."""
    sends = []

    def fake_send(task_id, action, payload):
        sends.append({"task_id": task_id, "action": action, "payload": payload})
        return "mock-external-id"

    monkeypatch.setattr(outbox, "send_notification", fake_send)
    return sends


# ---------------------------------------------------------------------------
# Test Case 1: Submit and Execute Success
# ---------------------------------------------------------------------------

def test_submit_and_execute_success(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Happy path: submit -> executor -> mock runner success -> done."""
    mock_runner = MockClaudeRunner(exit_code=0)
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "done"
    assert task["agent"] == "claude-code"
    assert mock_runner.call_count == 1
    assert len(sends) == 1
    assert sends[0]["action"] == "notify_done"
    assert "completed successfully" in sends[0]["payload"]["message"]


# ---------------------------------------------------------------------------
# Test Case 2: Submit and Execute Failure then Retry
# ---------------------------------------------------------------------------

def test_submit_and_execute_failure_then_retry(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """First attempt fails -> retry -> second attempt succeeds -> done."""
    call_count = 0

    def flaky_runner(task_id, prompt, worktree, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "connection timeout",
                "timed_out": False,
            }
        return {
            "exit_code": 0,
            "stdout": "success",
            "stderr": "",
            "timed_out": False,
        }

    mock_runner = MagicMock()
    mock_runner.run = flaky_runner
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)

    # Patch compute_delay to avoid sleeping in tests
    monkeypatch.setattr("hermes.executor.compute_delay", lambda n: 0.0)
    monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "done"
    assert call_count == 2
    assert len(sends) == 1
    assert sends[0]["action"] == "notify_done"


# ---------------------------------------------------------------------------
# Test Case 3: Permanent Failure No Retry
# ---------------------------------------------------------------------------

def test_permanent_failure_no_retry(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Permanent error should fail immediately without retrying."""
    mock_runner = MockClaudeRunner(exit_code=1, stderr="authentication failed")
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)
    monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "failed"
    assert task["failure_class"] == "permanent"
    assert mock_runner.call_count == 1  # Only one attempt, no retry
    assert len(sends) == 1
    assert sends[0]["action"] == "notify_failed"


# ---------------------------------------------------------------------------
# Test Case 4: Circuit Breaker Opens
# ---------------------------------------------------------------------------

def test_circuit_breaker_opens(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """After 3 consecutive failures, circuit breaker should open and skip execution."""
    mock_runner = MockClaudeRunner(exit_code=1, stderr="connection timeout")
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)
    monkeypatch.setattr("hermes.executor.compute_delay", lambda n: 0.0)
    monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

    # Submit a task that will exhaust all retries (3 retries + 1 initial = 4 calls max)
    task = executor.submit("implement login feature", override="claude-code")
    assert task["status"] == "failed"
    total_calls_first = mock_runner.call_count  # Should be max_attempts + 1 = 4

    # Now submit another task — circuit breaker should be open
    mock_runner2 = MockClaudeRunner(exit_code=0)
    monkeypatch.setattr(executor, "claude_runner", mock_runner2)

    task2 = executor.submit("another task", override="claude-code")
    assert task2["status"] == "failed"
    assert mock_runner2.call_count == 0  # Should not have been called
    assert len(sends) == 2


# ---------------------------------------------------------------------------
# Test Case 5: Outbox Notification on Success
# ---------------------------------------------------------------------------

def test_outbox_notification_on_success(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Successful task should trigger outbox notification with correct action."""
    mock_runner = MockClaudeRunner(exit_code=0)
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "done"
    assert len(sends) == 1
    assert sends[0]["action"] == "notify_done"
    assert sends[0]["task_id"] == task["id"]
    assert "completed successfully" in sends[0]["payload"]["message"]


# ---------------------------------------------------------------------------
# Test Case 6: Outbox Notification on Failure
# ---------------------------------------------------------------------------

def test_outbox_notification_on_failure(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Failed task should trigger outbox notification with correct action."""
    mock_runner = MockClaudeRunner(exit_code=1, stderr="authentication failed")
    _mock_worktree(monkeypatch, tmp_path, executor)
    sends = _mock_outbox_send(monkeypatch, outbox)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)
    monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "failed"
    assert len(sends) == 1
    assert sends[0]["action"] == "notify_failed"
    assert sends[0]["task_id"] == task["id"]
    assert "failed" in sends[0]["payload"]["message"]


# ---------------------------------------------------------------------------
# Test Case 7: Reconciler Recovers Running Task
# ---------------------------------------------------------------------------

def test_reconciler_recovers_running_task(registry, reconciler, temp_git_repo, worktree_base, monkeypatch):
    """Simulate crash -> reconciler detects -> marks as failed."""
    # Mock the config paths
    import hermes.config
    import hermes.reconciler

    monkeypatch.setattr(hermes.config, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.config, "WORKTREE_BASE", worktree_base)
    monkeypatch.setattr(hermes.reconciler, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.reconciler, "WORKTREE_BASE", worktree_base)

    # Create a task in 'running' state with a fake PID
    task_id = "feat-test-task-20240101-120000"
    task = registry.create_task(
        task_id=task_id,
        description="test task",
        agent="claude-code",
        branch=f"hermes/{task_id}",
        status="running"
    )

    # Set a fake PID that doesn't exist (simulating crash)
    fake_pid = 99999
    registry.update_task(task_id, pid=fake_pid)

    # Run reconciler
    result = reconciler.reconcile()

    # Task should be marked as failed
    recovered_task = registry.get_task(task_id)
    assert recovered_task["status"] == "failed"
    assert "PID" in recovered_task.get("stderr_tail", "")
    assert task_id in result["fixed"]


# ---------------------------------------------------------------------------
# Test Case 8: Worktree Cleanup on Failure
# ---------------------------------------------------------------------------

def test_worktree_cleanup_on_failure(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Failed task should clean up worktree directory."""
    worktrees = tmp_path / "test_worktrees"
    worktrees.mkdir(exist_ok=True)

    worktree_created = []
    worktree_cleaned = []

    def tracking_create(task_id, branch):
        wt = worktrees / task_id
        wt.mkdir(exist_ok=True)
        worktree_created.append(str(wt))
        return str(wt)

    def tracking_cleanup(task_id):
        wt = worktrees / task_id
        if wt.exists():
            worktree_cleaned.append(str(wt))
            wt.rmdir()

    monkeypatch.setattr(executor, "_create_worktree", tracking_create)
    monkeypatch.setattr(executor, "_cleanup_worktree", tracking_cleanup)

    mock_runner = MockClaudeRunner(exit_code=1, stderr="authentication failed")
    monkeypatch.setattr(executor, "claude_runner", mock_runner)
    monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

    sends = _mock_outbox_send(monkeypatch, outbox)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "failed"
    assert len(worktree_created) == 1
    assert len(worktree_cleaned) == 1
    assert worktree_created[0] == worktree_cleaned[0]


# ---------------------------------------------------------------------------
# Test Case 9: Worktree Kept on Success
# ---------------------------------------------------------------------------

def test_worktree_kept_on_success(executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
    """Successful task should keep worktree for inspection."""
    worktrees = tmp_path / "test_worktrees"
    worktrees.mkdir(exist_ok=True)

    worktree_created = []
    worktree_cleaned = []

    def tracking_create(task_id, branch):
        wt = worktrees / task_id
        wt.mkdir(exist_ok=True)
        worktree_created.append(str(wt))
        return str(wt)

    def tracking_cleanup(task_id):
        wt = worktrees / task_id
        if wt.exists():
            worktree_cleaned.append(str(wt))
            wt.rmdir()

    monkeypatch.setattr(executor, "_create_worktree", tracking_create)
    monkeypatch.setattr(executor, "_cleanup_worktree", tracking_cleanup)

    mock_runner = MockClaudeRunner(exit_code=0)
    monkeypatch.setattr(executor, "claude_runner", mock_runner)

    sends = _mock_outbox_send(monkeypatch, outbox)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "done"
    assert len(worktree_created) == 1
    assert len(worktree_cleaned) == 0  # Should NOT be cleaned up on success


# ---------------------------------------------------------------------------
# Test Case 10: Auto Commit on Success
# ---------------------------------------------------------------------------

def test_auto_commit_on_success(executor, registry, router, outbox, reconciler, temp_git_repo, worktree_base, monkeypatch):
    """Successful task should create git commit via _ensure_commit."""
    # Mock the config paths
    import hermes.config
    import hermes.executor

    monkeypatch.setattr(hermes.config, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.config, "WORKTREE_BASE", worktree_base)
    monkeypatch.setattr(hermes.executor, "REPO_PATH", temp_git_repo)
    monkeypatch.setattr(hermes.executor, "WORKTREE_BASE", worktree_base)

    # Track _ensure_commit calls
    ensure_commit_called = []
    original_ensure_commit = executor._ensure_commit

    def tracking_ensure_commit(worktree, task_id):
        ensure_commit_called.append((worktree, task_id))
        # Simulate creating a commit in the worktree
        subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, check=False)
        subprocess.run(
            ["git", "commit", "-m", f"feat: {task_id} (auto-committed by Hermes)"],
            cwd=worktree,
            capture_output=True,
            check=False
        )

    monkeypatch.setattr(executor, "_ensure_commit", tracking_ensure_commit)

    # Mock the runner to succeed and create a file (simulating work)
    def successful_runner_with_file(task_id, prompt, worktree, **kwargs):
        # Create a file to simulate work done
        test_file = Path(worktree) / "test_feature.py"
        test_file.write_text("# Feature implementation")
        return {
            "exit_code": 0,
            "stdout": "feature implemented",
            "stderr": "",
            "timed_out": False,
        }

    mock_runner = MagicMock()
    mock_runner.run = successful_runner_with_file
    monkeypatch.setattr(executor, "claude_runner", mock_runner)

    sends = _mock_outbox_send(monkeypatch, outbox)

    task = executor.submit("implement login feature", override="claude-code")

    assert task["status"] == "done"
    assert len(ensure_commit_called) == 1

    # Verify commit was created in worktree
    worktree_path = ensure_commit_called[0][0]
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False
    )
    assert "auto-committed by Hermes" in result.stdout


# ---------------------------------------------------------------------------
# Additional Helper Tests
# ---------------------------------------------------------------------------

def test_classify_failure():
    """Test failure classification logic."""
    # Permanent failures
    assert classify_failure(1, "authentication failed").value == "permanent"
    assert classify_failure(1, "permission denied").value == "permanent"
    assert classify_failure(1, "invalid api key").value == "permanent"

    # Retryable failures
    assert classify_failure(1, "connection timeout").value == "retryable"
    assert classify_failure(1, "timed out").value == "retryable"
    assert classify_failure(1, "502 Bad Gateway").value == "retryable"

    # Unknown failures
    assert classify_failure(1, "unknown error").value == "unknown"


def test_circuit_breaker_threshold():
    """Test circuit breaker threshold behavior."""
    cb = CircuitBreaker(threshold=2, reset_seconds=60)

    # Should not be open initially
    assert not cb.is_open("claude-code")

    # Record failures up to threshold
    cb.record_failure("claude-code")
    assert not cb.is_open("claude-code")

    cb.record_failure("claude-code")
    assert cb.is_open("claude-code")

    # Success should reset
    cb.record_success("claude-code")
    assert not cb.is_open("claude-code")


# ---------------------------------------------------------------------------
# Cleanup Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup_test_env(tmp_path):
    """Automatically clean up test environment after each test."""
    yield
    # Cleanup happens automatically when tmp_path goes out of scope
    pass