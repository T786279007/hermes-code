#!/usr/bin/env python3
"""
Quick test: Hermes delegate_task → Claude Code via ACP.

Creates /tmp/hermes-claude-test/hello.py using Claude Code,
then verifies the file exists.
"""
import os
import sys
import json
import tempfile
import subprocess

TEST_DIR = "/tmp/hermes-claude-test"
os.makedirs(TEST_DIR, exist_ok=True)

# Step 1: Direct test — spawn Claude Code via ACP stdio and send a simple task
print("=" * 60)
print("TEST 1: Claude Code ACP stdio 直接调用")
print("=" * 60)

# Test that claude --acp --stdio works
proc = subprocess.Popen(
    ["claude", "--acp", "--stdio"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=TEST_DIR,
    env={**os.environ, "CLAUDE_CODE_USE_BEDROCK": "0"},
)

import time

# Send ACP initialize
init_msg = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "clientInfo": {"name": "test-client", "version": "1.0"},
        "capabilities": {}
    }
}) + "\n"

print(f"→ Sending initialize...")
proc.stdin.write(init_msg)
proc.stdin.flush()

# Read response with timeout
import select
ready, _, _ = select.select([proc.stdout], [], [], 15)
if ready:
    line = proc.stdout.readline()
    resp = json.loads(line)
    if "result" in resp:
        print(f"← Initialized OK: {resp['result'].get('agent', {}).get('name', 'unknown')}")
        server_caps = resp['result'].get('capabilities', {})
        print(f"  Capabilities: {json.dumps(server_caps, indent=2)[:300]}")
    else:
        print(f"← Response: {json.dumps(resp, indent=2)[:500]}")
else:
    print("✗ TIMEOUT waiting for initialize response")
    proc.kill()
    sys.exit(1)

# Send initialized notification
notif = json.dumps({
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
}) + "\n"
proc.stdin.write(notif)
proc.stdin.flush()
time.sleep(0.5)

# Step 2: Create a session
print(f"\n→ Creating session...")
session_msg = json.dumps({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "newSession",
    "params": {}
}) + "\n"
proc.stdin.write(session_msg)
proc.stdin.flush()

ready, _, _ = select.select([proc.stdout], [], [], 15)
if ready:
    line = proc.stdout.readline()
    resp = json.loads(line)
    if "result" in resp:
        session_id = resp['result'].get('sessionId', 'unknown')
        print(f"← Session created: {session_id}")
    else:
        print(f"← Response: {json.dumps(resp, indent=2)[:500]}")
        proc.kill()
        sys.exit(1)
else:
    print("✗ TIMEOUT creating session")
    proc.kill()
    sys.exit(1)

# Step 3: Send a task
print(f"\n→ Sending task: create hello.py...")
task_msg = json.dumps({
    "jsonrpc": "2.0",
    "id": 3,
    "method": "prompt",
    "params": {
        "sessionId": session_id,
        "text": "Create a file called hello.py in the current directory. It should print 'Hello from Claude Code via Hermes ACP!' and also write the current timestamp."
    }
}) + "\n"
proc.stdin.write(task_msg)
proc.stdin.flush()

# Read streaming responses (tool calls + final text)
print(f"\n← Streaming responses:")
full_response = []
start = time.time()
while time.time() - start < 120:  # 2 min timeout
    ready, _, _ = select.select([proc.stdout], [], [], 5)
    if ready:
        line = proc.stdout.readline()
        if not line:
            print("  [stream ended]")
            break
        try:
            resp = json.loads(line)
            method = resp.get("method", "")
            
            if method == "notifications/session_update":
                content = resp.get("params", {}).get("update", {})
                update_type = content.get("type", "")
                if update_type == "tool_call_start":
                    tool = content.get("toolCall", {}).get("toolName", "unknown")
                    print(f"  🔧 Tool: {tool}")
                elif update_type == "tool_call_end":
                    pass  # tool completed
                elif update_type == "text":
                    text = content.get("text", "")
                    if text.strip():
                        full_response.append(text)
                        print(f"  📝 {text[:200]}")
            elif "result" in resp:
                # Final response
                print(f"  ✅ Task completed!")
                break
        except json.JSONDecodeError:
            print(f"  [raw] {line[:200]}")
    else:
        # No data, but process still running — wait more
        if proc.poll() is not None:
            print(f"  ✗ Process exited with code {proc.returncode}")
            break

# Cleanup
proc.stdin.close()
proc.terminate()
try:
    proc.wait(timeout=5)
except:
    proc.kill()

# Step 4: Verify
print(f"\n{'=' * 60}")
print(f"TEST 2: 验证文件")
print(f"{'=' * 60}")
hello_path = os.path.join(TEST_DIR, "hello.py")
if os.path.exists(hello_path):
    print(f"✅ {hello_path} exists!")
    print(f"--- Content ---")
    with open(hello_path) as f:
        print(f.read())
else:
    print(f"✗ {hello_path} not found")
    print(f"Files in {TEST_DIR}:")
    for f in os.listdir(TEST_DIR):
        print(f"  {f}")

print(f"\n{'=' * 60}")
print("TEST COMPLETE")
print(f"{'=' * 60}")
