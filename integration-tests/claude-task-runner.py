#!/usr/bin/env python3
"""
claude-task-runner.py — 启动 Claude Code 并实时输出进度到 .progress 文件
供 OpenClaw 轮询推送到飞书。

用法:
  python3 claude-task-runner.py --task "Build a URL shortener" --cwd /tmp/project --progress /tmp/project/.progress

输出格式: 每行一个 JSON 事件
  {"ts":"HH:MM:SS","type":"tool","tool":"Write","target":"/tmp/x.py","summary":"写入文件"}
  {"ts":"HH:MM:SS","type":"text","text":"Claude says something..."}
  {"ts":"HH:MM:SS","type":"result","status":"success","duration_ms":13000,"turns":7,"summary":"..."}
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime


def parse_args():
    p = argparse.ArgumentParser(description="Run Claude Code with progress output")
    p.add_argument("--task", required=True, help="Task description for Claude")
    p.add_argument("--cwd", required=True, help="Working directory")
    p.add_argument("--progress", required=True, help="Progress file path")
    p.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    p.add_argument("--model", default=None, help="Override model (e.g. glm-5-turbo)")
    return p.parse_args()


def emit(progress_file: str, event: dict):
    """Write a progress event to the progress file."""
    event["ts"] = datetime.now().strftime("%H:%M:%S")
    with open(progress_file, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), flush=True)


def summarize_tool(name: str, inp: dict) -> str:
    """Create a human-readable summary for a tool call."""
    try:
        if name == "Write":
            path = inp.get("file_path", "?")
            content = inp.get("content", "")
            lines = content.count("\n") + 1
            return f"📝 写入 {os.path.basename(path)} ({lines} 行)"
        elif name == "Read":
            path = inp.get("file_path", "?")
            return f"📖 读取 {os.path.basename(path)}"
        elif name == "Edit":
            path = inp.get("file_path", "?")
            return f"✏️ 编辑 {os.path.basename(path)}"
        elif name == "Bash":
            cmd = inp.get("command", "")[:80]
            desc = inp.get("description", "")
            return f"🖥️ {desc or cmd}"
        elif name == "Glob":
            pattern = inp.get("pattern", "?")[:50]
            return f"🔍 搜索 {pattern}"
        elif name == "Grep":
            pattern = inp.get("pattern", "?")[:50]
            return f"🔍 搜索 {pattern}"
        elif name == "NotebookEdit":
            return f"📓 编辑 Notebook"
        elif name == "WebFetch":
            url = inp.get("url", "?")[:60]
            return f"🌐 抓取 {url}"
        elif name == "WebSearch":
            query = inp.get("query", "?")[:60]
            return f"🌐 搜索 {query}"
        else:
            return f"🔧 {name}"
    except Exception:
        return f"🔧 {name}"


def extract_target(name: str, inp: dict) -> str:
    """Extract the primary target (file path, command, etc.)"""
    try:
        if name == "Write":
            return inp.get("file_path", "")
        elif name == "Read":
            return inp.get("file_path", "")
        elif name == "Edit":
            return inp.get("file_path", "")
        elif name == "Bash":
            return inp.get("command", "")[:120]
        elif name in ("Glob", "Grep"):
            return inp.get("pattern", "")
        elif name in ("WebFetch", "WebSearch"):
            return inp.get("url", "") or inp.get("query", "")
    except Exception:
        pass
    return ""


def run():
    args = parse_args()
    os.makedirs(args.cwd, exist_ok=True)

    # Clear progress file
    with open(args.progress, "w") as f:
        f.write("")

    emit(args.progress, {"type": "system", "text": f"🚀 Claude Code 开始执行任务\n📂 工作目录: {args.cwd}"})

    cmd = [
        "claude",
        "--permission-mode", "bypassPermissions",
        "--verbose",
        "--print",
        "--output-format", "stream-json",
    ]

    # Pass model override via Claude Code settings
    if args.model:
        cmd.extend(["--model", args.model])

    cmd.append(args.task)

    start = time.time()
    tool_count = 0
    last_tool_time = start
    files_written = []
    files_read = []
    bash_commands = []

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=args.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")

            if etype == "assistant":
                msg = event.get("message", {})
                content = msg.get("content", [])
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")

                    if bt == "tool_use":
                        tool_name = block.get("name", "?")
                        tool_input = block.get("input", {})
                        tool_count += 1
                        last_tool_time = time.time()

                        summary = summarize_tool(tool_name, tool_input)
                        target = extract_target(tool_name, tool_input)

                        emit_event = {
                            "type": "tool",
                            "tool": tool_name,
                            "target": target,
                            "summary": summary,
                            "tool_num": tool_count,
                        }

                        if tool_name == "Write" and target:
                            files_written.append(target)
                            emit_event["files_written"] = list(files_written)
                        elif tool_name == "Read" and target:
                            files_read.append(target)
                            emit_event["files_read"] = list(files_read)
                        elif tool_name == "Bash":
                            bash_commands.append(target[:80])
                            emit_event["bash_count"] = len(bash_commands)

                        emit(args.progress, emit_event)

                    elif bt == "text":
                        text = block.get("text", "").strip()
                        if text and len(text) > 10:
                            emit(args.progress, {
                                "type": "text",
                                "text": text[:300],
                            })

            elif etype == "result":
                duration = time.time() - start
                status = event.get("subtype", "unknown")
                is_error = event.get("is_error", False)
                result_text = event.get("result", "")
                duration_ms = event.get("duration_ms", int(duration * 1000))
                turns = event.get("num_turns", 0)

                emit(args.progress, {
                    "type": "result",
                    "status": "error" if is_error else status,
                    "duration_ms": duration_ms,
                    "turns": turns,
                    "summary": result_text[:500] if result_text else "",
                    "files_written": files_written,
                    "files_read": files_read,
                    "tool_count": tool_count,
                })

        proc.wait(timeout=30)

    except subprocess.TimeoutExpired:
        emit(args.progress, {
            "type": "result",
            "status": "timeout",
            "duration_ms": int((time.time() - start) * 1000),
            "summary": f"⏰ 任务超时 ({args.timeout}s)",
            "files_written": files_written,
            "tool_count": tool_count,
        })
    except Exception as e:
        emit(args.progress, {
            "type": "result",
            "status": "error",
            "duration_ms": int((time.time() - start) * 1000),
            "summary": f"❌ 执行错误: {e}",
            "tool_count": tool_count,
        })


if __name__ == "__main__":
    run()
