"""Tests for notification.py module."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from notification import NotificationService

WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/test"


class TestNotificationService(unittest.TestCase):
    """Test cases for NotificationService."""

    def setUp(self):
        """Set up test fixtures."""
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.webhook_url = WEBHOOK_URL
        self._init_db()

    def tearDown(self):
        """Clean up test fixtures."""
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _init_db(self):
        """Initialize test database with outbox table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                external_id TEXT,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, action)
            );
        """
        )
        conn.commit()
        conn.close()

    def _add_pending_notification(self, task_id: str, action: str, message: str) -> int:
        """Helper to add a pending notification to the outbox."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO outbox (task_id, action, payload, status) VALUES (?, ?, ?, 'pending');",
            (task_id, action, json.dumps({"message": message})),
        )
        outbox_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return outbox_id

    # Test 1: Initialization with webhook URL
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "https://test.example.com/webhook"})
    def test_init_with_webhook_url(self):
        """Test initialization when FEISHU_WEBHOOK_URL is set."""
        service = NotificationService(self.db_path)
        self.assertEqual(service.webhook_url, "https://test.example.com/webhook")

    # Test 2: Initialization without webhook URL
    @patch.dict(os.environ, {}, clear=True)
    def test_init_without_webhook_url(self):
        """Test initialization when FEISHU_WEBHOOK_URL is not set."""
        service = NotificationService(self.db_path)
        self.assertIsNone(service.webhook_url)

    # Test 3: send_pending with no webhook URL returns 0
    @patch.dict(os.environ, {}, clear=True)
    @patch("notification.logger")
    def test_send_pending_no_webhook_url(self, mock_logger):
        """Test send_pending returns 0 when webhook URL is not configured."""
        self._add_pending_notification("task1", "notify_done", "Test message")
        service = NotificationService(self.db_path)
        result = service.send_pending()
        self.assertEqual(result, 0)
        mock_logger.warning.assert_called()

    # Test 4: send_pending with no pending notifications returns 0
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    def test_send_pending_no_notifications(self):
        """Test send_pending returns 0 when there are no pending notifications."""
        service = NotificationService(self.db_path)
        result = service.send_pending()
        self.assertEqual(result, 0)

    # Test 5: send_pending successfully sends one notification
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_send_pending_one_notification(self, mock_post):
        """Test send_pending successfully sends a single notification."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        self._add_pending_notification("task1", "notify_done", "Task completed")

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 1)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], self.webhook_url)
        self.assertEqual(call_args[1]["timeout"], 10)

    # Test 6: send_pending sends multiple notifications
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_send_pending_multiple_notifications(self, mock_post):
        """Test send_pending sends multiple pending notifications."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        self._add_pending_notification("task1", "notify_done", "Task 1 done")
        self._add_pending_notification("task2", "notify_failed", "Task 2 failed")
        self._add_pending_notification("task3", "notify_done", "Task 3 done")

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 3)
        self.assertEqual(mock_post.call_count, 3)

    # Test 7: Card format is correct
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_card_format(self, mock_post):
        """Test that Feishu card format is correct."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        self._add_pending_notification("task123", "notify_done", "Test message")

        service = NotificationService(self.db_path)
        service.send_pending()

        call_args = mock_post.call_args
        card_data = call_args[1]["json"]

        self.assertEqual(card_data["msg_type"], "interactive")
        self.assertIn("header", card_data["card"])
        self.assertEqual(card_data["card"]["header"]["title"]["tag"], "plain_text")
        self.assertEqual(card_data["card"]["header"]["title"]["content"], "Task task123: notify_done")
        self.assertEqual(len(card_data["card"]["elements"]), 1)
        self.assertEqual(card_data["card"]["elements"][0]["tag"], "div")
        self.assertEqual(card_data["card"]["elements"][0]["text"]["tag"], "lark_md")
        self.assertEqual(card_data["card"]["elements"][0]["text"]["content"], "Test message")

    # Test 8: Updates status to sent after successful push
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_updates_status_to_sent(self, mock_post):
        """Test that notification status is updated to sent after successful push."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        outbox_id = self._add_pending_notification("task1", "notify_done", "Message")

        service = NotificationService(self.db_path)
        service.send_pending()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, sent_at FROM outbox WHERE id = ?;", (outbox_id,)).fetchone()
        conn.close()

        self.assertEqual(row["status"], "sent")
        self.assertIsNotNone(row["sent_at"])

    # Test 9: Only processes pending notifications
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_only_processes_pending(self, mock_post):
        """Test that only pending notifications are processed."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        # Add one pending and one already sent
        self._add_pending_notification("task1", "notify_done", "Pending message")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO outbox (task_id, action, payload, status, sent_at) VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP);",
            ("task2", "notify_done", json.dumps({"message": "Already sent"})),
        )
        conn.commit()
        conn.close()

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 1)
        mock_post.assert_called_once()

    # Test 10: Handles webhook request failure gracefully
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_handles_webhook_failure(self, mock_post):
        """Test that webhook failure is handled gracefully."""
        mock_post.side_effect = requests.RequestException("Network error")

        outbox_id = self._add_pending_notification("task1", "notify_done", "Message")

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 0)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, last_error FROM outbox WHERE id = ?;", (outbox_id,)).fetchone()
        conn.close()

        self.assertEqual(row["status"], "failed")
        self.assertIn("Network error", row["last_error"])

    # Test 11: Continues processing after one notification fails
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_continues_after_failure(self, mock_post):
        """Test that processing continues after one notification fails."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'

        # First call fails, second succeeds
        mock_post.side_effect = [
            requests.RequestException("First fails"),
            mock_response,
        ]

        self._add_pending_notification("task1", "notify_done", "First message")
        self._add_pending_notification("task2", "notify_done", "Second message")

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 1)
        self.assertEqual(mock_post.call_count, 2)

    # Test 12: _push_feishu raises error without webhook URL
    @patch.dict(os.environ, {}, clear=True)
    def test_push_feishu_no_webhook_url(self):
        """Test _push_feishu raises RuntimeError when webhook URL is not set."""
        service = NotificationService(self.db_path)
        with self.assertRaises(RuntimeError) as context:
            service._push_feishu("Title", "Body")
        self.assertIn("FEISHU_WEBHOOK_URL not configured", str(context.exception))

    # Test 13: _push_feishu makes correct POST request
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_push_feishu_post_request(self, mock_post):
        """Test that _push_feishu makes correct POST request."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        service = NotificationService(self.db_path)
        service._push_feishu("Test Title", "Test Body")

        mock_post.assert_called_once_with(self.webhook_url, json=unittest.mock.ANY, timeout=10)

    # Test 14: _push_feishu raises on HTTP error
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_push_feishu_http_error(self, mock_post):
        """Test that _push_feishu raises exception on HTTP error."""
        mock_post.side_effect = requests.HTTPError("404 Not Found")

        service = NotificationService(self.db_path)
        with self.assertRaises(requests.HTTPError):
            service._push_feishu("Title", "Body")

    # Test 15: Payload without message key uses string representation
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_payload_without_message_key(self, mock_post):
        """Test handling of payload without message key."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO outbox (task_id, action, payload, status) VALUES (?, ?, ?, 'pending');",
            ("task1", "notify_done", json.dumps({"other_key": "other_value"})),
        )
        conn.commit()
        conn.close()

        service = NotificationService(self.db_path)
        service.send_pending()

        call_args = mock_post.call_args
        body_content = call_args[1]["json"]["card"]["elements"][0]["text"]["content"]
        # Should use string representation of the payload
        self.assertIn("other_key", body_content)

    # Test 16: Empty pending list
    @patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": WEBHOOK_URL})
    @patch("notification.requests.post")
    def test_empty_pending_list(self, mock_post):
        """Test with an empty database."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"code":0}'
        mock_post.return_value = mock_response

        service = NotificationService(self.db_path)
        result = service.send_pending()

        self.assertEqual(result, 0)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
