#!/usr/bin/env python3
"""RCA v3: force tool_calls by making Claude Code return a tool call."""
import os, sys, json, time, traceback

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/rca-v3", exist_ok=True)

# Patch to log every API call
import run_agent as _ra

_call_count = {"n": 0}

def _log_api(self, api_kwargs, *, on_first_delta=None):
    _call_count["n"] += 1
    n = _call_count["n"]
    msgs = api_kwargs.get("messages", [])
    print(f"[CALL #{n}] msgs={len(msgs)}", flush=True)
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            print(f"  msg[{i}] NOT A DICT: {type(m).__name__}", flush=True)
            continue
        role = m.get("role", "?")
        content = m.get("content")
        tool_calls = m.get("tool_calls")
        tool_name = m.get("tool_name")
        
        parts = [f"role={role}"]
        if content is not None:
            parts.append(f"content={type(content).__name__}")
            if isinstance(content, list):
                for j, p in enumerate(content[:2]):
                    if isinstance(p, dict):
                        parts.append(f"[{j}]keys={list(p.keys())}")
        if tool_calls is not None:
            parts.append(f"tool_calls={type(tool_calls).__name__}[{len(tool_calls)}]")
        if tool_name:
            parts.append(f"tool_name={tool_name}")
        print(f"  msg[{i}] {' | '.join(parts)}", flush=True)
    
    try:
        if hasattr(self, '_has_stream_consumers') and self._has_stream_consumers():
            return _ra_orig_streaming(self, api_kwargs, on_first_delta=on_first_delta)
        return _ra_orig_call(self, api_kwargs)
    except AttributeError as e:
        print(f"[CALL #{n}] *** AttributeError: {e} ***", flush=True)
        traceback.print_stack()
        raise

_ra_orig_call = _ra.AIAgent._interruptible_api_call
_ra_orig_streaming = _ra.AIAgent._interruptible_streaming_api_call

_ra.AIAgent._interruptible_api_call = _log_api
_ra.AIAgent._interruptible_streaming_api_call = _log_api

# Patch CopilotACPClient to force tool calls by modifying Claude's response
_orig_run_prompt = None

import agent.copilot_acp_client as _acp

def _force_tool_calls(self, prompt_text, *, timeout_seconds):
    """Intercept _run_prompt to see what Claude returns."""
    import traceback as tb
    result = _orig_run_prompt(self, prompt_text, timeout_seconds=timeout_seconds)
    text, reasoning = result
    print(f"[ACP] _run_prompt returned text_type={type(text).__name__} len={len(str(text))} reasoning={bool(reasoning)}", flush=True)
    if text:
        print(f"[ACP] text preview: {str(text)[:300]}", flush=True)
    return result

_orig_run_prompt = _acp.CopilotACPClient._run_prompt
_acp.CopilotACPClient._run_prompt = _force_tool_calls

# Also patch tool_calls extraction
_orig_extract = _acp._extract_tool_calls_from_text

def _log_extract(text):
    tc, cleaned = _orig_extract(text)
    if tc:
        print(f"[ACP] Extracted {len(tc)} tool_calls", flush=True)
        for i, t in enumerate(tc):
            print(f"[ACP]   tc[{i}]: name={t.function.name} args_len={len(t.function.arguments)}", flush=True)
    return tc, cleaned

_acp._extract_tool_calls_from_text = _log_extract

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
    task_index=0, goal="", context="RCA v3",
    toolsets=["terminal", "file"],
    model=None, max_iterations=10,
    parent_agent=parent,
    override_provider="copilot-acp",
    override_acp_command="npx",
    override_acp_args=["-y", "@agentclientprotocol/claude-agent-acp@^0.25.0"],
    override_base_url="acp://claude",
    override_api_key="unused",
)

# A task that should trigger MULTIPLE tool calls (write file, then run test)
TASK = """Create /tmp/rca-v3/app.py with a simple Flask app that has one route '/' returning 'Hello'. Then create /tmp/rca-v3/test_app.py with a pytest test. Then run the tests.

IMPORTANT: Do NOT use fs/write_text_file. Instead, use the write_file tool to write each file. Then use terminal to run pytest.

Working dir: /tmp/rca-v3"""

child.ephemeral_system_prompt = f"YOUR TASK:\n{TASK}\n\nWorking dir: /tmp/rca-v3"

print("Starting RCA v3...", flush=True)
try:
    result = _run_single_child(0, TASK, child, parent)
    print(f"\nResult: {result.get('status')} api_calls={result.get('api_calls')}", flush=True)
except Exception as e:
    print(f"\nError: {e}", flush=True)
    traceback.print_exc()

child.close()
parent.close()
