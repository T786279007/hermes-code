#!/usr/bin/env python3
"""
Test 3: Hermes delegate_task → Claude Code via copilot-acp provider
This is the CORRECT way to spawn Claude Code as a subprocess.
"""
import os, sys, json, time

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-claude-test", exist_ok=True)

from run_agent import AIAgent
from tools.delegate_tool import delegate_task

print("=" * 60)
print("Hermes delegate_task → Claude Code (ACP mode)")
print("=" * 60)

# Parent agent: GLM as orchestrator
parent = AIAgent(
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
    api_key=os.environ.get("CUSTOM_API_KEY", ""),
    model="glm-5-turbo",
    provider="custom",
    enabled_toolsets=["terminal", "file"],
    quiet_mode=False,
    max_iterations=5,
    skip_context_files=True,
    skip_memory=True,
    clarify_callback=None,
)

print(f"Parent: {parent.model} (provider={parent.provider})")

# Delegate to Claude Code via ACP
# Key: acp_command="claude" + the child gets provider="copilot-acp" automatically
print(f"\n→ Delegating to Claude Code (acp_command=claude, --print mode)...")
start = time.time()

try:
    result = delegate_task(
        goal="Create /tmp/hermes-claude-test/acp_test.py: a Python file with a function that generates a random password of length 16, using string and random modules. At the bottom, call it and print the result.",
        context="Test of Hermes → Claude Code ACP delegation.",
        # These are the key params for Claude Code ACP:
        acp_command="claude",
        acp_args=["--print", "--dangerously-skip-permissions", "-p"],
        override_provider="copilot-acp",  # Force ACP transport
        max_iterations=15,
        parent_agent=parent,
    )
    elapsed = time.time() - start
    print(f"\n✅ Completed in {elapsed:.1f}s")
    
    # Parse result
    if isinstance(result, str):
        data = json.loads(result)
    else:
        data = result
    
    for r in data.get("results", []):
        print(f"\n📋 Task [{r.get('task_index')}]: {r.get('status')}")
        print(f"   Summary: {r.get('summary', '')[:300]}")
        print(f"   API calls: {r.get('api_calls')}")
        print(f"   Duration: {r.get('duration_seconds', 0):.1f}s")
        for t in r.get("tool_trace", []):
            print(f"   🔧 {t.get('tool')} → {t.get('status')}")

except Exception as e:
    elapsed = time.time() - start
    print(f"\n✗ Failed after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

# Verify
print(f"\n{'=' * 60}")
print("Verification")
print(f"{'=' * 60}")
path = "/tmp/hermes-claude-test/acp_test.py"
if os.path.exists(path):
    print(f"✅ {path} exists!")
    with open(path) as f:
        content = f.read()
    print(content)
    print(f"\nRunning it...")
    os.system(f"python3 {path} 2>&1")
else:
    print(f"✗ {path} not found")
    for f in sorted(os.listdir("/tmp/hermes-claude-test")):
        fp = os.path.join("/tmp/hermes-claude-test", f)
        print(f"  {f} ({os.path.getsize(fp)} bytes)")

parent.close()
print("\nDone.")
