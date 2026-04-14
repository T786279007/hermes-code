# Hermes Agent Cluster v2 — 编码规格书

> 基于 v1.1 方案 + 双 Agent 审查修复，精简为 10 个核心模块

## 项目结构

```
~/hermes/
├── __init__.py
├── config.py              # 全局配置常量
├── task_registry.py       # SQLite WAL 任务注册表
├── sandbox.py             # 隔离 HOME + 最小 env
├── claude_runner.py       # Claude Code 进程管理
├── codex_runner.py        # Codex 进程管理
├── router.py              # 评分制任务路由
├── retry.py               # 错误分类 + backoff + circuit breaker
├── reconciler.py          # 崩溃恢复
├── outbox.py              # 幂等通知
├── executor.py            # 核心编排循环（最重要）
├── check_agents.py        # 简化版健康检查
├── tests/
│   └── test_full_lifecycle.py  # 端到端集成测试
├── bin/
│   └── hermes.py          # CLI 入口
└── HERMES_SPEC.md         # 本文件
```

## 环境

- OS: Ubuntu 24.04, NUC 自建服务器
- Python: 3.12+
- Claude Code CLI: v2.1.x, 命令 `claude`
- Codex CLI: v0.120.x, 命令 `codex`
- 代理: `http://127.0.0.1:7897`
- GitHub token: 从环境变量 `$HERMES_GITHUB_TOKEN` 读取
- Claude Code 参数: `--permission-mode bypassPermissions --print`
- Codex 参数: `--dangerously-bypass-approvals-and-sandbox --quiet`
- Claude Code 超时: 300s, Codex 超时: 21600s（6h）

## 审查 Blocker 修复清单（必须全部落实）

| ID | 问题 | 修复 |
|----|------|------|
| B1 | 硬编码 GitHub token | → `os.environ["HERMES_GITHUB_TOKEN"]` |
| B2 | reconciler 用 updated_at 算 elapsed | → 新增 `started_at` 字段，transition 到 running 时设置 |
| B3 | _parse_progress 不存在 | → **已砍掉**成本监控功能 |
| B4 | Runner 定义两次 | → 只定义一次，合并所有功能 |
| B5 | BEGIN EXCLUSIVE + autocommit 冲突 | → `isolation_level=None` |
| B6 | INSERT OR REPLACE 破坏 attempts | → `ON CONFLICT(task_id, action) DO UPDATE` |
| W1 | worktree 不写入注册表 | → executor 在启动 runner 前写入 |
| W3 | 缺 started_at schema | → 加入 CREATE TABLE |
| W4 | keyword 包含匹配 | → `re.search(rf'\b{re.escape(keyword)}\b', ...)` |
| W5 | 组件未串联 | → executor.py 串联所有组件 |
| W7 | 缺 base schema | → 完整 CREATE TABLE 如下 |
| S1 | bare except: | → `except Exception:` |
| S5 | print() everywhere | → 用 `logging` 模块 |

## 数据库 Schema

```sql
-- tasks 表：核心任务注册表
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,                    -- 格式: feat-xxx-YYYYMMDD-HHMMSS
    description TEXT NOT NULL,              -- 原始需求描述
    agent TEXT NOT NULL,                    -- 'claude-code' | 'codex'
    status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|failed|retrying
    branch TEXT,                            -- git worktree 分支名
    worktree TEXT,                          -- worktree 绝对路径
    prompt TEXT,                            -- 送给 Agent 的完整 prompt
    done_checks_json TEXT,                  -- JSON 化的完成状态（PR/CI/审查等）
    result TEXT,                            -- Agent 输出摘要（最后 2KB）
    model TEXT,                             -- 使用的模型名
    exit_code INTEGER,
    stderr_tail TEXT,                       -- 最后 1KB stderr
    failure_class TEXT,                     -- 'retryable' | 'permanent' | 'unknown'
    attempt INTEGER DEFAULT 0,              -- 当前重试次数
    max_attempts INTEGER DEFAULT 3,         -- 最大重试次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,                   -- 进入 running 的时间（reconciler 用）
    pid INTEGER                             -- runner 进程 PID
);

-- outbox 表：幂等副作用
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,                   -- 'notify_done' | 'notify_failed'
    external_id TEXT,                       -- 飞书 message_id
    payload TEXT,                           -- JSON
    status TEXT DEFAULT 'pending',          -- 'pending' | 'sent' | 'failed'
    attempts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    last_error TEXT,
    UNIQUE(task_id, action)
);
```

## 模块接口规格

### 1. config.py — 全局配置

