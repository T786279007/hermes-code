#!/usr/bin/env python3
"""
Hermes → Claude Code ACP 穿测 v5 · 飞书实时进度
架构：Hermes tool_progress_callback → 写文件 → OpenClaw 轮询 → 飞书推送
"""
import os, sys, json, time

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

emit("system", "v5 starting")

from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

# ======== Parent with file-writing callback ========
def feishu_callback(event_type, text=None, **kwargs):
    """This callback gets called by Hermes's progress system."""
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
parent.tool_progress_callback = feishu_callback

emit("system", "Parent ready, building child...")

# ======== Child ========
child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E v5",
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

emit("system", f"Child ready: provider={child.provider}, acp={child.acp_command}")

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

emit("task", "Launching Claude Code via ACP...")
start = time.time()

try:
    result = _run_single_child(
        task_index=0, goal=TASK, child=child, parent_agent=parent,
    )
    elapsed = time.time() - start
    summary = result.get("summary", "")[:400]
    trace = result.get("tool_trace", [])
    
    emit("done", f"completed {elapsed:.1f}s | {len(trace)} tools | {summary[:200]}")
    
    # Verify
    for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                      ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
        if os.path.exists(p):
            emit("verify", f"✅ {label} ({os.path.getsize(p)}b)")
        else:
            emit("verify", f"❌ {label} MISSING")
    
    if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
        )
        for line in r.stdout.split("\n"):
            if any(k in line.lower() for k in ["passed", "failed", "error", "shortener"]):
                emit("test", line.strip())
    
    emit("end", "ALL DONE")

except Exception as e:
    elapsed = time.time() - start
    emit("error", f"Failed {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

child.close()
parent.close()
print(f"\nFinished. {time.time()-start:.1f}s total.", flush=True)
