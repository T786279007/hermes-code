"""Health checker for Hermes Agent Cluster v2."""

from __future__ import annotations

import calendar
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class HealthChecker:
    """Checks system health: disk, tasks, running agents, database."""

    def __init__(self, registry: TaskRegistry) -> None:
        """Initialize health checker with shared registry.

        Args:
            registry: TaskRegistry instance.
        """
        self._registry = registry

    def check(self) -> dict:
        """Run all health checks and return a summary dict.

        Returns:
            Dict with keys: system, tasks, agents, database.
        """
        return {
            "system": self._check_disk(),
            "tasks": self._check_tasks(),
            "agents": self._check_agents(),
            "database": self._check_database(),
        }

    def _check_disk(self) -> dict:
        """Check disk usage on the root filesystem.

        Returns:
            Dict with disk_total_gb, disk_used_gb, disk_percent.
        """
        usage = shutil.disk_usage(Path("/").resolve())
        return {
            "disk_total_gb": round(usage.total / (1024**3), 2),
            "disk_used_gb": round(usage.used / (1024**3), 2),
            "disk_percent": round(usage.used / usage.total * 100, 1),
        }

    def _check_tasks(self) -> dict:
        """Count tasks grouped by status.

        Returns:
            Dict mapping status names to counts.
        """
        counts: dict[str, int] = {}
        for status in ("pending", "running", "done", "failed", "retrying"):
            tasks = self._registry.list_tasks(status=status)
            counts[status] = len(tasks)
        logger.info("Task counts: %s", counts)
        return counts

    def _check_agents(self) -> dict:
        """Check running agents: PID alive and elapsed time.

        Returns:
            Dict keyed by task_id with pid, alive, elapsed_sec.
        """
        running = self._registry.list_tasks(status="running")
        result: dict[str, dict] = {}
        now = time.time()

        for task in running:
            pid = task.get("pid")
            started_at = task.get("started_at")
            alive = False
            elapsed = 0.0

            if pid is not None:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except OSError:
                    alive = False

            if started_at:
                try:
                    if isinstance(started_at, str):
                        # SQLite CURRENT_TIMESTAMP is UTC; parse as UTC (B2 fix)
                        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                        started_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                    else:
                        started_ts = float(started_at)
                    elapsed = round(now - started_ts, 1)
                except (ValueError, TypeError):
                    elapsed = -1.0

            result[task["id"]] = {
                "pid": pid,
                "alive": alive,
                "elapsed_sec": elapsed,
            }

        logger.info("Agent check: %d running tasks", len(result))
        return result

    def _check_database(self) -> dict:
        """Check database integrity and WAL checkpoint.

        Returns:
            Dict with integrity and wal_checkpoint results.
        """
        return self._registry.health_check()
"""Claude Code subprocess runner with timeout and process group kill."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading

from .config import CLAUDE_TIMEOUT
from .sandbox import prepare_runner_env

logger = logging.getLogger(__name__)


class ClaudeRunner:
    """Manages Claude Code subprocess execution."""

    TIMEOUT = CLAUDE_TIMEOUT

    def run(self, task_id: str, prompt: str, worktree: str,
            model: str = "claude-sonnet-4-6", on_spawn=None) -> dict:
        """Launch Claude Code as a subprocess and wait for completion.

        Args:
            task_id: Unique task identifier.
            prompt: The prompt to send to Claude Code.
            worktree: Absolute path to the git worktree.
            model: Model name to use.
            on_spawn: Optional callback(pid) invoked immediately after spawn.

        Returns:
            Dict with keys: exit_code, stdout, stderr, timed_out, pid.
        """
        env = prepare_runner_env("claude-code", task_id)
        cmd = [
            "claude",
            "--permission-mode", "bypassPermissions",
            "-p", prompt,
            "--model", model,
        ]

        logger.info("Starting Claude Code: task=%s model=%s", task_id, model)

        proc = subprocess.Popen(
            cmd,
            cwd=worktree,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # W4: notify caller of child PID immediately
        if on_spawn and proc.pid:
            try:
                on_spawn(proc.pid)
            except Exception:
                logger.warning("on_spawn callback failed", exc_info=True)

        timer: threading.Timer | None = None
        timed_out = False

        def _timeout_handler() -> None:
            nonlocal timed_out
            timed_out = True
            logger.warning("Claude Code timed out: task=%s (%ds)", task_id, self.TIMEOUT)
            self._kill(proc)

        timer = threading.Timer(self.TIMEOUT, _timeout_handler)
        timer.daemon = True
        timer.start()

        try:
            stdout, stderr = proc.communicate()
        except Exception:
            self._kill(proc)
            stdout, stderr = b"", b""
        finally:
            if timer is not None:
                timer.cancel()

        exit_code = proc.returncode if proc.returncode is not None else -1
        result = {
            "exit_code": exit_code,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "timed_out": timed_out,
            "pid": proc.pid,
        }

        if timed_out:
            result["exit_code"] = -1
            result["failure_class"] = "retryable"

        logger.info(
            "Claude Code finished: task=%s exit_code=%d timed_out=%s",
            task_id, result["exit_code"], timed_out,
        )
        return result

    def _kill(self, proc: subprocess.Popen) -> None:
        """Kill the entire process group.

        Args:
            proc: Subprocess to kill.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
"""Codex subprocess runner with timeout and process group kill."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading

from .config import CODEX_TIMEOUT
from .sandbox import prepare_runner_env

logger = logging.getLogger(__name__)


class CodexRunner:
    """Manages Codex subprocess execution."""

    TIMEOUT = CODEX_TIMEOUT

    def run(self, task_id: str, prompt: str, worktree: str,
            model: str = "gpt-5.4", reasoning: str = "high", on_spawn=None) -> dict:
        """Launch Codex as a subprocess and wait for completion.

        Args:
            task_id: Unique task identifier.
            prompt: The prompt to send to Codex.
            worktree: Absolute path to the git worktree.
            model: Model name to use.
            reasoning: Reasoning effort level.
            on_spawn: Optional callback(pid) invoked immediately after spawn.

        Returns:
            Dict with keys: exit_code, stdout, stderr, timed_out, pid.
        """
        env = prepare_runner_env("codex", task_id)
        cmd = [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "--quiet",
            "--model", model,
            prompt,
        ]

        logger.info("Starting Codex: task=%s model=%s", task_id, model)

        proc = subprocess.Popen(
            cmd,
            cwd=worktree,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # W4: notify caller of child PID immediately
        if on_spawn and proc.pid:
            try:
                on_spawn(proc.pid)
            except Exception:
                logger.warning("on_spawn callback failed", exc_info=True)

        timer: threading.Timer | None = None
        timed_out = False

        def _timeout_handler() -> None:
            nonlocal timed_out
            timed_out = True
            logger.warning("Codex timed out: task=%s (%ds)", task_id, self.TIMEOUT)
            self._kill(proc)

        timer = threading.Timer(self.TIMEOUT, _timeout_handler)
        timer.daemon = True
        timer.start()

        try:
            stdout, stderr = proc.communicate()
        except Exception:
            self._kill(proc)
            stdout, stderr = b"", b""
        finally:
            if timer is not None:
                timer.cancel()

        exit_code = proc.returncode if proc.returncode is not None else -1
        result = {
            "exit_code": exit_code,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "timed_out": timed_out,
            "pid": proc.pid,
        }

        if timed_out:
            result["exit_code"] = -1
            result["failure_class"] = "retryable"

        logger.info(
            "Codex finished: task=%s exit_code=%d timed_out=%s",
            task_id, result["exit_code"], timed_out,
        )
        return result

    def _kill(self, proc: subprocess.Popen) -> None:
        """Kill the entire process group.

        Args:
            proc: Subprocess to kill.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
