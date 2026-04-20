#!/usr/bin/env python3
"""Hermes 编排脚本 v2：更直接的 prompt，避免 Claude Code 提问"""
import sys
import os
import time
import json
import subprocess

sys.path.insert(0, os.path.dirname(__file__))

from executor import TaskExecutor
from task_registry import TaskRegistry
from router import TaskRouter
from outbox import Outbox
from reconciler import Reconciler
from config import DB_PATH

DIRECTIVE = (
    "\n\nIMPORTANT: Do NOT ask questions. Do NOT list options. "
    "Make reasonable design decisions yourself and implement immediately. "
    "Write code directly. Run tests to verify. "
    "Create ONLY the specified new files, do not modify existing files.\n"
)

tasks = [
    {
        "id": "fix-notification",
        "desc": (
            "Create notification.py in the project root. This module pushes pending notifications from the outbox table to Feishu.\n\n"
            "Implementation:\n"
            "- Class NotificationService(db_path)\n"
            "- send_pending(): query outbox WHERE sent=0, for each row call _push_feishu()\n"
            "- _push_feishu(webhook_url, title, body): POST to FEISHU_WEBHOOK_URL env var with Feishu card JSON format\n"
            "- Card format: {\"msg_type\":\"interactive\",\"card\":{\"header\":{\"title\":{\"tag\":\"plain_text\",\"content\":title}},\"elements\":[{\"tag\":\"div\",\"text\":{\"tag\":\"lark_md\",\"content\":body}}]}}\n"
            "- After successful push, UPDATE outbox SET sent=1 WHERE id=?\n"
            "- If FEISHU_WEBHOOK_URL not set, log warning and skip (don't crash)\n"
            "- Also create tests/test_notification.py with 15+ tests using unittest.mock to mock requests.post\n\n"
            "Reference: Read outbox.py first to understand the table schema.\n"
            "Do NOT modify any existing files. Create only notification.py and tests/test_notification.py."
            + DIRECTIVE
        ),
    },
    {
        "id": "fix-doctor",
        "desc": (
            "Create doctor.py in the project root. CLI tool: python3 doctor.py\n\n"
            "Implement these checks (each returns pass/warn/fail):\n"
            "1. claude_code: shutil.which('claude') exists\n"
            "2. codex: shutil.which('codex') exists\n"
            "3. git: shutil.which('git') and git config user.name is not empty\n"
            "4. database: sqlite3.connect(DB_PATH) works, PRAGMA integrity_check\n"
            "5. proxy: if HTTP_PROXY set, try urllib.request.urlopen through it with 5s timeout\n"
            "6. disk: shutil.disk_usage(WORKTREE_BASE).free > 1GB\n"
            "7. github: subprocess.run(['gh', 'auth', 'status'], capture_output=True).returncode == 0\n"
            "8. feishu: FEISHU_APP_ID and FEISHU_APP_SECRET env vars set\n\n"
            "Output format: Unicode box table with columns: Check, Status, Detail\n"
            "Exit code: 0=all pass, 1=warnings, 2=errors\n"
            "Use argparse: --json flag for JSON output\n\n"
            "Also create tests/test_doctor.py with 15+ tests using unittest.mock.\n"
            "Reference: Read config.py for DB_PATH and WORKTREE_BASE.\n"
            "Do NOT modify any existing files. Create only doctor.py and tests/test_doctor.py."
            + DIRECTIVE
        ),
    },
    {
        "id": "fix-cost-monitor",
        "desc": (
            "Create cost_monitor.py in the project root.\n\n"
            "Implementation:\n"
            "- On import, run ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_tokens INTEGER DEFAULT 0\n"
            "- On import, run ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_usd REAL DEFAULT 0.0\n"
            "- Class CostMonitor(db_path)\n"
            "- update_cost(task_id, input_tokens, output_tokens): calculate USD using rates from config\n"
            "- get_task_cost(task_id) -> dict with tokens/usd\n"
            "- get_daily_cost(date_str) -> total USD for that day\n"
            "- cost_report(days=7) -> list of daily costs\n"
            "- check_budget(task_id, limit_usd=1.0) -> bool (True if under budget)\n"
            "- Default rates: input $0.5/M tokens, output $1.5/M tokens (configurable via env vars)\n\n"
            "Also create tests/test_cost_monitor.py with 12+ tests.\n"
            "Reference: Read config.py and task_registry.py for DB schema.\n"
            "Do NOT modify any existing files. Create only cost_monitor.py and tests/test_cost_monitor.py."
            + DIRECTIVE
        ),
    },
    {
        "id": "fix-cleanup",
        "desc": (
            "Create cleanup.py in the project root. CLI tool: python3 cleanup.py [--dry-run] [--max-age-days 7]\n\n"
            "Implementation:\n"
            "- cleanup_worktrees(max_age_hours=24): find worktree dirs modified >24h ago for done/failed tasks, remove them\n"
            "- cleanup_old_tasks(max_age_days=7): DELETE FROM tasks WHERE status IN ('done','failed') AND updated_at < datetime('now', '-7 days')\n"
            "- cleanup_old_logs(max_age_days=30): find .log files in logs/ dir older than 30 days, remove them\n"
            "- cleanup_zombie_tasks(): find tasks with status='running' but PID not alive, mark as 'failed'\n"
            "- Each function returns a list of cleaned items with paths/names\n"
            "- main() calls all four, prints summary table, returns exit code\n"
            "- --dry-run: print what would be deleted but don't delete\n\n"
            "Also create tests/test_cleanup.py with 12+ tests.\n"
            "Reference: Read config.py for paths, reconciler.py for zombie detection pattern.\n"
            "Do NOT modify any existing files. Create only cleanup.py and tests/test_cleanup.py."
            + DIRECTIVE
        ),
    },
]


