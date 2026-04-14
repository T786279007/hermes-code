"""Isolated runner environment with sandboxed HOME and minimal config."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from config import RUNNER_HOME

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

    # Copy claude auth files into isolated HOME (symlinks would break sandbox isolation)
    real_home = Path.home()
    claude_json = real_home / ".claude.json"
    claude_dir = real_home / ".claude"
    if claude_json.exists():
        import shutil
        dest = runner_home / ".claude.json"
        shutil.copy2(claude_json, dest)
        dest.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    if claude_dir.is_dir():
        import shutil
        dest = runner_home / ".claude"
        if not dest.exists():
            shutil.copytree(claude_dir, dest)

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
