# Hermes Code 🦞

> 用 AI 管理 AI — 让 Claude Code 和 Codex 成为你的编码集群

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests 330](https://img.shields.io/badge/tests-330%20passed-brightgreen.svg)](tests/)
[![License MIT](https://img.shields.io/badge/license-MIT-informational.svg)](LICENSE)

Hermes Code 是一个轻量级 Agent 编排系统，将编码任务自动派发给 Claude Code / Codex，并处理沙箱隔离、智能重试、熔断保护、自动提交和通知推送。

你只管提需求，Hermes 负责派活、监控、重试、交付。

## ✨ 特性

- **双 Agent 支持** — Claude Code + Codex，关键词自动路由 + 人工 override
- **沙箱隔离** — 每个 task 独立 HOME、独立 worktree、独立 git 分支
- **智能重试** — 失败自动分类（22 种场景），指数退避 + 10% 抖动
- **熔断器** — 连续失败自动熔断，冷却后半开探测
- **自动提交** — 任务完成后自动 git commit，产出不丢失
- **崩溃恢复** — Reconciler 按 agent 类型差异化超时（Claude 600s / Codex 43200s），自动清理 stale task
- **健康检查** — 7 子系统巡检（磁盘/任务/Agent/数据库/worktree/PR 状态/stale 检测）
- **幂等通知** — logged → sent 状态流转，通过 Gateway 推送飞书，降级到日志
- **AI 代码审查** — 双 Agent 并行审查 + 共识检测 + 严重性升级 + GitHub 评论
- **工作流引擎** — 管道/并行/依赖图/重试/持久化
- **实时看板** — 内置 HTTP Dashboard，3 秒自动刷新

## 🏗️ 架构

```
用户需求
  ↓
executor.submit(description)
  ↓
TaskRouter → claude-code / codex（关键词评分 + override）
  ↓
sandbox.prepare_runner_env()  ← 隔离 HOME + git config + askpass
  ↓
claude_runner.run() / codex_runner.run()
  ↓ subprocess in worktree
Claude Code / Codex 执行编码 + 测试
  ↓
_ensure_commit()  ← 自动补提交
  ↓
TaskRegistry.finish_task(done_checks_json=)  → done / failed
  ↓
Outbox.send_notification()  → logged → sent → 飞书 / 日志
  ↑
Reconciler.reconcile()  ← stale 检测 + per-agent timeout
  ↑
HealthChecker.check()  ← 7 子系统巡检
```

**状态机**：`pending → running → done | retrying → failed`

## 📦 安装

```bash
git clone https://github.com/T786279007/hermes-code.git
cd hermes-code
pip install -e .
```

**依赖**：
- Python 3.12+
- Claude Code CLI v2.1+（`npm install -g @anthropic-ai/claude-code`）
- Codex CLI v0.120+（`npm install -g @openai/codex`）
- gh CLI（`brew install gh` 或 `apt install gh`）

## 🚀 快速开始

### 提交任务（Python API）

```python
import sys
sys.path.insert(0, "/home/txs")  # hermes 包的父目录

from hermes.executor import TaskExecutor
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler

registry = TaskRegistry()
executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))

task = executor.submit(
    "创建一个 stats.py 模块，包含 mean/median/stddev 函数。"
    "同时创建 test_stats.py，至少 10 个测试。运行所有测试。",
    override="claude-code"  # 可选：强制指定 agent
)

print(f"Task {task['id']}: {task['status']}")
```

### CLI 使用

```bash
python ~/hermes/bin/cli.py submit "创建一个 config_parser.py..."
python ~/hermes/bin/cli.py status
python ~/hermes/bin/cli.py check
python ~/hermes/bin/cli.py reconcile
```

### 启动看板

```bash
python -m hermes.dashboard --port 8420
# 打开 http://localhost:8420
```

### 运行测试

```bash
cd ~/hermes
PYTHONPATH=/home/txs pytest tests/ -v
# 330 passed
```

## 📁 项目结构

```
hermes-code/
├── config.py              # 全局配置常量
├── task_registry.py       # SQLite WAL 任务注册表（状态机 + prompt + done_checks）
├── executor.py            # 核心编排循环（submit → run → retry → notify）
├── claude_runner.py       # Claude Code 子进程管理
├── codex_runner.py        # Codex 子进程管理（exec 子命令）
├── router.py              # 评分制任务路由（中英文关键词 + matched_keywords）
├── retry.py               # 失败分类（22 场景）+ 指数退避 + 熔断器
├── sandbox.py             # 沙箱隔离（独立 HOME + git config + askpass）
├── outbox.py              # 幂等通知（logged → sent 状态流转）
├── reconciler.py          # 崩溃恢复（per-agent timeout + stale 检测）
├── pr_manager.py          # PR 管理（gh CLI）
├── review_pr.py           # AI 代码审查（Claude + GitHub 评论 + fallback）
├── dual_review.py         # 双 Agent 并行审查（共识检测 + 严重性升级）
├── workflow_engine.py     # 工作流引擎（管道 + 并行 + 依赖 + 重试 + 持久化）
├── check_agents.py        # 7 子系统健康检查
├── done_checker.py        # 任务完成验证
├── smart_retry.py         # 智能重试策略
├── dashboard.py           # 实时监控看板
├── bin/
│   └── cli.py             # CLI 入口（submit/status/check/reconcile）
├── tests/                 # 330 个测试用例（全 mock，无需真实 Agent）
│   ├── test_task_registry.py    (39)  # CRUD / 状态机 / prompt / done_checks
│   ├── test_review_pr.py        (42)  # AI 审查 / 评论 / fallback
│   ├── test_retry.py            (33)  # 失败分类 / backoff / 熔断器
│   ├── test_router.py           (26)  # 关键词评分 / override / 置信度
│   ├── test_pr_manager.py       (22)  # PR 创建/列表/合并/CI
│   ├── test_workflow_engine.py  (24)  # 管道/并行/依赖/重试
│   ├── test_check_agents.py     (21)  # 7 子系统巡检
│   ├── test_sandbox.py          (13)  # 环境隔离 / gitconfig / askpass
│   ├── test_smart_retry.py      (15)  # 智能重试策略
│   ├── test_outbox.py           (10)  # 幂等性 / CAS / 重试 / 状态流转
│   ├── test_dual_review.py      (23)  # 并行审查 / 共识 / 审批
│   ├── test_config.py           (17)  # 配置常量
│   ├── test_e2e_orchestration.py (12)  # E2E 编排
│   ├── test_hermes_e2e.py       (12)  # E2E 集成
│   ├── test_done_checker.py      (8)  # 完成验证
│   ├── test_reconciler.py        (8)  # stale / timeout / cleanup
│   └── test_full_lifecycle.py    (5)  # 核心生命周期
├── experiments/            # 实验脚本（11 个实验 + 全流程穿测）
├── integration-tests/      # 集成测试（需要真实 Agent）
├── skills/                 # OpenClaw Skill 定义
├── docs/
│   ├── DEVELOPMENT.md      # 开发指南
│   ├── review_pr_usage.md  # 审查使用说明
│   └── status_checklist.md # 状态检查清单
├── HERMES_SPEC.md          # 编码规格书（v2）
├── README.md
├── LICENSE
└── pyproject.toml
```

## 🧪 测试

```bash
cd ~/hermes
PYTHONPATH=/home/txs pytest tests/ -v          # 全量 330 个
PYTHONPATH=/home/txs pytest tests/ -k "test_retry"  # 按关键词过滤
PYTHONPATH=/home/txs pytest tests/test_router.py -v    # 单模块
```

所有测试全 mock，不需要真实 Agent，秒级完成。

## 🔧 配置

编辑 `config.py`：

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `CLAUDE_TIMEOUT` | `300` | Claude Code 超时（秒） |
| `CODEX_TIMEOUT` | `21600` | Codex 超时（秒，6 小时） |
| `RECONCILER_TIMEOUT` | `600` | 未知 agent 的默认恢复超时 |
| `MAX_RETRIES` | `3` | 最大重试次数 |
| `CIRCUIT_BREAKER_THRESHOLD` | `3` | 熔断触发阈值 |
| `CIRCUIT_BREAKER_RESET` | `300` | 熔断器冷却时间（秒） |
| `REPO_PATH` | `/tmp/hermes-repo` | Git 仓库路径 |
| `DB_PATH` | `hermes-agent/tasks.db` | SQLite 数据库路径 |
| `PROXY` | `http://127.0.0.1:7897` | HTTP 代理 |

## 🛡️ 安全

- **沙箱隔离**：每个 task 独立 HOME（copy 方式传递凭据，权限 0600），不使用 symlink
- **最小权限**：Claude Code `--permission-mode bypassPermissions` 仅在沙箱内；Codex `--dangerously-bypass-approvals-and-sandbox`
- **Worktree 隔离**：每个 task 独立 git worktree + 分支，互不干扰
- **凭据保护**：GitHub token 通过 git-askpass 注入，不写入环境变量
- **数据库完整性**：SQLite WAL 模式 + 事务保护，Reconciler 定期 checkpoint

## 📊 真实测试数据

6 个真实任务由 Claude Code 独立完成，0 次人工干预：

| 任务 | Agent | 耗时 | 测试 | 重试 |
|------|-------|------|------|------|
| pr_manager.py | Claude Code | 109s | 22 pass | 0 |
| workflow_engine.py | Claude Code | 90s | 24 pass | 0 |
| config_parser.py | Claude Code | 113s | 17 pass | 0 |
| http_client.py | Claude Code | 306s | 24 pass | 0 |
| file_watcher.py | Claude Code | 264s | 15 pass | 0 |
| **累计** | — | **882s** | **102 pass** | **0** |

自举开发模块（由 Hermes 自己调度 Claude Code 生成）：
- `pr_manager.py` — PR 创建/列表/合并/CI 状态检查
- `review_pr.py` — AI 代码审查 + GitHub 评论 + 自动审批 + fallback
- `workflow_engine.py` — 管道/并行/依赖/重试/持久化工作流

## 📜 更新日志

### v1.2 (2026-04-14)

- `done_checks_json` + `prompt` 持久化，自动 schema 迁移
- Reconciler per-agent 超时（Claude 600s / Codex 43200s）
- Router `matched_keywords` 字段
- Outbox `logged` vs `sent` 状态流转
- Health Checker 7 子系统巡检
- review_pr fallback 路径
- 330 tests 全绿

### v1.1

- 双 Agent 路由 + 沙箱隔离
- 智能重试 + 熔断器
- Reconciler 崩溃恢复
- 幂等通知

## 📄 License

MIT

## 🙏 致谢

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic
- [Codex CLI](https://github.com/openai/codex) — OpenAI
- [OpenClaw](https://github.com/openclaw/openclaw) — Agent 编排框架
