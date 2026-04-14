"""Codex subprocess runner with timeout and process group kill."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time

from config import CODEX_TIMEOUT
from sandbox import prepare_runner_env

logger = logging.getLogger(__name__)


def _tmux_available() -> bool:
    """Check if tmux is available and functional.

    Returns:
        True if tmux command exists and can actually run.
    """
    tmux_bin = shutil.which("tmux")
    if not tmux_bin:
        return False
    try:
        result = subprocess.run(
            [tmux_bin, "list-sessions"],
            capture_output=True, timeout=5,
        )
        return True  # Works even if no sessions (exit 1) — lib errors would be non-zero + stderr
    except (OSError, subprocess.TimeoutExpired):
        return False


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
            Dict with keys: exit_code, stdout, stderr, timed_out, pid, tmux_session (if using tmux).
        """
        use_tmux = _tmux_available()
        session_name = f"hermes-{task_id}" if use_tmux else None

        if use_tmux:
            return self._run_with_tmux(task_id, prompt, worktree, model, reasoning, on_spawn, session_name)
        else:
            return self._run_legacy(task_id, prompt, worktree, model, reasoning, on_spawn)

    def _run_with_tmux(self, task_id: str, prompt: str, worktree: str,
                       model: str, reasoning: str, on_spawn, session_name: str) -> dict:
        """Run Codex in a tmux session for real-time progress capture.

        Args:
            task_id: Unique task identifier.
            prompt: The prompt to send to Codex.
            worktree: Absolute path to the git worktree.
            model: Model name to use.
            reasoning: Reasoning effort level.
            on_spawn: Optional callback(pid) invoked immediately after spawn.
            session_name: tmux session name.

        Returns:
            Dict with keys: exit_code, stdout, stderr, timed_out, pid, tmux_session.
        """
        env = prepare_runner_env("codex", task_id)
        # Build command with proper shell quoting
        cmd = f'codex exec --dangerously-bypass-approvals-and-sandbox --model {model} {shlex.quote(prompt)}'

        logger.info("Starting Codex in tmux: task=%s model=%s session=%s", task_id, model, session_name)

        # Start tmux session
        proc = subprocess.Popen(
            ["tmux", "new-session", "-d", "-s", session_name, cmd],
            cwd=worktree,
            env=env,
            preexec_fn=os.setsid,
        )

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
            self._kill_tmux(session_name, proc)

        timer = threading.Timer(self.TIMEOUT, _timeout_handler)
        timer.daemon = True
        timer.start()

        try:
            # tmux -d returns immediately; wait for the session to close
            # (i.e. the command inside the session to finish)
            while self._tmux_session_exists(session_name):
                if timed_out:
                    break
                time.sleep(1)
            # Reap the tmux launcher process
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            self._kill_tmux(session_name, proc)
        finally:
            if timer is not None:
                timer.cancel()

        # Capture full output from tmux
        stdout = self._capture_tmux_output(session_name)
        stderr = ""  # tmux captures both streams together

        # Cleanup tmux session
        self._cleanup_tmux(session_name)

        exit_code = proc.returncode if proc.returncode is not None else -1
        result = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "pid": proc.pid,
            "tmux_session": session_name,
        }

        if timed_out:
            result["exit_code"] = -1
            result["failure_class"] = "retryable"

        logger.info(
            "Codex finished: task=%s exit_code=%d timed_out=%s",
            task_id, result["exit_code"], timed_out,
        )
        return result

    def _run_legacy(self, task_id: str, prompt: str, worktree: str,
                    model: str, reasoning: str, on_spawn) -> dict:
        """Run Codex with subprocess.PIPE (fallback when tmux unavailable).

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

        logger.info("Starting Codex (legacy mode): task=%s model=%s", task_id, model)

        proc = subprocess.Popen(
            cmd,
            cwd=worktree,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

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

    def _tmux_session_exists(self, session_name: str) -> bool:
        """Check if a tmux session is still running."""
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session_name],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _capture_tmux_output(self, session_name: str) -> str:
        """Capture full output from a tmux session.

        Args:
            session_name: tmux session name.

        Returns:
            Captured output as string.
        """
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout or ""
        except Exception:
            return ""

    def _cleanup_tmux(self, session_name: str) -> None:
        """Kill a tmux session.

        Args:
            session_name: tmux session name.
        """
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _kill_tmux(self, session_name: str, proc: subprocess.Popen) -> None:
        """Kill both tmux session and the process group.

        Args:
            session_name: tmux session name.
            proc: Subprocess to kill.
        """
        self._cleanup_tmux(session_name)
        self._kill(proc)

    def _kill(self, proc: subprocess.Popen) -> None:
        """Kill the entire process group.

        Args:
            proc: Subprocess to kill.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
