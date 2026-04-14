"""Core orchestration loop — submits tasks, runs agents, handles retries."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_runner import ClaudeRunner
from codex_runner import CodexRunner
from config import REPO_PATH, WORKTREE_BASE
from retry import CircuitBreaker, classify_failure, compute_delay, FailureClass
from sandbox import cleanup_runner_env
from done_checker import run_done_checks
from smart_retry import generate_retry_prompt

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
                # On retry (attempt > 0), use smart retry prompt instead of original
                if attempt > 0:
                    task_for_analysis = self.registry.get_task(task_id)
                    full_prompt = generate_retry_prompt(
                        description, task_for_analysis, attempt
                    )
                    logger.info("Using smart retry prompt for %s (attempt %d)", task_id, attempt)
                else:
                    # Append commit instruction so files survive worktree cleanup
                    full_prompt = (
                        description
                        + "\n\nIMPORTANT: After completing the task, run "
                        "`git add -A && git commit -m 'feat: <brief description>'` "
                        "to commit your work."
                    )
                runner = self.claude_runner if agent == "claude-code" else self.codex_runner
                # Store prompt in registry for traceability
                self.registry.update_task(task_id, prompt=full_prompt)
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
                # B2: Verify/ensure commit so files survive worktree removal
                self._ensure_commit(worktree, task_id)

                # Run done-definition checks (PR, CI, screenshot)
                task_snapshot = self.registry.get_task(task_id)
                done_checks = run_done_checks(task_snapshot, worktree)
                logger.info("Done checks for %s: %s", task_id, done_checks.get("all_passed"))

                self.registry.finish_task(
                    task_id, "done",
                    exit_code=exit_code,
                    stderr_tail=stderr_tail,
                    result=result_tail,
                    done_checks_json=json.dumps(done_checks, default=str),
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

    def _ensure_commit(self, worktree: str, task_id: str) -> None:
        """Ensure the worktree has at least one commit so files survive cleanup.

        If Claude Code didn't auto-commit, we do it ourselves.

        Args:
            worktree: Path to the git worktree.
            task_id: Task identifier (used in commit message).
        """
        try:
            # Check if there are uncommitted changes
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if status.returncode == 0 and status.stdout.strip():
                logger.info("Uncommitted changes in %s, auto-committing", task_id)
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=worktree,
                    capture_output=True,
                    timeout=15,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"feat: {task_id} (auto-committed by Hermes)"],
                    cwd=worktree,
                    capture_output=True,
                    timeout=15,
                )
        except Exception:
            logger.warning("Failed to auto-commit for task %s", task_id, exc_info=True)

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
