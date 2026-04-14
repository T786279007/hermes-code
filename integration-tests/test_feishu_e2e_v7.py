#!/usr/bin/env python3
"""
Hermes → Claude Code ACP 穿测 v7 · 有实质内容的飞书卡片进度

方案：
1. Monkey-patch CopilotACPClient._handle_server_message 拦截 Claude 的实时输出
2. 每 10 秒汇总一次，写进度文件
3. OpenClaw 轮询进度文件，组装飞书卡片推送

进度卡片格式参考用户给的模板：
- 已执行：Claude Code 在做什么
- 当前状态：思考中/写代码/跑测试/完成
- 发现：Claude 的中间输出摘要
- 结论：下一步预估
"""
import os, sys, json, time, threading

sys.path.insert(0, "/home/txs/hermes-agent")
os.chdir("/home/txs/hermes-agent")
os.makedirs("/tmp/hermes-feishu-e2e", exist_ok=True)

PROGRESS_FILE = "/tmp/hermes-feishu-e2e/.progress"
SNAPSHOT_FILE = "/tmp/hermes-feishu-e2e/.snapshot"

for f in [PROGRESS_FILE, SNAPSHOT_FILE]:
    if os.path.exists(f):
        os.remove(f)

# ======== Claude 实时输出捕获 ========
_claude_chunks = []          # 文本块
_claude_thoughts = []        # 思考块
_claude_tool_calls = []      # 工具调用（从 fs/ 事件捕获）
_claude_lock = threading.Lock()
_last_snapshot_ts = [0.0]

def _snapshot_progress():
    """将当前 Claude 输出快照写入文件，供 OpenClaw 读取。"""
    now = time.time()
    if now - _last_snapshot_ts[0] < 10:  # 最多每 10 秒一次
        return
    _last_snapshot_ts[0] = now
    
    with _claude_lock:
        text = "".join(_claude_chunks[-20:])  # 最近 20 块
        thoughts = "".join(_claude_thoughts[-10:])
        tools = list(_claude_tool_calls[-10:])
    
    snapshot = {
        "ts": time.strftime("%H:%M:%S"),
        "elapsed": 0,  # filled by runner
        "text_preview": text[-500:] if text else "",
        "thought_preview": thoughts[-300:] if thoughts else "",
        "recent_tools": tools,
        "chunk_count": len(_claude_chunks),
    }
    
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, ensure_ascii=False)

def emit(event_type, text):
    event = {"ts": time.strftime("%H:%M:%S"), "type": event_type, "text": str(text)[:500]}
    with open(PROGRESS_FILE, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"[{event['ts']}] {event_type}: {str(text)[:100]}", flush=True)

# ======== Patch Hermes ACP client ========
import agent.copilot_acp_client as _acp_mod
_orig_handle = _acp_mod.CopilotACPClient._handle_server_message

def _patched_handle(self, msg, *, process, cwd, text_parts=None, reasoning_parts=None):
    """Intercept Claude Code messages to capture real-time progress."""
    method = msg.get("method", "")
    params = msg.get("params") or {}
    
    # Capture fs/write_text_file events (file writes)
    if method == "fs/write_text_file":
        path = params.get("path", "")
        with _claude_lock:
            _claude_tool_calls.append({"type": "write_file", "path": path, "ts": time.strftime("%H:%M:%S")})
        emit("tool", f"📝 写入文件: {os.path.basename(path)}")
    
    # Capture fs/read_text_file events (file reads)
    elif method == "fs/read_text_file":
        path = params.get("path", "")
        with _claude_lock:
            _claude_tool_calls.append({"type": "read_file", "path": path, "ts": time.strftime("%H:%M:%S")})
        emit("tool", f"📖 读取文件: {os.path.basename(path)}")
    
    # Capture session/update for text/thought chunks
    elif method == "session/update":
        update = params.get("update") or {}
        kind = str(update.get("sessionUpdate") or "")
        content = update.get("content") or {}
        chunk = str(content.get("text") or "")
        
        if kind == "agent_message_chunk" and chunk:
            with _claude_lock:
                _claude_chunks.append(chunk)
        elif kind == "agent_thought_chunk" and chunk:
            with _claude_lock:
                _claude_thoughts.append(chunk)
    
    # Call original
    return _orig_handle(self, msg, process=process, cwd=cwd, text_parts=text_parts, reasoning_parts=reasoning_parts)

