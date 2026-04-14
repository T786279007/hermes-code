#!/usr/bin/env python3
"""
NUC E2E v2: Hermes → Claude Code ACP + Feishu 实时进度
用飞书消息工具的 HTTP API 直接推送，不走 openclaw CLI。
"""
import os, sys, json, time, threading, subprocess, urllib.request, urllib.parse

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

# ======== Feishu relay ========
_feishu_queue = []
_feishu_lock = threading.Lock()
_feishu_thread = None
_feishu_stop = threading.Event()

def _feishu_sender_loop():
    """Background thread: batch-send messages to Feishu."""
    while not _feishu_stop.is_set():
        time.sleep(1)  # check every second
        with _feishu_lock:
            if not _feishu_queue:
                continue
            batch = list(_feishu_queue)
            _feishu_queue.clear()
        
        for msg in batch:
            try:
                subprocess.run(
                    ["openclaw", "message", "send", "--channel", "feishu", "--message", msg],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ}
                )
            except:
                pass  # silent fail for progress messages

def start_feishu_relay():
    global _feishu_thread
    _feishu_thread = threading.Thread(target=_feishu_sender_loop, daemon=True)
    _feishu_thread.start()

def send_progress(text):
    """Queue a progress message (non-blocking)."""
    with _feishu_lock:
        _feishu_queue.append(text)

def stop_feishu_relay():
    _feishu_stop.set()
    # Flush remaining
    with _feishu_lock:
        remaining = list(_feishu_queue)
    for msg in remaining:
        try:
            subprocess.run(
                ["openclaw", "message", "send", "--channel", "feishu", "--message", msg],
                capture_output=True, text=True, timeout=10,
                env={**os.environ}
            )
        except:
            pass

# ======== Build Hermes ========
from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

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

# Progress callback
def make_callback():
    call_count = {"n": 0}
    def cb(event_type, text=None, **kwargs):
        call_count["n"] += 1
        if event_type in ("tool_started", "subagent_progress") and text:
            send_progress(f"🔧 {text}")
        elif event_type == "_thinking" and text and len(text) < 200:
            send_progress(f"💭 {text[:120]}")
    return cb

parent.tool_progress_callback = make_callback()

# ======== Build Claude Code ACP child ========
child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E test v2",
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
child.tool_progress_callback = make_callback()

# ======== Task ========
TASK = """Build a URL shortener in /tmp/hermes-feishu-e2e/:
1. shortener.py - URLShortener class with base62 encode/decode, JSON persistence, collision handling, argparse CLI
2. test_shortener.py - pytest tests for roundtrip, collisions, persistence, invalid URLs, CLI
3. Run pytest -v

Working dir: /tmp/hermes-feishu-e2e"""

child.ephemeral_system_prompt = (
    f"You are a focused subagent.\n\nYOUR TASK:\n{TASK}\n\n"
    f"WORKSPACE PATH:\n/tmp/hermes-feishu-e2e\n\n"
    f"Complete and summarize."
)

# ======== Run ========
start_feishu_relay()
send_progress("🚀 Claude Code ACP 启动中...")
start = time.time()

try:
    result = _run_single_child(
        task_index=0, goal=TASK, child=child, parent_agent=parent,
    )
    elapsed = time.time() - start
    
    summary = result.get("summary", "")[:400]
    trace = result.get("tool_trace", [])
    
    lines = [f"✅ 完成 · {elapsed:.1f}s", "", "**工具链:**"]
    for i, t in enumerate(trace, 1):
        icon = "✅" if t.get("status") == "ok" else "❌"
        lines.append(f"{i}. {icon} {t.get('tool', '?')}")
    lines.extend(["", summary])
    send_progress("\n".join(lines))
    
    # Verify
    for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                      ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
        if os.path.exists(p):
            send_progress(f"✅ {label} ({os.path.getsize(p)}b)")
        else:
            send_progress(f"❌ {label} NOT FOUND")
    
    if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
        r = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
        )
        for line in r.stdout.split("\n"):
            if "passed" in line or "failed" in line or "error" in line:
                send_progress(f"🧪 {line.strip()}")
    
    # Final summary via direct send (blocking)
    stop_feishu_relay()
    
    final_msg = f"📊 穿测完成\n耗时 {elapsed:.1f}s | 工具调用 {len(trace)} 次 | 文件 2 个"
    subprocess.run(
        ["openclaw", "message", "send", "--channel", "feishu", "--message", final_msg],
        capture_output=True, text=True, timeout=10, env={**os.environ}
    )
    
    print(f"\n✅ Done in {elapsed:.1f}s")
    print(f"Tool trace: {len(trace)} calls")
    
except Exception as e:
    elapsed = time.time() - start
    stop_feishu_relay()
    send_progress(f"❌ 失败 ({elapsed:.1f}s): {e}")
    import traceback
    traceback.print_exc()

child.close()
parent.close()
