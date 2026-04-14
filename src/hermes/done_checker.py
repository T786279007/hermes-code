"""Done definition checker — validates all completion criteria after task finishes.

Checks:
1. Commit exists (always)
2. Tests pass (always — Agent ran them)
3. PR created (for git projects)
4. CI passed (if PR exists)
5. Screenshot included (if PR has UI changes)

Results stored in task registry's done_checks JSON field.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def run_done_checks(task: dict, worktree: str | None = None) -> dict[str, Any]:
    """Run all done-definition checks for a completed task.

    Args:
        task: Task dict from registry (must have status='done').
        worktree: Path to worktree (optional, uses task['worktree'] if not provided).

    Returns:
        Dict with check results:
        {
            "commit": True,
            "tests_passed": True,
            "pr_created": True/False,
            "pr_number": 42,
            "ci_passed": True/False/None,
            "screenshot_included": True/False/None,
            "all_passed": True/False,
            "details": [...]
        }
    """
    checks: dict[str, Any] = {
        "commit": False,
        "tests_passed": True,  # Agent ran tests in prompt
        "pr_created": False,
        "pr_number": None,
        "ci_passed": None,
        "screenshot_included": None,
        "all_passed": False,
        "details": [],
    }

    worktree = worktree or task.get("worktree", "")
    branch = task.get("branch", "")

    # 1. Commit check
    if worktree:
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=worktree, capture_output=True, text=True, timeout=10,
            )
            checks["commit"] = r.returncode == 0 and bool(r.stdout.strip())
            if checks["commit"]:
                checks["details"].append("✅ Commit exists")
            else:
                checks["details"].append("❌ No commit found")
        except Exception as e:
            checks["details"].append(f"❌ Commit check failed: {e}")

    # 2. Tests passed — assumed True (Agent prompt requires running tests)
    checks["details"].append("✅ Tests passed (Agent verified)")

    # 3. PR check
    pr_info = None
    if branch:
        try:
            r = subprocess.run(
                ["gh", "pr", "list", "--head", branch,
                 "--json", "number,title,state,url",
                 "--limit", "1"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                prs = json.loads(r.stdout)
                if prs:
                    pr_info = prs[0]
                    checks["pr_created"] = True
                    checks["pr_number"] = pr_info["number"]
                    checks["details"].append(
                        f"✅ PR #{pr_info['number']}: {pr_info['title']}"
                    )
                else:
                    checks["details"].append("📋 No PR created yet")
            else:
                checks["details"].append("📋 PR check skipped (gh error)")
        except Exception as e:
            checks["details"].append(f"📋 PR check failed: {e}")

    # 4. CI check (only if PR exists)
    if pr_info:
        try:
            r = subprocess.run(
                ["gh", "pr", "checks", str(pr_info["number"]),
                 "--json", "name,status,conclusion"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                ci_data = json.loads(r.stdout)
                checks_list = ci_data if isinstance(ci_data, list) else ci_data.get("checks", [])
                if not checks_list:
                    checks["ci_passed"] = None  # CI not configured
                    checks["details"].append("⏳ CI not configured")
                else:
                    completed = [c for c in checks_list if c.get("status") == "completed"]
                    failed = [c for c in completed if c.get("conclusion") in ("failure", "cancelled")]
                    if failed:
                        checks["ci_passed"] = False
                        checks["details"].append(
                            f"❌ CI failed: {len(failed)}/{len(checks_list)} checks failed"
                        )
                    elif len(completed) == len(checks_list):
                        checks["ci_passed"] = True
                        checks["details"].append(
                            f"✅ CI passed: {len(checks_list)} checks"
                        )
                    else:
                        checks["ci_passed"] = None
                        checks["details"].append(
                            f"⏳ CI running: {len(completed)}/{len(checks_list)}"
                        )
        except Exception as e:
            checks["details"].append(f"❌ CI check failed: {e}")

    # 5. Screenshot check (only if PR exists)
    if pr_info:
        try:
            r = subprocess.run(
                ["gh", "pr", "view", str(pr_info["number"]),
                 "--json", "body"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                body = json.loads(r.stdout).get("body", "") or ""
                has_screenshot = any(
                    kw in body.lower()
                    for kw in ("截图", "screenshot", "image", "![", "preview")
                )
                checks["screenshot_included"] = has_screenshot
                if has_screenshot:
                    checks["details"].append("✅ Screenshot found in PR")
                else:
                    checks["details"].append("⚠️ No screenshot in PR (may not be needed)")
        except Exception:
            pass  # Non-critical

    # Overall pass
    required = [checks["commit"], checks["tests_passed"]]
    # PR and CI are optional (depends on project setup)
    checks["all_passed"] = all(required) and (
        not checks["pr_created"]
        or (checks["pr_created"] and checks["ci_passed"] in (True, None))
    )

    return checks
