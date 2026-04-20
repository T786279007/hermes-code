"""Hermes Web API — FastAPI application for task management and monitoring.

Provides REST API + SSE for task submission, log viewing, command injection,
and system status monitoring.

Usage:
    python3 -m web_api [--port 8420]
    or
    uvicorn web_api:app --host 0.0.0.0 --port 8420
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import DB_PATH as _DB_PATH, REPO_PATH, WORKTREE_BASE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = str(_DB_PATH)
SESSION_TTL_HOURS = 24
SSE_HEARTBEAT_S = 15
POLL_INTERVAL_S = 1


def _load_api_key() -> tuple[str, bool]:
    """Load the configured API key or generate an ephemeral one."""
    configured = os.environ.get("HERMES_API_KEY")
    if configured:
        return configured, False
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "HERMES_API_KEY is not set; generated an ephemeral API key for this process only"
    )
    return generated, True


API_KEY, API_KEY_IS_EPHEMERAL = _load_api_key()

# ---------------------------------------------------------------------------
# Data models (Pydantic)
# ---------------------------------------------------------------------------

class TaskSubmitRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=5000, description="Task description")
    agent: str = Field("auto", description="Agent: claude-code, codex, or auto")
    priority: int = Field(50, ge=0, le=100, description="Task priority")


class CommandRequest(BaseModel):
    command: str = Field(..., description="Command type: cancel, inject, priority, retry, pause, resume")
    payload: Optional[dict] = Field(None, description="Command payload")


class AuthRequest(BaseModel):
    api_key: str = Field(..., description="API key for authentication")


# ---------------------------------------------------------------------------
# DB helpers (shared with task_registry)
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Get a read-only DB connection."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _get_tasks(status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """Read tasks from DB."""
    conn = _get_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?;",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?;",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_task(task_id: str) -> dict | None:
    """Get a single task by ID."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_task_logs(task_id: str, since_id: int | None = None, limit: int = 200) -> list[dict]:
    """Get execution logs for a task."""
    conn = _get_db()
    try:
        if since_id is not None:
            rows = conn.execute(
                "SELECT * FROM execution_logs WHERE task_id = ? AND id > ? ORDER BY id ASC LIMIT ?;",
                (task_id, since_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY id ASC LIMIT ?;",
                (task_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_stats() -> dict:
    """Get aggregate stats."""
    conn = _get_db()
    try:
        tasks = conn.execute("SELECT status FROM tasks;").fetchall()
        total = len(tasks)
        counts = {}
        for r in tasks:
            s = r["status"]
            counts[s] = counts.get(s, 0) + 1
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "retrying": counts.get("retrying", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
            "success_rate": f"{counts.get('done', 0) / total * 100:.0f}%" if total > 0 else "N/A",
        }
    finally:
        conn.close()


def _get_system_status() -> dict:
    """Get system status including running processes."""
    import subprocess
    status = {
        "uptime": _get_uptime(),
        "db_path": DB_PATH,
        "db_size_mb": round(os.path.getsize(DB_PATH) / 1024 / 1024, 2) if os.path.exists(DB_PATH) else 0,
        "worktree_base": WORKTREE_BASE,
        "repo_path": REPO_PATH,
    }
    # Check running Hermes processes
    try:
        r = subprocess.run(
            ["pgrep", "-f", "hermes"],
            capture_output=True, text=True, timeout=5
        )
        status["hermes_processes"] = len(r.stdout.strip().split('\n')) if r.stdout.strip() else 0
    except Exception:
        status["hermes_processes"] = -1
    return status


def _get_uptime() -> str:
    """Get system uptime string."""
    try:
        with open("/proc/uptime") as f:
            uptime_s = float(f.read().split()[0])
        days = int(uptime_s // 86400)
        hours = int((uptime_s % 86400) // 3600)
        return f"{days}d {hours}h"
    except Exception:
        return "unknown"


def _parse_timestamp(value: object) -> datetime | None:
    """Parse a DB timestamp into a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _serialize_task(task: dict) -> dict:
    """Augment a task row with UI-friendly derived fields."""
    result = dict(task)
    created_at = _parse_timestamp(result.get("created_at"))
    started_at = _parse_timestamp(result.get("started_at")) or created_at
    updated_at = _parse_timestamp(result.get("updated_at"))

    duration_s: int | None = None
    if result.get("status") == "running" and started_at is not None:
        duration_s = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
    elif updated_at is not None and started_at is not None:
        duration_s = max(0, int((updated_at - started_at).total_seconds()))

    result["duration_s"] = duration_s
    return result


def _run_task_and_notify(task_id: str) -> None:
    """Execute a submitted task in the background and send completion notification."""
    from executor import TaskExecutor
    from outbox import Outbox
    from reconciler import Reconciler
    from router import TaskRouter
    from task_registry import TaskRegistry

    registry = TaskRegistry(DB_PATH)
    outbox = Outbox(registry)
    executor = TaskExecutor(registry, TaskRouter(), outbox, Reconciler(registry))

    try:
        task = executor.execute(task_id)
        if task["status"] == "done":
            outbox.send_notification(
                task_id,
                "notify_done",
                {"message": f"Task {task_id} completed successfully"},
            )
        else:
            outbox.send_notification(
                task_id,
                "notify_failed",
                {"message": f"Task {task_id} failed: {task.get('stderr_tail', 'unknown')}"},
            )
    except Exception:
        logger.exception("Background execution failed for task %s", task_id)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}  # session_id -> {api_key, created_at, expires_at}


def _purge_expired_sessions() -> None:
    """Drop expired in-memory sessions."""
    now = datetime.now(timezone.utc)
    expired = [session_id for session_id, session in _sessions.items() if now > session["expires_at"]]
    for session_id in expired:
        del _sessions[session_id]


def _verify_session(session_id: str) -> bool:
    """Verify a session is valid."""
    _purge_expired_sessions()
    if session_id not in _sessions:
        return False
    session = _sessions[session_id]
    if datetime.now(timezone.utc) > session["expires_at"]:
        del _sessions[session_id]
        return False
    session["last_accessed_at"] = datetime.now(timezone.utc)
    return True


def _create_session(api_key: str) -> str:
    """Create a new session."""
    from datetime import timedelta

    _purge_expired_sessions()
    session_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    _sessions[session_id] = {
        "api_key": api_key,
        "created_at": now,
        "expires_at": now + timedelta(hours=SESSION_TTL_HOURS),
    }
    return session_id


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def _auth(
    authorization: Optional[str] = Header(None),
    x_session: Optional[str] = Header(None, alias="x-session"),
) -> None:
    """Verify authentication via session token or API key."""
    # Try session token
    if x_session and _verify_session(x_session):
        return
    # Try Bearer token / API key
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        if token in _sessions and _verify_session(token):
            return
        if token and secrets.compare_digest(token, API_KEY):
            return
    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

async def _sse_task_stream(task_id: str):
    """SSE stream for a single task's logs."""
    last_id = 0
    while True:
        logs = _get_task_logs(task_id, since_id=last_id, limit=100)
        task = _get_task(task_id)
        if logs:
            for log in logs:
                data = json.dumps({
                    "type": "log",
                    "id": log["id"],
                    "level": log["level"],
                    "source": log["source"],
                    "message": log["message"],
                    "created_at": log["created_at"],
                }, ensure_ascii=False, default=str)
                yield f"data: {data}\n\n"
                last_id = log["id"]
        if task:
            data = json.dumps({
                "type": "status",
                "task_id": task_id,
                "status": task["status"],
                "exit_code": task.get("exit_code"),
            }, ensure_ascii=False, default=str)
            yield f"data: {data}\n\n"
            if task["status"] in ("done", "failed"):
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
        # Heartbeat
        yield f": heartbeat\n\n"
        await asyncio.sleep(POLL_INTERVAL_S)


async def _sse_all_tasks_stream():
    """SSE stream for all tasks status changes."""
    last_counts: dict[str, int] = {}
    while True:
        stats = _get_stats()
        current = {k: v for k, v in stats.items() if isinstance(v, int)}
        if current != last_counts:
            data = json.dumps({"type": "stats", **stats}, ensure_ascii=False)
            yield f"data: {data}\n\n"
            last_counts = current
        yield f": heartbeat\n\n"
        await asyncio.sleep(3)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Hermes Web API starting (DB=%s)", DB_PATH)
    yield
    logger.info("Hermes Web API shutting down")


app = FastAPI(title="Hermes Web API", version="1.0.0", lifespan=_lifespan)

_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("HERMES_WEB_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if _CORS_ORIGINS:
    allow_credentials = "*" not in _CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Session"],
    )


# --- Auth ---

@app.post("/api/auth/login")
async def login(req: AuthRequest):
    """Authenticate with API key, return session token."""
    if req.api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    session_id = _create_session(req.api_key)
    return {"session_id": session_id, "expires_in_hours": SESSION_TTL_HOURS}


@app.get("/api/auth/verify")
async def verify(x_session: Optional[str] = Header(None, alias="x-session")):
    """Verify session is valid."""
    if x_session and _verify_session(x_session):
        return {"valid": True}
    raise HTTPException(status_code=401, detail="Invalid or expired session")


# --- Task management ---

@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(_auth),
):
    """List tasks with optional filtering."""
    tasks = _get_tasks(status=status, limit=limit, offset=offset)
    stats = _get_stats()
    return {"tasks": [_serialize_task(task) for task in tasks], "stats": stats}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, _: None = Depends(_auth)):
    """Get task details including logs."""
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    logs = _get_task_logs(task_id, limit=200)
    return {"task": _serialize_task(task), "logs": logs}


@app.post("/api/tasks")
async def submit_task(
    req: TaskSubmitRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(_auth),
):
    """Submit a new task."""
    # Import here to avoid circular deps
    from execution_log import ExecutionLog
    from task_registry import TaskRegistry
    from router import TaskRouter

    registry = TaskRegistry(DB_PATH)
    router = TaskRouter()

    override = req.agent if req.agent in ("claude-code", "codex") else None
    decision = router.route(req.description, override)
    slug = _slugify(req.description)[:30]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    task_id = f"feat-{slug}-{ts}"

    task = registry.create_task(
        task_id=task_id,
        description=req.description,
        agent=decision.agent,
        branch=f"hermes/{task_id}",
        model=decision.model,
    )
    ExecutionLog(registry).append(
        task_id,
        f"Task submitted via web: agent={decision.agent} model={decision.model}",
        source="system",
    )
    background_tasks.add_task(_run_task_and_notify, task_id)
    logger.info("Task submitted via web: %s (agent=%s)", task_id, decision.agent)
    return {"task": _serialize_task(task), "message": "Task submitted successfully"}


# --- Commands ---

@app.post("/api/tasks/{task_id}/commands")
async def send_command(task_id: str, req: CommandRequest, _: None = Depends(_auth)):
    """Send a command to a task."""
    from command_queue import CommandQueue
    from task_registry import TaskRegistry

    registry = TaskRegistry(DB_PATH)
    cmd_queue = CommandQueue(registry)

    try:
        cmd_id = cmd_queue.enqueue(task_id, req.command, req.payload)
        return {"command_id": cmd_id, "message": f"Command '{req.command}' enqueued"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tasks/{task_id}/commands")
async def list_commands(
    task_id: str,
    status: Optional[str] = None,
    _: None = Depends(_auth),
):
    """List commands for a task."""
    from command_queue import CommandQueue
    from task_registry import TaskRegistry

    registry = TaskRegistry(DB_PATH)
    cmd_queue = CommandQueue(registry)
    return {"commands": cmd_queue.list_commands(task_id, status=status)}


# --- System status ---

@app.get("/api/status")
async def system_status(_: None = Depends(_auth)):
    """Get system status."""
    return _get_system_status()


@app.get("/api/stats")
async def stats(_: None = Depends(_auth)):
    """Get aggregate statistics."""
    return _get_stats()


# --- SSE ---

@app.get("/sse/tasks/{task_id}")
async def sse_task(task_id: str, _: None = Depends(_auth)):
    """SSE stream for single task logs + status."""
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return StreamingResponse(
        _sse_task_stream(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sse/tasks")
async def sse_all_tasks(_: None = Depends(_auth)):
    """SSE stream for all tasks status."""
    return StreamingResponse(
        _sse_all_tasks_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Pages (embedded HTML) ---

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main dashboard page."""
    return _DASHBOARD_HTML


@app.get("/task/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: str):
    """Serve the task detail page."""
    return _TASK_DETAIL_HTML


@app.get("/submit", response_class=HTMLResponse)
async def submit_page():
    """Serve the task submission page."""
    return _SUBMIT_HTML


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    import re
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:30]


# ---------------------------------------------------------------------------
# Embedded HTML pages
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes Dashboard</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{padding:20px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
.header h1{font-size:20px;font-weight:600}
.header .tag{background:var(--green);color:#000;font-size:11px;font-weight:700;padding:2px 8px;border-radius:12px}
.header .ts{color:var(--text2);font-size:13px;margin-left:auto}
.header nav{margin-left:24px;display:flex;gap:16px}
.header nav a{color:var(--text2);text-decoration:none;font-size:14px;transition:color .2s}
.header nav a:hover{color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:16px 24px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.stat-card .label{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.stat-card .value{font-size:28px;font-weight:700;margin-top:4px}
.stat-card .value.green{color:var(--green)}.stat-card .value.red{color:var(--red)}
.stat-card .value.blue{color:var(--accent)}.stat-card .value.yellow{color:var(--yellow)}
.table-wrap{padding:0 24px 24px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:10px 12px;color:var(--text2);font-weight:500;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}
tr:hover{background:rgba(255,255,255,.02)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge.done{background:rgba(63,185,80,.15);color:var(--green)}.badge.failed{background:rgba(248,81,73,.15);color:var(--red)}
.badge.running{background:rgba(88,166,255,.15);color:var(--accent)}.badge.retrying{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge.pending{background:rgba(139,148,158,.15);color:var(--text2)}
.task-id{font-family:'SF Mono',Monaco,monospace;font-size:11px;color:var(--accent);cursor:pointer;text-decoration:underline}
.agent-tag{background:rgba(88,166,255,.1);color:var(--accent);padding:2px 6px;border-radius:4px;font-size:11px}
.result-preview{color:var(--text2);font-size:12px;max-width:400px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pulse{animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.filters{padding:12px 24px;display:flex;gap:8px}
.filters button{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px}
.filters button.active{background:var(--accent);color:#000;border-color:var(--accent)}
</style>
</head>
<body>
<div class="header">
<span style="font-size:24px">🦞</span>
<h1>Hermes Dashboard</h1>
<span class="tag">LIVE</span>
<nav>
<a href="/">Tasks</a>
<a href="/submit">+ New Task</a>
</nav>
<span class="ts" id="ts"></span>
</div>
<div class="stats" id="stats"></div>
<div class="filters" id="filters">
<button class="active" data-status="">All</button>
<button data-status="running">Running</button>
<button data-status="done">Done</button>
<button data-status="failed">Failed</button>
<button data-status="pending">Pending</button>
</div>
<div class="table-wrap">
<table><thead><tr>
<th>Task ID</th><th>Agent</th><th>Status</th><th>Duration</th><th>Code</th><th>Result Preview</th><th>Created</th>
</tr></thead><tbody id="tbody"></tbody></table>
</div>
<div id="empty" style="display:none;text-align:center;padding:60px;color:var(--text2)">No tasks found</div>
<script>
const API='/api/tasks';let currentFilter='';const SESSION_KEY='hermes_web_session';let sessionPromise=null;
const STATUS_CLASS={pending:'pending',running:'running',retrying:'retrying',done:'done',failed:'failed'};
function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML}
async function loginWithPrompt(){
const apiKey=window.prompt('Enter Hermes API key');
if(!apiKey){throw new Error('API key is required')}
const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:apiKey})});
const data=await r.json();
if(!r.ok){throw new Error(data.detail||'Login failed')}
localStorage.setItem(SESSION_KEY,data.session_id);
return data.session_id;
}
async function ensureSession(forcePrompt=false){
if(sessionPromise){return sessionPromise}
sessionPromise=(async()=>{
if(!forcePrompt){
const cached=localStorage.getItem(SESSION_KEY);
if(cached){
const verify=await fetch('/api/auth/verify',{headers:{'x-session':cached}});
if(verify.ok){return cached}
localStorage.removeItem(SESSION_KEY);
}
}
return loginWithPrompt();
})();
try{return await sessionPromise}finally{sessionPromise=null}
}
async function apiFetch(url,options={},retry=true){
const session=await ensureSession(false);
const headers=new Headers(options.headers||{});
headers.set('x-session',session);
const response=await fetch(url,{...options,headers});
if(response.status===401&&retry){
localStorage.removeItem(SESSION_KEY);
return apiFetch(url,options,false);
}
return response;
}
function statusBadge(s){const cls=STATUS_CLASS[s]||'pending';return '<span class="badge '+cls+'">'+esc(s||'unknown')+'</span>'}
function taskHref(id){return '/task/'+encodeURIComponent(id||'')}
function render(data){
const{tasks,stats}=data;
document.getElementById('ts').textContent=new Date().toLocaleString('zh-CN');
document.getElementById('stats').innerHTML=[
['TOTAL',stats.total,'blue'],['RUNNING',stats.running,'blue'],['DONE',stats.done,'green'],['FAILED',stats.failed,'red'],['SUCCESS',stats.success_rate,'green']
].map(([l,v,c])=>'<div class="stat-card"><div class="label">'+l+'</div><div class="value '+c+'">'+v+'</div></div>').join('');
const tbody=document.getElementById('tbody');const empty=document.getElementById('empty');
const filtered=currentFilter?tasks.filter(t=>t.status===currentFilter):tasks;
if(!filtered.length){tbody.innerHTML='';empty.style.display='block';return}
empty.style.display='none';
tbody.innerHTML=filtered.map(t=>'<tr><td><a class="task-id" href="'+taskHref(t.id)+'" title="'+esc(t.id)+'">'+esc(t.id.length>30?t.id.slice(0,30)+'…':t.id)+'</a></td><td><span class="agent-tag">'+esc(t.agent)+'</span></td><td>'+(t.status==='running'?'<span class="pulse">'+statusBadge(t.status)+'</span>':statusBadge(t.status))+'</td><td>'+(t.duration_s!=null?t.duration_s+'s':'—')+'</td><td>'+(t.exit_code!=null?t.exit_code:'—')+'</td><td class="result-preview" title="'+esc(t.result||'')+'">'+esc((t.result||'').slice(0,100))+'</td><td style="white-space:nowrap;color:var(--text2)">'+esc(t.created_at)+'</td></tr>').join('');
}
async function refresh(){try{const r=await apiFetch(API);if(!r.ok){throw new Error('Failed to load tasks')}render(await r.json())}catch(e){console.error(e)}}
refresh();setInterval(refresh,3000);
document.getElementById('filters').addEventListener('click',e=>{if(e.target.tagName==='BUTTON'){document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');currentFilter=e.target.dataset.status;refresh()}});
</script>
</body></html>"""


_TASK_DETAIL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes - Task Detail</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
.header a{color:var(--accent);text-decoration:none;font-size:14px}
.info{padding:16px 24px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.info-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px}
.info-card .label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.info-card .value{font-size:16px;font-weight:600;margin-top:4px;word-break:break-all}
.split{display:grid;grid-template-columns:1fr 360px;gap:0;height:calc(100vh - 200px)}
.logs{padding:16px 24px;overflow-y:auto;font-family:'SF Mono',Monaco,Consolas,monospace;font-size:13px;line-height:1.6}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge.done{background:rgba(63,185,80,.15);color:var(--green)}.badge.failed{background:rgba(248,81,73,.15);color:var(--red)}
.badge.running{background:rgba(88,166,255,.15);color:var(--accent)}.badge.retrying{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge.pending{background:rgba(139,148,158,.15);color:var(--text2)}
.log-line{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.03)}
.log-line .ts{color:var(--text2);margin-right:8px}
.log-line .level{margin-right:8px;font-weight:600}
.log-line .level.info{color:var(--accent)}.log-line .level.warn{color:var(--yellow)}
.log-line .level.error{color:var(--red)}.log-line .level.debug{color:var(--text2)}
.log-line .msg{color:var(--text)}
.cmd-panel{border-left:1px solid var(--border);padding:16px;display:flex;flex-direction:column}
.cmd-panel h3{font-size:14px;margin-bottom:12px;color:var(--text2)}
.cmd-input{display:flex;gap:8px;margin-bottom:12px}
.cmd-input textarea{flex:1;background:var(--surface);border:1px solid var(--border);color:var(--text);padding:10px;border-radius:6px;font-size:13px;resize:none;min-height:60px;font-family:inherit}
.cmd-input button{background:var(--accent);color:#000;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:600;font-size:13px}
.cmd-input button:hover{opacity:.85}
.cmd-input button:disabled{opacity:.5;cursor:not-allowed}
.cmd-history{flex:1;overflow-y:auto}
.cmd-item{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:8px;font-size:12px}
.cmd-item .cmd-type{color:var(--accent);font-weight:600}
.cmd-item .cmd-status{float:right;font-size:11px}
.cmd-item .cmd-status.pending{color:var(--yellow)}.cmd-item .cmd-status.delivered{color:var(--accent)}
.cmd-item .cmd-status.executed{color:var(--green)}.cmd-item .cmd-status.expired{color:var(--text2)}
.cmd-item .cmd-payload{color:var(--text2);margin-top:4px}
.cmd-item .cmd-result{color:var(--text);margin-top:4px;white-space:pre-wrap}
</style>
</head>
<body>
<div class="header">
<a href="/">← Back</a>
<h1 id="title">Task Detail</h1>
</div>
<div class="info" id="info"></div>
<div class="split">
<div class="logs" id="logs"><div style="color:var(--text2);text-align:center;padding:40px">Loading logs...</div></div>
<div class="cmd-panel">
<h3>Send Command</h3>
<div class="cmd-input">
<textarea id="cmdPayload" placeholder="Enter command payload (JSON or text)..."></textarea>
<button id="cmdSend" onclick="sendCmd()">Send</button>
</div>
<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">
<button onclick="quickCmd('cancel')" style="background:var(--red);color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">Cancel</button>
<button onclick="quickCmd('retry')" style="background:var(--yellow);color:#000;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">Retry</button>
</div>
<h3>Command History</h3>
<div class="cmd-history" id="cmdHistory"><div style="color:var(--text2);font-size:12px">No commands</div></div>
</div>
</div>
<script>
const TASK_ID=decodeURIComponent(location.pathname.slice('/task/'.length));const SESSION_KEY='hermes_web_session';let sessionPromise=null;
const STATUS_CLASS={pending:'pending',running:'running',retrying:'retrying',done:'done',failed:'failed'};
const esc=s=>{const d=document.createElement('div');d.textContent=s||'';return d.innerHTML};
async function loginWithPrompt(){
const apiKey=window.prompt('Enter Hermes API key');
if(!apiKey){throw new Error('API key is required')}
const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:apiKey})});
const data=await r.json();
if(!r.ok){throw new Error(data.detail||'Login failed')}
localStorage.setItem(SESSION_KEY,data.session_id);
return data.session_id;
}
async function ensureSession(forcePrompt=false){
if(sessionPromise){return sessionPromise}
sessionPromise=(async()=>{
if(!forcePrompt){
const cached=localStorage.getItem(SESSION_KEY);
if(cached){
const verify=await fetch('/api/auth/verify',{headers:{'x-session':cached}});
if(verify.ok){return cached}
localStorage.removeItem(SESSION_KEY);
}
}
return loginWithPrompt();
})();
try{return await sessionPromise}finally{sessionPromise=null}
}
async function apiFetch(url,options={},retry=true){
const session=await ensureSession(false);
const headers=new Headers(options.headers||{});
headers.set('x-session',session);
const response=await fetch(url,{...options,headers});
if(response.status===401&&retry){
localStorage.removeItem(SESSION_KEY);
return apiFetch(url,options,false);
}
return response;
}
function statusBadge(status){const cls=STATUS_CLASS[status]||'pending';return '<span class="badge '+cls+'">'+esc(status||'unknown')+'</span>'}
async function loadTask(){
const r=await apiFetch('/api/tasks/'+encodeURIComponent(TASK_ID));if(!r.ok){throw new Error('Failed to load task')}const data=await r.json();
const t=data.task;
document.getElementById('title').textContent='Task: '+(t.id||TASK_ID);
document.getElementById('info').innerHTML=[
['ID','<span style="font-family:monospace;font-size:12px">'+esc(t.id)+'</span>'],
['Agent','<span style="background:rgba(88,166,255,.1);color:var(--accent);padding:2px 6px;border-radius:4px">'+esc(t.agent)+'</span>'],
['Status',statusBadge(t.status)],
['Created',esc(t.created_at)],['Started',esc(t.started_at||'—')],
['Description',esc(t.description)],['Model',esc(t.model||'—')]
].map(([l,v])=>'<div class="info-card"><div class="label">'+l+'</div><div class="value">'+v+'</div></div>').join('');
}
async function loadLogs(){
const r=await apiFetch('/api/tasks/'+encodeURIComponent(TASK_ID));if(!r.ok){throw new Error('Failed to load logs')}const data=await r.json();
const logs=data.logs||[];
const el=document.getElementById('logs');
if(!logs.length){el.innerHTML='<div style="color:var(--text2);text-align:center;padding:40px">No logs yet</div>';return}
el.innerHTML=logs.map(l=>'<div class="log-line"><span class="ts">'+esc(l.created_at)+'</span><span class="level '+esc(l.level)+'">'+esc(l.level.toUpperCase())+'</span>'+(l.source?'<span style="color:var(--text2);margin-right:8px">['+esc(l.source)+']</span>':'')+'<span class="msg">'+esc(l.message)+'</span></div>').join('');
el.scrollTop=el.scrollHeight;
}
async function loadCmds(){
const r=await apiFetch('/api/tasks/'+encodeURIComponent(TASK_ID)+'/commands');if(!r.ok){throw new Error('Failed to load commands')}const data=await r.json();
const cmds=data.commands||[];
const el=document.getElementById('cmdHistory');
if(!cmds.length){el.innerHTML='<div style="color:var(--text2);font-size:12px">No commands</div>';return}
el.innerHTML=cmds.map(c=>'<div class="cmd-item"><span class="cmd-type">'+esc(c.command)+'</span><span class="cmd-status '+esc(STATUS_CLASS[c.status]||'pending')+'">'+esc(c.status)+'</span>'+(c.payload?'<div class="cmd-payload">'+esc(c.payload)+'</div>':'')+(c.result?'<div class="cmd-result">'+esc(c.result)+'</div>':'')+'</div>').join('');
}
async function sendCmd(){
const payload=document.getElementById('cmdPayload').value.trim();
if(!payload)return;
let parsed;
try{parsed=JSON.parse(payload)}catch{parsed={text:payload}}
const r=await apiFetch('/api/tasks/'+encodeURIComponent(TASK_ID)+'/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:'inject',payload:parsed})});
if(r.ok){document.getElementById('cmdPayload').value='';loadCmds()}
}
async function quickCmd(cmd){
const r=await apiFetch('/api/tasks/'+encodeURIComponent(TASK_ID)+'/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
if(r.ok){loadCmds()}
}
loadTask();loadLogs();loadCmds();
setInterval(()=>{loadLogs();loadCmds()},3000);
setInterval(loadTask,5000);
</script>
</body></html>"""


_SUBMIT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes - Submit Task</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;align-items:flex-start;padding:60px 20px}
.form{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:32px;width:100%;max-width:600px}
.form h1{font-size:20px;margin-bottom:24px}
.form label{display:block;font-size:13px;color:var(--text2);margin-bottom:6px;margin-top:16px}
.form textarea{width:100%;min-height:120px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:12px;border-radius:8px;font-size:14px;resize:vertical;font-family:inherit}
.form select{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px;border-radius:8px;font-size:14px}
.form .btn{margin-top:24px;background:var(--green);color:#000;border:none;padding:12px 24px;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;width:100%}
.form .btn:hover{opacity:.85}
.form .btn:disabled{opacity:.5;cursor:not-allowed}
.form .result{margin-top:16px;padding:12px;border-radius:8px;font-size:13px;display:none}
.form .result.success{background:rgba(63,185,80,.1);border:1px solid var(--green);color:var(--green)}
.form .result.error{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red)}
.back{margin-bottom:20px}
.back a{color:var(--accent);text-decoration:none;font-size:14px}
</style>
</head>
<body>
<div class="form">
<div class="back"><a href="/">← Back to Dashboard</a></div>
<h1>🦞 Submit New Task</h1>
<label>Task Description</label>
<textarea id="desc" placeholder="Describe what you want the agent to do..."></textarea>
<label>Agent</label>
<select id="agent">
<option value="auto">Auto (Router decides)</option>
<option value="claude-code">Claude Code</option>
<option value="codex">Codex</option>
</select>
<button class="btn" id="submitBtn" onclick="submit()">Submit Task</button>
<div class="result" id="result"></div>
</div>
<script>
const SESSION_KEY='hermes_web_session';let sessionPromise=null;
async function loginWithPrompt(){
const apiKey=window.prompt('Enter Hermes API key');
if(!apiKey){throw new Error('API key is required')}
const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:apiKey})});
const data=await r.json();
if(!r.ok){throw new Error(data.detail||'Login failed')}
localStorage.setItem(SESSION_KEY,data.session_id);
return data.session_id;
}
async function ensureSession(forcePrompt=false){
if(sessionPromise){return sessionPromise}
sessionPromise=(async()=>{
if(!forcePrompt){
const cached=localStorage.getItem(SESSION_KEY);
if(cached){
const verify=await fetch('/api/auth/verify',{headers:{'x-session':cached}});
if(verify.ok){return cached}
localStorage.removeItem(SESSION_KEY);
}
}
return loginWithPrompt();
})();
try{return await sessionPromise}finally{sessionPromise=null}
}
async function apiFetch(url,options={},retry=true){
const session=await ensureSession(false);
const headers=new Headers(options.headers||{});
headers.set('x-session',session);
const response=await fetch(url,{...options,headers});
if(response.status===401&&retry){
localStorage.removeItem(SESSION_KEY);
return apiFetch(url,options,false);
}
return response;
}
async function submit(){
const desc=document.getElementById('desc').value.trim();
const agent=document.getElementById('agent').value;
if(!desc){showResult('Please enter a task description','error');return}
const btn=document.getElementById('submitBtn');btn.disabled=true;btn.textContent='Submitting...';
try{
const r=await apiFetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description:desc,agent:agent})});
const data=await r.json();
if(r.ok){showResult('Task submitted: '+data.task.id,'success');setTimeout(()=>location.href='/task/'+data.task.id,1500)}
else{showResult(data.detail||'Submit failed','error')}
}catch(e){showResult('Network error: '+e.message,'error')}
btn.disabled=false;btn.textContent='Submit Task';
}
function showResult(msg,type){
const el=document.getElementById('result');el.textContent=msg;el.className='result '+type;el.style.display='block';
}
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Hermes Web API")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        import sys
        sys.exit(1)

    print(f"🦞 Hermes Web API running at http://{args.host}:{args.port}")
    print(f"   DB: {DB_PATH}")
    if API_KEY_IS_EPHEMERAL:
        print(f"   API Key (ephemeral): {API_KEY}")
    else:
        print(f"   API Key: {API_KEY[:8]}...")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
