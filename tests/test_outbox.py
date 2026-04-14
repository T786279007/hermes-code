#!/usr/bin/env python3
"""Comprehensive tests for outbox.py — idempotency, CAS, send_feishu fallback."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outbox import Outbox
from task_registry import TaskRegistry


class BaseOutboxTest(unittest.TestCase):
    """Base with temp DB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.registry = TaskRegistry(self.db_path)
        self.outbox = Outbox(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestSendNotification(BaseOutboxTest):
    """send_notification() tests."""

    @patch("outbox.Outbox._send_feishu", return_value="msg-001")
    def test_first_send_succeeds(self, mock_send):
        self.registry.create_task("t1", "desc", "claude-code")
        result = self.outbox.send_notification("t1", "notify_done", {"message": "Done!"})
        self.assertEqual(result, "msg-001")
        mock_send.assert_called_once()

    @patch("outbox.Outbox._send_feishu", return_value="msg-001")
    def test_idempotent_duplicate_call(self, mock_send):
        """Second call with same task_id+action should not re-send."""
        self.registry.create_task("t1", "desc", "claude-code")
        r1 = self.outbox.send_notification("t1", "notify_done", {"message": "Done!"})
        r2 = self.outbox.send_notification("t1", "notify_done", {"message": "Done!"})
        self.assertEqual(r1, "msg-001")
        self.assertEqual(r2, "msg-001")
        mock_send.assert_called_once()  # Only sent once

    @patch("outbox.Outbox._send_feishu", return_value="msg-002")
    def test_different_actions_send_independently(self, mock_send):
        self.registry.create_task("t1", "desc", "claude-code")
        r1 = self.outbox.send_notification("t1", "notify_done", {"message": "Done"})
        r2 = self.outbox.send_notification("t1", "notify_failed", {"message": "Failed"})
        self.assertEqual(r1, "msg-002")
        self.assertEqual(r2, "msg-002")
        self.assertEqual(mock_send.call_count, 2)

    @patch("outbox.Outbox._send_feishu", side_effect=RuntimeError("Network error"))
    def test_send_failure_returns_none(self, mock_send):
        self.registry.create_task("t1", "desc", "claude-code")
        result = self.outbox.send_notification("t1", "notify_done", {"message": "Done"})
        self.assertIsNone(result)

    def test_retry_after_failure(self):
        """After a failed send, next attempt should retry."""
        self.registry.create_task("t1", "desc", "claude-code")
        # First call fails
        with patch("outbox.Outbox._send_feishu", side_effect=RuntimeError("fail")):
            r1 = self.outbox.send_notification("t1", "notify_done", {"message": "Done"})
        self.assertIsNone(r1)
        # Second call retries (patch back to success)
        with patch("outbox.Outbox._send_feishu", return_value="msg-retry"):
            r2 = self.outbox.send_notification("t1", "notify_done", {"message": "Done"})
        self.assertIsNotNone(r2)

    @patch("outbox.Outbox._send_feishu", return_value="logged")
    def test_payload_serialized(self, mock_send):
        self.registry.create_task("t1", "desc", "claude-code")
        self.outbox.send_notification("t1", "notify_done", {"message": "Test", "key": "value"})
        # Check DB for serialized payload
        with self.registry._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM outbox WHERE task_id = ? AND action = ?;",
                ("t1", "notify_done"),
            ).fetchone()
        payload = json.loads(row["payload"])
        self.assertEqual(payload["message"], "Test")
        self.assertEqual(payload["key"], "value")

    @patch("outbox.Outbox._send_feishu", return_value="msg-003")
    def test_status_transitions(self, mock_send):
        """Status should go: pending → sending → sent."""
        self.registry.create_task("t1", "desc", "claude-code")
        self.outbox.send_notification("t1", "notify_done", {"message": "Done"})
        with self.registry._connect() as conn:
            row = conn.execute(
                "SELECT status FROM outbox WHERE task_id = ? AND action = ?;",
                ("t1", "notify_done"),
            ).fetchone()
        self.assertEqual(row["status"], "sent")


class TestSendFeishu(BaseOutboxTest):
    """_send_feishu() tests."""

    def test_returns_message_id(self):
        """Should return a non-empty string even on fallback."""
        result = self.outbox._send_feishu({"message": "Hello"})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    @patch.dict(os.environ, {"OPENCLAW_GATEWAY_PORT": "18789"})
    @patch("urllib.request.urlopen")
    def test_gateway_success(self, mock_urlopen):
        """Successful gateway call should return message_id."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"message_id": "gw-001"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = self.outbox._send_feishu({"message": "Test"})
        self.assertEqual(result, "gw-001")

    def test_fallback_to_log(self):
        """When gateway is unreachable, falls back to 'logged'."""
        # Port that doesn't exist
        with patch.dict(os.environ, {"OPENCLAW_GATEWAY_PORT": "19999"}):
            result = self.outbox._send_feishu({"message": "Fallback test"})
        self.assertEqual(result, "logged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
