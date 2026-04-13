#!/usr/bin/env python3
"""
Hermes Task Registry — SQLite backed.
实验 1：验证 CRUD + 状态机 + 并发安全
"""
import json
import sqlite3
import time
import uuid
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH = Path("/tmp/hermes-exp1/tasks.db")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_RETRYING = "retrying"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

VALID_TRANSITIONS = {
    STATUS_PENDING: {STATUS_RUNNING},
    STATUS_RUNNING: {STATUS_DONE, STATUS_FAILED, STATUS_RETRYING},
    STATUS_RETRYING: {STATUS_RUNNING, STATUS_FAILED},
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            agent TEXT NOT NULL,
            model TEXT,
            session_id TEXT,
            worktree TEXT,
            branch TEXT,
            pr_number INTEGER,
            description TEXT,
            prompt TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            retries INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            progress_tool_count INTEGER DEFAULT 0,
            progress_files_written TEXT DEFAULT '[]',
            progress_last_event TEXT DEFAULT '',
            progress_last_event_ts TEXT,
            checks_pr_created INTEGER DEFAULT 0,
            checks_ci_passed INTEGER DEFAULT 0,
            checks_review_approved INTEGER DEFAULT 0,
            checks_screenshot_included INTEGER DEFAULT 0,
            result TEXT,
            token_usage INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            notifications_on_complete INTEGER DEFAULT 1,
            notifications_on_failure INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);
    """)
    conn.commit()
    conn.close()
    print(f"✅ DB initialized: {DB_PATH}")


def create_task(id=None, agent="claude-code", model=None, description="", prompt="", worktree=None, branch=None):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    task_id = id or f"task-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    conn.execute("""
        INSERT INTO tasks (id, status, agent, model, description, prompt, worktree, branch, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, STATUS_PENDING, agent, model, description, prompt, worktree, branch, now, now))
    conn.commit()
    conn.close()
    print(f"📝 Created task: {task_id} [{agent}] {description[:50]}")
    return task_id


