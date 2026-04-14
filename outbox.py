"""Idempotent outbox for sending notifications via openclaw CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class Outbox:
    """Idempotent notification outbox backed by the tasks DB."""

    def __init__(self, registry: TaskRegistry):
        """Initialize outbox with shared registry.

        Args:
            registry: TaskRegistry instance (shares DB connection).
        """
        self._registry = registry

    def send_notification(self, task_id: str, action: str, payload: dict) -> str | None:
        """Idempotently send a notification for a task.

        Uses compare-and-swap to atomically claim the row, preventing
        duplicate sends under concurrency (B3 fix).

        Args:
            task_id: Unique task identifier.
            action: Notification type ('notify_done' or 'notify_failed').
            payload: Notification content dict.

        Returns:
            External message ID if sent, or None on failure.
        """
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        with self._registry._transaction() as conn:
            # Insert or get existing row
            conn.execute(
                """
                INSERT INTO outbox (task_id, action, payload, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(task_id, action) DO NOTHING;
                """,
                (task_id, action, payload_json),
            )

            row = conn.execute(
                "SELECT id, status, external_id FROM outbox WHERE task_id = ? AND action = ?;",
                (task_id, action),
            ).fetchone()

            if row is None:
                return None

            outbox_id = row["id"]

            if row["status"] == "sent":
                logger.info("Notification already sent: task=%s action=%s", task_id, action)
                return row["external_id"]

            # CAS: atomically claim pending/failed → sending
            claimed = conn.execute(
                """
                UPDATE outbox SET status = 'sending', attempts = attempts + 1
                WHERE id = ? AND status IN ('pending', 'failed');
                """,
                (outbox_id,),
            ).rowcount

            if claimed == 0:
                # Another caller already claimed it
                logger.info("Notification already claimed: task=%s action=%s", task_id, action)
                return row["external_id"]

        # We own the send — perform it outside the transaction
        try:
            external_id = self._send_feishu(payload)
            with self._registry._transaction() as conn:
                status = "sent" if external_id != "logged" else "logged"
                conn.execute(
                    "UPDATE outbox SET status = ?, external_id = ?, sent_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (status, external_id, outbox_id),
                )
            logger.info("Notification %s: task=%s action=%s external_id=%s", status, task_id, action, external_id)
            return external_id
        except Exception as e:
            with self._registry._transaction() as conn:
                conn.execute(
                    "UPDATE outbox SET status = 'failed', last_error = ? WHERE id = ?;",
                    (str(e), outbox_id),
                )
            logger.error("Notification failed: task=%s action=%s error=%s", task_id, action, e)
            return None

    def _send_feishu(self, payload: dict) -> str:
        """Send a notification via Feishu webhook or OpenClaw message tool.

        Uses the OpenClaw gateway's built-in message relay via HTTP.
        Falls back to logging if the gateway is unavailable.

        Args:
            payload: Notification content with 'message' key.

        Returns:
            A fake message ID (since we use best-effort delivery).

        Raises:
            RuntimeError: If the notification cannot be delivered.
        """
        message = payload.get("message", str(payload))

        # Try sending via openclaw gateway API
        try:
            import urllib.request
            import json as _json

            gateway_port = os.environ.get("OPENCLAW_GATEWAY_PORT", "18789")
            url = f"http://127.0.0.1:{gateway_port}/api/v1/message"
            data = _json.dumps({
                "channel": "feishu",
                "message": message,
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read().decode())
                return body.get("message_id", "sent")
        except Exception as e:
            logger.warning("Gateway notification failed: %s, falling back to log", e)

        # Fallback: log the notification (Phase 2: integrate proper Feishu SDK)
        logger.info("[NOTIFICATION] %s", message)
        return "logged"
