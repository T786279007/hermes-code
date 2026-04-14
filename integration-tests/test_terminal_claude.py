#!/usr/bin/env python3
"""
Test 4: 最简单的验证 — Hermes parent agent 通过 terminal 工具调用 claude --print
这模拟了实际使用场景：Hermes (GLM) 作为调度器，通过 shell 调用 Claude Code。
"""
import os, sys, time

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-claude-test", exist_ok=True)

from run_agent import AIAgent

print("=" * 60)
print("Hermes (GLM) → terminal → Claude Code --print")
print("=" * 60)

agent = AIAgent(
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

# Tell GLM to use Claude Code for the actual coding task
task = """Use the terminal tool to run this exact command:

claude --print --dangerously-skip-permissions -p "Create a file /tmp/hermes-claude-test/hermes_claude_test.py that: 1) Defines a class TaskManager with methods add_task(name, priority), get_tasks(), complete_task(name). 2) At the bottom, create an instance, add 3 tasks with different priorities, print all tasks, complete one, and print again."

Then verify the file was created by reading it with the file tool."""

print(f"\n→ Sending task to Hermes (GLM)...")
print(f"  GLM will use terminal tool to invoke Claude Code\n")

start = time.time()
result = agent.run_conversation(user_message=task)
elapsed = time.time() - start

print(f"\n{'=' * 60}")
print(f"✅ Completed in {elapsed:.1f}s")
print(f"{'=' * 60}")

# Verify
path = "/tmp/hermes-claude-test/hermes_claude_test.py"
if os.path.exists(path):
    print(f"✅ {path} exists ({os.path.getsize(path)} bytes)!")
    with open(path) as f:
        content = f.read()
    print(f"\n--- Content ---")
    print(content)
    print(f"\n--- Run output ---")
    os.system(f"python3 {path} 2>&1")
else:
    print(f"✗ {path} not found")
    for f in sorted(os.listdir("/tmp/hermes-claude-test")):
        print(f"  {f}")

agent.close()
