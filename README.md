# Hermes 🦞

> 用 AI 管理 AI — 让 Claude Code 和 Codex 成为你的编码集群

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-104%20passed-green.svg)](#)

Hermes 是一个轻量级 Agent 编排系统，将编码任务自动派发给 Claude Code / Codex，并处理沙箱隔离、智能重试、熔断保护、自动提交和通知推送。

你只管提需求，Hermes 负责派活、监控、重试、交付。

## ✨ 特性

- **双 Agent 支持** — Claude Code + Codex，关键词自动路由
- **沙箱隔离** — 每个 task 独立 HOME、独立 worktree、独立 git 分支
- **智能重试** — 失败自动分类（retryable/permanent），指数退避
- **熔断器** — 连续失败自动熔断，防止雪崩
- **自动提交** — 任务完成后自动 git commit，产出不丢失
- **崩溃恢复** — Reconciler 检测 stale task，自动清理
- **实时看板** — 内置 HTTP Dashboard，3 秒自动刷新
- **幂等通知** — 通过 Gateway 推送飞书，降级到日志

## 🏗️ 架构

```
用户需求
  ↓
executor.submit(description)
  ↓
TaskRouter → claude-code / codex
  ↓
sandbox.prepare_runner_env()  ← 隔离 HOME + auth
  ↓
claude_runner.run() / codex_runner.run()
  ↓ subprocess in worktree
Claude Code / Codex 执行编码 + 测试
  ↓
_ensure_commit()  ← 自动补提交
  ↓
TaskRegistry.finish_task()  → done / failed
  ↓
Outbox.notify()  → 飞书 / 日志
```

**状态机**：`pending → running → done / retrying → failed`

## 📦 安装

```bash
git clone https://github.com/T786279007/hermes.git
cd hermes
pip install -e .
```

**依赖**：
- Python 3.12+
- Claude Code CLI（`npm install -g @anthropic-ai/claude-code`）
- Codex CLI（`npm install -g @openai/codex`）
- gh CLI（`brew install gh`）

## 🚀 快速开始

### 1. 提交任务

```python
import sys
sys.path.insert(0, "src")

from hermes.executor import TaskExecutor
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler

registry = TaskRegistry("hermes-agent/tasks.db")
executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))

task = executor.submit(
    "创建一个 stats.py 模块，包含 mean/median/stddev 函数。"
    "同时创建 test_stats.py，至少 10 个测试。运行所有测试。",
    override="claude-code"
)

print(f"Task {task['id']}: {task['status']}")
```

### 2. CLI 使用

```bash
python -m hermes.cli submit "创建一个 config_parser.py..."
python -m hermes.cli status
python -m hermes.cli check
```

### 3. 启动看板

```bash
python -m hermes.dashboard --port 8420
# 打开 http://localhost:8420
```

### 4. 运行测试

```bash
cd hermes
PYTHONPATH=src pytest tests/ -v
# 104 passed
```

## 📁 项目结构

```
hermes/
├── src/hermes/
│   ├── __init__.py
│   ├── config.py              # 全局配置
│   ├── task_registry.py       # SQLite 任务注册表（WAL + 状态机）
│   ├── executor.py            # 核心编排循环
│   ├── claude_runner.py       # Claude Code 子进程管理
│   ├── codex_runner.py        # Codex 子进程管理
│   ├── router.py              # 关键词路由引擎
│   ├── retry.py               # 失败分类 + 指数退避 + 熔断器
│   ├── outbox.py              # 幂等通知系统
│   ├── reconciler.py          # 崩溃恢复
│   ├── sandbox.py             # 沙箱隔离（独立 HOME + auth）
│   ├── pr_manager.py          # PR 管理（gh CLI）
│   ├── review_pr.py           # AI 代码审查（Claude + gh）
│   ├── workflow_engine.py     # 工作流引擎（管道 + 并行）
│   ├── check_agents.py        # Agent 健康检查
│   ├── dashboard.py           # 实时监控看板
│   └── cli.py                 # CLI 入口
├── tests/
│   ├── test_full_lifecycle.py     # 核心生命周期测试 (5)
│   ├── test_pr_manager.py         # PR 管理测试 (22)
│   ├── test_review_pr.py          # 代码审查测试 (36)
│   ├── test_workflow_engine.py    # 工作流引擎测试 (24)
│   └── test_e2e_orchestration.py  # E2E 编排测试 (12+)
├── README.md
├── LICENSE
└── pyproject.toml
```

## 🧪 测试

```bash
# 全量测试（104 个）
PYTHONPATH=src pytest tests/ -v

# 单个模块
PYTHONPATH=src pytest tests/test_pr_manager.py -v
PYTHONPATH=src pytest tests/test_e2e_orchestration.py -v
```

| 测试文件 | 用例数 | 覆盖 |
|---------|--------|------|
| test_full_lifecycle.py | 5 | 核心生命周期、重试、熔断、通知 |
| test_pr_manager.py | 22 | PR 创建/列表/合并/CI |
| test_review_pr.py | 36 | AI 审查/评论/状态/自动审批 |
| test_workflow_engine.py | 24 | 管道/并行/依赖/重试/持久化 |
| test_e2e_orchestration.py | 12+ | 端到端编排/恢复/cleanup |

## 🔧 配置

编辑 `src/hermes/config.py`：

```python
DB_PATH = "hermes-agent/tasks.db"        # SQLite 数据库
REPO_PATH = "/tmp/hermes-repo"            # Git 仓库
WORKTREE_BASE = "hermes-agent/worktrees/" # Worktree 目录
RUNNER_HOME = "hermes-agent/runner_home/" # 沙箱 HOME
CLAUDE_TIMEOUT = 300                      # Claude 超时（秒）
CODEX_TIMEOUT = 180                       # Codex 超时（秒）
MAX_ATTEMPTS = 3                          # 最大重试次数
CIRCUIT_BREAKER_THRESHOLD = 3             # 熔断阈值
```

## 🛡️ 安全

- **沙箱隔离**：每个 task 独立 HOME，不使用 symlink（copy 方式传递凭据）
- **凭据权限**：GitHub token 文件权限 0600
- **最小权限**：Claude Code 使用 `--permission-mode bypassPermissions` 仅在沙箱内
- **Worktree 隔离**：每个 task 独立 git worktree + 分支

## 📊 真实测试数据

| 任务 | Agent | 耗时 | 测试 | 重试 |
|------|-------|------|------|------|
| stats.py | Claude Code | 89s | 15 pass | 0 |
| pr_manager.py | Claude Code | 109s | 22 pass | 0 |
| workflow_engine.py | Claude Code | 90s | 24 pass | 0 |
| config_parser.py | Claude Code | 113s | 17 pass | 0 |
| http_client.py | Claude Code | 306s | 24 pass | 0 |
| file_watcher.py | Claude Code | 264s | 15 pass | 0 |

**累计**：6 个真实任务，100% 成功率，0 次重试

## 📄 License

MIT

## 🙏 致谢

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic 的 AI 编码工具
- [Codex CLI](https://github.com/openai/codex) — OpenAI 的 AI 编码工具
- Elvis Sun 的 [Agent 集群架构](https://www.youtube.com/watch?v=...) — 架构灵感来源
