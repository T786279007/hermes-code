#!/usr/bin/env python3
"""
feishu-progress-relay.py — 轮询 .progress 文件，有新事件就打印飞书消息内容
供 OpenClaw 的 exec + message 工具配合使用。

用法:
  python3 feishu-progress-relay.py --progress /tmp/project/.progress --interval 3

输出: 每行一个 JSON，OpenClaw 读到就推飞书
  {"action":"send","text":"📝 写入 shortener.py (45 行)"}
  {"action":"done","text":"✅ 完成！13s · 3个文件 · 7轮"}
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--progress", required=True, help="Progress file path")
    p.add_argument("--interval", type=int, default=3, help="Poll interval (seconds)")
    p.add_argument("--heartbeat", type=int, default=15, help="Heartbeat interval (seconds)")
    return p.parse_args()


def run():
    args = parse_args()
    last_pos = 0
    last_event_time = time.time()
    event_count = 0
    last_tool_summary = None

    # Wait for progress file to appear
    while not os.path.exists(args.progress):
        time.sleep(0.5)

    print(json.dumps({"action": "system", "text": "🚀 Relay started, watching progress..."}), flush=True)

    while True:
        try:
            size = os.path.getsize(args.progress)
        except OSError:
            time.sleep(args.interval)
            continue

        if size > last_pos:
            with open(args.progress, "r") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                last_pos = size

            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                event_count += 1
                last_event_time = time.time()

                if etype == "system":
                    print(json.dumps({"action": "send", "text": event.get("text", "")}), flush=True)

                elif etype == "tool":
                    tool = event.get("tool", "")
                    summary = event.get("summary", "")
                    last_tool_summary = summary
                    print(json.dumps({"action": "send", "text": summary}), flush=True)

                elif etype == "text":
                    text = event.get("text", "")
                    # Only forward substantial text (not every token)
                    if len(text) > 30:
                        print(json.dumps({"action": "send", "text": f"💬 {text[:200]}"}), flush=True)

                elif etype == "result":
                    status = event.get("status", "unknown")
                    duration_ms = event.get("duration_ms", 0)
                    turns = event.get("turns", 0)
                    tool_count = event.get("tool_count", 0)
                    files = event.get("files_written", [])
                    summary = event.get("summary", "")

                    if status == "success":
                        duration_s = duration_ms / 1000
                        parts = [f"✅ 完成！{duration_s:.0f}s"]
                        if turns:
                            parts.append(f"{turns} 轮")
                        if tool_count:
                            parts.append(f"{tool_count} 次工具调用")
                        if files:
                            names = [os.path.basename(f) for f in files]
                            parts.append(f"{len(files)} 个文件 ({', '.join(names[:5])})")

                        msg = " · ".join(parts)
                        if summary:
                            msg += f"\n\n{summary[:300]}"

                        print(json.dumps({"action": "done", "text": msg}), flush=True)
                    else:
                        msg = f"❌ 失败: {summary or status}"
                        print(json.dumps({"action": "done", "text": msg}), flush=True)

                    return  # Done!

        elif time.time() - last_event_time > args.heartbeat:
            # Heartbeat
            elapsed = time.time() - last_event_time
            last_tool = last_tool_summary or "启动中"
            print(json.dumps({
                "action": "heartbeat",
                "text": f"⏳ [{elapsed:.0f}s] 工作中… 上次: {last_tool}"
            }), flush=True)
            last_event_time = time.time()  # Reset to avoid spam

        time.sleep(args.interval)


if __name__ == "__main__":
    run()
