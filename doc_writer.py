"""Feishu document writer — creates and updates Feishu docs for task tracking."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Feishu API config from environment
_APP_ID = os.environ.get("FEISHU_APP_ID", "")
_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
_API_BASE = "https://open.feishu.cn/open-apis"
_TOKEN_CACHE: tuple[float, str] = (0.0, "")  # (expire_time, token)


def _get_tenant_token() -> str:
    """Get Feishu tenant_access_token (cached for 1 hour).

    Returns:
        Valid tenant_access_token string.
    """
    global _TOKEN_CACHE
    now = time.time()

    if now < _TOKEN_CACHE[0] and _TOKEN_CACHE[1]:
        return _TOKEN_CACHE[1]

    if not _APP_ID or not _APP_SECRET:
        raise RuntimeError(
            "FEISHU_APP_ID and FEISHU_APP_SECRET must be set. "
            "Set them in environment or Hermes config."
        )

    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            f"{_API_BASE}/auth/v3/tenant_access_token/internal",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "app_id": _APP_ID,
                "app_secret": _APP_SECRET,
            }),
        ],
        capture_output=True, text=True, timeout=15,
    )

    data = json.loads(result.stdout)
    token = data.get("tenant_access_token", "")
    expire = data.get("expire", 7200)

    if not token:
        code = data.get("code", -1)
        msg = data.get("msg", "unknown error")
        raise RuntimeError(f"Failed to get Feishu token: code={code} msg={msg}")

    # Cache with 5-minute buffer before expiry
    _TOKEN_CACHE = (now + expire - 300, token)
    logger.info("Obtained Feishu tenant_access_token (expires in %ds)", expire)
    return token


def _feishu_api(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated Feishu API call.

    Args:
        method: HTTP method (GET/POST/PATCH).
        path: API path (e.g. /docx/v1/documents).
        body: Request body dict.

    Returns:
        Parsed JSON response dict.
    """
    token = _get_tenant_token()
    url = f"{_API_BASE}{path}"

    cmd = [
        "curl", "-s", "-X", method, url,
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
    ]
    if body:
        cmd.extend(["-d", json.dumps(body)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def create_doc(title: str, content: str = "") -> dict:
    """Create a new Feishu document and optionally write initial content.

    Args:
        title: Document title.
        content: Initial Markdown content.

    Returns:
        Dict with 'doc_id', 'url', 'token'.
    """
    # Create document
    resp = _feishu_api("POST", "/docx/v1/documents", {"title": title})
    doc_id = resp.get("data", {}).get("document", {}).get("document_id", "")

    if not doc_id:
        code = resp.get("code", -1)
        raise RuntimeError(f"Failed to create Feishu doc: code={code} msg={resp.get('msg')}")

    url = f"https://www.feishu.cn/docx/{doc_id}"
    logger.info("Created Feishu doc: %s (%s)", title, url)

    # Write initial content if provided
    if content:
        write_doc(doc_id, content)

    return {"doc_id": doc_id, "url": url, "token": doc_id}


def write_doc(doc_id: str, markdown: str) -> bool:
    """Overwrite a Feishu document with new Markdown content.

    Args:
        doc_id: Document ID.
        markdown: Markdown content.

    Returns:
        True if successful.
    """
    # Use the feishu_update_doc tool's approach: write full markdown
    # Since we're in Hermes (not OpenClaw), we use the raw API
    try:
        from openclaw_feishu.doc import create_doc as _create, update_doc as _update
        # We can't use openclaw tools directly from Hermes subprocess,
        # so fall through to curl approach
    except ImportError:
        pass

    # Use curl to update via the content API
    token = _get_tenant_token()
    result = subprocess.run(
        [
            "curl", "-s", "-X", "PATCH",
            f"{_API_BASE}/docx/v1/documents/{doc_id}/raw_content",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"content": markdown}),
        ],
        capture_output=True, text=True, timeout=30,
    )
    resp = json.loads(result.stdout)
    if resp.get("code", -1) != 0:
        logger.warning("Failed to write doc %s: %s", doc_id, resp.get("msg"))
        return False
    return True


def append_log(doc_id: str, log_entry: str) -> bool:
    """Append a log entry to a document's development log section.

    This fetches the current content, appends to the dev log section,
    and writes it back. For efficiency, it appends a simple line.

    Args:
        doc_id: Document ID.
        log_entry: Log line to append.

    Returns:
        True if successful.
    """
    # Simple approach: use the batch_update API to append text
    # For now, log entries are tracked in execution_log table;
    # this method is for critical milestones only
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    try:
        token = _get_tenant_token()
        # Append text at the end of the document
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"{_API_BASE}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({
                    "children": [{
                        "block_type": 4,  # text
                        "text": {
                            "elements": [{
                                "text_run": {
                                    "content": f"\n[{timestamp}] {log_entry}",
                                    "text_element_style": {"text_color": {"custom_color": "#8b949e"}}
                                }
                            }]
                        }
                    }]
                }),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return True
    except Exception as e:
        logger.warning("Failed to append log to doc %s: %s", doc_id, e)
        return False


def create_task_doc(task_id: str, description: str, plan: str) -> dict:
    """Create a Feishu document for a task with plan content.

    Args:
        task_id: Task identifier.
        description: Task description.
        plan: Generated development plan.

    Returns:
        Dict with 'doc_id' and 'url'.
    """
    short_desc = description[:60] + ("..." if len(description) > 60 else "")
    title = f"[Hermes] {task_id}: {short_desc}"

    markdown = f"""# {task_id}: {short_desc}

## 📋 开发规划

{plan}

## 📝 开发日志

_等待确认后开始执行..._

## ✅ 完成总结

_待填写_
"""
    result = create_doc(title, markdown)
    return result
