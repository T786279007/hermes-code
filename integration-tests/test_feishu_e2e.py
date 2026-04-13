#!/usr/bin/env python3
"""
NUC E2E: Hermes → Claude Code ACP with real-time Feishu progress relay.
Uses OpenClaw's message tool channel to push progress to Feishu.
No separate Feishu app needed.
"""
import os, sys, json, time, threading, subprocess

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

# ======== Feishu relay via openclaw CLI ========
def send_feishu(text):
    """Send progress message to Feishu via OpenClaw."""
    try:
        result = subprocess.run(
            ["openclaw", "message", "send", "--channel", "feishu",
             "--message", text],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PATH": os.environ.get("PATH", "")}
        )
        if result.returncode != 0:
            print(f"  [feishu relay error] {result.stderr[:200]}")
    except Exception as e:
        print(f"  [feishu relay exception] {e}")

def send_feishu_card(title, lines):
    """Send a progress card to Feishu."""
    card_lines = [f"**{title}**", ""]
    for line in lines:
        card_lines.append(line)
    card_lines.append("")
    card_lines.append(f"_via Hermes→Claude Code ACP · {time.strftime('%H:%M:%S')}_")
    send_feishu("\n".join(card_lines))

# ======== Build Hermes parent ========
from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

send_feishu_card("🚀 Hermes ACP 穿测启动", [
    "调度器: GLM-5-Turbo",
    "执行器: Claude Code (ACP协议)",
    "项目: /tmp/hermes-feishu-e2e/",
    "",
    "即将启动 Claude Code，每个工具调用实时推送..."
])

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

# ======== Progress callback → Feishu ========
def make_feishu_progress_callback():
    tool_counts = {}
    
    def callback(event_type, text=None, **kwargs):
        ts = time.strftime("%H:%M:%S")
        
        if event_type in ("tool_started", "subagent_progress"):
            if text:
                tool_name = text.split(" — ")[0] if " — " in text else text
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                send_feishu(f"🔧 {text}")
        
        elif event_type == "_thinking":
            if text and len(text) < 200:
                send_feishu(f"💭 {text[:150]}")
    
    return callback, tool_counts

callback, tool_counts = make_feishu_progress_callback()
parent.tool_progress_callback = callback

# ======== Build Claude Code ACP child ========
send_feishu("🔧 正在启动 Claude Code ACP 子进程...")

child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E test",
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

child.tool_progress_callback = callback

send_feishu("✅ Claude Code ACP 子进程就绪，开始执行任务...")

# ======== The task ========
TASK = """Build a URL shortener service in /tmp/hermes-feishu-e2e/:

1. Create `shortener.py` with:
   - A URLShortener class with encode(long_url) → short_code and decode(short_code) → long_url
   - Use base62 encoding (0-9, a-z, A-Z) for short codes
   - Store mappings in a JSON file (data.json)
   - Handle collisions, invalid URLs, duplicate entries
   - Include a simple CLI interface using argparse

2. Create `test_shortener.py` with pytest tests:
   - Test encode/decode roundtrip
   - Test collision handling
   - Test persistence (save/load JSON)
   - Test invalid URL handling
   - Test CLI interface

3. Run all tests with pytest -v

Working directory: /tmp/hermes-feishu-e2e"""

child.ephemeral_system_prompt = (
    f"You are a focused subagent working on a specific delegated task.\n\n"
    f"YOUR TASK:\n{TASK}\n\n"
    f"WORKSPACE PATH:\n/tmp/hermes-feishu-e2e\n\n"
    f"Complete this task. When finished, provide a summary."
)

# ======== Run ========
start = time.time()

try:
    send_feishu("⚡ Claude Code 开始工作...")
    
    result = _run_single_child(
        task_index=0,
        goal=TASK,
        child=child,
        parent_agent=parent,
    )
    elapsed = time.time() - start
    
    # ======== Results ========
    summary = result.get("summary", "")[:400]
    api_calls = result.get("api_calls", 0)
    trace = result.get("tool_trace", [])
    
    result_lines = [
        f"✅ **完成** · {elapsed:.1f}s · {api_calls} API calls",
        "",
        f"**工具调用链:**",
    ]
    for i, t in enumerate(trace, 1):
        status_icon = "✅" if t.get("status") == "ok" else "❌"
        tool_name = t.get("tool", "unknown")
        result_lines.append(f"{i}. {status_icon} {tool_name}")
    
    result_lines.extend(["", f"**摘要:**", summary])
    
    send_feishu_card("📊 Claude Code 执行完成", result_lines)
    
    # ======== Verification ========
    shortener = "/tmp/hermes-feishu-e2e/shortener.py"
    test_file = "/tmp/hermes-feishu-e2e/test_shortener.py"
    
    verify_lines = []
    for p, label in [(shortener, "shortener.py"), (test_file, "test_shortener.py")]:
        if os.path.exists(p):
            verify_lines.append(f"✅ {label} ({os.path.getsize(p)} bytes)")
        else:
            verify_lines.append(f"❌ {label} NOT FOUND")
    
    # Run tests
    if os.path.exists(test_file):
        test_result = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30,
            cwd="/tmp/hermes-feishu-e2e"
        )
        verify_lines.append("")
        verify_lines.append("**pytest 结果:**")
        # Extract summary line
        for line in test_result.stdout.split("\n"):
            if "passed" in line or "failed" in line or "error" in line:
                verify_lines.append(line.strip())
        # Show failures if any
        if "FAILED" in test_result.stdout:
            verify_lines.append("")
            verify_lines.append("```")
            verify_lines.append(test_result.stdout[-500:])
            verify_lines.append("```")
    
    send_feishu_card("🔍 验证结果", verify_lines)

except Exception as e:
    elapsed = time.time() - start
    send_feishu(f"❌ 穿测失败 ({elapsed:.1f}s): {e}")
    import traceback
    traceback.print_exc()

child.close()
parent.close()
print("\nDone.")
