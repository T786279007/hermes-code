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
