#!/usr/bin/env python3
"""Hermes Doctor - System health check CLI tool.

Performs system checks and reports status with pass/warn/fail results.
Exit codes: 0=all pass, 1=warnings, 2=errors.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

# Import from config
from config import DB_PATH, WORKTREE_BASE


# Status codes
STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"


class CheckResult:
    """Result of a single health check."""

    def __init__(self, name: str, status: str, detail: str):
        self.name = name
        self.status = status
        self.detail = detail


def check_claude_code() -> CheckResult:
    """Check if claude CLI is available."""
    claude_path = shutil.which("claude")
    if claude_path:
        return CheckResult("claude_code", STATUS_PASS, f"Found at {claude_path}")
    return CheckResult("claude_code", STATUS_FAIL, "claude CLI not found in PATH")


def check_codex() -> CheckResult:
    """Check if codex CLI is available."""
    codex_path = shutil.which("codex")
    if codex_path:
        return CheckResult("codex", STATUS_PASS, f"Found at {codex_path}")
    return CheckResult("codex", STATUS_FAIL, "codex CLI not found in PATH")


def check_git() -> CheckResult:
    """Check if git is available and configured."""
    git_path = shutil.which("git")
    if not git_path:
        return CheckResult("git", STATUS_FAIL, "git not found in PATH")

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        username = result.stdout.strip()
        if username:
            return CheckResult("git", STATUS_PASS, f"Configured as '{username}'")
        return CheckResult("git", STATUS_WARN, "git user.name not configured")
    except subprocess.TimeoutExpired:
        return CheckResult("git", STATUS_WARN, "git config timed out")
    except Exception as e:
        return CheckResult("git", STATUS_WARN, f"git config error: {e}")


def check_database() -> CheckResult:
    """Check if database is accessible and valid."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()

        if result and result[0] == "ok":
            return CheckResult("database", STATUS_PASS, f"Database OK at {DB_PATH}")
        return CheckResult("database", STATUS_FAIL, f"Integrity check failed: {result[0]}")
    except sqlite3.OperationalError as e:
        return CheckResult("database", STATUS_FAIL, f"Cannot open database: {e}")
    except Exception as e:
        return CheckResult("database", STATUS_WARN, f"Database check error: {e}")


def check_proxy() -> CheckResult:
    """Check if HTTP_PROXY works (if set)."""
    proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if not proxy_url:
        return CheckResult("proxy", STATUS_PASS, "No proxy configured")

    try:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        opener.open("http://www.google.com", timeout=5).close()
        return CheckResult("proxy", STATUS_PASS, f"Proxy OK: {proxy_url}")
    except urllib.request.URLError as e:
        return CheckResult("proxy", STATUS_WARN, f"Proxy connection failed: {e}")
    except Exception as e:
        return CheckResult("proxy", STATUS_WARN, f"Proxy check error: {e}")


def check_disk() -> CheckResult:
    """Check if there's enough disk space."""
    try:
        usage = shutil.disk_usage(str(WORKTREE_BASE))
        free_gb = usage.free / (1024**3)
        if free_gb >= 1.0:
            return CheckResult("disk", STATUS_PASS, f"{free_gb:.2f} GB free")
        return CheckResult("disk", STATUS_WARN, f"Only {free_gb:.2f} GB free (< 1 GB)")
    except Exception as e:
        return CheckResult("disk", STATUS_FAIL, f"Cannot check disk usage: {e}")


def check_github() -> CheckResult:
    """Check if GitHub CLI is authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return CheckResult("github", STATUS_PASS, "GitHub CLI authenticated")
        return CheckResult("github", STATUS_FAIL, "GitHub CLI not authenticated")
    except FileNotFoundError:
        return CheckResult("github", STATUS_WARN, "GitHub CLI not installed")
    except subprocess.TimeoutExpired:
        return CheckResult("github", STATUS_WARN, "gh auth status timed out")
    except Exception as e:
        return CheckResult("github", STATUS_WARN, f"GitHub check error: {e}")


def check_feishu() -> CheckResult:
    """Check if Feishu credentials are configured."""
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")

    if app_id and app_secret:
        return CheckResult("feishu", STATUS_PASS, "Feishu credentials configured")
    if app_id or app_secret:
        return CheckResult("feishu", STATUS_WARN, "Feishu partially configured (missing app_id or app_secret)")
    return CheckResult("feishu", STATUS_WARN, "Feishu credentials not configured")


def run_all_checks() -> List[CheckResult]:
    """Run all health checks."""
    return [
        check_claude_code(),
        check_codex(),
        check_git(),
        check_database(),
        check_proxy(),
        check_disk(),
        check_github(),
        check_feishu(),
    ]


def format_table(results: List[CheckResult]) -> str:
    """Format results as a Unicode box table."""
    # Box drawing characters
    TOP_LEFT = "┌"
    TOP_RIGHT = "┐"
    BOTTOM_LEFT = "└"
    BOTTOM_RIGHT = "┘"
    HORIZONTAL = "─"
    VERTICAL = "│"
    CROSS = "┼"

    # Column widths
    check_width = max(len(r.name) for r in results)
    status_width = max(len(r.status) for r in results)
    detail_width = max(len(r.detail) for r in results)

    # Ensure minimum widths
    check_width = max(check_width, len("Check"))
    status_width = max(status_width, len("Status"))
    detail_width = max(detail_width, len("Detail"))

    # Build separators
    separator = f"{TOP_LEFT}{HORIZONTAL * (check_width + 2)}{CROSS}{HORIZONTAL * (status_width + 2)}{CROSS}{HORIZONTAL * (detail_width + 2)}{TOP_RIGHT}"
    middle_sep = f"{VERTICAL}{HORIZONTAL * (check_width + 2)}{CROSS}{HORIZONTAL * (status_width + 2)}{CROSS}{HORIZONTAL * (detail_width + 2)}{VERTICAL}"
    bottom = f"{BOTTOM_LEFT}{HORIZONTAL * (check_width + 2)}{CROSS}{HORIZONTAL * (status_width + 2)}{CROSS}{HORIZONTAL * (detail_width + 2)}{BOTTOM_RIGHT}"

    # Header row
    header = f"{VERTICAL} {check('Check', check_width)} {VERTICAL} {check('Status', status_width)} {VERTICAL} {check('Detail', detail_width)} {VERTICAL}"

    # Data rows
    rows = []
    for r in results:
        rows.append(
            f"{VERTICAL} {check(r.name, check_width)} {VERTICAL} {check(r.status, status_width)} {VERTICAL} {check(r.detail, detail_width)} {VERTICAL}"
        )

    # Combine
    output = [separator, header, middle_sep] + rows + [bottom]
    return "\n".join(output)


def check(s: str, width: int) -> str:
    """Pad string to width."""
    return s.ljust(width)


def format_json(results: List[CheckResult]) -> str:
    """Format results as JSON."""
    return json.dumps(
        [{"check": r.name, "status": r.status, "detail": r.detail} for r in results],
        indent=2,
    )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Hermes Doctor - System health check tool")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        print(format_json(results))
    else:
        print(format_table(results))

    # Exit code: 0=all pass, 1=warnings, 2=errors
    has_errors = any(r.status == STATUS_FAIL for r in results)
    has_warnings = any(r.status == STATUS_WARN for r in results)

    if has_errors:
        sys.exit(2)
    elif has_warnings:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
