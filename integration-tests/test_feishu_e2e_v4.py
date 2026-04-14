#!/usr/bin/env python3
"""
Hermes → Claude Code ACP 穿测 v4
进度写入文件，由外部读取并推送飞书。
不依赖 openclaw CLI、不依赖 callback、不依赖 npx。
直接用 Hermes 的 copilot-acp transport（已验证可用）。
"""
import os, sys, json, time

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

# ======== Progress file ========
PROGRESS_FILE = "/tmp/hermes-feishu-e2e/.progress"
PROCESSED_FILE = "/tmp/hermes-feishu-e2e/.processed"

def emit(event_type, text):
    """Write a progress event to the progress file (one JSON per line)."""
    event = {
        "ts": time.strftime("%H:%M:%S"),
        "type": event_type,
        "text": str(text),
    }
    with open(PROGRESS_FILE, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # Also print for local logging
    print(f"[{event['ts']}] {event_type}: {text[:120]}", flush=True)

# Clear progress
for f in [PROGRESS_FILE, PROCESSED_FILE]:
    if os.path.exists(f):
        os.remove(f)

# ======== Build Hermes ========
from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

# Monkey-patch: hook into CopilotACPClient to emit progress
import agent.copilot_acp_client as _acp_mod
_orig_process_message = None

def _patched_process_response(self, response):
    """Intercept ACP responses to extract tool calls."""
    # Emit progress from response
    if isinstance(response, dict):
        method = response.get("method", "")
        params = response.get("params", {}).get("update", {})
        
        if method == "notifications/session_update":
            utype = params.get("type", "")
            if utype == "tool_call_start":
                tc = params.get("toolCall", {})
                name = tc.get("toolName", "?")
                inp = str(tc.get("input", ""))[:150]
                emit("tool_start", f"{name} | {inp}")
            elif utype == "tool_call_end":
                tc = params.get("toolCall", {})
                name = tc.get("toolName", "?")
                out = str(tc.get("output", ""))[:300]
                emit("tool_end", f"{name} | {out}")
            elif utype == "text":
                txt = params.get("text", "").strip()
                if txt:
                    emit("text", txt[:300])
            elif utype == "thinking":
                txt = params.get("text", "")
                if txt and len(txt) < 200:
                    emit("thinking", txt[:150])
    
    if _orig_process_message:
        return _orig_process_message(self, response)

# Try to patch
try:
    client_cls = getattr(_acp_mod, "CopilotACPClient", None)
    if client_cls:
        # Look for a method that processes incoming messages
        for attr in dir(client_cls):
            if "process" in attr.lower() and "response" in attr.lower():
                _orig_process_message = getattr(client_cls, attr)
                setattr(client_cls, attr, _patched_process_response)
                emit("system", f"Patched {attr} for progress tracking")
                break
        else:
            emit("system", "Could not find process_response method to patch")
except Exception as e:
    emit("system", f"Patch failed: {e}")

# ======== Build parent & child ========
emit("start", "Building Hermes parent agent...")

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

emit("start", "Building Claude Code ACP child...")

child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E v4",
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

emit("start", "Claude Code ACP child ready, launching task...")

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

# ======== Run ========
start = time.time()

try:
    result = _run_single_child(
        task_index=0, goal=TASK, child=child, parent_agent=parent,
    )
    elapsed = time.time() - start
    
    summary = result.get("summary", "")[:400]
    trace = result.get("tool_trace", [])
    
    emit("done", f"completed in {elapsed:.1f}s | {len(trace)} tool calls | summary: {summary[:200]}")
    
    # Verify files
    for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                      ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
        if os.path.exists(p):
            emit("verify", f"✅ {label} ({os.path.getsize(p)} bytes)")
        else:
            emit("verify", f"❌ {label} NOT FOUND")
    
    # Run tests
    if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
        )
        for line in r.stdout.split("\n"):
            if "passed" in line.lower() or "failed" in line.lower() or "error" in line.lower():
                emit("test", line.strip())
    
    emit("end", f"FINISHED")
    
except Exception as e:
    elapsed = time.time() - start
    emit("error", f"Failed after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

child.close()
parent.close()
