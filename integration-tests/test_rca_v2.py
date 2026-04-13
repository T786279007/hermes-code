#!/usr/bin/env python3
"""RCA v2: trigger tool calls to find the bug."""
import os, sys, json, time, traceback

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")

# Monkey-patch
import run_agent as _ra
_orig_interruptible = _ra.AIAgent._interruptible_api_call

_call_count = {"n": 0}

def _patched_interruptible(self, api_kwargs):
    _call_count["n"] += 1
    n = _call_count["n"]
    print(f"[API CALL #{n}] api_mode={self.api_mode} provider={self.provider}", flush=True)
    
    # Log messages content types
    msgs = api_kwargs.get("messages", [])
    for i, m in enumerate(msgs):
        role = m.get("role", "?") if isinstance(m, dict) else "?"
        content = m.get("content") if isinstance(m, dict) else None
        ctype = type(content).__name__
        if isinstance(content, list):
            print(f"  msg[{i}] role={role} content=LIST[{len(content)}]", flush=True)
            for j, p in enumerate(content[:2]):
                print(f"    [{j}] type={type(p).__name__} keys={list(p.keys()) if isinstance(p, dict) else 'N/A'}", flush=True)
        else:
            print(f"  msg[{i}] role={role} content={ctype}", flush=True)
    
    try:
        result = _orig_interruptible(self, api_kwargs)
        print(f"[API CALL #{n}] SUCCESS", flush=True)
        return result
    except AttributeError as e:
        print(f"[API CALL #{n}] AttributeError: {e}", flush=True)
        traceback.print_stack()
        raise

_ra.AIAgent._interruptible_api_call = _patched_interruptible

# Patch streaming too
_orig_streaming = _ra.AIAgent._interruptible_streaming_api_call

def _patched_streaming(self, api_kwargs, **kw):
    _call_count["n"] += 1
    n = _call_count["n"]
    print(f"[STREAM #{n}] api_mode={self.api_mode}", flush=True)
    try:
        return _orig_streaming(self, api_kwargs, **kw)
    except AttributeError as e:
        print(f"[STREAM #{n}] AttributeError: {e}", flush=True)
        traceback.print_stack()
        raise

_ra.AIAgent._interruptible_streaming_api_call = _patched_streaming

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
)

child = _build_child_agent(
    task_index=0, goal="", context="RCA v2",
    toolsets=["terminal", "file"],
    model=None, max_iterations=10,
    parent_agent=parent,
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

TASK = """Create a Python file at /tmp/rca-v2/calculator.py with a Calculator class that has add, subtract, multiply, divide methods. Then run pytest on it. Working dir: /tmp/rca-v2"""
child.ephemeral_system_prompt = f"YOUR TASK:\n{TASK}\n\nWorking dir: /tmp/rca-v2"
os.makedirs("/tmp/rca-v2", exist_ok=True)

print("Starting RCA v2...", flush=True)
try:
    result = _run_single_child(0, TASK, child, parent)
    print(f"Result: {result.get('status')} api_calls={result.get('api_calls')}", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    traceback.print_exc()

child.close()
parent.close()
