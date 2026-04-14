"""Global configuration constants for Hermes Agent Cluster v2."""

import os
from pathlib import Path

# Allow override via HERMES_HOME env var
HERMES_HOME = Path(os.environ.get("HERMES_HOME", ".hermes-agent"))
WORKTREE_BASE = HERMES_HOME / "worktrees"
DB_PATH = HERMES_HOME / "tasks.db"
RUNNER_HOME = HERMES_HOME / "runner_home"
LOG_DIR = Path(os.environ.get("HERMES_LOG_DIR", ".hermes-logs"))
PROXY = os.environ.get("HERMES_PROXY", "")

CLAUDE_TIMEOUT = int(os.environ.get("HERMES_CLAUDE_TIMEOUT", "300"))
CODEX_TIMEOUT = int(os.environ.get("HERMES_CODEX_TIMEOUT", "180"))
MAX_RETRIES = int(os.environ.get("HERMES_MAX_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.environ.get("HERMES_RETRY_BASE_DELAY", "10.0"))
RETRY_MAX_DELAY = float(os.environ.get("HERMES_RETRY_MAX_DELAY", "300.0"))
CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("HERMES_CB_THRESHOLD", "3"))
CIRCUIT_BREAKER_RESET = int(os.environ.get("HERMES_CB_RESET", "300"))

REPO_PATH = os.environ.get("HERMES_REPO_PATH", "/tmp/hermes-repo")
