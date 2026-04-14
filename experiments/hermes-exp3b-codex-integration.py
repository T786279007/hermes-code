#!/usr/bin/env python3
"""
🧪 实验 3（修正）：Codex + 任务注册表集成
acpx --format json 输出 JSON-RPC stream，需要解析 session/update 事件。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg

WORKDIR = "/tmp/hermes-exp3b"
PROGRESS = f"{WORKDIR}/.progress"
TASK_ID = "exp3b-codex-fibonacci"


def parse_acpx_stream(line):
    """Parse acpx JSON-RPC stream line into structured events."""
    try:
        d = json.loads(line)
    except:
        return None
    
    method = d.get("method", "")
    params = d.get("params", {})
    
    # We care about session/update notifications
    if method == "session/update":
        update = params.get("update", {})
        session_update = update.get("sessionUpdate", "")
        content = update.get("content", {})
        # content can be a dict or a list
        if isinstance(content, list):
            content = content[0] if content else {}
        
        if session_update == "tool_call":
            return {
                "type": "tool_call",
                "title": content.get("title", "") if isinstance(content, dict) else str(content),
                "status": content.get("status", "") if isinstance(content, dict) else "",
                "tool_call_id": content.get("toolCallId", "") if isinstance(content, dict) else "",
            }
        elif session_update == "agent_message_chunk":
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if text:
                return {"type": "text_chunk", "text": text}
    
    # Also check for result (session/prompt response)
    if "result" in d and isinstance(d["result"], dict):
        result = d["result"]
        if "stopReason" in result:
            return {
                "type": "result",
                "stop_reason": result.get("stopReason", "unknown"),
            }
    
    return None


def run_codex_with_registry():
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    subprocess.run(["git", "init"], capture_output=True)
    subprocess.run(["git", "config", "user.email", "zoe@hermes.local"], capture_output=True)
    subprocess.run(["git", "config", "user.name", "Zoe"], capture_output=True)
    
    # Init registry (reuse same DB)
    reg.init_db()
    
    # Create task
    reg.create_task(
        id=TASK_ID,
        agent="codex",
        model="gpt-5.3-codex",
        description="Codex 实现斐波那契计算器 + 测试",
        prompt="实现 fibonacci 模块"
    )
    reg.transition_status(TASK_ID, reg.STATUS_RUNNING)
    
    task_text = (
        "Create fibonacci.py with fib(n), fib_memo(n), and fib_generator(). "
        "Create test_fibonacci.py with comprehensive tests. Run the tests."
    )
    
    cmd = [
        "acpx",
        "--format", "json",
        "--approve-all",
        "--cwd", WORKDIR,
        "--timeout", "120",
        "codex", "exec",
        task_text,
    ]
    
    print(f"🚀 Starting Codex via acpx in {WORKDIR}...")
    
    tool_count = 0
    text_buffer = ""
    start = time.time()
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    for line in proc.stdout:
        event = parse_acpx_stream(line.strip())
        if not event:
            continue
        
        elapsed = time.time() - start
        etype = event["type"]
        
        if etype == "tool_call":
            tool_count += 1
            title = event.get("title", "unknown")
            status = event.get("status", "")
            if status == "completed":
                print(f"  [{elapsed:5.1f}s] ✅ {title}")
                reg.update_progress(TASK_ID, tool_count=tool_count, last_event=f"✅ {title}")
            elif status == "in_progress":
                print(f"  [{elapsed:5.1f}s] 🔧 {title}...")
        
        elif etype == "text_chunk":
            text_buffer += event["text"]
        
        elif etype == "result":
            stop = event.get("stop_reason", "unknown")
            duration_ms = int((time.time() - start) * 1000)
            success = stop in ("end_turn", "stopped")
            reg.update_progress(TASK_ID, tool_count=tool_count, 
                              last_event=f"{'✅' if success else '❌'} Codex {stop} ({duration_ms/1000:.1f}s)")
            print(f"  [{elapsed:5.1f}s] {'✅' if success else '❌'} stop_reason={stop} | {duration_ms/1000:.1f}s")
    
    proc.wait(timeout=30)
    
    # Get actual files
    actual_files = []
    for f in sorted(os.listdir(WORKDIR)):
        if f.startswith(".") or f == "__pycache__":
            continue
        path = os.path.join(WORKDIR, f)
        if os.path.isfile(path):
            actual_files.append(path)
    
    reg.update_progress(TASK_ID, files_written=actual_files)
    
    if proc.returncode == 0:
        reg.transition_status(TASK_ID, reg.STATUS_DONE)
    
    t = reg.get_task(TASK_ID)
    elapsed = time.time() - start
    return t, proc.returncode, elapsed, actual_files, text_buffer[:500]


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 3（修正）：Codex + 注册表集成")
    print("=" * 60)
    
    task, rc, elapsed, files, text = run_codex_with_registry()
    
    print("\n--- 注册表最终状态 ---")
    print(f"  状态: {task['status']}")
    print(f"  工具调用: {task['progress_tool_count']} 次")
    print(f"  最后事件: {task['progress_last_event']}")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  文件: {[os.path.basename(f) for f in files]}")
    
    print("\n--- Codex 输出摘要 ---")
    print(text)
    
    print("\n--- 工作目录文件 ---")
    for f in files:
        size = os.path.getsize(f)
        print(f"  {os.path.basename(f)}: {size} bytes")
    
    # Verify
    ok = True
    if not files:
        print("❌ 没有创建文件")
        ok = False
    if task['progress_tool_count'] == 0:
        print("⚠️ 没有记录工具调用（acpx 事件格式可能不包含 tool_call completed）")
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 3 完成 {'✅' if ok and rc == 0 else '⚠️（部分通过）'}")
    print("=" * 60)
