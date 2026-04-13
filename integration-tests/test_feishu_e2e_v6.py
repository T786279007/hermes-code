#!/usr/bin/env python3
"""
Hermes → Claude Code ACP 穿测 v6 · 定期心跳进度
用户要看：模型还在工作吗？卡没卡？做到哪了？
"""
import os, sys, json, time, threading

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

PROGRESS_FILE = "/tmp/hermes-feishu-e2e/.progress"

# Clear
if os.path.exists(PROGRESS_FILE):
    os.remove(PROGRESS_FILE)

def emit(event_type, text):
    event = {"ts": time.strftime("%H:%M:%S"), "type": event_type, "text": str(text)[:300]}
    with open(PROGRESS_FILE, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"[{event['ts']}] {event_type}: {text[:100]}", flush=True)

emit("system", "v6 starting")

from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

# ======== Parent with progress callback ========
def progress_callback(event_type, text=None, **kwargs):
    emit(event_type, text or str(kwargs))

parent = AIAgent(
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
    api_key=os.environ.get("CUSTOM_API_KEY", ""),
    model="glm-5-turbo",
    provider="custom",
    enabled_toolsets=["terminal", "file"],
    quiet_mode=False,
    max_iterations=3,
    skip_context_files=True,
    skip_memory=True,
    clarify_callback=None,
)
parent.tool_progress_callback = progress_callback

# ======== Child ========
child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E v6",
    toolsets=["terminal", "file"],
    model=None,
    max_iterations=25,
    parent_agent=parent,
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

# ======== Task ========
TASK = """Build a URL shortener in /tmp/hermes-feishu-e2e/:
1. shortener.py - URLShortener class with base62 encode/decode, JSON persistence, collision handling, argparse CLI
2. test_shortener.py - pytest tests for roundtrip, collisions, persistence, invalid URLs, CLI  
3. Run pytest -v and show results

Working dir: /tmp/hermes-feishu-e2e"""

child.ephemeral_system_prompt = (
    f"You are a focused subagent.\n\nYOUR TASK:\n{TASK}\n\n"
    f"WORKSPACE PATH:\n/tmp/hermes-feishu-e2e\n\nComplete and summarize."
)

# ======== Heartbeat thread ========
heartbeat_stop = threading.Event()
last_activity_time = [time.time()]
last_event_count = [0]

def heartbeat_loop():
    """Every 15 seconds, emit a heartbeat with elapsed time and activity check."""
    start = time.time()
    while not heartbeat_stop.wait(15):
        elapsed = time.time() - start
        since_activity = time.time() - last_activity_time[0]
        
        # Count events so far
        try:
            with open(PROGRESS_FILE) as f:
                current_count = sum(1 for _ in f)
        except:
            current_count = 0
        
        new_events = current_count - last_event_count[0]
        last_event_count[0] = current_count
        
        if new_events > 0:
            emit("heartbeat", f"⏳ [{elapsed:.0f}s] 工作中… 本轮新增 {new_events} 条事件")
        else:
            # No new activity — might be stuck
            if since_activity > 30:
                emit("stuck", f"⚠️ [{elapsed:.0f}s] 模型可能卡住了… 已 {since_activity:.0f}s 无新输出")
            else:
                emit("heartbeat", f"⏳ [{elapsed:.0f}s] 等待中…")

# Start heartbeat
hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
hb_thread.start()

# ======== Run ========
emit("task", "🚀 Claude Code 开始做 URL Shortener 项目")
start = time.time()

try:
    result = _run_single_child(
        task_index=0, goal=TASK, child=child, parent_agent=parent,
    )
    elapsed = time.time() - start
    summary = result.get("summary", "")[:400]
    
    emit("done", f"✅ 完成！{elapsed:.1f}s\n{summary}")
    
    # Verify
    files_ok = 0
    for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                      ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
        if os.path.exists(p):
            emit("verify", f"✅ {label} ({os.path.getsize(p)}b)")
            files_ok += 1
        else:
            emit("verify", f"❌ {label} MISSING")
    
    if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
        )
        # Extract summary
        for line in r.stdout.split("\n"):
            if "passed" in line.lower() or "failed" in line.lower():
                emit("test", line.strip())
    
    emit("end", f"🏁 全部完成 · {elapsed:.1f}s · {files_ok} 个文件")

except Exception as e:
    elapsed = time.time() - start
    emit("error", f"❌ 失败 {elapsed:.1f}s: {e}")

heartbeat_stop.set()
child.close()
parent.close()
print(f"\nDone. {time.time()-start:.1f}s", flush=True)