```python
HERMES_HOME = Path("/home/txs/hermes-agent")
WORKTREE_BASE = HERMES_HOME / "worktrees"
DB_PATH = HERMES_HOME / "tasks.db"
RUNNER_HOME = HERMES_HOME / "runner_home"
LOG_DIR = Path("/home/txs/hermes/logs")
PROXY = "http://127.0.0.1:7897"

CLAUDE_TIMEOUT = 300
CODEX_TIMEOUT = 21600  # Codex 任务最长 6 小时
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10.0
RETRY_MAX_DELAY = 300.0
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_RESET = 300
RECONCILER_TIMEOUT = 600  # fallback 超时，用于未识别 agent 或未知最长运行期

REPO_PATH = "/tmp/hermes-repo"  # 目标 git repo（后续可配）
```

### 2. task_registry.py — SQLite WAL 任务注册表

```python
class TaskRegistry:
    def __init__(self, db_path: str):
        # 创建目录、初始化 DB（WAL 模式）
        # isolation_level=None（修 B5）
    
    def _connect(self) -> sqlite3.Connection:
        # WAL + busy_timeout=5000 + row_factory=sqlite3.Row
    
    @contextmanager
    def _transaction(self):
        # threading.Lock + BEGIN EXCLUSIVE（修 B5: isolation_level=None）
        # except Exception:（修 S1）
    
    def create_task(self, task_id: str, description: str, agent: str, **kwargs) -> dict:
        # INSERT INTO tasks ... RETURNING *
    
    def get_task(self, task_id: str) -> dict | None:
        # SELECT * FROM tasks WHERE id=?
    
    def update_task(self, task_id: str, **fields) -> bool:
        # UPDATE tasks SET ... WHERE id=?
    
    def transition_status(self, task_id: str, new_status: str,
                          expected_current: str = None) -> bool:
        # 乐观锁（修 B2: transition 到 running 时同时设置 started_at）
    
    def list_tasks(self, status: str = None, limit: int = 100) -> list[dict]:
        # SELECT * WHERE status=? ORDER BY created_at DESC
    
    def health_check(self) -> dict:
        # PRAGMA integrity_check + wal_checkpoint（修 S8）
```

### 3. sandbox.py — 隔离运行环境

```python
def prepare_runner_env(agent: str, task_id: str) -> dict:
    """
    创建隔离 HOME 目录 + 最小 .gitconfig + git-askpass.sh
    返回 env dict（修 B1: token 从 env var 读取）
    """

def cleanup_runner_env(agent: str, task_id: str):
    """任务完成后删除隔离目录"""
```

### 4. claude_runner.py — Claude Code 进程管理

```python
class ClaudeRunner:
    TIMEOUT = 300  # 从 config 读取
    
    def run(self, task_id: str, prompt: str, worktree: str,
            model: str = "claude-sonnet-4-6") -> dict:
        """
        启动 Claude Code 子进程:
        - preexec_fn=os.setsid（进程组 kill）
        - threading.Timer 超时 kill
        - 返回 {exit_code, stdout, stderr, timed_out}
        
        命令: claude --permission-mode bypassPermissions --print "<prompt>"
        cwd=worktree, env=prepare_runner_env("claude-code", task_id)
        """
    
    def _kill(self, proc):
        """os.killpg 整棵进程树"""
```

### 5. codex_runner.py — Codex 进程管理

```python
class CodexRunner:
    TIMEOUT = 180
    
    def run(self, task_id: str, prompt: str, worktree: str,
            model: str = "gpt-5.4", reasoning: str = "high") -> dict:
        """
        命令: codex --dangerously-bypass-approvals-and-sandbox --quiet "<prompt>"
        cwd=worktree, env=prepare_runner_env("codex", task_id)
        """
    
    def _kill(self, proc):
        """同上"""
```

### 6. router.py — 评分制任务路由

```python
@dataclass
class RoutingDecision:
    agent: str           # 'claude-code' | 'codex'
    model: str           # 模型名
    timeout: int         # 超时秒数
    confidence: float    # 0.0-1.0
    reason: str

class TaskRouter:
    def route(self, description: str, override: str = None) -> RoutingDecision:
        """
        评分路由（修 W4: word boundary matching）
        override='claude-code'|'codex' 可跳过评分
        
        Claude Code: 实现/开发/创建/编写/重构/前端/UI/API/添加/新增/修改/优化/implement/create/build/refactor/add/frontend/feature
        Codex: 审查/review/代码审查/修复/fix/bug/检查/check/lint
        """
```

### 7. retry.py — 重试策略

```python
class FailureClass(Enum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"

def classify_failure(exit_code: int, stderr: str) -> FailureClass:
    """根据 exit_code + stderr 关键词分类"""

def compute_delay(retry_count: int) -> float:
    """Exponential backoff + 10% jitter"""

class CircuitBreaker:
    def is_open(self, agent: str) -> bool:
    def record_success(self, agent: str):
    def record_failure(self, agent: str):
```

### 8. outbox.py — 幂等通知