_acp_mod.CopilotACPClient._handle_server_message = _patched_handle
emit("system", "Patched ACP client for real-time progress capture")

# ======== Build Hermes ========
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
    clarify_callback=None,
)

def progress_callback(event_type, text=None, **kwargs):
    emit(event_type, text or str(kwargs))

parent.tool_progress_callback = progress_callback

child = _build_child_agent(
    task_index=0,
    goal="",
    context="Feishu E2E v7",
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

# ======== Heartbeat: 每 10 秒拍快照 ========
heartbeat_stop = threading.Event()

def heartbeat_loop():
    start = time.time()
    while not heartbeat_stop.wait(10):
        elapsed = time.time() - start
        _snapshot_progress()
        
        with _claude_lock:
            text = "".join(_claude_chunks[-20:])
            tools = list(_claude_tool_calls)
        
        # 判断阶段
        if not text:
            phase = "启动中"
        elif "pytest" in text[-500:] or "test" in text[-500:].lower():
            phase = "🧪 跑测试中"
        elif tools and any(t["type"] == "write_file" for t in tools[-3:]):
            phase = "📝 写代码中"
        elif text:
            phase = "💭 思考中"
        else:
            phase = "⏳ 等待中"
        
        # 写文件数
        writes = [t for t in tools if t["type"] == "write_file"]
        reads = [t for t in tools if t["type"] == "read_file"]
        
        emit("heartbeat", f"⏳ [{elapsed:.0f}s] {phase} | 写入 {len(writes)} 文件 | 读取 {len(reads)} 次")

hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
hb_thread.start()

# ======== Run ========
emit("task", "🚀 Claude Code 开始执行任务")
start = time.time()

try:
    result = _run_single_child(
        task_index=0, goal=TASK, child=child, parent_agent=parent,
    )
    elapsed = time.time() - start
    summary = result.get("summary", "")[:600]
    
    with _claude_lock:
        all_text = "".join(_claude_chunks)
        all_tools = list(_claude_tool_calls)
    
    writes = [t for t in all_tools if t["type"] == "write_file"]
    reads = [t for t in all_tools if t["type"] == "read_file"]
    
    emit("done", f"✅ 任务完成")
    emit("summary", f"耗时 {elapsed:.1f}s | 写入 {len(writes)} 个文件 | 读取 {len(reads)} 次 | Claude 输出 {len(all_text)} 字符")
    emit("detail", summary)
    
    # 验证
    for p, label in [("/tmp/hermes-feishu-e2e/shortener.py", "shortener.py"),
                      ("/tmp/hermes-feishu-e2e/test_shortener.py", "test_shortener.py")]:
        if os.path.exists(p):
            emit("verify", f"✅ {label} ({os.path.getsize(p)}b)")
        else:
            emit("verify", f"❌ {label} MISSING")
    
    if os.path.exists("/tmp/hermes-feishu-e2e/test_shortener.py"):
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "pytest", "test_shortener.py", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30, cwd="/tmp/hermes-feishu-e2e"
        )
        for line in r.stdout.split("\n"):
            if "passed" in line.lower() or "failed" in line.lower():
                emit("test", line.strip())
    
    # 写最终快照
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump({
            "ts": time.strftime("%H:%M:%S"),
            "elapsed": elapsed,
            "status": "done",
            "summary": summary,
            "files_written": [t["path"] for t in writes],
            "files_read": [t["path"] for t in reads],
            "claude_output_len": len(all_text),
        }, f, ensure_ascii=False)
    
    emit("end", f"🏁 全部完成")

except Exception as e:
    elapsed = time.time() - start
    emit("error", f"❌ 失败: {e}")
    import traceback
    traceback.print_exc()

heartbeat_stop.set()
child.close()
parent.close()
print(f"\nDone. {time.time()-start:.1f}s", flush=True)
