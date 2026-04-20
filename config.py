"""Global configuration constants for Hermes Agent Cluster v2."""

from pathlib import Path

HERMES_HOME = Path("/home/txs/hermes-agent")
WORKTREE_BASE = HERMES_HOME / "worktrees"
DB_PATH = HERMES_HOME / "tasks.db"
RUNNER_HOME = HERMES_HOME / "runner_home"
LOG_DIR = Path("/home/txs/hermes/logs")
PROXY = "http://127.0.0.1:7899"

CLAUDE_TIMEOUT = 300
CODEX_TIMEOUT = 21600
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10.0
RETRY_MAX_DELAY = 300.0
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_RESET = 300
RECONCILER_TIMEOUT = 600  # Default fallback timeout for tasks without a known agent profile

REPO_PATH = "/home/txs/hermes"

# Cost control (P1-3: per-task cost ceiling)
# Default: $1.00 per task. Kill process if exceeded.
COST_LIMIT_PER_TASK_USD = 1.00
# Estimated cost per 1K tokens for different models (USD)
COST_PER_1K_INPUT_TOKENS = {
    "claude-sonnet-4": 0.003,
    "claude-sonnet-4-6": 0.003,
    "gpt-4o": 0.0025,
    "glm-5-turbo": 0.001,
}
COST_PER_1K_OUTPUT_TOKENS = {
    "claude-sonnet-4": 0.015,
    "claude-sonnet-4-6": 0.015,
    "gpt-4o": 0.01,
    "glm-5-turbo": 0.004,
}
