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
