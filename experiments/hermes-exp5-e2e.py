#!/usr/bin/env python3
"""
🧪 实验 5：端到端闭环
完整流程：创建任务 → 注册 → 派发 → 监控 → 完成 → 报告
（模拟 Claude Code 执行，验证注册表全生命周期）
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg
sys.path.insert(0, "/tmp/hermes-exp4")
import hermes_exp4_cron_monitor as monitor


DB_PATH = reg.DB_PATH
WORKDIR = "/tmp/hermes-exp5"


def simulate_agent_execution(task_id, agent, steps):
    """模拟 Agent 执行过程（不实际调用 Claude/Codex）"""
    for step in steps:
        time.sleep(0.1)  # 模拟耗时
        action = step["action"]
        
        if action == "tool":
            reg.update_progress(task_id, 
                tool_count=step.get("tool_num"),
                last_event=step["summary"],
                files_written=step.get("files_written"))
            print(f"  🔧 {step['summary']}")
        
        elif action == "status":
            reg.transition_status(task_id, step["status"])
            print(f"  🔄 → {step['status']}")
        
        elif action == "check":
            reg.update_checks(task_id, **step.get("checks", {}))
            print(f"  ✅ Checks updated: {step.get('checks', {})}")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 5：端到端闭环")
    print("=" * 60)
    
    # Clean setup
    if reg.DB_PATH.exists():
        reg.DB_PATH.unlink()
    reg.init_db()
    os.makedirs(WORKDIR, exist_ok=True)
    
    # === Step 1: Zoe 收到用户需求，创建任务 ===
    print("\n📋 Step 1: Zoe 创建任务")
    t1 = reg.create_task(
        id="e5-auth-api",
        agent="claude-code",
        model="glm-5-turbo",
        description="实现 JWT 认证 API",
        prompt="创建 auth 模块...",
        worktree=WORKDIR,
        branch="feature/auth-api"
    )
    print(f"  任务已创建: {t1}")
    
    t2 = reg.create_task(
        id="e5-unit-tests",
        agent="codex",
        model="gpt-5.3-codex",
        description="补充 auth 模块单元测试",
        prompt="为 auth 模块添加测试...",
        worktree=WORKDIR,
        branch="feature/auth-api"
    )
    print(f"  任务已创建: {t2}")
    
    # === Step 2: Claude Code 执行任务 1 ===
    print(f"\n🚀 Step 2: Claude Code 执行 {t1}")
    reg.transition_status(t1, reg.STATUS_RUNNING)
    
    simulate_agent_execution(t1, "claude-code", [
        {"action": "tool", "tool_num": 1, "summary": "📝 写入 auth/jwt.py (85 行)", "files_written": [f"{WORKDIR}/auth/jwt.py"]},
        {"action": "tool", "tool_num": 2, "summary": "📝 写入 auth/middleware.py (42 行)", "files_written": [f"{WORKDIR}/auth/jwt.py", f"{WORKDIR}/auth/middleware.py"]},
        {"action": "tool", "tool_num": 3, "summary": "📝 写入 auth/routes.py (67 行)", "files_written": [f"{WORKDIR}/auth/jwt.py", f"{WORKDIR}/auth/middleware.py", f"{WORKDIR}/auth/routes.py"]},
        {"action": "tool", "tool_num": 4, "summary": "📝 写入 test_auth.py (120 行)", "files_written": [f"{WORKDIR}/auth/jwt.py", f"{WORKDIR}/auth/middleware.py", f"{WORKDIR}/auth/routes.py", f"{WORKDIR}/test_auth.py"]},
        {"action": "tool", "tool_num": 5, "summary": "🖥️ 运行 pytest..."},
        {"action": "tool", "tool_num": 6, "summary": "✏️ 修复 middleware.py 类型错误"},
        {"action": "tool", "tool_num": 7, "summary": "🖥️ 重新运行 pytest..."},
        {"action": "status", "status": reg.STATUS_DONE},
        {"action": "check", "checks": {"checks_pr_created": True, "checks_ci_passed": True}},
    ])
    
    # === Step 3: Codex 执行任务 2 ===
    print(f"\n🚀 Step 3: Codex 执行 {t2}")
    reg.transition_status(t2, reg.STATUS_RUNNING)
    
    simulate_agent_execution(t2, "codex", [
        {"action": "tool", "tool_num": 1, "summary": "📝 写入 test_jwt_edge_cases.py (95 行)", "files_written": [f"{WORKDIR}/test_jwt_edge_cases.py"]},
        {"action": "tool", "tool_num": 2, "summary": "📝 写入 test_middleware.py (78 行)", "files_written": [f"{WORKDIR}/test_jwt_edge_cases.py", f"{WORKDIR}/test_middleware.py"]},
        {"action": "tool", "tool_num": 3, "summary": "🖥️ 运行 pytest..."},
        {"action": "status", "status": reg.STATUS_DONE},
        {"action": "check", "checks": {"checks_review_approved": True}},
    ])
    
    # === Step 4: 模拟一个失败+重试的任务 ===
    print(f"\n🔄 Step 4: 模拟失败+重试")
    t3 = reg.create_task(
        id="e5-docs",
        agent="claude-code",
        description="生成 API 文档",
    )
    reg.transition_status(t3, reg.STATUS_RUNNING)
    reg.update_progress(t3, tool_count=2, last_event="📝 写入 docs/api.md")
    
    # Simulate timeout
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    old_time = (datetime.now(timezone(timedelta(hours=8))) - timedelta(minutes=10)).isoformat()
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old_time, t3))
    conn.commit()
    conn.close()
    
    # === Step 5: Cron 监控检查 ===
    print(f"\n📊 Step 5: Cron 监控检查")
    report = monitor.generate_report()
    exit_code = monitor.print_report(report)
    
    assert report["summary"]["needs_attention"] == 1, "Should detect 1 stale task"
    assert len(report["stale_running"]) == 1, "Should have 1 stale task"
    print(f"  ✅ 正确识别卡住的任务: {report['stale_running'][0]['id']}")
    
    # === Step 6: Zoe 处理卡住的任务（模拟） ===
    print(f"\n🤖 Step 6: Zoe 自动处理")
    # Zoe decides to retry
    reg.transition_status(t3, reg.STATUS_RETRYING)
    print(f"  🔄 {t3} → retrying (Zoe 自动决策)")
    
    reg.transition_status(t3, reg.STATUS_RUNNING)
    print(f"  🔄 {t3} → running (重新派发)")
    
    simulate_agent_execution(t3, "claude-code", [
        {"action": "tool", "tool_num": 3, "summary": "📝 写入 docs/api.md (150 行)"},
        {"action": "status", "status": reg.STATUS_DONE},
    ])
    
    # === Step 7: 最终报告 ===
    print(f"\n📊 Step 7: 最终状态")
    report2 = monitor.generate_report()
    exit_code2 = monitor.print_report(report2)
    
    print(f"\n--- 任务详情 ---")
    for tid in [t1, t2, t3]:
        t = reg.get_task(tid)
        print(f"  {tid}: [{t['status']:8s}] tools={t['progress_tool_count']} checks={t['checks']}")
    
    # === 验证 ===
    print(f"\n--- 验证 ---")
    all_tasks = reg.list_tasks()
    done_count = sum(1 for t in all_tasks if t["status"] == "done")
    assert done_count == 3, f"Expected 3 done, got {done_count}"
    assert report2["summary"]["needs_attention"] == 0, "Should be all clean"
    assert exit_code2 == 0, "Exit code should be 0"
    
    t1_detail = reg.get_task(t1)
    assert t1_detail["progress_tool_count"] == 7
    assert len(t1_detail["progress_files_written"]) == 4
    assert t1_detail["checks"]["pr_created"] == True
    assert t1_detail["checks"]["ci_passed"] == True
    
    t3_detail = reg.get_task(t3)
    assert t3_detail["retries"] == 1, "Should have 1 retry"
    
    print("✅ 全部验证通过")
    print(f"  3 个任务全部完成")
    print(f"  1 个任务经历了 retry 流程")
    print(f"  监控正确识别 stale → Zoe 自动重试 → 最终完成")
    print(f"  exit_code=0 (一切正常)")
    
    print("\n" + "=" * 60)
    print("🧪 实验 5 完成 ✅ — 端到端闭环验证通过")
    print("=" * 60)
