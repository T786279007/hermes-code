#!/usr/bin/env python3
"""Hermes Dashboard — Real-time task monitoring UI.

Serves a single-page dashboard that polls the Hermes SQLite DB every 3s.

Usage:
    python3 /home/txs/hermes/dashboard.py [--port 8420]
"""

import json
import sqlite3
import os
import sys
import http.server
import urllib.parse
import argparse
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH as _DB_PATH
DB_PATH = str(_DB_PATH)
WORKTREE_BASE = "/home/txs/hermes-agent/worktrees"
HERMES_ROOT = "/home/txs/hermes"


def get_tasks():
    """Read all tasks from DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    tasks = [dict(r) for r in rows]
    for t in tasks:
        # Compute duration
        if t.get("started_at") and t.get("updated_at"):
            try:
                started = datetime.strptime(t["started_at"], "%Y-%m-%d %H:%M:%S")
                updated = datetime.strptime(t["updated_at"], "%Y-%m-%d %H:%M:%S")
                t["duration_s"] = int((updated - started).total_seconds())
            except (ValueError, TypeError):
                t["duration_s"] = None
        else:
            t["duration_s"] = None
        # Worktree exists?
        wt = Path(WORKTREE_BASE) / t["id"]
        t["worktree_exists"] = wt.is_dir()
        # Git commit?
        if wt.is_dir():
            import subprocess
            try:
                r = subprocess.run(
                    ["git", "log", "--oneline", "-1"],
                    cwd=str(wt), capture_output=True, text=True, timeout=5
                )
                t["last_commit"] = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                t["last_commit"] = ""
        else:
            t["last_commit"] = ""
        # Truncate result
        if t.get("result") and len(t["result"]) > 200:
            t["result_short"] = t["result"][:200] + "..."
        else:
            t["result_short"] = t.get("result") or ""
    return tasks


def get_stats(tasks):
    """Compute aggregate stats."""
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    running = sum(1 for t in tasks if t["status"] == "running")
    retrying = sum(1 for t in tasks if t["status"] == "retrying")
    return {
        "total": total,
        "done": done,
        "failed": failed,
        "running": running,
        "retrying": retrying,
        "success_rate": f"{done/total*100:.0f}%" if total > 0 else "N/A",
    }


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922; --orange: #db6d28;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { padding: 20px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 20px; font-weight: 600; }
  .header .tag { background: var(--green); color: #000; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 12px; }
  .header .ts { color: var(--text2); font-size: 13px; margin-left: auto; }
  .stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; padding: 16px 24px; }
  .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .stat-card .label { font-size: 12px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .stat-card .value.green { color: var(--green); }
  .stat-card .value.red { color: var(--red); }
  .stat-card .value.blue { color: var(--accent); }
  .stat-card .value.yellow { color: var(--yellow); }
  .stat-card .value.orange { color: var(--orange); }
  .table-wrap { padding: 0 24px 24px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; color: var(--text2); font-weight: 500; border-bottom: 1px solid var(--border); white-space: nowrap; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:hover { background: rgba(255,255,255,0.02); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge.done { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge.failed { background: rgba(248,81,73,0.15); color: var(--red); }
  .badge.running { background: rgba(88,166,255,0.15); color: var(--accent); }
  .badge.retrying { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .badge.pending { background: rgba(139,148,158,0.15); color: var(--text2); }
  .task-id { font-family: 'SF Mono', Monaco, monospace; font-size: 11px; color: var(--accent); }
  .agent-tag { background: rgba(88,166,255,0.1); color: var(--accent); padding: 2px 6px; border-radius: 4px; font-size: 11px; }
  .result-preview { color: var(--text2); font-size: 12px; max-width: 400px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .commit { font-family: 'SF Mono', Monaco, monospace; font-size: 11px; color: var(--green); }
  .wt-icon { font-size: 14px; }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
  .empty { text-align: center; padding: 60px; color: var(--text2); }
</style>
</head>
<body>
<div class="header">
  <span style="font-size:24px">🦞</span>
  <h1>Hermes Dashboard</h1>
  <span class="tag">LIVE</span>
  <span class="ts" id="ts"></span>
</div>
<div class="stats" id="stats"></div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>任务 ID</th>
        <th>Agent</th>
        <th>状态</th>
        <th>耗时</th>
        <th>退出码</th>
        <th>Worktree</th>
        <th>Commit</th>
        <th>结果预览</th>
        <th>创建时间</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<div class="empty" id="empty" style="display:none">暂无任务记录</div>

<script>
const API = '/api/tasks';
const REFRESH_MS = 3000;

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function statusBadge(s) {
  const m = { done:'done', failed:'failed', running:'running', retrying:'retrying', pending:'pending' };
  return `<span class="badge ${m[s]||'pending'}">${s||'unknown'}</span>`;
}

function render(data) {
  const { tasks, stats } = data;
  document.getElementById('ts').textContent = new Date().toLocaleString('zh-CN');

  // Stats
  document.getElementById('stats').innerHTML = [
    ['TOTAL', stats.total, 'blue'],
    ['DONE', stats.done, 'green'],
    ['FAILED', stats.failed, 'red'],
    ['RUNNING', stats.running, 'blue'],
    ['SUCCESS', stats.success_rate, 'green'],
  ].map(([l,v,c]) => `<div class="stat-card"><div class="label">${l}</div><div class="value ${c}">${v}</div></div>`).join('');

  // Table
  const tbody = document.getElementById('tbody');
  const empty = document.getElementById('empty');
  if (!tasks.length) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  tbody.innerHTML = tasks.map(t => `<tr>
    <td class="task-id" title="${esc(t.id)}">${esc(t.id.length>30?t.id.slice(0,30)+'…':t.id)}</td>
    <td><span class="agent-tag">${esc(t.agent)}</span></td>
    <td>${t.status==='running'?'<span class="pulse">'+statusBadge(t.status)+'</span>':statusBadge(t.status)}</td>
    <td>${t.duration_s!=null?t.duration_s+'s':'—'}</td>
    <td>${t.exit_code!=null?t.exit_code:'—'}</td>
    <td class="wt-icon">${t.worktree_exists?'✅':'❌'}</td>
    <td class="commit">${esc(t.last_commit)}</td>
    <td class="result-preview" title="${esc(t.result_short)}">${esc(t.result_short)}</td>
    <td style="white-space:nowrap;color:var(--text2)">${esc(t.created_at)}</td>
  </tr>`).join('');
}

async function refresh() {
  try {
    const r = await fetch(API);
    const data = await r.json();
    render(data);
  } catch(e) { console.error('Fetch failed', e); }
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif parsed.path == "/api/tasks":
            tasks = get_tasks()
            stats = get_stats(tasks)
            payload = json.dumps({"tasks": tasks, "stats": stats}, ensure_ascii=False, default=str)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logs


def main():
    parser = argparse.ArgumentParser(description="Hermes Dashboard")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    server = http.server.HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"🦞 Hermes Dashboard running at http://0.0.0.0:{args.port}")
    print(f"   DB: {DB_PATH}")
    print(f"   Refresh: every 3s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