def run_task(executor, task):
    """Submit a single task and return the result."""
    print(f"\n{'='*60}")
    print(f"📤 [{task['id']}] 开始执行...")
    print(f"{'='*60}")

    try:
        result = executor.submit_and_execute(task["desc"])
        status = result.get("status", "unknown")
        exit_code = result.get("exit_code")
        duration = result.get("duration_ms", 0)

        print(f"   状态: {status}")
        if exit_code is not None:
            print(f"   退出码: {exit_code}")
        if duration:
            print(f"   耗时: {duration/1000:.1f}s")

        return {"id": task["id"], "status": status, "exit_code": exit_code}
    except Exception as e:
        print(f"❌ 失败: {e}")
        return {"id": task["id"], "status": "error", "error": str(e)}


def collect_worktree_results(task_id, worktree_base):
    """Find and copy new files from worktree to project root."""
    import glob
    import shutil

    # Find the most recent worktree for this task
    worktrees = sorted(glob.glob(os.path.join(worktree_base, f"feat-*{task_id}*")),
                       key=os.path.getmtime, reverse=True)

    if not worktrees:
        # Try broader match
        worktrees = sorted(glob.glob(os.path.join(worktree_base, "feat-*")),
                           key=os.path.getmtime, reverse=True)[:1]

    if not worktrees:
        print(f"   ⚠️ 未找到 worktree")
        return []

    wt = worktrees[0]
    copied = []

    # Find new .py files not in the main repo
    main_files = set(os.listdir(os.path.dirname(__file__)))
    for f in os.listdir(wt):
        if f.endswith('.py') and f not in main_files and not f.startswith('.'):
            src = os.path.join(wt, f)
            dst = os.path.join(os.path.dirname(__file__), f)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                copied.append(f)
                print(f"   📄 复制 {f}")

    # Check tests/ dir
    wt_tests = os.path.join(wt, "tests")
    main_tests = os.path.join(os.path.dirname(__file__), "tests")
    if os.path.isdir(wt_tests):
        for f in os.listdir(wt_tests):
            if f.endswith('.py') and f.startswith('test_'):
                src = os.path.join(wt_tests, f)
                dst = os.path.join(main_tests, f)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    copied.append(f"tests/{f}")
                    print(f"   📄 复制 tests/{f}")

    return copied


def main():
    registry = TaskRegistry(DB_PATH)
    executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))

    results = []
    for i, task in enumerate(tasks):
        result = run_task(executor, task)
        results.append(result)

        # Collect output files from worktree
        if result["status"] == "done":
            collected = collect_worktree_results(task["id"], "/home/txs/hermes-agent/worktrees")
            if not collected:
                print(f"   ⚠️ worktree 中未找到新文件，可能需要手动检查")

            # Run tests for new modules
            for f in collected:
                if f.startswith("tests/test_"):
                    test_file = f
                    print(f"   🧪 运行 {test_file}...")
                    try:
                        proc = subprocess.run(
                            ["python3", "-m", "pytest", test_file, "-q", "--tb=short"],
                            capture_output=True, text=True, timeout=60,
                            cwd=os.path.dirname(__file__)
                        )
                        if proc.returncode == 0:
                            print(f"   ✅ {test_file} 通过")
                        else:
                            print(f"   ❌ {test_file} 失败:\n{proc.stdout[-500:]}")
                    except Exception as e:
                        print(f"   ❌ 测试异常: {e}")

        # Rate limit spacing
        if i < len(tasks) - 1:
            wait = 35
            print(f"\n⏳ 等待 {wait}s 避免速率限制...")
            time.sleep(wait)

    # Summary
    print(f"\n{'='*60}")
    print("📊 最终结果")
    print(f"{'='*60}")
    done = sum(1 for r in results if r["status"] == "done")
    print(f"  完成: {done}/{len(tasks)}")
    for r in results:
        emoji = "✅" if r["status"] == "done" else "❌"
        detail = f"exit={r['exit_code']}" if r.get("exit_code") is not None else r.get("error", "")[:60]
        print(f"  {emoji} {r['id']}: {detail}")

    return 0 if done == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(main())
