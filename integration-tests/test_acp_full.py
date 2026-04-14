#!/usr/bin/env python3
"""
Test 5: Hermes → Claude Code via ACP 协议（真正的双向交互模式）

Uses acpx's claude-agent-acp adapter as the ACP subprocess,
giving full progress tracking + interactive capability.
"""
import os, sys, json, time

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-acp-test", exist_ok=True)

from run_agent import AIAgent

print("=" * 60)
print("Hermes (GLM) → Claude Code ACP（完整交互模式）")
print("=" * 60)

# Create parent agent with copilot-acp provider
# This tells Hermes to use the ACP subprocess transport
parent = AIAgent(
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
    api_key=os.environ.get("CUSTOM_API_KEY", ""),
    model="glm-5-turbo",
    provider="custom",
    enabled_toolsets=["terminal", "file"],
    quiet_mode=False,
    max_iterations=10,
    skip_context_files=True,
    skip_memory=True,
    clarify_callback=None,
)

print(f"Parent: {parent.model} (provider={parent.provider})")

# Now manually build a child agent with copilot-acp provider + claude ACP command
from tools.delegate_tool import _build_child_agent

print(f"\n→ Building child agent with copilot-acp transport...")

child = _build_child_agent(
    task_index=0,
    goal="Create /tmp/hermes-acp-test/acp_interactive.py: a Python file with a PasswordGenerator class that can generate passwords of configurable length (default 16), with options for including uppercase, lowercase, digits, and special characters. Add a method to check password strength. At the bottom, demonstrate usage.",
    context="Test of Hermes → Claude Code ACP delegation with full interactive progress.",
    toolsets=["terminal", "file"],
    model=None,  # inherit from Claude Code
    max_iterations=15,
    parent_agent=parent,
    # These are the KEY params for ACP transport:
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

print(f"Child provider: {child.provider}")
print(f"Child acp_command: {child.acp_command}")
print(f"Child acp_args: {child.acp_args}")
print(f"Child api_mode: {child.api_mode}")

# Run the child
from tools.delegate_tool import _run_single_child

print(f"\n→ Running Claude Code via ACP...")
start = time.time()

try:
    result = _run_single_child(
        task_index=0,
        goal="Create /tmp/hermes-acp-test/acp_interactive.py with PasswordGenerator class",
        child=child,
        parent_agent=parent,
    )
    elapsed = time.time() - start
    print(f"\n✅ Completed in {elapsed:.1f}s")
    print(f"Status: {result.get('status')}")
    print(f"API calls: {result.get('api_calls')}")
    print(f"Summary: {result.get('summary', '')[:500]}")
    for t in result.get("tool_trace", []):
        print(f"  🔧 {t.get('tool')} → {t.get('status')}")
except Exception as e:
    elapsed = time.time() - start
    print(f"\n✗ Failed after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()

# Verify
print(f"\n{'=' * 60}")
print("Verification")
print(f"{'=' * 60}")
path = "/tmp/hermes-acp-test/acp_interactive.py"
if os.path.exists(path):
    print(f"✅ {path} exists ({os.path.getsize(path)} bytes)!")
    with open(path) as f:
        print(f.read())
else:
    print(f"✗ {path} not found")
    for f in sorted(os.listdir("/tmp/hermes-acp-test")):
        fp = os.path.join("/tmp/hermes-acp-test", f)
        print(f"  {f} ({os.path.getsize(fp)} bytes)")

child.close()
parent.close()
print("\nDone.")
