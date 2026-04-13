#!/usr/bin/env python3
"""Quick RCA: add traceback to catch the exact 'list' has no 'get' error."""
import os, sys, json, time, traceback

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")

# Monkey-patch to catch AttributeError with full traceback
import run_agent as _ra

_orig_interruptible = _ra.AIAgent._interruptible_api_call

def _patched_interruptible(self, api_kwargs):
    try:
        return _orig_interruptible(self, api_kwargs)
    except AttributeError as e:
        print("=" * 60, flush=True)
        print(f"AttributeError caught: {e}", flush=True)
        traceback.print_stack()
        print("=" * 60, flush=True)
        raise

_ra.AIAgent._interruptible_api_call = _patched_interruptible

# Also patch _create_chat_completion to log what it returns
import agent.copilot_acp_client as _acp
_orig_create = _acp.CopilotACPClient._create_chat_completion

def _patched_create(self, **kwargs):
    result = _orig_create(self, **kwargs)
    print(f"[PATCHED] _create_chat_completion returned: type={type(result).__name__}", flush=True)
    if hasattr(result, 'choices') and result.choices:
        msg = result.choices[0].message
        print(f"[PATCHED] message.content type={type(msg.content).__name__}", flush=True)
        if isinstance(msg.content, list):
            print(f"[PATCHED] content is LIST: {msg.content[:3]}", flush=True)
        if msg.tool_calls:
            print(f"[PATCHED] tool_calls count={len(msg.tool_calls)}", flush=True)
            for i, tc in enumerate(msg.tool_calls):
                print(f"[PATCHED]   tc[{i}]: type={type(tc).__name__}, has fn={hasattr(tc, 'function')}", flush=True)
    return result

_acp.CopilotACPClient._create_chat_completion = _patched_create

# Now run the test
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
    task_index=0, goal="", context="RCA test",
    toolsets=["terminal", "file"],
    model=None, max_iterations=3,
    parent_agent=parent,
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

TASK = "Say hello in one sentence. Then use write_file to create /tmp/rca-test/hello.txt with the text 'Hello from Claude'."
child.ephemeral_system_prompt = f"YOUR TASK:\n{TASK}\n\nWorking dir: /tmp/rca-test"
os.makedirs("/tmp/rca-test", exist_ok=True)

print("Starting RCA test...", flush=True)
try:
    result = _run_single_child(0, TASK, child, parent)
    print(f"Result: {result.get('status')}", flush=True)
except Exception as e:
    print(f"Error: {e}", flush=True)
    traceback.print_exc()

child.close()
parent.close()