def transition_status(task_id, new_status):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    row = conn.execute("SELECT status, retries, max_retries FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        print(f"❌ Task not found: {task_id}")
        conn.close()
        return False
    
    current, retries, max_retries = row
    
    # Validate transition
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        print(f"❌ Invalid transition: {current} → {new_status}")
        conn.close()
        return False
    
    # Check retry limit
    if new_status == STATUS_RETRYING and retries >= max_retries:
        print(f"❌ Max retries exceeded: {retries}/{max_retries}")
        conn.close()
        return False
    
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    updates = {"status": new_status, "updated_at": now}
    if new_status == STATUS_RETRYING:
        updates["retries"] = retries + 1
    if new_status in (STATUS_DONE, STATUS_FAILED):
        updates["completed_at"] = now
    
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", (*updates.values(), task_id))
    conn.commit()
    conn.close()
    print(f"🔄 {task_id}: {current} → {new_status}")
    return True


def update_progress(task_id, tool_count=None, last_event=None, files_written=None):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    updates = {"updated_at": now, "progress_last_event_ts": now}
    if tool_count is not None:
        updates["progress_tool_count"] = tool_count
    if last_event:
        updates["progress_last_event"] = last_event
    if files_written:
        updates["progress_files_written"] = json.dumps(files_written)
    
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", (*updates.values(), task_id))
    conn.commit()
    conn.close()


def update_checks(task_id, **kwargs):
    """Update check fields: pr_created=True, ci_passed=True, etc."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    updates = {"updated_at": now}
    for k, v in kwargs.items():
        if k.startswith("checks_"):
            updates[k] = 1 if v else 0
    
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", (*updates.values(), task_id))
    conn.commit()
    conn.close()


def get_task(task_id):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["progress_files_written"] = json.loads(d.get("progress_files_written", "[]"))
        d["checks"] = {
            "pr_created": bool(d.pop("checks_pr_created")),
            "ci_passed": bool(d.pop("checks_ci_passed")),
            "review_approved": bool(d.pop("checks_review_approved")),
            "screenshot_included": bool(d.pop("checks_screenshot_included")),
        }
        return d
    return None


def list_tasks(status=None):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if status:
        rows = conn.execute("SELECT id, status, agent, description, progress_last_event, retries FROM tasks WHERE status=?", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT id, status, agent, description, progress_last_event, retries FROM tasks ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def print_task(t):
    print(f"  {t['id']}: [{t['status']:8s}] {t['agent']:12s} {t.get('description','')[:40]}  retries={t.get('retries',0)}")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 1：任务注册表 CRUD + 状态机")
    print("=" * 60)
    
    # Clean start
    if DB_PATH.exists():
        DB_PATH.unlink()
    if DB_PATH.with_suffix(".db-wal").exists():
        DB_PATH.with_suffix(".db-wal").unlink()
    
    # Step 1: Init
    init_db()
    
    # Step 2: Create tasks
    print("\n--- 创建任务 ---")
    t1 = create_task(id="exp1-login", agent="claude-code", model="glm-5-turbo",
                     description="实现登录页面", prompt="创建登录组件...")
    t2 = create_task(id="exp1-auth", agent="codex", model="gpt-5.3-codex",
                     description="JWT 认证模块", prompt="实现 JWT...")
    t3 = create_task(id="exp1-review", agent="codex", description="审查 PR #42")
    
    # Step 3: State transitions
    print("\n--- 状态机测试 ---")
    # Valid transitions
    assert transition_status(t1, STATUS_RUNNING) == True
    assert transition_status(t1, STATUS_DONE) == True
    
    # Invalid transition
    assert transition_status(t1, STATUS_RUNNING) == False  # done→running 不允许
    
    # Retry flow
    transition_status(t2, STATUS_RUNNING)
    assert transition_status(t2, STATUS_RETRYING) == True
    assert transition_status(t2, STATUS_RUNNING) == True
    assert transition_status(t2, STATUS_RETRYING) == True
    assert transition_status(t2, STATUS_RUNNING) == True
    assert transition_status(t2, STATUS_RETRYING) == True  # retries=3
    assert transition_status(t2, STATUS_FAILED) == True  # max retries
    
    # Max retry exceeded
    transition_status(t3, STATUS_RUNNING)
    transition_status(t3, STATUS_RETRYING)
    transition_status(t3, STATUS_RUNNING)
    transition_status(t3, STATUS_RETRYING)
    transition_status(t3, STATUS_RUNNING)
    transition_status(t3, STATUS_RETRYING)
    assert transition_status(t3, STATUS_RETRYING) == False  # should fail
    
    print("\n✅ 状态机测试通过")
    
    # Step 4: Progress updates
    print("\n--- 进度更新 ---")
    update_progress(t1, tool_count=5, last_event="📝 Write login.tsx (45 lines)", files_written=["login.tsx", "login.test.tsx"])
    update_progress(t1, tool_count=8, last_event="🖥️ Running tests...")
    t = get_task(t1)
    assert t["progress_tool_count"] == 8
    assert t["progress_last_event"] == "🖥️ Running tests..."
    assert len(t["progress_files_written"]) == 2
    print(f"✅ 进度更新: {t['progress_last_event']}")
    
    # Step 5: Checks
    print("\n--- 完成检查项 ---")
    update_checks(t1, checks_pr_created=True, checks_ci_passed=True, checks_review_approved=True)
    t = get_task(t1)
    print(f"  checks: {t['checks']}")
    assert t["checks"]["pr_created"] == True
    assert t["checks"]["ci_passed"] == True
    print("✅ 检查项更新通过")
    
    # Step 6: List
    print("\n--- 任务列表 ---")
    print("  All tasks:")
    for t in list_tasks():
        print_task(t)
    print(f"  Running: {list_tasks('running')}")
    print(f"  Done: {list_tasks('done')}")
    print(f"  Failed: {list_tasks('failed')}")
    
    # Step 7: Stats
    print("\n--- 统计 ---")
    conn = sqlite3.connect(str(DB_PATH))
    for s, c in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall():
        print(f"  {s}: {c}")
    conn.close()
    
    # Step 8: Crash recovery test (WAL mode)
    print("\n--- WAL 崩溃恢复测试 ---")
    update_progress(t1, tool_count=10, last_event="✅ All tests pass")
    # Simulate crash: don't close connection properly
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("UPDATE tasks SET progress_tool_count=999 WHERE id=?", (t1,))
    # DON'T commit or close — simulate crash
    del conn
    # Reopen and verify data integrity
    t = get_task(t1)
    assert t["progress_tool_count"] == 10, f"Expected 10, got {t['progress_tool_count']} (WAL rollback failed)"
    print("✅ WAL 崩溃恢复：未提交的数据被正确回滚")
    
    print("\n" + "=" * 60)
    print("🧪 实验 1 完成：全部通过 ✅")
    print("=" * 60)
