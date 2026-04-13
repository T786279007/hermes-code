#!/usr/bin/env python3
"""
Hermes → Claude Code ACP 端到端穿测（Musk 级别）
通过 Hermes copilot-acp provider 调 Claude Code，
实时转发每个工具调用到飞书。
"""
import os, sys, json, time, threading

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-acp-e2e", exist_ok=True)

from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent, _run_single_child

# ======== Progress relay to Feishu ========
# We'll collect progress events and print them
# The parent agent's tool_progress_callback will relay to stdout
progress_events = []
progress_lock = threading.Lock()

def make_progress_callback(task_count=1):
    """Create a callback that captures child agent progress."""
    events = []
    
    def callback(event_type, text=None, **kwargs):
        ts = time.strftime("%H:%M:%S")
        entry = {"ts": ts, "type": event_type, "text": text}
        events.append(entry)
        with progress_lock:
            progress_events.append(entry)
        
        if event_type == "subagent_progress":
            print(f"  [{ts}] 🔀 {text}")
        elif event_type == "tool_started":
            print(f"  [{ts}] 🔧 START: {text}")
        elif event_type == "tool_completed":
            print(f"  [{ts}] ✅ DONE: {text}")
    
    return callback, events

# ======== Build parent agent ========
parent = AIAgent(
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
    api_key=os.environ.get("CUSTOM_API_KEY", ""),
    model="glm-5-turbo",
    provider="custom",
    enabled_toolsets=["terminal", "file"],
    quiet_mode=False,
    max_iterations=3,  # Parent only delegates, doesn't do work
    skip_context_files=True,
    skip_memory=True,
    clarify_callback=None,
)

# Inject progress callback
callback, _ = make_progress_callback()
parent.tool_progress_callback = callback

print("Parent agent ready (GLM调度)")

# ======== Build child with ACP transport ========
print("\n→ Building Claude Code ACP child...")

child = _build_child_agent(
    task_index=0,
    goal="",  # Set later
    context="E2E ACP test with real-time progress",
    toolsets=["terminal", "file"],
    model=None,
    max_iterations=20,
    parent_agent=parent,
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

# Inject progress callback into child too
child.tool_progress_callback = callback

print(f"  Child provider: {child.provider}")
print(f"  Child ACP command: {child.acp_command} {' '.join(child.acp_args)}")

# ======== Run the test ========
TEST_TASK = """Create a complete Python project in /tmp/hermes-acp-e2e/:

1. Create a file called `calculator.py` with a Calculator class that supports:
   - add, subtract, multiply, divide operations
   - A history of all operations (stored as a list of dicts)
   - A method to export history to JSON
   - Proper error handling (division by zero, invalid input)

2. Create a file called `test_calculator.py` with pytest tests covering:
   - All 4 operations with positive/negative numbers
   - Division by zero handling
   - Invalid input handling
   - History tracking
   - JSON export

3. Run the tests and show results.

Working directory: /tmp/hermes-acp-e2e"""

print(f"\n→ Launching Claude Code via ACP...")
print(f"  Task: Calculator project with tests")
start = time.time()

# Manually run the child with the actual goal
child_goal = TEST_TASK
child.ephemeral_system_prompt = f"You are a focused subagent.\n\nYOUR TASK:\n{child_goal}\n\nWORKSPACE PATH:\n/tmp/hermes-acp-e2e\n\nComplete this task using the tools available to you. When finished, provide a clear, concise summary."

try:
    result = _run_single_child(
        task_index=0,
        goal=child_goal,
        child=child,
        parent_agent=parent,
    )
    elapsed = time.time() - start
    
    print(f"\n{'=' * 60}")
    print(f"✅ Claude Code ACP completed in {elapsed:.1f}s")
    print(f"{'=' * 60}")
    print(f"Status: {result.get('status')}")
    print(f"API calls: {result.get('api_calls')}")
    print(f"Summary: {result.get('summary', '')[:500]}")
    print(f"\nTool trace:")
    for i, t in enumerate(result.get("tool_trace", []), 1):
        print(f"  {i}. 🔧 {t.get('tool', 'unknown')} → {t.get('status', '?')}")

except Exception as e:
    elapsed = time.time() - start
    print(f"\n✗ Failed after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

# ======== Verification ========
print(f"\n{'=' * 60}")
print("Verification")
print(f"{'=' * 60}")

calc_path = "/tmp/hermes-acp-e2e/calculator.py"
test_path = "/tmp/hermes-acp-e2e/test_calculator.py"

for p, label in [(calc_path, "calculator.py"), (test_path, "test_calculator.py")]:
    if os.path.exists(p):
        print(f"✅ {label} ({os.path.getsize(p)} bytes)")
    else:
        print(f"✗ {label} NOT FOUND")

# Run tests if they exist
if os.path.exists(test_path):
    print(f"\n→ Running pytest...")
    os.system(f"cd /tmp/hermes-acp-e2e && python3 -m pytest test_calculator.py -v 2>&1 | head -30")

# Show all files
print(f"\n→ All files in /tmp/hermes-acp-e2e/:")
for f in sorted(os.listdir("/tmp/hermes-acp-e2e")):
    fp = os.path.join("/tmp/hermes-acp-e2e", f)
    if os.path.isfile(fp):
        print(f"  {f} ({os.path.getsize(fp)} bytes)")

child.close()
parent.close()
print("\nDone.")
