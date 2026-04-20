"""Feishu notification service for outbox processing."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending pending notifications from outbox to Feishu.

    Queries the outbox table for pending notifications and pushes them
    via Feishu webhook API.
    """

    def __init__(self, db_path: str):
        """Initialize notification service.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self.webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK_URL not set, notifications will be skipped")

    def send_pending(self) -> int:
        """Send all pending notifications from the outbox.

        Queries rows where status='pending', pushes each to Feishu,
        and marks them as sent.

        Returns:
            Number of notifications successfully sent.
        """
        if not self.webhook_url:
            logger.warning("Cannot send notifications: FEISHU_WEBHOOK_URL not configured")
            return 0

        import sqlite3

        sent_count = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, task_id, action, payload FROM outbox WHERE status = 'pending';"
            )
            pending_rows = cursor.fetchall()

            for row in pending_rows:
                outbox_id = row["id"]
                task_id = row["task_id"]
                action = row["action"]
                payload = json.loads(row["payload"])

                title = f"Task {task_id}: {action}"
                body = payload.get("message", str(payload))

                try:
                    self._push_feishu(title, body)
                    conn.execute(
                        "UPDATE outbox SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (outbox_id,),
                    )
                    sent_count += 1
                    logger.info("Notification sent: outbox_id=%s task_id=%s action=%s", outbox_id, task_id, action)
                except Exception as e:
                    logger.error("Failed to send notification: outbox_id=%s error=%s", outbox_id, e)
                    conn.execute(
                        "UPDATE outbox SET status = 'failed', last_error = ? WHERE id = ?;",
                        (str(e), outbox_id),
                    )

        return sent_count

    def _push_feishu(self, title: str, body: str) -> None:
        """Push a notification to Feishu via webhook.

        Args:
            title: Notification title.
            body: Notification body content (Markdown supported).

        Raises:
            requests.RequestException: If the webhook request fails.
        """
        if not self.webhook_url:
            raise RuntimeError("FEISHU_WEBHOOK_URL not configured")

        card_data = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    }
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": body,
                        },
                    }
                ],
            },
        }

        response = requests.post(self.webhook_url, json=card_data, timeout=10)
        response.raise_for_status()
        logger.debug("Feishu webhook response: %s", response.text)
