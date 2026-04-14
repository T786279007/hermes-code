"""Tests for HTTP client with retry, timeout, and circuit breaker."""

import unittest
from unittest.mock import Mock, patch, MagicMock
import time
import requests

from http_client import (
    HttpClient,
    CircuitBreaker,
    CircuitState,
    CircuitBreakerConfig,
)


class TestCircuitBreaker(unittest.TestCase):
    """Test circuit breaker functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout=1.0,
            half_open_max_calls=3,
        )
        self.circuit_breaker = CircuitBreaker(self.config)

    def test_circuit_breaker_initial_state(self):
        """Test circuit breaker starts in closed state."""
        self.assertEqual(self.circuit_breaker.get_state(), CircuitState.CLOSED)
        self.assertTrue(self.circuit_breaker.can_attempt())

    def test_circuit_breaker_opens_after_threshold(self):
        """Test circuit breaker opens after failure threshold."""
        for _ in range(self.config.failure_threshold):
            self.circuit_breaker.record_failure()

        self.assertEqual(self.circuit_breaker.get_state(), CircuitState.OPEN)
        self.assertFalse(self.circuit_breaker.can_attempt())

    def test_circuit_breaker_blocks_requests_when_open(self):
        """Test circuit breaker blocks requests in open state."""
        self.circuit_breaker.state = CircuitState.OPEN
        self.circuit_breaker.opened_at = time.time()
        self.assertFalse(self.circuit_breaker.can_attempt())

    def test_circuit_breaker_transitions_to_half_open_after_timeout(self):
        """Test circuit breaker transitions to half-open after timeout."""
        self.circuit_breaker.state = CircuitState.OPEN
        self.circuit_breaker.opened_at = time.time() - self.config.timeout - 0.1
        self.assertTrue(self.circuit_breaker.can_attempt())
        self.assertEqual(self.circuit_breaker.get_state(), CircuitState.HALF_OPEN)

    def test_circuit_breaker_closes_after_success_threshold(self):
        """Test circuit breaker closes after success threshold in half-open."""
        self.circuit_breaker.state = CircuitState.HALF_OPEN
        for _ in range(self.config.success_threshold):
            self.circuit_breaker.record_success()

        self.assertEqual(self.circuit_breaker.get_state(), CircuitState.CLOSED)

    def test_circuit_breaker_reopens_on_half_open_failure(self):
        """Test circuit breaker reopens on failure in half-open state."""
        self.circuit_breaker.state = CircuitState.HALF_OPEN
        self.circuit_breaker.record_failure()
        self.assertEqual(self.circuit_breaker.get_state(), CircuitState.OPEN)

    def test_circuit_breaker_respects_half_open_max_calls(self):
        """Test circuit breaker respects max calls in half-open state."""
        self.circuit_breaker.state = CircuitState.HALF_OPEN
        self.circuit_breaker.half_open_calls = self.config.half_open_max_calls
        self.assertFalse(self.circuit_breaker.can_attempt())

    def test_circuit_breaker_resets_failure_count_on_success(self):
        """Test failure count resets on success."""
        self.circuit_breaker.record_failure()
        self.circuit_breaker.record_failure()
        self.assertEqual(self.circuit_breaker.failure_count, 2)

        self.circuit_breaker.record_success()
        self.assertEqual(self.circuit_breaker.failure_count, 0)


class TestHttpClient(unittest.TestCase):
    """Test HTTP client functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = HttpClient(
            base_url="https://api.example.com",
            timeout=5.0,
            max_retries=3,
            retry_backoff_factor=0.1,
        )

    @patch("requests.request")
    def test_get_request_success(self, mock_request):
        """Test successful GET request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_response

        result = self.client.get("/test")

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["data"], {"result": "success"})
        mock_request.assert_called_once()

    @patch("requests.request")
    def test_post_request_success(self, mock_request):
        """Test successful POST request."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 123}
        mock_response.content = b'{"id": 123}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_response

        result = self.client.post("/create", json={"name": "test"})

        self.assertEqual(result["status_code"], 201)
        self.assertEqual(result["data"], {"id": 123})

    @patch("requests.request")
    def test_put_request_success(self, mock_request):
        """Test successful PUT request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"updated": True}
        mock_response.content = b'{"updated": true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_response

        result = self.client.put("/update/123", json={"name": "updated"})

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["data"], {"updated": True})

    @patch("requests.request")
    def test_delete_request_success(self, mock_request):
        """Test successful DELETE request."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_response.json.return_value = None
        mock_response.content = b''
        mock_response.headers = {}
        mock_request.return_value = mock_response

        result = self.client.delete("/delete/123")

        self.assertEqual(result["status_code"], 204)
        self.assertIsNone(result["data"])

    @patch("requests.request")
    def test_retry_on_timeout(self, mock_request):
        """Test retry behavior on timeout."""
        mock_request.side_effect = requests.exceptions.Timeout("Request timed out")

        with self.assertRaises(requests.exceptions.Timeout):
            self.client.get("/test")

        self.assertEqual(mock_request.call_count, self.client.max_retries)

    @patch("requests.request")
    def test_retry_on_connection_error(self, mock_request):
        """Test retry behavior on connection error."""
        mock_request.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with self.assertRaises(requests.exceptions.ConnectionError):
            self.client.get("/test")

        self.assertEqual(mock_request.call_count, self.client.max_retries)

    @patch("requests.request")
    def test_retry_on_server_error(self, mock_request):
        """Test retry behavior on 500 error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'Internal Server Error'
        mock_request.return_value = mock_response

        with self.assertRaises(Exception):
            self.client.get("/test")

        self.assertEqual(mock_request.call_count, self.client.max_retries)

    @patch("requests.request")
    def test_retry_eventually_succeeds(self, mock_request):
        """Test request succeeds after retries."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"Content-Type": "application/json"}

        mock_request.side_effect = [
            requests.exceptions.Timeout("Timeout"),
            requests.exceptions.ConnectionError("Connection error"),
            mock_response,
        ]

        result = self.client.get("/test")

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(mock_request.call_count, 3)

    @patch("requests.request")
    def test_exponential_backoff(self, mock_request):
        """Test exponential backoff between retries."""
        mock_request.side_effect = requests.exceptions.Timeout("Timeout")

        with patch("time.sleep") as mock_sleep:
            with self.assertRaises(requests.exceptions.Timeout):
                self.client.get("/test")

            # For 3 retries, we have 2 sleep calls (between attempt 1-2 and 2-3)
            expected_delays = [0.1, 0.2]  # 0.1 * 2^0, 0.1 * 2^1
            actual_delays = [call[0][0] for call in mock_sleep.call_args_list]
            self.assertEqual(len(actual_delays), 2)
            for expected, actual in zip(expected_delays, actual_delays):
                self.assertAlmostEqual(expected, actual, places=5)

    @patch("requests.request")
    def test_circuit_breaker_opens_on_failures(self, mock_request):
        """Test circuit breaker opens after multiple failures."""
        mock_request.side_effect = requests.exceptions.Timeout("Timeout")

        # Trigger enough failures to open circuit
        config = CircuitBreakerConfig(failure_threshold=2)
        client = HttpClient(
            base_url="https://api.example.com",
            circuit_breaker_config=config,
        )

        with self.assertRaises(requests.exceptions.Timeout):
            client.get("/test1")

        with self.assertRaises(requests.exceptions.Timeout):
            client.get("/test2")

        self.assertEqual(client.get_circuit_breaker_state(), CircuitState.OPEN)

    @patch("requests.request")
    def test_circuit_breaker_blocks_requests_when_open(self, mock_request):
        """Test circuit breaker blocks requests in open state."""
        # Manually set circuit breaker to open
        self.client._circuit_breaker.state = CircuitState.OPEN
        self.client._circuit_breaker.opened_at = time.time()

        with self.assertRaises(Exception) as context:
            self.client.get("/test")

        self.assertIn("Circuit breaker is open", str(context.exception))
        mock_request.assert_not_called()

    @patch("requests.request")
    def test_circuit_breaker_allows_request_after_timeout(self, mock_request):
        """Test circuit breaker allows requests after timeout."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_response

        config = CircuitBreakerConfig(failure_threshold=1, success_threshold=1, timeout=0.1)
        client = HttpClient(
            base_url="https://api.example.com",
            circuit_breaker_config=config,
        )

        # Open the circuit
        client._circuit_breaker.state = CircuitState.OPEN
        client._circuit_breaker.opened_at = time.time() - 0.2

        # Should now be in half-open and allow request
        result = client.get("/test")

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(client.get_circuit_breaker_state(), CircuitState.CLOSED)

    def test_url_building(self):
        """Test URL building with various inputs."""
        client = HttpClient(base_url="https://api.example.com")

        # Test relative path
        url1 = client._build_url("/users")
        self.assertEqual(url1, "https://api.example.com/users")

        # Test relative path without leading slash
        url2 = client._build_url("users")
        self.assertEqual(url2, "https://api.example.com/users")

        # Test absolute URL
        url3 = client._build_url("https://other.com/test")
        self.assertEqual(url3, "https://other.com/test")

    @patch("requests.request")
    def test_request_with_custom_headers(self, mock_request):
        """Test request with custom headers."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {}
        mock_request.return_value = mock_response

        headers = {"Authorization": "Bearer token123", "Custom-Header": "value"}
        self.client.get("/test", headers=headers)

        call_args = mock_request.call_args
        self.assertEqual(call_args[1]["headers"]["Authorization"], "Bearer token123")
        self.assertEqual(call_args[1]["headers"]["Custom-Header"], "value")

    @patch("requests.request")
    def test_request_with_query_params(self, mock_request):
        """Test request with query parameters."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {}
        mock_request.return_value = mock_response

        params = {"page": 1, "limit": 10}
        self.client.get("/test", params=params)

        call_args = mock_request.call_args
        self.assertEqual(call_args[1]["params"], params)

    def test_reset_circuit_breaker(self):
        """Test circuit breaker reset functionality."""
        self.client._circuit_breaker.state = CircuitState.OPEN
        self.client._circuit_breaker.failure_count = 10

        self.client.reset_circuit_breaker()

        self.assertEqual(self.client.get_circuit_breaker_state(), CircuitState.CLOSED)
        self.assertEqual(self.client._circuit_breaker.failure_count, 0)


if __name__ == "__main__":
    unittest.main()
