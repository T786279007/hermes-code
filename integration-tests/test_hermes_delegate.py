#!/usr/bin/env python3
"""
Test 2: Hermes delegate_task with acp_command=claude
Uses Claude Code via --print mode (non-ACP, direct subprocess)
"""
import os, sys, json, time

# Add hermes-agent to path
sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")

os.makedirs("/tmp/hermes-claude-test", exist_ok=True)

# Minimal AIAgent setup for delegation
from run_agent import AIAgent

print("=" * 60)
print("Hermes delegate_task → Claude Code 测试")
print("=" * 60)

# Create a parent agent (uses GLM as the orchestrator model)
parent = AIAgent(
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
    api_key=os.environ.get("CUSTOM_API_KEY", ""),
    model="glm-5-turbo",
    provider="custom",
    enabled_toolsets=["terminal", "file"],
    quiet_mode=False,
    max_iterations=5,  # parent only needs a few turns
    skip_context_files=True,
    skip_memory=True,
    clarify_callback=None,
)

print(f"Parent agent created: {parent.model}")
print(f"Parent API mode: {parent.api_mode}")

# Now test delegate_task with Claude Code
from tools.delegate_tool import delegate_task

print(f"\n→ Delegating task to Claude Code...")
start = time.time()

try:
    result = delegate_task(
        goal="Create a file /tmp/hermes-claude-test/delegated.py that defines a function fibonacci(n) returning the n-th Fibonacci number, then write a simple test at the bottom that prints fibonacci(10)",
        context="This is a test of Hermes → Claude Code delegation via ACP.",
        acp_command="claude",
        acp_args=["--print", "--dangerously-skip-permissions", "-p"],
        max_iterations=10,
        parent_agent=parent,
    )
    elapsed = time.time() - start
    print(f"\n✅ Delegation completed in {elapsed:.1f}s")
    print(f"Result:\n{result}")
except Exception as e:
    elapsed = time.time() - start
    print(f"\n✗ Delegation failed after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

# Verify
print(f"\n{'=' * 60}")
print("Verification")
print(f"{'=' * 60}")
path = "/tmp/hermes-claude-test/delegated.py"
if os.path.exists(path):
    print(f"✅ {path} exists!")
    with open(path) as f:
        print(f.read())
else:
    print(f"✗ {path} not found")
    print(f"Files: {os.listdir('/tmp/hermes-claude-test')}")

parent.close()
print("\nDone.")
