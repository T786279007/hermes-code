#!/usr/bin/env python3
"""
🧪 实验 2：Claude Code + 任务注册表集成
验证 claude-task-runner.py 的 stream-json 输出能实时同步到注册表。
"""
import json
import os
import subprocess
import sys
import time
import uuid

# Import registry functions
sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg
init_db = reg.init_db
create_task = reg.create_task
transition_status = reg.transition_status
update_progress = reg.update_progress
update_checks = reg.update_checks
get_task = reg.get_task
list_tasks = reg.list_tasks
DB_PATH = reg.DB_PATH
STATUS_RUNNING = reg.STATUS_RUNNING
STATUS_DONE = reg.STATUS_DONE

WORKDIR = "/tmp/hermes-exp2"
PROGRESS = f"{WORKDIR}/.progress"
TASK_ID = "exp2-markdown-parser"


def run_claude_with_registry():
    """Run claude-task-runner.py, which writes to .progress.
    We'll monitor .progress and sync to registry in real-time."""
    
    # Setup
    os.makedirs(WORKDIR, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    
    # Create task in registry
    create_task(
        id=TASK_ID,
        agent="claude-code",
        model="glm-5-turbo",
        description="写一个简单的 Markdown 解析器 + 测试",
        prompt="创建 markdown_parser.py 和 test_markdown.py"
    )
    transition_status(TASK_ID, STATUS_RUNNING)
    
    # Task prompt
    task_text = (
        "Create a simple Markdown parser in Python that can parse: "
        "headings (# ## ###), bold (**text**), italic (*text*), code blocks (```), "
        "and lists (- item). Write tests that verify all parsing works correctly. "
        "Run the tests and ensure they all pass."
    )
    
    # Write task to file (avoid shell escaping issues)
    task_file = f"{WORKDIR}/task.txt"
    with open(task_file, "w") as f:
        f.write(task_text)
    
    # Start claude-task-runner.py in background
    runner_script = "/home/txs/hermes-agent/claude-task-runner.py"
    cmd = [
        "python3", runner_script,
        "--task", task_text,
        "--cwd", WORKDIR,
        "--progress", PROGRESS,
        "--timeout", "120",
    ]
    
    print(f"🚀 Starting Claude Code in {WORKDIR}...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Monitor .progress file and sync to registry
    last_sync_pos = 0
    tool_count = 0
    files_written = []
    
    start = time.time()
    while proc.poll() is None:
        elapsed = time.time() - start
        if elapsed > 120:
            proc.kill()
            print("⏰ Timeout!")
            break
        
        # Read new lines from .progress
        if os.path.exists(PROGRESS):
            with open(PROGRESS, "r") as f:
                f.seek(last_sync_pos)
                new_lines = f.readlines()
                last_sync_pos = f.tell()
            
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except:
                    continue
                
                etype = event.get("type")
                
                if etype == "tool":
                    tool_count = event.get("tool_num", tool_count)
                    summary = event.get("summary", "")
                    fw = event.get("files_written", [])
                    if fw:
                        files_written = fw
                    
                    # Sync to registry
                    update_progress(TASK_ID, tool_count=tool_count, last_event=summary, 
                                   files_written=files_written)
                    print(f"  [{elapsed:5.1f}s] {summary}")
                
                elif etype == "result":
                    status = event.get("status", "unknown")
                    duration_ms = event.get("duration_ms", 0)
                    fw = event.get("files_written", [])
                    if fw:
                        files_written = fw
                    
                    update_progress(TASK_ID, tool_count=tool_count, last_event=f"{'✅' if status=='success' else '❌'} 完成 ({duration_ms/1000:.1f}s)", files_written=files_written)
                    print(f"  [{elapsed:5.1f}s] 结果: {status} | {duration_ms/1000:.1f}s | {len(files_written)} files")
        
        time.sleep(0.5)
    
    # Get final state
    proc.wait()
    t = get_task(TASK_ID)
    
    # Mark done
    if proc.returncode == 0:
        transition_status(TASK_ID, STATUS_DONE)
    
    return t, proc.returncode


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 2：Claude Code + 注册表集成")
    print("=" * 60)
    
    task, rc = run_claude_with_registry()
    
    print("\n--- 注册表最终状态 ---")
    print(json.dumps(task, indent=2, ensure_ascii=False, default=str))
    
    # Verify
    print("\n--- 验证 ---")
    assert task["status"] in ("done", "running"), f"Unexpected status: {task['status']}"
    assert task["progress_tool_count"] > 0, "No tool calls recorded"
    assert task["progress_last_event"], "No last event"
    print(f"✅ 状态: {task['status']}")
    print(f"✅ 工具调用: {task['progress_tool_count']} 次")
    print(f"✅ 最后事件: {task['progress_last_event']}")
    print(f"✅ 文件列表: {task['progress_files_written']}")
    
    # Check if files were actually created
    print("\n--- 工作目录文件 ---")
    for f in sorted(os.listdir(WORKDIR)):
        if f.startswith(".") or f == "task.txt":
            continue
        path = os.path.join(WORKDIR, f)
        size = os.path.getsize(path)
        print(f"  {f}: {size} bytes")
    
    print("\n" + "=" * 60)
    print("🧪 实验 2 完成 ✅" if rc == 0 else "🧪 实验 2 完成（Claude 返回非零，检查结果）")
    print("=" * 60)