"""Global configuration constants for Hermes Agent Cluster v2."""

from pathlib import Path

HERMES_HOME = Path("/home/txs/hermes-agent")
WORKTREE_BASE = HERMES_HOME / "worktrees"
DB_PATH = HERMES_HOME / "tasks.db"
RUNNER_HOME = HERMES_HOME / "runner_home"
LOG_DIR = Path("/home/txs/hermes/logs")
PROXY = "http://127.0.0.1:7897"

CLAUDE_TIMEOUT = 300
CODEX_TIMEOUT = 180
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10.0
RETRY_MAX_DELAY = 300.0
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_RESET = 300

REPO_PATH = "/tmp/hermes-repo"
"""Core orchestration loop — submits tasks, runs agents, handles retries."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .claude_runner import ClaudeRunner
from .codex_runner import CodexRunner
from .config import REPO_PATH, WORKTREE_BASE
from .retry import CircuitBreaker, classify_failure, compute_delay, FailureClass
from .sandbox import cleanup_runner_env

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Orchestrates the full task lifecycle: route, run, retry, notify."""

    def __init__(self, registry, router, outbox, reconciler):
        """Initialize executor with all dependencies.

        Args:
            registry: TaskRegistry instance.
            router: TaskRouter instance.
            outbox: Outbox instance.
            reconciler: Reconciler instance.
        """
        self.registry = registry
        self.router = router
        self.outbox = outbox
        self.reconciler = reconciler
        self.claude_runner = ClaudeRunner()
        self.codex_runner = CodexRunner()
        self.circuit_breaker = CircuitBreaker()

    def submit(self, description: str, override: str | None = None) -> dict:
        """Submit a new task and block until it completes.

        Args:
            description: Natural-language task description.
            override: If set to 'claude-code' or 'codex', skip routing.

        Returns:
            Complete task dict after execution finishes.
        """
        # 1. Generate task_id
        slug = _slugify(description)[:30]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        task_id = f"feat-{slug}-{ts}"

        # 2. Route
        decision = self.router.route(description, override)
        agent = decision.agent
        branch = f"hermes/{task_id}"

        logger.info(
            "Submitting task %s: agent=%s model=%s description=%.60s",
            task_id, agent, decision.model, description,
        )

        # 3. Create task in registry
        task = self.registry.create_task(
            task_id=task_id,
            description=description,
            agent=agent,
            branch=branch,
            model=decision.model,
        )

        # 4. Execute (blocking)
        task = self.execute(task_id)

        # 5. Send notification
        if task["status"] == "done":
            self.outbox.send_notification(
                task_id,
                "notify_done",
                {"message": f"Task {task_id} completed successfully"},
            )
        else:
            self.outbox.send_notification(
                task_id,
                "notify_failed",
                {"message": f"Task {task_id} failed: {task.get('stderr_tail', 'unknown')}"}
            )

        return self.registry.get_task(task_id)

    def execute(self, task_id: str) -> dict:
        """Core execution loop with retry, circuit breaker, and worktree management.

        Args:
            task_id: Unique task identifier.

        Returns:
            Task dict after execution finishes (done or failed).
        """
        task = self.registry.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        agent = task["agent"]
        model = task.get("model", "claude-sonnet-4-6")
        max_attempts = task.get("max_attempts", 3)
        description = task["description"]
        branch = task.get("branch", f"hermes/{task_id}")

        for attempt in range(max_attempts):
            logger.info("Execute attempt %d/%d for task %s", attempt, max_attempts, task_id)

            # 1. Circuit breaker check
            if self.circuit_breaker.is_open(agent):
                self.registry.finish_task(
                    task_id, "failed",
                    failure_class="permanent",
                    stderr_tail="Circuit breaker open",
                    attempt=attempt,
                )
                logger.error("Circuit breaker open for %s, failing task %s", agent, task_id)
                break

            # 2. Transition to running (W7: check return value)
            expected = "pending" if attempt == 0 else "retrying"
            if attempt > 0:
                # B1: cleanup worktree before retry to avoid branch conflict
                self._cleanup_worktree(task_id)
                if not self.registry.transition_status(task_id, "running", expected):
                    logger.error("Task %s state mismatch on retry, aborting", task_id)
                    break

                # Backoff delay
                delay = compute_delay(attempt - 1)
                logger.info("Retrying task %s in %.1fs", task_id, delay)
                time.sleep(delay)
            else:
                if not self.registry.transition_status(task_id, "running", "pending"):
                    logger.error("Task %s not in pending state, aborting", task_id)
                    break

            # 3. Create worktree (one per task, reused across retries)
            try:
                worktree = self._create_worktree(task_id, branch)
                self.registry.update_task(task_id, worktree=worktree, attempt=attempt)
            except Exception as e:
                logger.error("Failed to create worktree for %s: %s", task_id, e)
                self.registry.finish_task(
                    task_id, "failed",
                    failure_class="retryable",
                    stderr_tail=f"Worktree creation failed: {e}",
                    attempt=attempt,
                )
                break

            # 4. Run agent (W4: capture pid via on_spawn callback)
            child_pid = None

            def _on_spawn(pid: int) -> None:
                nonlocal child_pid
                child_pid = pid
                self.registry.update_task(task_id, pid=pid)

            try:
                # Append commit instruction so files survive worktree cleanup
                full_prompt = (
                    description
                    + "\n\nIMPORTANT: After completing the task, run "
                    "`git add -A && git commit -m 'feat: <brief description>'` "
                    "to commit your work."
                )
                runner = self.claude_runner if agent == "claude-code" else self.codex_runner
                result = runner.run(
                    task_id=task_id,
                    prompt=full_prompt,
                    worktree=worktree,
                    model=model,
                    on_spawn=_on_spawn,
                )
            except Exception as e:
                result = {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": str(e),
                    "timed_out": False,
                    "pid": child_pid,
                }

            # 5. Determine outcome (W10: prefer runner's timed_out/failure_class)
            exit_code = result.get("exit_code", -1)
            stderr_tail = (result.get("stderr", "") or "")[-1024:]
            stdout = result.get("stdout", "") or ""
            result_tail = stdout[-2048:]
            runner_timed_out = result.get("timed_out", False)
            runner_failure_class = result.get("failure_class")

            # 6. Handle outcome with atomic finish_task (W8)
            if exit_code == 0:
                self.registry.finish_task(
                    task_id, "done",
                    exit_code=exit_code,
                    stderr_tail=stderr_tail,
                    result=result_tail,
                )
                self.circuit_breaker.record_success(agent)
                logger.info("Task %s completed successfully", task_id)
                break
            else:
                # W10: use runner's failure_class if provided, else classify from stderr
                if runner_timed_out:
                    failure_class = FailureClass.RETRYABLE
                elif runner_failure_class:
                    failure_class = FailureClass(runner_failure_class)
                else:
                    failure_class = FailureClass(classify_failure(exit_code, stderr_tail))

                self.circuit_breaker.record_failure(agent)

                if failure_class.value == "permanent":
                    self.registry.finish_task(
                        task_id, "failed",
                        exit_code=exit_code,
                        stderr_tail=stderr_tail,
                        result=result_tail,
                        failure_class="permanent",
                        attempt=attempt,
                    )
                    logger.error("Task %s permanent failure: %s", task_id, stderr_tail[:200])
                    break

                # Retryable or unknown
                if attempt < max_attempts - 1:
                    self.registry.finish_task(
                        task_id, "retrying",
                        exit_code=exit_code,
                        stderr_tail=stderr_tail,
                        result=result_tail,
                        failure_class=failure_class.value,
                        attempt=attempt,
                    )
                    logger.warning("Task %s retryable failure, will retry", task_id)
                    continue
                else:
                    self.registry.finish_task(
                        task_id, "failed",
                        exit_code=exit_code,
                        stderr_tail=stderr_tail,
                        result=result_tail,
                        failure_class=failure_class.value,
                        attempt=attempt,
                    )
                    logger.error("Task %s failed after %d attempts", task_id, max_attempts)
                    break

        # Re-read final state from registry (task dict may be stale after loop)
        task = self.registry.get_task(task_id)

        # Keep worktree on success for inspection; always cleanup runner env
        if task["status"] == "done":
            logger.info("Keeping worktree for completed task %s at %s",
                        task_id, WORKTREE_BASE / task_id)
        else:
            try:
                self._cleanup_worktree(task_id)
            except Exception:
                logger.warning("Failed to cleanup worktree for %s", task_id, exc_info=True)

        try:
            cleanup_runner_env(agent, task_id)
        except Exception:
            logger.warning("Failed to cleanup runner env for %s", task_id, exc_info=True)

        return self.registry.get_task(task_id)

    def _create_worktree(self, task_id: str, branch: str) -> str:
        """Create or reuse a git worktree for the task.

        On retry, the branch may already exist — reuse it (B1 fix).

        Args:
            task_id: Unique task identifier.
            branch: Branch name to create.

        Returns:
            Absolute path to the worktree.

        Raises:
            RuntimeError: If git worktree operations fail.
        """
        worktree_path = str(WORKTREE_BASE / task_id)
        WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

        # If worktree directory already exists, prune and recreate
        if Path(worktree_path).exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", worktree_path],
                    cwd=REPO_PATH,
                    capture_output=True,
                    timeout=15,
                )
            except Exception:
                pass

        cmd = ["git", "worktree", "add", worktree_path]
        # Check if branch already exists
        branch_check = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=REPO_PATH,
            capture_output=True,
            timeout=10,
        )
        if branch_check.returncode == 0:
            # Branch exists, checkout existing
            cmd.append(branch)
        else:
            # New branch
            cmd.extend(["-b", branch])

        result = subprocess.run(
            cmd,
            cwd=REPO_PATH,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

        logger.info("Created worktree %s for task %s", worktree_path, task_id)
        return worktree_path

    def _cleanup_worktree(self, task_id: str) -> None:
        """Remove the git worktree for a completed task.

        Args:
            task_id: Unique task identifier.
        """
        worktree_path = WORKTREE_BASE / task_id
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=REPO_PATH,
                capture_output=True,
                timeout=15,
            )
            logger.info("Removed worktree for task %s", task_id)
        except Exception:
            # Fallback: remove directory directly
            try:
                import shutil
                if worktree_path.exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
                    logger.info("Force-removed worktree directory for task %s", task_id)
            except Exception:
                pass


