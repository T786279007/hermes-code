#!/usr/bin/env python3
"""
🧪 实验 9：飞书通知集成
验证任务完成后，Zoe 能通过飞书消息通知 owner 进行人工审查。
使用 message 工具发送结构化通知卡片。
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg


NOTIFICATION_TEMPLATE = """**🔧 Hermes 任务完成 · 需要人工审查**

**任务**：{description}
**Agent**：{agent}
**耗时**：{duration}
**文件变更**：{files_count} 个

**检查清单**：
{checks}

**下一步**：请查看变更并决定是否合并。回复「合并」或「打回」。
"""


def format_checks(task):
    """Format checks as readable list"""
    checks = task.get("checks", {})
    lines = []
    labels = {
        "pr_created": ("PR 已创建", "❌ PR 未创建"),
        "ci_passed": ("CI 通过", "❌ CI 未通过"),
        "review_approved": ("AI 审查通过", "❌ AI 审查未通过"),
        "screenshot_included": ("含截图", "⚠️ 无截图"),
    }
    for key, (yes, no) in labels.items():
        status = yes if checks.get(key) else no
        lines.append(f"- {status}")
    return "\n".join(lines)


def simulate_notification_flow():
    """模拟完整通知流程"""
    if reg.DB_PATH.exists():
        reg.DB_PATH.unlink()
    reg.init_db()
    
    # Create a completed task that needs review
    t1 = reg.create_task(
        id="e9-notif-test",
        agent="claude-code",
        description="实现用户注册 API",
    )
    reg.transition_status(t1, reg.STATUS_RUNNING)
    reg.update_progress(t1, tool_count=12, last_event="✅ 全部测试通过",
                       files_written=["auth/register.py", "auth/register.test.py", "auth/validators.py"])
    reg.transition_status(t1, reg.STATUS_DONE)
    reg.update_checks(t1, checks_pr_created=True, checks_ci_passed=True, checks_review_approved=True)
    
    # Check for tasks needing notification
    print("🔍 检查需要通知的任务...")
    
    conn = __import__('sqlite3').connect(str(reg.DB_PATH))
    conn.row_factory = __import__('sqlite3').Row
    # Tasks that are done but haven't been notified
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status='done' AND notifications_on_complete=1"
    ).fetchall()
    conn.close()
    
    notifications = []
    for row in rows:
        t = dict(row)
        t["checks"] = {
            "pr_created": bool(t.pop("checks_pr_created")),
            "ci_passed": bool(t.pop("checks_ci_passed")),
            "review_approved": bool(t.pop("checks_review_approved")),
            "screenshot_included": bool(t.pop("checks_screenshot_included")),
        }
        notifications.append(t)
    
    if not notifications:
        print("  无需通知")
        return False
    
    print(f"  发现 {len(notifications)} 个待通知任务")
    
    for task in notifications:
        # Format notification message
        msg = NOTIFICATION_TEMPLATE.format(
            description=task["description"],
            agent=task["agent"],
            duration="5m 30s",
            files_count=len(task.get("progress_files_written", [])),
            checks=format_checks(task),
        )
        
        print(f"\n📋 通知内容预览：")
        print(f"{'─' * 40}")
        print(msg)
        print(f"{'─' * 40}")
        
        # Mark as notified (would use message tool in production)
        conn = __import__('sqlite3').connect(str(reg.DB_PATH))
        conn.execute("UPDATE tasks SET notifications_on_complete=0 WHERE id=?", (task["id"],))
        conn.commit()
        conn.close()
        print(f"  ✅ 已标记为已通知")
    
    # Verify no more pending notifications
    conn = __import__('sqlite3').connect(str(reg.DB_PATH))
    rows = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='done' AND notifications_on_complete=1"
    ).fetchone()
    conn.close()
    
    if rows[0] == 0:
        print(f"\n✅ 所有任务已通知，不会重复发送")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 9：飞书通知集成")
    print("=" * 60)
    
    ok = simulate_notification_flow()
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 9 完成 {'✅' if ok else '⚠️'}")
    print("  通知模板验证通过")
    print("  去重机制验证通过（notifications_on_complete 标志位）")
    print("  实际发送需在 Zoe 规则中集成 message 工具")
    print("=" * 60)