```python
class Outbox:
    def __init__(self, registry: TaskRegistry):
        # 共用 registry 的 DB 连接
    
    def send_notification(self, task_id: str, action: str, payload: dict) -> str | None:
        """
        幂等发送（修 B6: ON CONFLICT DO UPDATE）
        同一 task_id + action 只发送一次
        返回 external_id（message_id）
        """
    
    def _send_feishu(self, payload: dict) -> str:
        """
        通过 openclaw CLI 发送飞书通知
        命令: openclaw message send --channel feishu --message "..."
        或者直接调用 Python subprocess
        """
```

### 9. reconciler.py — 崩溃恢复

```python
class Reconciler:
    def __init__(self, registry: TaskRegistry):
    
    def reconcile(self) -> dict:
        """
        启动时执行：
        1. 扫描所有 running 任务
        2. 检查 PID 是否存活（os.kill(pid, 0)）
        3. 检查 worktree 目录是否存在
        4. 检查 branch 是否存在（修 W8: cwd=repo_path）
        5. 超时检查（用 started_at，针对 Claude/Codex 使用各自超时，缺 agent 时退到 RECONCILER_TIMEOUT）
        6. 清理孤儿 worktree
        
        返回 {fixed: [...], orphaned: [...]}
        """
```

### 10. executor.py — 核心编排循环（最重要，串联所有组件）

```python
class TaskExecutor:
    def __init__(self, registry, router, outbox, reconciler):
        self.claude_runner = ClaudeRunner()
        self.codex_runner = CodexRunner()
        self.circuit_breaker = CircuitBreaker()
    
    def submit(self, description: str, override: str = None) -> dict:
        """
        提交新任务：
        1. 生成 task_id: f"{prefix}-{slug}-YYYYMMDD-HHMMSS"
        2. router.route(description, override) → decision
        3. registry.create_task(...)
        4. self.execute(task_id) → 阻塞等待完成
        5. outbox.send_notification(...)
        返回完整 task dict
        """
    
    def execute(self, task_id: str) -> dict:
        """
        核心执行循环（修 W5: 串联所有组件）：
        for attempt in range(max_attempts + 1):
            1. circuit_breaker.is_open(agent) → raise
            2. 创建 worktree（修 W1: 写入 registry）
            3. registry.transition_status(task_id, 'running', 'pending')  # 设置 started_at
            4. runner.run(task_id, prompt, worktree) → result
            5. registry.update_task(exit_code, stderr_tail, ...)
            6. if exit_code == 0 → status='done', circuit_breaker.success
            7. else → classify_failure → if permanent → status='failed'
                                    → if retryable → status='retrying', backoff, continue
            8. outbox.send_notification(...)
        """
    
    def _create_worktree(self, task_id: str, branch: str) -> str:
        """
        git worktree add /home/txs/hermes-agent/worktrees/{task_id} -b {branch}
        返回 worktree 路径
        """
    
    def _cleanup_worktree(self, task_id: str):
        """git worktree remove --force"""
```

### 11. check_agents.py — 简化版健康检查

```python
class HealthChecker:
    def check(self) -> dict:
        """
        返回 {
            "system": {disk_total_gb, disk_used_gb, disk_percent},
            "tasks": {pending: N, running: N, done: N, failed: N},
            "agents": {task_id: {pid, alive, elapsed_sec}},
            "database": {integrity, wal_checkpoint},
        }
        每个 agent 的 `elapsed_sec` 由 agent 所属超时（Claude/Codex 自己的 timeout，不知模型时退到 RECONCILER_TIMEOUT）计算，用以驱动 stale/needs_attention 决策。
        """
```

### 12. bin/hermes.py — CLI 入口

```python
# python ~/hermes/bin/hermes.py submit "实现一个登录功能"
# python ~/hermes/bin/hermes.py status
# python ~/hermes/bin/hermes.py reconcile
# python ~/hermes/bin/hermes.py check
```

### 13. tests/test_full_lifecycle.py — 端到端集成测试

```python
def test_full_lifecycle():
    """
    不依赖真实 Agent，mock runner 输出：
    1. submit("实现登录功能") → task created, status=pending
    2. execute() → worktree created, status=running, then done
    3. notification sent (outbox)
    4. worktree cleaned up
    """
```

## 编码规范

1. **logging** 模块，不用 print()（修 S5）
2. **类型注解**：所有 public 函数必须有参数和返回值类型
3. **docstring**：所有 public 类和方法必须有 docstring
4. **异常处理**：`except Exception:` 不用 bare `except:`（修 S1）
5. **pathlib**：不用 os.path，用 Path 对象
6. **单一职责**：每个模块一个文件，每个类一个职责
7. **import 顺序**：stdlib → third-party → local

## 不要实现的功能（已砍）

- ❌ 成本监控（_parse_progress, _estimate_cost）
- ❌ Prompt Sanitizer（false positive 风险 > 注入风险）
- ❌ PR Manager（v1 不需要）
- ❌ Doctor / Cleanup（运维工具，后续迭代）
- ❌ FeishuAuth（DM 直接用）
- ❌ tmux 降级方案
