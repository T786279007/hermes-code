#!/usr/bin/env python3
"""
NUC E2E v3: 直接监听 Claude Code ACP 子进程的 stdout，
实时解析工具调用事件并推送到飞书。
"""
import os, sys, json, time, threading, subprocess, select

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

# ======== Feishu sender (background thread) ========
_msg_queue = []
_msg_lock = threading.Lock()

def _send_loop():
    while True:
        time.sleep(0.8)
        with _msg_lock:
            batch = list(_msg_queue)
            _msg_queue.clear()
        for msg in batch:
            subprocess.run(
                ["openclaw", "message", "send", "--channel", "feishu", "--message", msg],
                capture_output=True, text=True, timeout=10, env={**os.environ}
            )

threading.Thread(target=_send_loop, daemon=True).start()

def feishu(text):
    with _msg_lock:
        _msg_queue.append(text)

def feishu_flush():
    time.sleep(2)  # wait for queue to drain

# ======== Send startup message ========
feishu("🚀 Claude Code ACP 穿测启动\n调度: GLM-5-Turbo → 执行: Claude Code\n项目: URL Shortener\n即将启动...")
feishu_flush()

# ======== Launch Claude Code ACP subprocess directly ========
ACP_CMD = ["npx", "-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"]

proc = subprocess.Popen(
    ACP_CMD,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd="/tmp/hermes-feishu-e2e",
    env={**os.environ},
)

# ======== ACP protocol: initialize + create session ========
def acp_send(msg_id, method, params=None):
    """Send JSON-RPC message to Claude Code ACP."""
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params:
        msg["params"] = params
    data = json.dumps(msg) + "\n"
    proc.stdin.write(data)
    proc.stdin.flush()

def acp_read(timeout=30):
    """Read one JSON-RPC line from Claude Code ACP."""
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if ready:
        line = proc.stdout.readline().strip()
        if line:
            return json.loads(line)
    return None

feishu("🔧 启动 Claude Code ACP 子进程...")
feishu_flush()

# Initialize
acp_send(1, "initialize", {
    "clientInfo": {"name": "hermes-test", "version": "1.0"},
    "capabilities": {}
})
resp = acp_read(timeout=15)
if resp and "result" in resp:
    agent_name = resp["result"].get("agent", {}).get("name", "unknown")
    feishu(f"✅ ACP 连接成功: {agent_name}")
else:
    feishu(f"❌ ACP 初始化失败: {resp}")
    sys.exit(1)

# Send initialized notification
notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
proc.stdin.write(notif)
proc.stdin.flush()
time.sleep(0.5)

# Create session
acp_send(2, "newSession", {})
resp = acp_read(timeout=15)
if resp and "result" in resp:
    session_id = resp["result"].get("sessionId", "?")
    feishu(f"✅ 会话创建: {session_id[:8]}...")
else:
    feishu(f"❌ 会话创建失败: {resp}")
    sys.exit(1)

feishu("⚡ 发送任务给 Claude Code...")
feishu_flush()

# ======== Send the task ========
TASK = f"""Build a URL shortener service:

1. Create shortener.py with a URLShortener class:
   - encode(long_url) → short_code using base62 (0-9, a-z, A-Z)
   - decode(short_code) → original long_url
   - Store mappings in data.json for persistence
   - Handle collisions, invalid URLs, duplicates
   - Include argparse CLI interface

2. Create test_shortener.py with pytest tests:
   - Roundtrip encode/decode
   - Collision handling
   - JSON persistence
   - Invalid URL handling
   - CLI tests

3. Run all tests: python3 -m pytest test_shortener.py -v

Working directory: /tmp/hermes-feishu-e2e"""

acp_send(3, "prompt", {"sessionId": session_id, "text": TASK})

# ======== Read streaming responses (THE KEY PART) ========
start = time.time()
tool_count = 0
last_tool = ""
final_text = ""

feishu("👀 Claude Code 开始工作，实时监听中...")

while time.time() - start < 120:
    ready, _, _ = select.select([proc.stdout], [], [], 5)
    if ready:
        line = proc.stdout.readline().strip()
        if not line:
            print("[stream ended]")
            break
        
        try:
            resp = json.loads(line)
        except:
            continue
        
        method = resp.get("method", "")
        params = resp.get("params", {}).get("update", {})
        
        if method == "notifications/session_update":
            update_type = params.get("type", "")
            
            if update_type == "tool_call_start":
                tool_info = params.get("toolCall", {})
                tool_name = tool_info.get("toolName", "unknown")
                input_preview = str(tool_info.get("input", ""))[:100]
                last_tool = tool_name
                tool_count += 1
                feishu(f"🔧 [{tool_count}] {tool_name}\n   ┆ {input_preview}")
            
            elif update_type == "tool_call_end":
                tool_info = params.get("toolCall", {})
                tool_name = tool_info.get("toolName", "?")
                output = str(tool_info.get("output", ""))[:200]
                # Show file writes and test results
                if "file_path" in str(tool_info.get("input", "")):
                    files = tool_info.get("files", [])
                    for f in files:
                        feishu(f"   ✅ 写入: {f}")
                if "passed" in output or "FAILED" in output:
                    feishu(f"   📋 {output[:300]}")
            
            elif update_type == "text":
                text = params.get("text", "").strip()
                if text:
                    final_text = text
                    # Only send to Feishu if it's the final summary (short)
                    if len(text) < 100 and tool_count > 0:
                        feishu(f"📝 {text}")
            
            elif update_type == "thinking":
                text = params.get("text", "")
                if text and len(text) < 150:
                    feishu(f"💭 {text[:120]}")
        
        elif "result" in resp:
            feishu("🏁 Claude Code 完成!")
            break
    else:
        # No data - check if process died
        if proc.poll() is not None:
            feishu(f"❌ 进程退出 (code {proc.returncode})")
            break

elapsed = time.time() - start

# Cleanup
try:
    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)
except:
    proc.kill()

# ======== Verify ========
feishu_flush()
time.sleep(1)

feishu(f"\n📊 穿测完成\n⏱️ {elapsed:.1f}s | 🔧 {tool_count} 工具调用")

for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                  ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
    if os.path.exists(p):
        feishu(f"✅ {label} ({os.path.getsize(p)} bytes)")
    else:
        feishu(f"❌ {label} NOT FOUND")

if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
    r = subprocess.run(
        ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
        capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
    )
    for line in r.stdout.split("\n"):
        if "passed" in line.lower() or "failed" in line.lower() or "error" in line.lower():
            feishu(f"🧪 {line.strip()}")

feishu_flush()
time.sleep(3)  # final drain
print(f"\nDone. {elapsed:.1f}s, {tool_count} tool calls.")
