#!/usr/bin/env python3
"""
🧪 实验 4：Cron 监控检查脚本
验证 check-agents.py 能识别 running/done/failed 任务并输出结构化报告。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg

DB_PATH = reg.DB_PATH
TIMEOUT_SECONDS = 300  # 5 分钟超时


def check_stale_tasks():
    """检查超过 TIMEOUT_SECONDS 仍在 running 的任务"""
    now = datetime.now(timezone(timedelta(hours=8)))
    conn = __import__('sqlite3').connect(str(DB_PATH))
    conn.row_factory = __import__('sqlite3').Row
    rows = conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
    conn.close()
    stale = []
    for row in rows:
        t = dict(row)
        updated = datetime.fromisoformat(t["updated_at"])
        elapsed = (now - updated).total_seconds()
        if elapsed > TIMEOUT_SECONDS:
            stale.append({**t, "elapsed_seconds": int(elapsed)})
    return stale


def check_failed_tasks():
    """检查 failed 状态的任务"""
    return reg.list_tasks(status="failed")


def check_retry_exhausted():
    """检查重试次数耗尽的任务"""
    conn = __import__('sqlite3').connect(str(DB_PATH))
    conn.row_factory = __import__('sqlite3').Row
    rows = conn.execute(
        "SELECT id, description, retries, max_retries FROM tasks WHERE retries >= max_retries AND status != 'done'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def generate_report():
    """生成结构化监控报告"""
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    
    stale = check_stale_tasks()
    failed = check_failed_tasks()
    exhausted = check_retry_exhausted()
    all_tasks = reg.list_tasks()
    
    # Summary
    status_counts = {}
    for t in all_tasks:
        s = t["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    
    report = {
        "timestamp": now,
        "summary": {
            "total": len(all_tasks),
            "by_status": status_counts,
            "needs_attention": len(stale) + len(failed) + len(exhausted),
        },
        "stale_running": [{"id": t["id"], "desc": t["description"], "elapsed_min": round(t["elapsed_seconds"]/60, 1)} for t in stale],
        "failed": [{"id": t["id"], "desc": t["description"], "retries": t.get("retries", 0)} for t in failed],
        "retry_exhausted": [{"id": t["id"], "desc": t["description"], "retries": t["retries"]} for t in exhausted],
    }
    
    return report


def print_report(report):
    """Pretty print report"""
    s = report["summary"]
    print(f"📊 监控报告 [{report['timestamp']}]")
    print(f"  总任务: {s['total']} | 需关注: {s['needs_attention']}")
    print(f"  状态分布: {json.dumps(s['by_status'])}")
    
    if report["stale_running"]:
        print(f"\n  ⚠️ 卡住的任务 ({len(report['stale_running'])}):")
        for t in report["stale_running"]:
            print(f"    {t['id']}: {t['desc'][:40]} (已运行 {t['elapsed_min']}min)")
    
    if report["failed"]:
        print(f"\n  ❌ 失败的任务 ({len(report['failed'])}):")
        for t in report["failed"]:
            print(f"    {t['id']}: {t['desc'][:40]} (retries={t['retries']})")
    
    if report["retry_exhausted"]:
        print(f"\n  🔁 重试耗尽 ({len(report['retry_exhausted'])}):")
        for t in report["retry_exhausted"]:
            print(f"    {t['id']}: {t['desc'][:40]} (retries={t['retries']})")
    
    if not report["stale_running"] and not report["failed"] and not report["retry_exhausted"]:
        print(f"\n  ✅ 一切正常")
    
    # Return exit code for cron
    if report["summary"]["needs_attention"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 4：Cron 监控检查脚本")
    print("=" * 60)
    
    # Setup: create mixed task states
    if reg.DB_PATH.exists():
        reg.DB_PATH.unlink()
    reg.init_db()
    
    # Normal completed task
    t1 = reg.create_task(id="e4-done", agent="claude-code", description="已完成任务")
    reg.transition_status(t1, reg.STATUS_RUNNING)
    reg.transition_status(t1, reg.STATUS_DONE)
    
    # Failed task
    t2 = reg.create_task(id="e4-failed", agent="codex", description="失败任务")
    reg.transition_status(t2, reg.STATUS_RUNNING)
    reg.transition_status(t2, reg.STATUS_RETRYING)
    reg.transition_status(t2, reg.STATUS_RUNNING)
    reg.transition_status(t2, reg.STATUS_FAILED)
    
    # Running task (simulate stale by modifying updated_at)
    t3 = reg.create_task(id="e4-stale", agent="claude-code", description="卡住的任务")
    reg.transition_status(t3, reg.STATUS_RUNNING)
    # Backdate updated_at to simulate stale
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    old_time = (datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=10)).isoformat()
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old_time, t3))
    conn.commit()
    conn.close()
    
    # Happy running task
    t4 = reg.create_task(id="e4-active", agent="claude-code", description="正常执行中")
    reg.transition_status(t4, reg.STATUS_RUNNING)
    
    print("\n--- 运行检查 ---")
    report = generate_report()
    exit_code = print_report(report)
    
    # Verify
    print("\n--- 验证 ---")
    assert report["summary"]["total"] == 4, f"Expected 4 tasks, got {report['summary']['total']}"
    assert report["summary"]["needs_attention"] == 2, f"Expected 2 needs attention, got {report['summary']['needs_attention']}"
    assert len(report["stale_running"]) == 1, "Should have 1 stale task"
    assert len(report["failed"]) == 1, "Should have 1 failed task"
    assert exit_code == 1, "Exit code should be 1 (needs attention)"
    print("✅ 全部验证通过")
    
    # Now mark everything done and verify clean (except stale which needs manual action)
    reg.transition_status(t4, reg.STATUS_DONE)
    # Also mark stale as failed (simulating watchdog action)
    reg.transition_status(t3, reg.STATUS_FAILED)
    report2 = generate_report()
    exit_code2 = print_report(report2)
    # Still 1 failed, so needs_attention=1. Only truly clean when no failed either.
    # This is correct - failed tasks need Zoe intervention
    print(f"✅ exit_code={exit_code2} (failed tasks still need attention)")
    
    # Output JSON for Zoe consumption
    print("\n--- JSON 输出（供 Zoe 解析）---")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    
    print("\n" + "=" * 60)
    print("🧪 实验 4 完成 ✅")
    print("=" * 60)
