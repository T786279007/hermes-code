# Hermes Architecture

## 状态机

```
pending → running → done
                 → retrying → running (backoff)
                 → failed (permanent / max_attempts)
```

非法转换会被拒绝（done→running 等）。

## 核心数据流

```
用户需求
  ↓
executor.submit(description)
  ↓
TaskRegistry.create_task() → pending
  ↓
TaskRouter.route(description) → claude-code / codex
  ↓
sandbox.prepare_runner_env() → 隔离 HOME + git config + auth
  ↓
claude_runner.run() / codex_runner.run()
  ↓ subprocess in worktree
Claude Code / Codex 执行编码 + 测试
  ↓
_ensure_commit() → git add -A && git commit
  ↓
TaskRegistry.finish_task() → done / failed
  ↓
Outbox.notify() → Gateway / 日志 fallback
  ↓
worktree 保留（done）或清理（failed）
```

## 模块职责

### executor.py
核心编排循环。接收需求 → 创建任务 → 创建 worktree → 准备 sandbox → 启动 runner → 处理结果 → 重试或完成。

关键方法：
- `submit(description, override=None)` — 提交任务，返回任务 dict
- `_create_worktree(task_id, branch)` — 创建隔离 git worktree
- `_ensure_commit(worktree, task_id)` — 检查未提交变更并自动补 commit

### task_registry.py
SQLite WAL 模式的任务存储，是系统的单一事实来源。

Schema：id, description, agent, model, status, branch, worktree, prompt, result, exit_code, stderr_tail, failure_class, attempt, max_attempts, created_at, updated_at, started_at, pid

### claude_runner.py
通过 `claude --print --output-format stream-json` 启动 Claude Code 子进程。
- 超时控制（默认 300s）
- 实时进度回调（on_spawn）
- 捕获 stdout/stderr/exit_code

### codex_runner.py
通过 `codex exec` 启动 Codex 子进程。
- 类似 claude_runner 的接口
- 解析 JSON 输出

### router.py
基于关键词的任务路由：
- 包含 "python", "module", "class", "function", "test" → claude-code
- 包含 "api", "endpoint", "server", "database", "sql" → codex
- 可用 `override` 参数强制指定

### retry.py
失败分类 + 重试策略：
- FailureClass: RETRYABLE / PERMANENT
- classify_failure(exit_code, stderr) → 自动分类
- 指数退避：base_delay * 2^attempt
- 熔断器：连续 N 次失败后打开，5 分钟后 half-open

### sandbox.py
为每个任务创建隔离的执行环境：
- 独立 HOME 目录（~/.openclaw/workspace/hermes-agent/runner_home/）
- copy .claude.json 和 .claude/（安全隔离，非 symlink）
- 最小 .gitconfig（Hermes Agent 身份）
- git-askpass.sh（GitHub token 注入，权限 0600）

### outbox.py
幂等通知系统：
- 优先通过 Gateway 发送飞书通知
- Gateway 不可用时 fallback 到日志
- 幂等去重（external_id）

### reconciler.py
崩溃恢复：
- 扫描 status=running 但进程不存在的任务
- 超时检测：elapsed > 2 × max(runner_timeout)
- 自动标记 failed + 清理 worktree

## 配置

所有配置在 `config.py`：
- DB_PATH: /home/txs/hermes-agent/tasks.db
- REPO_PATH: /tmp/hermes-repo
- WORKTREE_BASE: /home/txs/hermes-agent/worktrees/
- RUNNER_HOME: /home/txs/hermes-agent/runner_home/
- CLAUDE_TIMEOUT: 300s
- CODEX_TIMEOUT: 180s
- MAX_ATTEMPTS: 3
- CIRCUIT_BREAKER_THRESHOLD: 3
