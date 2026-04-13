#!/usr/bin/env python3
"""
🧪 实验 3：Codex + 任务注册表集成
验证 acpx 调用 Codex，结果写入注册表。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg

WORKDIR = "/tmp/hermes-exp3"
PROGRESS = f"{WORKDIR}/.progress"
TASK_ID = "exp3-codex-fibonacci"


def run_codex_with_registry():
    """Run Codex via acpx, monitor output, sync to registry."""
    
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    subprocess.run(["git", "init"], capture_output=True)
    subprocess.run(["git", "config", "user.email", "zoe@hermes.local"], capture_output=True)
    subprocess.run(["git", "config", "user.name", "Zoe"], capture_output=True)
    
    # Init registry
    if reg.DB_PATH.exists():
        reg.DB_PATH.unlink()
    reg.init_db()
    
    # Create task
    reg.create_task(
        id=TASK_ID,
        agent="codex",
        model="gpt-5.3-codex",
        description="用 Codex 实现斐波那契计算器 + 测试",
        prompt="实现 fibonacci 模块"
    )
    reg.transition_status(TASK_ID, reg.STATUS_RUNNING)
    
    # Task prompt
    task_text = (
        "Create a Python module fibonacci.py with: "
        "1. A fib(n) function that returns the nth Fibonacci number "
        "2. A fib_memo(n) function using memoization "
        "3. A fib_generator() that yields Fibonacci numbers infinitely "
        "4. Unit tests in test_fibonacci.py covering edge cases (n=0, n=1, n=10, negative input) "
        "Run the tests and make sure they all pass."
    )
    
    # Run Codex via acpx in exec mode with json output
    cmd = [
        "acpx",
        "--format", "json",
        "--cwd", WORKDIR,
        "--timeout", "120",
        "codex", "exec",
        task_text,
    ]
    
    print(f"🚀 Starting Codex in {WORKDIR}...")
    
    tool_count = 0
    files_written = []
    start = time.time()
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON output, might be text
                if line and len(line) > 5:
                    print(f"  [{time.time()-start:5.1f}s] {line[:100]}")
                continue
            
            etype = event.get("type", "")
            elapsed = time.time() - start
            
            if etype == "tool_call":
                tool_count += 1
                title = event.get("title", "unknown tool")
                status = event.get("status", "")
                print(f"  [{elapsed:5.1f}s] 🔧 [{status}] {title}")
                reg.update_progress(TASK_ID, tool_count=tool_count, last_event=f"🔧 {title}")
            
            elif etype == "tool_result":
                title = event.get("title", "")
                print(f"  [{elapsed:5.1f}s] ✅ {title}")
            
            elif etype == "assistant":
                text = event.get("text", "")
                if text and len(text) > 10:
                    print(f"  [{elapsed:5.1f}s] 💬 {text[:120]}...")
            
            elif etype == "done" or etype == "result":
                text = event.get("text", event.get("result", ""))
                is_error = event.get("is_error", False)
                duration_ms = int((time.time() - start) * 1000)
                
                status_str = "error" if is_error else "success"
                reg.update_progress(TASK_ID, tool_count=tool_count, 
                                  last_event=f"{'✅' if not is_error else '❌'} Codex 完成 ({duration_ms/1000:.1f}s)")
                print(f"  [{elapsed:5.1f}s] {'✅' if not is_error else '❌'} 结果: {status_str} | {duration_ms/1000:.1f}s")
            
            elif etype == "error":
                msg = event.get("message", event.get("text", "unknown error"))
                print(f"  [{elapsed:5.1f}s] ❌ 错误: {msg[:100]}")
        
        proc.wait(timeout=30)
        
    except subprocess.TimeoutExpired:
        proc.kill()
        print("⏰ Timeout!")
    
    # Get final state
    t = reg.get_task(TASK_ID)
    elapsed = time.time() - start
    
    # Check created files
    actual_files = []
    for f in sorted(os.listdir(WORKDIR)):
        if f.startswith(".") or f in ("task.txt",):
            continue
        path = os.path.join(WORKDIR, f)
        if os.path.isfile(path):
            actual_files.append(path)
    
    reg.update_progress(TASK_ID, files_written=actual_files)
    
    if proc.returncode == 0:
        reg.transition_status(TASK_ID, reg.STATUS_DONE)
    
    return t, proc.returncode, elapsed, actual_files


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 3：Codex + 注册表集成")
    print("=" * 60)
    
    task, rc, elapsed, files = run_codex_with_registry()
    
    print("\n--- 注册表最终状态 ---")
    print(json.dumps(task, indent=2, ensure_ascii=False, default=str))
    
    # Verify
    print("\n--- 验证 ---")
    print(f"  状态: {task['status']}")
    print(f"  工具调用: {task['progress_tool_count']} 次")
    print(f"  最后事件: {task['progress_last_event']}")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  文件: {files}")
    
    if files:
        print("✅ 文件已创建")
    if task['progress_tool_count'] > 0:
        print("✅ 工具调用已记录")
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 3 完成 ({'✅' if rc == 0 else '⚠️'})")
    print("=" * 60)
