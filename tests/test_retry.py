#!/usr/bin/env python3
"""Comprehensive tests for retry.py — classify_failure, compute_delay, CircuitBreaker."""

import time
import unittest
from retry import FailureClass, classify_failure, compute_delay, CircuitBreaker


class TestClassifyFailure(unittest.TestCase):
    """Failure classification tests."""

    def test_exit_zero_is_unknown(self):
        self.assertEqual(classify_failure(0, ""), FailureClass.UNKNOWN)

    def test_permanent_permission_denied(self):
        self.assertEqual(
            classify_failure(1, "Error: permission denied"),
            FailureClass.PERMANENT,
        )

    def test_permanent_auth_failed(self):
        self.assertEqual(
            classify_failure(1, "authentication failed for user"),
            FailureClass.PERMANENT,
        )

    def test_permanent_unauthorized(self):
        self.assertEqual(
            classify_failure(1, "401 unauthorized"),
            FailureClass.PERMANENT,
        )

    def test_permanent_invalid_api_key(self):
        self.assertEqual(
            classify_failure(1, "invalid api key"),
            FailureClass.PERMANENT,
        )

    def test_permanent_quota_exceeded(self):
        self.assertEqual(
            classify_failure(1, "quota exceeded for model"),
            FailureClass.PERMANENT,
        )

    def test_permanent_billing(self):
        self.assertEqual(
            classify_failure(1, "billing issue detected"),
            FailureClass.PERMANENT,
        )

    def test_permanent_account_suspended(self):
        self.assertEqual(
            classify_failure(1, "account suspended"),
            FailureClass.PERMANENT,
        )

    def test_retryable_timeout(self):
        self.assertEqual(
            classify_failure(124, "operation timed out"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_timed_out(self):
        self.assertEqual(
            classify_failure(1, "request timed out after 30s"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_connection_reset(self):
        self.assertEqual(
            classify_failure(1, "connection reset by peer"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_connection_refused(self):
        self.assertEqual(
            classify_failure(1, "connection refused"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_502(self):
        self.assertEqual(
            classify_failure(1, "gateway error 502"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_503(self):
        self.assertEqual(
            classify_failure(1, "service unavailable 503"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_504(self):
        self.assertEqual(
            classify_failure(1, "gateway timeout 504"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_rate_limit(self):
        self.assertEqual(
            classify_failure(1, "rate limit exceeded"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_429(self):
        self.assertEqual(
            classify_failure(1, "HTTP 429 too many requests"),
            FailureClass.RETRYABLE,
        )

    def test_retryable_network_error(self):
        self.assertEqual(
            classify_failure(1, "network error occurred"),
            FailureClass.RETRYABLE,
        )

    def test_permanent_over_retryable(self):
        """Permanent keywords take priority over retryable."""
        self.assertEqual(
            classify_failure(1, "permission denied and timeout"),
            FailureClass.PERMANENT,
        )

    def test_unknown_generic(self):
        self.assertEqual(
            classify_failure(1, "some random error xyz"),
            FailureClass.UNKNOWN,
        )

    def test_empty_stderr(self):
        self.assertEqual(classify_failure(1, ""), FailureClass.UNKNOWN)

    def test_none_stderr(self):
        self.assertEqual(classify_failure(1, None), FailureClass.UNKNOWN)

    def test_case_insensitive(self):
        self.assertEqual(
            classify_failure(1, "PERMISSION DENIED"),
            FailureClass.PERMANENT,
        )
        self.assertEqual(
            classify_failure(1, "TIMEOUT"),
            FailureClass.RETRYABLE,
        )


class TestComputeDelay(unittest.TestCase):
    """Exponential backoff with jitter tests."""

    def test_first_retry_base(self):
        """First retry should be ~10s with ±1s jitter."""
        delays = [compute_delay(0) for _ in range(100)]
        self.assertTrue(all(9.0 <= d <= 11.0 for d in delays))

    def test_exponential_growth(self):
        """Each retry should roughly double."""
        d0 = compute_delay(0)
        d1 = compute_delay(1)
        d2 = compute_delay(2)
        self.assertGreater(d1, d0)
        self.assertGreater(d2, d1)

    def test_max_delay_cap(self):
        """Delay should never exceed 300s."""
        for i in range(20):
            d = compute_delay(i)
            self.assertLessEqual(d, 300.0 + 30.0)  # Allow jitter above 300

    def test_minimum_delay(self):
        """Delay should always be >= 0.1s."""
        for i in range(10):
            d = compute_delay(i)
            self.assertGreaterEqual(d, 0.1)


class TestCircuitBreaker(unittest.TestCase):
    """Circuit breaker tests."""

    def test_initially_closed(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=300)
        self.assertFalse(cb.is_open("claude-code"))

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=1)
        cb.record_failure("claude-code")
        cb.record_failure("claude-code")
        self.assertFalse(cb.is_open("claude-code"))
        cb.record_failure("claude-code")
        self.assertTrue(cb.is_open("claude-code"))

    def test_resets_after_timeout(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=1)
        for _ in range(3):
            cb.record_failure("claude-code")
        self.assertTrue(cb.is_open("claude-code"))
        time.sleep(1.1)
        self.assertFalse(cb.is_open("claude-code"))

    def test_success_resets_count(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=300)
        cb.record_failure("claude-code")
        cb.record_failure("claude-code")
        cb.record_success("claude-code")
        self.assertFalse(cb.is_open("claude-code"))

    def test_per_agent_independent(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=300)
        for _ in range(3):
            cb.record_failure("claude-code")
        self.assertTrue(cb.is_open("claude-code"))
        self.assertFalse(cb.is_open("codex"))

    def test_custom_threshold(self):
        cb = CircuitBreaker(threshold=5, reset_seconds=300)
        for _ in range(4):
            cb.record_failure("claude-code")
        self.assertFalse(cb.is_open("claude-code"))
        cb.record_failure("claude-code")
        self.assertTrue(cb.is_open("claude-code"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
