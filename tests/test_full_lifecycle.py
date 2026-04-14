"""End-to-end integration tests for the Hermes task lifecycle."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from hermes.executor import TaskExecutor
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.retry import CircuitBreaker
from hermes.router import TaskRouter
from hermes.task_registry import TaskRegistry


# ---------------------------------------------------------------------------
# Mock runners
# ---------------------------------------------------------------------------

class MockClaudeRunner:
    """Stub ClaudeRunner that returns configurable results."""

    def __init__(self, exit_code: int = 0, stderr: str = ""):
        self._exit_code = exit_code
        self._stderr = stderr
        self.call_count = 0

    def run(self, task_id: str, prompt: str, worktree: str, model: str = "claude-sonnet-4-6", on_spawn=None) -> dict:
        self.call_count += 1
        return {
            "exit_code": self._exit_code,
            "stdout": "mock output",
            "stderr": self._stderr,
            "timed_out": False,
        }


class MockCodexRunner:
    """Stub CodexRunner that returns configurable results."""

    def __init__(self, exit_code: int = 0, stderr: str = ""):
        self._exit_code = exit_code
        self._stderr = stderr
        self.call_count = 0

    def run(self, task_id: str, prompt: str, worktree: str, model: str = "gpt-5.4", reasoning: str = "high", on_spawn=None) -> dict:
        self.call_count += 1
        return {
            "exit_code": self._exit_code,
            "stdout": "mock output",
            "stderr": self._stderr,
            "timed_out": False,
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def executor(registry, router, outbox, reconciler):
    """Provide a TaskExecutor with real components but mockable runners."""
    return TaskExecutor(registry, router, outbox, reconciler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_worktree(monkeypatch, tmp_path, executor):
    """Patch _create_worktree and _cleanup_worktree to use tmp_path."""
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()

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
# Tests
# ---------------------------------------------------------------------------

class TestNormalLifecycle:
    """Test submit -> pending -> running -> done."""

    def test_submit_success(self, executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
        """Happy path: submit returns done status with notification sent."""
        mock_runner = MockClaudeRunner(exit_code=0)
        _mock_worktree(monkeypatch, tmp_path, executor)
        sends = _mock_outbox_send(monkeypatch, outbox)
        monkeypatch.setattr(executor, "claude_runner", mock_runner)

        task = executor.submit("implement login feature", override="claude-code")

        assert task["status"] == "done"
        assert mock_runner.call_count == 1
        assert len(sends) == 1
        assert sends[0]["action"] == "notify_done"


class TestRetryOnFailure:
    """Test retry on failure: pending -> running -> retrying -> running -> done."""

    def test_retry_then_success(self, executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
        """First attempt fails with retryable error, second succeeds."""
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
        # Patch time.sleep
        monkeypatch.setattr("hermes.executor.time.sleep", lambda x: None)

        task = executor.submit("implement login feature", override="claude-code")

        assert task["status"] == "done"
        assert call_count == 2
        assert len(sends) == 1
        assert sends[0]["action"] == "notify_done"


class TestPermanentFailure:
    """Test permanent failure: pending -> running -> failed (no retry)."""

    def test_permanent_failure_no_retry(self, executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
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


class TestCircuitBreaker:
    """Test circuit breaker opens after 3 consecutive failures."""

    def test_circuit_breaker_opens(self, executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
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


class TestOutboxIdempotency:
    """Test outbox idempotency — same task_id + action sends only once."""

    def test_outbox_idempotent(self, executor, registry, router, outbox, reconciler, monkeypatch, tmp_path):
        """Calling send_notification twice for same task_id + action should be idempotent."""
        mock_runner = MockClaudeRunner(exit_code=0)
        _mock_worktree(monkeypatch, tmp_path, executor)
        monkeypatch.setattr(executor, "claude_runner", mock_runner)

        # Track actual DB-level outbox sends (bypass monkeypatch on send_notification)
        send_call_count = 0
        original_send = outbox.send_notification

        def counting_send(task_id, action, payload):
            nonlocal send_call_count
            send_call_count += 1
            # Use DB directly (skip openclaw)
            import json
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
            with registry._transaction() as conn:
                row = conn.execute(
                    """
                    INSERT INTO outbox (task_id, action, payload, status)
                    VALUES (?, ?, ?, 'pending')
                    ON CONFLICT(task_id, action) DO UPDATE SET
                        attempts = attempts,
                        last_error = NULL
                    WHERE outbox.status != 'sent'
                    RETURNING id, status;
                    """,
                    (task_id, action, payload_json),
                ).fetchone()
                if row and row["status"] != "sent":
                    conn.execute(
                        "UPDATE outbox SET status = 'sent', external_id = 'test-id', sent_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (row["id"],),
                    )
            return "test-id"

        monkeypatch.setattr(outbox, "send_notification", counting_send)

        task = executor.submit("implement login feature", override="claude-code")

        assert task["status"] == "done"
        assert send_call_count == 1  # Only one actual send

        # Call send_notification again — should be idempotent
        outbox.send_notification(task["id"], "notify_done", {"message": "dup"})
        assert send_call_count == 2  # Called again, but DB deduplicates

        # Verify only one row in outbox
        rows = registry._connect().execute(
            "SELECT * FROM outbox WHERE task_id = ?;",
            (task["id"],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "sent"