def _slugify(text: str) -> str:
    """Convert a description string to a URL-safe slug.

    Args:
        text: Raw description text.

    Returns:
        Lowercase slug with hyphens replacing non-alphanumeric chars.
    """
    text = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    slug = slug.strip("-")[:40]
    return slug or "task"
"""Hermes Agent Cluster v2 — Multi-agent task orchestration."""
"""Idempotent outbox for sending notifications via openclaw CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class Outbox:
    """Idempotent notification outbox backed by the tasks DB."""

    def __init__(self, registry: TaskRegistry):
        """Initialize outbox with shared registry.

        Args:
            registry: TaskRegistry instance (shares DB connection).
        """
        self._registry = registry

    def send_notification(self, task_id: str, action: str, payload: dict) -> str | None:
        """Idempotently send a notification for a task.

        Uses compare-and-swap to atomically claim the row, preventing
        duplicate sends under concurrency (B3 fix).

        Args:
            task_id: Unique task identifier.
            action: Notification type ('notify_done' or 'notify_failed').
            payload: Notification content dict.

        Returns:
            External message ID if sent, or None on failure.
        """
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        with self._registry._transaction() as conn:
            # Insert or get existing row
            conn.execute(
                """
                INSERT INTO outbox (task_id, action, payload, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(task_id, action) DO NOTHING;
                """,
                (task_id, action, payload_json),
            )

            row = conn.execute(
                "SELECT id, status, external_id FROM outbox WHERE task_id = ? AND action = ?;",
                (task_id, action),
            ).fetchone()

            if row is None:
                return None

            outbox_id = row["id"]

            if row["status"] == "sent":
                logger.info("Notification already sent: task=%s action=%s", task_id, action)
                return row["external_id"]

            # CAS: atomically claim pending/failed → sending
            claimed = conn.execute(
                """
                UPDATE outbox SET status = 'sending', attempts = attempts + 1
                WHERE id = ? AND status IN ('pending', 'failed');
                """,
                (outbox_id,),
            ).rowcount

            if claimed == 0:
                # Another caller already claimed it
                logger.info("Notification already claimed: task=%s action=%s", task_id, action)
                return row["external_id"]

        # We own the send — perform it outside the transaction
        try:
            external_id = self._send_feishu(payload)
            with self._registry._transaction() as conn:
                conn.execute(
                    "UPDATE outbox SET status = 'sent', external_id = ?, sent_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (external_id, outbox_id),
                )
            logger.info("Notification sent: task=%s action=%s external_id=%s", task_id, action, external_id)
            return external_id
        except Exception as e:
            with self._registry._transaction() as conn:
                conn.execute(
                    "UPDATE outbox SET status = 'failed', last_error = ? WHERE id = ?;",
                    (str(e), outbox_id),
                )
            logger.error("Notification failed: task=%s action=%s error=%s", task_id, action, e)
            return None

    def _send_feishu(self, payload: dict) -> str:
        """Send a notification via Feishu webhook or OpenClaw message tool.

        Uses the OpenClaw gateway's built-in message relay via HTTP.
        Falls back to logging if the gateway is unavailable.

        Args:
            payload: Notification content with 'message' key.

        Returns:
            A fake message ID (since we use best-effort delivery).

        Raises:
            RuntimeError: If the notification cannot be delivered.
        """
        message = payload.get("message", str(payload))

        # Try sending via openclaw gateway API
        try:
            import urllib.request
            import json as _json

            gateway_port = os.environ.get("OPENCLAW_GATEWAY_PORT", "18789")
            url = f"http://127.0.0.1:{gateway_port}/api/v1/message"
            data = _json.dumps({
                "channel": "feishu",
                "message": message,
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read().decode())
                return body.get("message_id", "sent")
        except Exception as e:
            logger.warning("Gateway notification failed: %s, falling back to log", e)

        # Fallback: log the notification (Phase 2: integrate proper Feishu SDK)
        logger.info("[NOTIFICATION] %s", message)
        return "logged"
#!/usr/bin/env python3
"""Pull Request Manager using gh CLI.

This module provides functions to manage GitHub pull requests via the gh CLI tool.
"""

import subprocess
import json
from typing import Optional, List, Dict, Any


class PRManagerError(Exception):
    """Base exception for PR Manager errors."""
    pass


class CLIError(PRManagerError):
    """Exception raised when gh CLI command fails."""
    pass


class ValidationError(PRManagerError):
    """Exception raised when input validation fails."""
    pass


def _run_gh_command(args: List[str]) -> Dict[str, Any]:
    """Run a gh CLI command and return the parsed JSON output.

    Args:
        args: Command arguments to pass to gh CLI

    Returns:
        Parsed JSON response as a dictionary

    Raises:
        CLIError: If the gh CLI command fails
    """
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout:
            return json.loads(result.stdout)
        return {}
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise CLIError(f"gh CLI command failed: {error_msg}") from e
    except json.JSONDecodeError as e:
        raise CLIError(f"Failed to parse gh CLI output: {e}") from e


def create_pr(
    title: str,
    body: str,
    base: str,
    head: Optional[str] = None,
    draft: bool = False,
    repo: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new pull request.

    Args:
        title: PR title
        body: PR description/body
        base: Target branch to merge into
        head: Source branch (defaults to current branch)
        draft: Whether to create as a draft PR
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing PR information

    Raises:
        ValidationError: If required parameters are invalid
        CLIError: If gh CLI command fails
    """
    if not title or not title.strip():
        raise ValidationError("PR title cannot be empty")
    if not base or not base.strip():
        raise ValidationError("Base branch cannot be empty")
    if not body or not body.strip():
        raise ValidationError("PR body cannot be empty")

    args = ["pr", "create", "--title", title, "--body", body, "--base", base]

    if head:
        args.extend(["--head", head])
    if draft:
        args.append("--draft")
    if repo:
        args.extend(["--repo", repo])

    args.extend(["--json", "number,title,state,headRefName,baseRefName,url"])

    return _run_gh_command(args)


def check_ci(pr_number: Optional[int] = None, repo: Optional[str] = None) -> Dict[str, Any]:
    """Check CI status for a pull request.

    Args:
        pr_number: PR number (defaults to current PR)
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing CI status information

    Raises:
        CLIError: If gh CLI command fails
    """
    args = ["pr", "checks", "--json", "name,status,conclusion,startedAt,completedAt"]

    if pr_number is not None:
        args.extend([str(pr_number)])
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh_command(args)

    # Calculate overall status
    if result is not None:
        checks = result if isinstance(result, list) else result.get("checks", [])
        total = len(checks)
        completed = sum(1 for c in checks if c.get("status") == "completed")
        failed = sum(1 for c in checks if c.get("conclusion") in ["failure", "cancelled"])

        if total == 0:
            status = "pending"
        elif failed > 0:
            status = "failure"
        elif completed == total:
            status = "success"
        else:
            status = "pending"

        return {
            "status": status,
            "total_checks": total,
            "completed_checks": completed,
            "failed_checks": failed,
            "checks": checks
        }

    return {"status": "unknown", "checks": []}


def list_prs(
    state: str = "open",
    limit: int = 30,
    head: Optional[str] = None,
    base: Optional[str] = None,
    repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List pull requests.

    Args:
        state: PR state to filter by (open, closed, merged, all)
        limit: Maximum number of PRs to return
        head: Filter by head branch
        base: Filter by base branch
        repo: Repository in format "owner/repo" (optional)

    Returns:
        List of dictionaries containing PR information

    Raises:
        ValidationError: If state parameter is invalid
        CLIError: If gh CLI command fails
    """
    valid_states = ["open", "closed", "merged", "all"]
    if state not in valid_states:
        raise ValidationError(
            f"Invalid state '{state}'. Must be one of: {', '.join(valid_states)}"
        )

    args = [
        "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,state,headRefName,baseRefName,author,createdAt,updatedAt,url"
    ]

    if head:
        args.extend(["--head", head])
    if base:
        args.extend(["--base", base])
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh_command(args)

    return result if isinstance(result, list) else result.get("pullRequests", [])


def merge_pr(
    pr_number: int,
    merge_method: str = "merge",
    delete_branch: bool = False,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    repo: Optional[str] = None
) -> Dict[str, Any]:
    """Merge a pull request.

    Args:
        pr_number: PR number to merge
        merge_method: Merge method (merge, squash, rebase)
        delete_branch: Whether to delete the branch after merging
        subject: Custom commit subject (for squash)
        body: Custom commit body (for squash)
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing merge result information

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI command fails
    """
    valid_methods = ["merge", "squash", "rebase"]
    if merge_method not in valid_methods:
        raise ValidationError(
            f"Invalid merge method '{merge_method}'. Must be one of: {', '.join(valid_methods)}"
        )

    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    args = ["pr", "merge", str(pr_number), "--merge-method", merge_method]

    if delete_branch:
        args.append("--delete-branch")
    if subject:
        args.extend(["--subject", subject])
    if body:
        args.extend(["--body", body])
    if repo:
        args.extend(["--repo", repo])

    args.extend(["--json", "merged,mergedAt,mergedBy"])

    return _run_gh_command(args)
"""Crash recovery — detect and fix orphaned tasks from previous runs."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .config import REPO_PATH, WORKTREE_BASE

if TYPE_CHECKING:
    from .task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class Reconciler:
    """Detects and recovers from crashed/interrupted tasks."""

    def __init__(self, registry: TaskRegistry):
        """Initialize reconciler with shared registry.

        Args:
            registry: TaskRegistry instance.
        """
        self._registry = registry

    def reconcile(self) -> dict:
        """Scan running tasks and recover from crashes.

        Checks:
        1. PID liveness (os.kill(pid, 0))
        2. Worktree directory existence
        3. Branch existence in repo
        4. Timeout based on started_at (fix B2)

        Returns:
            Dict with 'fixed' (list of recovered task IDs) and
            'orphaned' (list of orphaned worktree paths).
        """
        fixed: list[str] = []
        orphaned: list[str] = []
        now = time.time()

        running_tasks = self._registry.list_tasks(status="running")
        logger.info("Reconciler: %d running tasks to check", len(running_tasks))

        for task in running_tasks:
            task_id = task["id"]
            worktree = task.get("worktree")
            branch = task.get("branch")
            pid = task.get("pid")
            started_at = task.get("started_at")

            is_dead = False
            reason = ""

            # Check PID liveness
            if pid is not None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    is_dead = True
                    reason = f"PID {pid} not found"
                except PermissionError:
                    # Process exists but we can't signal it
                    logger.warning("Cannot signal PID %d for task %s", pid, task_id)
                except OSError as e:
                    logger.warning("Error checking PID %d: %s", pid, e)

            # Check timeout via started_at
            if not is_dead and started_at:
                try:
                    if isinstance(started_at, str):
                        # SQLite CURRENT_TIMESTAMP is UTC; parse as UTC (B2 fix)
                        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                        started_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                    else:
                        started_ts = float(started_at)
                    elapsed = now - started_ts
                    # 10-minute hard timeout for reconciliation
                    if elapsed > 600:
                        is_dead = True
                        reason = f"Timed out ({elapsed:.0f}s > 600s)"
                        if pid is not None:
                            try:
                                os.kill(pid, 9)
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning("Error parsing started_at for task %s: %s", task_id, e)

            # Check worktree existence
            worktree_missing = False
            if worktree:
                if not Path(worktree).exists():
                    worktree_missing = True
                    if not is_dead:
                        is_dead = True
                        reason = "Worktree directory missing"

            # Check branch existence
            branch_missing = False
            if branch and not is_dead:
                try:
                    result = subprocess.run(
                        ["git", "rev-parse", "--verify", branch],
                        cwd=REPO_PATH,
                        capture_output=True,
                        timeout=10,
                    )
                    if result.returncode != 0:
                        branch_missing = True
                        if not is_dead:
                            is_dead = True
                            reason = f"Branch {branch} not found"
                except Exception as e:
                    logger.warning("Error checking branch %s: %s", branch, e)

            if is_dead:
                # B3 fix: use finish_task for atomic status+reason update
                try:
                    self._registry.finish_task(task_id, "failed", stderr_tail=reason)
                except Exception:
                    # Fallback: separate calls if finish_task not available
                    try:
                        self._registry.transition_status(task_id, "failed", "running")
                    except Exception:
                        self._registry.update_task(task_id, status="failed")
                    self._registry.update_task(task_id, stderr_tail=reason)
                fixed.append(task_id)
                logger.info("Reconciled task %s: %s", task_id, reason)

                # Clean up worktree immediately (W9 fix)
                if worktree:
                    self._cleanup_worktree(worktree)

            # Clean up orphaned worktrees
            if worktree and worktree_missing:
                orphaned.append(worktree)
                self._cleanup_worktree(worktree)

        # Also scan for orphaned worktrees not tracked by any task
        if WORKTREE_BASE.exists():
            for entry in WORKTREE_BASE.iterdir():
                if entry.is_dir():
                    tracked = any(
                        t.get("worktree") == str(entry)
                        for t in running_tasks
                    )
                    if not tracked:
                        orphaned.append(str(entry))
                        self._cleanup_worktree(str(entry))
                        logger.info("Orphaned worktree: %s", entry)

        logger.info("Reconciliation complete: fixed=%d orphaned=%d", len(fixed), len(orphaned))
        return {"fixed": fixed, "orphaned": orphaned}

    def _cleanup_worktree(self, worktree: str) -> None:
        """Remove a worktree directory.

        Args:
            worktree: Absolute path to the worktree.
        """
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                cwd=REPO_PATH,
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass
        try:
            from pathlib import Path
            import shutil
            p = Path(worktree)
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass
"""Failure classification, exponential backoff, and circuit breaker."""

from __future__ import annotations

import logging
import random
import time
from enum import Enum

logger = logging.getLogger(__name__)


class FailureClass(Enum):
    """Classification of a task failure."""

    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


# Keywords that indicate a permanent (non-retryable) failure
_PERMANENT_KEYWORDS = [
    "permission denied",
    "authentication failed",
    "unauthorized",
    "invalid api key",
    "quota exceeded",
    "billing",
    "account suspended",
    "payment required",
]

# Keywords that indicate a retryable transient failure
_RETRYABLE_KEYWORDS = [
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporary failure",
    "gateway timeout",
    "502",
    "503",
    "504",
    "econnreset",
    "network error",
    "rate limit",
    "429",
    "too many requests",
]


def classify_failure(exit_code: int, stderr: str) -> FailureClass:
    """Classify a failure as retryable, permanent, or unknown.

    Args:
        exit_code: Process exit code.
        stderr: Last kilobyte of stderr output.

    Returns:
        FailureClass indicating whether the error is retryable.
    """
    if exit_code == 0:
        return FailureClass.UNKNOWN

    stderr_lower = (stderr or "").lower()

    for kw in _PERMANENT_KEYWORDS:
        if kw in stderr_lower:
            logger.debug("Permanent failure keyword found: %s", kw)
            return FailureClass.PERMANENT

    for kw in _RETRYABLE_KEYWORDS:
        if kw in stderr_lower:
            logger.debug("Retryable failure keyword found: %s", kw)
            return FailureClass.RETRYABLE

    # Default: unknown — conservative, allow one retry
    return FailureClass.UNKNOWN


def compute_delay(retry_count: int) -> float:
    """Compute exponential backoff delay with 10% jitter.

    Args:
        retry_count: Zero-based retry attempt number.

    Returns:
        Delay in seconds before the next retry.
    """
    delay = min(10.0 * (2 ** retry_count), 300.0)
    jitter = delay * 0.1 * (random.random() * 2 - 1)
    result = max(delay + jitter, 0.1)
    logger.debug("Retry %d: delay=%.1fs", retry_count, result)
    return result


class CircuitBreaker:
    """Circuit breaker per agent to prevent cascading failures."""

    def __init__(self, threshold: int = 3, reset_seconds: int = 300):
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._failures: dict[str, int] = {}
        self._last_failure_time: dict[str, float] = {}

    def is_open(self, agent: str) -> bool:
        """Check if the circuit breaker is open for an agent.

        Args:
            agent: Agent name ('claude-code' or 'codex').

        Returns:
            True if circuit is open (should not send requests).
        """
        if self._failures.get(agent, 0) < self._threshold:
            return False

        last_time = self._last_failure_time.get(agent, 0)
        if time.time() - last_time > self._reset_seconds:
            logger.info("Circuit breaker reset for %s", agent)
            self._failures[agent] = 0
            return False

        logger.warning("Circuit breaker OPEN for %s (%d failures)", agent, self._failures.get(agent, 0))
        return True

    def record_success(self, agent: str) -> None:
        """Record a successful call, resetting the failure count.

        Args:
            agent: Agent name.
        """
        self._failures[agent] = 0
        logger.debug("Circuit breaker success recorded for %s", agent)

    def record_failure(self, agent: str) -> None:
        """Record a failed call.

        Args:
            agent: Agent name.
        """
        self._failures[agent] = self._failures.get(agent, 0) + 1
        self._last_failure_time[agent] = time.time()
        logger.warning(
            "Circuit breaker failure recorded for %s (%d/%d)",
            agent, self._failures[agent], self._threshold,
        )
"""Score-based task routing to select agent, model, and timeout."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of task routing: which agent to use and why."""

    agent: str
    model: str
    timeout: int
    confidence: float
    reason: str


class TaskRouter:
    """Route tasks to Claude Code or Codex based on keyword scoring."""

    CLAUDE_KEYWORDS = [
        "implement", "create", "build", "refactor", "add", "frontend",
        "feature", "api", "ui", "实现", "开发", "创建", "编写", "重构",
        "前端", "添加", "新增", "修改", "优化",
    ]
    CODEX_KEYWORDS = [
        "review", "fix", "bug", "check", "lint", "审查", "代码审查",
        "修复", "检查",
    ]

    def route(self, description: str, override: str | None = None) -> RoutingDecision:
        """Route a task to an agent based on keyword scoring.

        Args:
            description: Natural-language task description.
            override: If set to 'claude-code' or 'codex', skip scoring.

        Returns:
            RoutingDecision with selected agent, model, timeout, confidence, reason.
        """
        if override in ("claude-code", "codex"):
            agent = override
            if agent == "claude-code":
                return RoutingDecision(
                    agent="claude-code",
                    model="claude-sonnet-4-6",
                    timeout=300,
                    confidence=1.0,
                    reason="User override",
                )
            return RoutingDecision(
                agent="codex",
                model="gpt-5.4",
                timeout=180,
                confidence=1.0,
                reason="User override",
            )

        desc_lower = description.lower()
        claude_score = 0
        codex_score = 0

        for kw in self.CLAUDE_KEYWORDS:
            if re.search(re.escape(kw), desc_lower):
                claude_score += 1

        for kw in self.CODEX_KEYWORDS:
            if re.search(re.escape(kw), desc_lower):
                codex_score += 1

        total = claude_score + codex_score
        if total == 0:
            # Default to Claude Code for unknown tasks
            logger.info("No keywords matched, defaulting to claude-code")
            return RoutingDecision(
                agent="claude-code",
                model="claude-sonnet-4-6",
                timeout=300,
                confidence=0.5,
                reason="Default (no keywords matched)",
            )

        if claude_score >= codex_score:
            confidence = claude_score / total
            logger.info(
                "Routed to claude-code (score=%d/%d, confidence=%.2f)",
                claude_score, total, confidence,
            )
            return RoutingDecision(
                agent="claude-code",
                model="claude-sonnet-4-6",
                timeout=300,
                confidence=confidence,
                reason=f"Claude keywords matched {claude_score}/{total}",
            )

        confidence = codex_score / total
        logger.info(
            "Routed to codex (score=%d/%d, confidence=%.2f)",
            codex_score, total, confidence,
        )
        return RoutingDecision(
            agent="codex",
            model="gpt-5.4",
            timeout=180,
            confidence=confidence,
            reason=f"Codex keywords matched {codex_score}/{total}",
        )
"""Isolated runner environment with sandboxed HOME and minimal config."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from .config import RUNNER_HOME

logger = logging.getLogger(__name__)


def prepare_runner_env(agent: str, task_id: str) -> dict[str, str]:
    """Create an isolated HOME directory with minimal .gitconfig and git-askpass.

    Args:
        agent: Agent name ('claude-code' or 'codex').
        task_id: Unique task identifier.

    Returns:
        Environment dict suitable for subprocess.Popen.
    """
    runner_home = RUNNER_HOME / agent / task_id
    runner_home.mkdir(parents=True, exist_ok=True)

    # Symlink claude auth files so claude --print works in isolated HOME
    real_home = Path.home()
    claude_json = real_home / ".claude.json"
    claude_dir = real_home / ".claude"
    claude_link = runner_home / ".claude.json"
    claude_dir_link = runner_home / ".claude"
    if claude_json.exists() and not claude_link.exists():
        claude_link.symlink_to(claude_json)
    if claude_dir.is_dir() and not claude_dir_link.exists():
        claude_dir_link.symlink_to(claude_dir)

    # Minimal .gitconfig
    gitconfig = runner_home / ".gitconfig"
    gitconfig.write_text(
        '[user]\n'
        '    name = Hermes Agent\n'
        '    email = hermes@localhost\n'
        '[core]\n'
        '    autocrlf = input\n'
        '[init]\n'
        '    defaultBranch = main\n',
        encoding="utf-8",
    )

    # git-askpass.sh for credential injection (v1.2: use GIT_ASKPASS env var, not token in file)
    github_token = os.environ.get("HERMES_GITHUB_TOKEN", "")
    askpass_path = runner_home / "git-askpass.sh"

    # B1 fix: create env dict BEFORE referencing it
    env = os.environ.copy()

    if github_token:
        askpass_path.write_text(
            "#!/bin/bash\n"
            "cat \"$HERMES_GITHUB_TOKEN_FILE\" 2>/dev/null || echo ''\n",
            encoding="utf-8",
        )
        askpass_path.chmod(askpass_path.stat().st_mode | stat.S_IEXEC)
        # Store token in a file with restrictive permissions (0600)
        token_file = runner_home / ".gh_token"
        token_file.write_text(github_token, encoding="utf-8")
        token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        env["HERMES_GITHUB_TOKEN_FILE"] = str(token_file)

    env["HOME"] = str(runner_home)
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    env["GIT_ASKPASS"] = str(askpass_path)
    env["GIT_TERMINAL_PROMPT"] = "0"

    logger.info("Prepared runner env: agent=%s task=%s home=%s", agent, task_id, runner_home)
    return env


def cleanup_runner_env(agent: str, task_id: str) -> None:
    """Delete the isolated runner HOME directory after task completion.

    Args:
        agent: Agent name.
        task_id: Unique task identifier.
    """
    runner_home = RUNNER_HOME / agent / task_id
    try:
        import shutil
        shutil.rmtree(runner_home, ignore_errors=True)
        logger.info("Cleaned up runner env: agent=%s task=%s", agent, task_id)
    except Exception:
        logger.warning("Failed to cleanup runner env: %s", runner_home, exc_info=True)
"""SQLite WAL task registry — single source of truth for all tasks."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    branch TEXT,
    worktree TEXT,
    prompt TEXT,
    result TEXT,
    model TEXT,
    exit_code INTEGER,
    stderr_tail TEXT,
    failure_class TEXT,
    attempt INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    pid INTEGER
);
"""

_CREATE_OUTBOX_TABLE = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,
    external_id TEXT,
    payload TEXT,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    last_error TEXT,
    UNIQUE(task_id, action)
);
"""


class TaskRegistry:
    """Thread-safe SQLite task registry with WAL mode."""

    def __init__(self, db_path: str | Path):
        """Initialize registry, create DB directory and schema.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._lock = threading.Lock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.executescript(_CREATE_TASKS_TABLE)
            conn.executescript(_CREATE_OUTBOX_TABLE)
            conn.execute("PRAGMA journal_mode=WAL;")
        logger.info("TaskRegistry initialized at %s", self._db_path)

    def _connect(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL mode and Row factory.

        Returns:
            Configured sqlite3.Connection.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,
            timeout=10,
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _transaction(self):
        """Context manager for exclusive transactions with thread lock.

        Yields:
            sqlite3.Connection within an exclusive transaction.
        """
        conn = self._connect()
        try:
            with self._lock:
                conn.execute("BEGIN EXCLUSIVE;")
                yield conn
                conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def create_task(self, task_id: str, description: str, agent: str, **kwargs) -> dict:
        """Insert a new task into the registry.

        Args:
            task_id: Unique task identifier (e.g. 'feat-xxx-20240101-120000').
            description: Natural-language task description.
            agent: Agent name ('claude-code' or 'codex').
            **kwargs: Additional fields (branch, worktree, prompt, model, etc.).

        Returns:
            Dict representation of the created task row.
        """
        fields = {
            "id": task_id,
            "description": description,
            "agent": agent,
            "status": kwargs.get("status", "pending"),
            "branch": kwargs.get("branch"),
            "worktree": kwargs.get("worktree"),
            "prompt": kwargs.get("prompt"),
            "model": kwargs.get("model"),
            "max_attempts": kwargs.get("max_attempts", 3),
        }
        # Remove None values so DEFAULT clauses apply
        fields = {k: v for k, v in fields.items() if v is not None}

        columns = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        values = list(fields.values())

        with self._transaction() as conn:
            row = conn.execute(
                f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) RETURNING *;",
                values,
            ).fetchone()

        result = dict(row)
        logger.info("Created task %s (agent=%s)", task_id, agent)
        return result

    def get_task(self, task_id: str) -> dict | None:
        """Fetch a task by ID.

        Args:
            task_id: Unique task identifier.

        Returns:
            Task dict or None if not found.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()

        if row is None:
            return None
        return dict(row)

    def update_task(self, task_id: str, **fields) -> bool:
        """Update arbitrary fields on a task.

        Args:
            task_id: Unique task identifier.
            **fields: Column names and values to update.
                     Special handling: 'updated_at' is set via SQL CURRENT_TIMESTAMP.

        Returns:
            True if exactly one row was updated.
        """
        if not fields:
            return False

        # Always set updated_at via SQL function, not as a literal value
        set_parts = ["updated_at = CURRENT_TIMESTAMP"]
        values: list = []

        for k, v in fields.items():
            if k == "updated_at":
                continue  # Already handled above
            set_parts.append(f"{k} = ?")
            values.append(v)

        values.append(task_id)

        set_clause = ", ".join(set_parts)

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?;",
                values,
            )
            updated = cursor.rowcount > 0

        if updated:
            logger.debug("Updated task %s: %s", task_id, list(fields.keys()))
        return updated

    def transition_status(self, task_id: str, new_status: str,
                          expected_current: str | None = None) -> bool:
        """Atomically transition task status with optimistic locking.

        When transitioning to 'running', also sets started_at timestamp.

        Args:
            task_id: Unique task identifier.
            new_status: Target status.
            expected_current: If set, only transition if current status matches.

        Returns:
            True if the transition succeeded.
        """
        with self._transaction() as conn:
            if expected_current:
                row = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?;",
                    (task_id,),
                ).fetchone()
                if row is None or row["status"] != expected_current:
                    logger.warning(
                        "Status transition failed: task=%s expected=%s got=%s",
                        task_id, expected_current, row["status"] if row else "not found",
                    )
                    return False

            extra = ""
            params: list = [new_status, task_id]
            if new_status == "running":
                extra = ", started_at = CURRENT_TIMESTAMP"
                params = [new_status, task_id]

            conn.execute(
                f"UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP{extra} WHERE id = ?;",
                params,
            )

        logger.info("Task %s transitioned to %s", task_id, new_status)
        return True

    def finish_task(self, task_id: str, new_status: str, **fields) -> bool:
        """Atomically write result fields and transition status in one transaction.

        Prevents partial states where result is written but status is not
        (e.g. if the executor process crashes between the two operations).

        Args:
            task_id: Unique task identifier.
            new_status: Target status ('done' or 'failed').
            **fields: Additional fields to update (exit_code, stderr_tail, etc.).

        Returns:
            True if the transition succeeded.
        """
        set_parts = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        values: list = [new_status]

        if new_status == "done":
            set_parts.append("started_at = NULL")
            set_parts.append("pid = NULL")

        for k, v in fields.items():
            if k in ("status", "updated_at"):
                continue
            set_parts.append(f"{k} = ?")
            values.append(v)

        values.append(task_id)
        set_clause = ", ".join(set_parts)

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?;",
                values,
            )
            updated = cursor.rowcount > 0

        if updated:
            logger.info("Task %s finished: status=%s fields=%s", task_id, new_status, list(fields.keys()))
        return updated

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        """List tasks, optionally filtered by status.

        Args:
            status: Filter by status value, or None for all.
            limit: Maximum number of tasks to return.

        Returns:
            List of task dicts ordered by created_at DESC.
        """
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?;",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?;",
                    (limit,),
                ).fetchall()

        return [dict(row) for row in rows]

    def health_check(self) -> dict:
        """Run database integrity check and WAL checkpoint.

        Returns:
            Dict with 'integrity' and 'wal_checkpoint' results.
        """
        with self._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()

        result = {
            "integrity": integrity,
            "wal_checkpoint": {
                "busy": checkpoint[0],
                "log": checkpoint[1],
                "checkpointed": checkpoint[2],
            },
        }
        logger.info("DB health check: integrity=%s", integrity)
        return result
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
#!/usr/bin/env python3
"""CLI entry point for Hermes Agent Cluster v2."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# Ensure parent of hermes package is on sys.path
_hermes_root = Path(__file__).resolve().parent.parent  # ~/hermes
_pkg_parent = _hermes_root.parent  # ~/ (or /home/txs)
if str(_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_pkg_parent))

from hermes.config import DB_PATH
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.check_agents import HealthChecker

logger = logging.getLogger(__name__)

# ANSI color helpers
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"

STATUS_COLORS = {
    "pending": _YELLOW,
    "running": _CYAN,
    "done": _GREEN,
    "failed": _RED,
    "retrying": _MAGENTA,
}


def _color(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def cmd_status(args: argparse.Namespace) -> None:
    """List all tasks from the registry."""
    registry = TaskRegistry(DB_PATH)
    tasks = registry.list_tasks(limit=args.limit)

    if not tasks:
        print("No tasks found.")
        return

    use_color = _supports_color()
    header = f"{'ID':<40} {'AGENT':<12} {'STATUS':<10} {'CREATED':<20}"
    print(header)
    print("-" * 82)

    for t in tasks:
        tid = t["id"][:38]
        agent = t["agent"]
        status = t["status"]
        created = str(t.get("created_at", ""))[:19]

        if use_color:
            status_str = _color(status.ljust(10), STATUS_COLORS.get(status, ""))
        else:
            status_str = status.ljust(10)

        print(f"{tid:<40} {agent:<12} {status_str} {created:<20}")

    logger.info("Listed %d tasks", len(tasks))


def cmd_check(args: argparse.Namespace) -> None:
    """Run health checker and print results."""
    registry = TaskRegistry(DB_PATH)
    checker = HealthChecker(registry)
    result = checker.check()

    use_color = _supports_color()

    print("=== System ===")
    sys_info = result["system"]
    print(f"  Disk: {sys_info['disk_used_gb']}/{sys_info['disk_total_gb']} GB "
          f"({sys_info['disk_percent']}%)")

    print("\n=== Tasks ===")
    for status, count in result["tasks"].items():
        if count > 0:
            if use_color:
                status_str = _color(status, STATUS_COLORS.get(status, ""))
            else:
                status_str = status
            print(f"  {status_str}: {count}")

    print("\n=== Running Agents ===")
    agents = result["agents"]
    if agents:
        for tid, info in agents.items():
            if use_color:
                alive_str = _color("ALIVE", _GREEN) if info["alive"] else _color("DEAD", _RED)
            else:
                alive_str = "ALIVE" if info["alive"] else "DEAD"
            print(f"  {tid}: PID={info['pid']} {alive_str} ({info['elapsed_sec']}s)")
    else:
        print("  No running agents.")

    print("\n=== Database ===")
    db = result["database"]
    if use_color:
        integrity_str = _color(db["integrity"], _GREEN if db["integrity"] == "ok" else _RED)
    else:
        integrity_str = db["integrity"]
    print(f"  Integrity: {integrity_str}")
    wc = db["wal_checkpoint"]
    print(f"  WAL checkpoint: log={wc['log']} checkpointed={wc['checkpointed']}")


def cmd_reconcile(args: argparse.Namespace) -> None:
    """Run crash recovery and print results."""
    registry = TaskRegistry(DB_PATH)
    reconciler = Reconciler(registry)
    result = reconciler.reconcile()

    print(f"Fixed tasks: {len(result['fixed'])}")
    for tid in result["fixed"]:
        print(f"  - {tid}")

    print(f"Orphaned worktrees: {len(result['orphaned'])}")
    for wt in result["orphaned"]:
        print(f"  - {wt}")


def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a new task and execute it (blocking)."""
    registry = TaskRegistry(DB_PATH)
    router = TaskRouter()
    outbox = Outbox(registry)
    reconciler = Reconciler(registry)
    from executor import TaskExecutor
    executor = TaskExecutor(registry, router, outbox, reconciler)

    description = args.description

    print(f"Submitting and executing: {description}")
    logger.info("Submitting task: %s", description)

    task = executor.submit(description, override=args.agent)

    status_color = STATUS_COLORS.get(task["status"], "")
    if _supports_color():
        status_str = _color(task["status"], status_color)
    else:
        status_str = task["status"]

    print(f"\nTask {task['id']}: {status_str}")
    if task.get("failure_class"):
        print(f"  Failure: {task['failure_class']}")
    if task.get("stderr_tail"):
        print(f"  Error: {task['stderr_tail'][:200]}")
    logger.info("Task %s finished: %s", task["id"], task["status"])


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(prog="hermes", description="Hermes Agent Cluster v2")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="List all tasks")
    p_status.add_argument("--limit", type=int, default=50)

    sub.add_parser("check", help="Health check")

    sub.add_parser("reconcile", help="Crash recovery")

    p_submit = sub.add_parser("submit", help="Submit and execute a new task")
    p_submit.add_argument("description", help="Task description")
    p_submit.add_argument("--agent", choices=["claude-code", "codex"], default=None,
                          help="Override agent selection")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "status": cmd_status,
        "check": cmd_check,
        "reconcile": cmd_reconcile,
        "submit": cmd_submit,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
