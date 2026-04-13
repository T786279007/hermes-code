"""HTTP Client with retry, timeout, and circuit breaker functionality."""

import time
import logging
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0
    half_open_max_calls: int = 3


class CircuitBreaker:
    """Circuit breaker implementation."""

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_failure_time: Optional[float] = None
        self.opened_at: Optional[float] = None

    def record_success(self):
        """Record a successful call."""
        self.failure_count = 0
        self.last_failure_time = None

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            self.half_open_calls += 1
            logger.info(
                f"Circuit breaker half-open success: {self.success_count}/"
                f"{self.config.success_threshold}"
            )
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.success_count = 0
                self.half_open_calls = 0
                logger.info("Circuit breaker closed after successful recovery")
        elif self.state == CircuitState.CLOSED:
            logger.debug("Circuit breaker recorded success in closed state")

    def record_failure(self):
        """Record a failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        self.success_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.opened_at = time.time()
            self.half_open_calls = 0
            logger.warning("Circuit breaker opened due to failure in half-open state")
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()
                logger.warning(
                    f"Circuit breaker opened after {self.failure_count} failures"
                )

    def can_attempt(self) -> bool:
        """Check if a call can be attempted."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if time.time() - self.opened_at >= self.config.timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("Circuit breaker transitioned to half-open")
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.config.half_open_max_calls

        return False

    def get_state(self) -> CircuitState:
        """Get current circuit breaker state."""
        return self.state


class HttpClient:
    """HTTP client with retry, timeout, and circuit breaker."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_factor = retry_backoff_factor
        self.circuit_breaker = CircuitBreakerConfig() if circuit_breaker_config is None else circuit_breaker_config
        self._circuit_breaker = CircuitBreaker(self.circuit_breaker)

    def _build_url(self, path: str) -> str:
        """Build full URL from path."""
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        return url

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        return self.retry_backoff_factor * (2 ** (attempt - 1))

    def _make_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request with retry and circuit breaker."""
        full_url = self._build_url(url)

        if not self._circuit_breaker.can_attempt():
            logger.error(f"Circuit breaker is {self._circuit_breaker.get_state().value}, request blocked")
            raise Exception(f"Circuit breaker is {self._circuit_breaker.get_state().value}")

        last_exception = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"{method} {full_url} - Attempt {attempt}/{self.max_retries}")

                import requests
                response = requests.request(
                    method=method,
                    url=full_url,
                    params=params,
                    data=data,
                    json=json,
                    headers=headers,
                    timeout=self.timeout,
                )

                logger.info(
                    f"{method} {full_url} - Status: {response.status_code}, "
                    f"Attempt: {attempt}"
                )

                if response.status_code >= 500:
                    raise Exception(f"Server error: {response.status_code}")

                self._circuit_breaker.record_success()
                return {
                    "status_code": response.status_code,
                    "data": response.json() if response.content else None,
                    "headers": dict(response.headers),
                }

            except requests.exceptions.Timeout as e:
                last_exception = e
                logger.warning(f"Timeout on attempt {attempt}: {e}")
            except requests.exceptions.RequestException as e:
                last_exception = e
                logger.warning(f"Request failed on attempt {attempt}: {e}")
            except Exception as e:
                last_exception = e
                logger.warning(f"Error on attempt {attempt}: {e}")

            if attempt < self.max_retries:
                backoff = self._calculate_backoff(attempt)
                logger.info(f"Retrying in {backoff:.2f} seconds...")
                time.sleep(backoff)

        self._circuit_breaker.record_failure()
        raise last_exception or Exception("Request failed after all retries")

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make GET request."""
        return self._make_request("GET", url, params=params, headers=headers)

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make POST request."""
        return self._make_request("POST", url, data=data, json=json, headers=headers)

    def put(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make PUT request."""
        return self._make_request("PUT", url, data=data, json=json, headers=headers)

    def delete(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make DELETE request."""
        return self._make_request("DELETE", url, params=params, headers=headers)

    def get_circuit_breaker_state(self) -> CircuitState:
        """Get current circuit breaker state."""
        return self._circuit_breaker.get_state()

    def reset_circuit_breaker(self):
        """Reset circuit breaker to closed state."""
        self._circuit_breaker = CircuitBreaker(self.circuit_breaker)
        logger.info("Circuit breaker reset")
