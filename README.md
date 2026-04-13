# Hermes Code 🦞

> 用 AI 管理 AI — 让 Claude Code 和 Codex 成为你的编码集群

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-104%20passed-green.svg)](#)

Hermes Code 是一个轻量级 Agent 编排系统，将编码任务自动派发给 Claude Code / Codex，并处理沙箱隔离、智能重试、熔断保护、自动提交和通知推送。

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
- **自举开发** — 用 Hermes 自己开发新模块（pr_manager、review_pr、workflow_engine 均由 Claude Code 生成）

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
git clone https://github.com/T786279007/hermes-code.git
cd hermes-code
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
cd hermes-code
PYTHONPATH=src pytest tests/ -v
# 104 passed
```

## 📁 项目结构

```
hermes-code/
├── src/hermes/                    # 核心源码
│   ├── config.py                  # 全局配置（支持环境变量覆盖）
│   ├── task_registry.py           # SQLite 任务注册表（WAL + 状态机）
│   ├── executor.py                # 核心编排循环（submit→run→retry→notify）
│   ├── claude_runner.py           # Claude Code 子进程（stream-json）
│   ├── codex_runner.py            # Codex 子进程（acpx）
│   ├── router.py                  # 关键词路由引擎
│   ├── retry.py                   # 失败分类 + 指数退避 + 熔断器
│   ├── sandbox.py                 # 沙箱隔离（独立 HOME + auth copy）
│   ├── outbox.py                  # 幂等通知系统
│   ├── reconciler.py              # 崩溃恢复（stale task 检测）
│   ├── pr_manager.py              # PR 管理（gh CLI）
│   ├── review_pr.py               # AI 代码审查（Claude + gh）
│   ├── workflow_engine.py         # 工作流引擎（管道 + 并行 + 重试）
│   ├── check_agents.py            # Agent 健康检查
│   ├── dashboard.py               # 实时监控看板（HTTP + 3s 刷新）
│   └── cli.py                     # CLI 入口
├── tests/                         # 单元测试（104 个，全 mock）
│   ├── test_full_lifecycle.py     # 核心生命周期（5）
│   ├── test_pr_manager.py         # PR 管理（22）
│   ├── test_review_pr.py          # 代码审查（36）
│   ├── test_workflow_engine.py    # 工作流引擎（24）
│   └── test_e2e_orchestration.py  # E2E 编排（12+）
├── experiments/                   # 实验脚本（开发过程完整记录）
│   ├── hermes-exp1-task-registry.py    # Exp1: 任务注册表
│   ├── hermes-exp2-runner-integration.py # Exp2: Runner 集成
│   ├── hermes-exp3-codex-integration.py # Exp3: Codex 集成
│   ├── hermes-exp4-cron-monitor.py      # Exp4: Cron 监控
│   ├── hermes-exp5-e2e.py               # Exp5: 端到端测试
│   ├── hermes-exp6-git-workflow.py      # Exp6: Git 工作流
│   ├── hermes-exp7-code-review.py       # Exp7: 代码审查
│   ├── hermes-exp8-auto-test.py         # Exp8: 自动测试
│   ├── hermes-exp9-notification.py      # Exp9: 通知系统
│   ├── hermes-exp10-pr-creation.py      # Exp10: PR 创建
│   ├── hermes-exp11-send-keys.py        # Exp11: 终端交互
│   ├── hermes-full-flow-e2e.py          # 全流程穿测
│   ├── hermes-v2-all-source.py          # V2 全源码合并
│   ├── hermes-v2-final-source.py        # V2 最终源码
│   ├── hermes-v2-e2e-tested.py          # V2 E2E 测试版
│   └── outputs/                         # Agent 实际生成的代码
│       ├── feat-create-pr-manager-py/        # PR 管理模块（Claude 生成）
│       ├── feat-create-a-python-module-called/ # 工作流引擎（Claude 生成）
│       ├── feat-create-config-parser-py/      # 配置解析器（Claude 生成）
│       ├── feat-create-http-client-py/        # HTTP 客户端（Claude 生成）
│       └── feat-create-file-watcher-py/       # 文件监控（Claude 生成）
├── integration-tests/              # 集成测试（需要真实 Agent）
│   ├── test_acp_delegate.py        # ACP 委托测试
│   ├── test_feishu_e2e_v*.py       # 飞书 E2E 测试（v1-v7 迭代）
│   ├── claude-task-runner.py       # Claude 任务运行器
│   └── feishu-progress-relay.py    # 飞书进度推送
├── skills/                         # OpenClaw Skill（AI Agent 集成）
│   ├── SKILL.md                    # Skill 定义（触发条件 + 使用方式）
│   ├── scripts/submit.py           # 一键提交脚本
│   ├── scripts/e2e_test.py         # 穿测脚本
│   ├── scripts/dashboard.py        # 看板脚本
│   └── references/architecture.md  # 架构详情
├── docs/
│   └── DEVELOPMENT.md              # 开发指南
├── README.md
├── LICENSE                         # MIT
└── pyproject.toml
```

## 🧪 测试

```bash
# 单元测试（104 个，全 mock，不需要真实 Agent）
PYTHONPATH=src pytest tests/ -v

# 按模块
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

编辑 `src/hermes/config.py` 或使用环境变量：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `HERMES_HOME` | `.hermes-agent` | 工作目录 |
| `HERMES_REPO_PATH` | `/tmp/hermes-repo` | Git 仓库路径 |
| `HERMES_PROXY` | `""` | HTTP 代理 |
| `HERMES_CLAUDE_TIMEOUT` | `300` | Claude 超时（秒） |
| `HERMES_CODEX_TIMEOUT` | `180` | Codex 超时（秒） |
| `HERMES_MAX_RETRIES` | `3` | 最大重试次数 |
| `HERMES_CB_THRESHOLD` | `3` | 熔断阈值 |

## 🛡️ 安全

- **沙箱隔离**：每个 task 独立 HOME，不使用 symlink（copy 方式传递凭据，权限 0600）
- **最小权限**：Claude Code 使用 `--permission-mode bypassPermissions` 仅在沙箱内
- **Worktree 隔离**：每个 task 独立 git worktree + 分支
- **凭据保护**：GitHub token 通过 git-askpass 注入，不写入环境变量

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
- `review_pr.py` — AI 代码审查 + GitHub 评论 + 自动审批
- `workflow_engine.py` — 管道/并行/依赖/重试/持久化工作流

## 🔬 实验记录

`experiments/` 目录保留了完整的开发过程，从 Exp1 到全流程穿测，共 15+ 个实验脚本。每个脚本都是独立可运行的，记录了 Hermes 从概念到可用的完整演进。

`experiments/outputs/` 保存了 Claude Code 在真实任务中生成的全部代码（5 个模块 + 测试），可作为 AI 编码能力的参考样本。

## 📄 License

MIT

## 🙏 致谢

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic 的 AI 编码工具
- [Codex CLI](https://github.com/openai/codex) — OpenAI 的 AI 编码工具
- [OpenClaw](https://github.com/openclaw/openclaw) — Agent 编排框架
- Elvis Sun 的 Agent 集群架构 — 系统设计灵感来源
