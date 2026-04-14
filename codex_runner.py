"""Codex subprocess runner with timeout and process group kill."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading

from config import CODEX_TIMEOUT
from sandbox import prepare_runner_env

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
            "codex", "exec",
            "--dangerously-bypass-approvals-and-sandbox",
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
