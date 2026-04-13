---
name: hermes
description: >-
  Hermes Agent 编排系统。当需要将编码任务委托给 Claude Code/Codex 时使用。
  触发条件：(1) 用户说"用 Hermes"、"交给 Hermes"、"harness"、"自举开发"
  (2) 编码需求明确且可独立完成（>50行，有清晰输入/输出/验收标准）
  (3) 需要监控 Agent 执行进度、查看任务历史
  (4) 需要启动 Hermes 实时看板
  不触发：简单单文件修改、配置调整、非编码任务、需要频繁交互的任务。
---

# Hermes — Agent 编排系统

Hermes 将编码任务派发给 Claude Code / Codex，自动处理沙箱隔离、重试、熔断、通知和 worktree 管理。

## 什么时候用

| 场景 | 用？ |
|------|------|
| "开发一个 XXX 模块" | ✅ |
| "用 Hermes 做 XXX" | ✅ |
| "自举开发下一个组件" | ✅ |
| 简单改几行配置 | ❌ 直接改 |
| 需要边做边确认 | ❌ 手动做 |

## 快速提交

```bash
# 方式 1：Python 脚本（推荐）
python3 ~/.openclaw/workspace/skills/hermes/scripts/submit.py "创建 XXX 模块，功能：..."

# 方式 2：带参数
python3 ~/.openclaw/workspace/skills/hermes/scripts/submit.py \
  "创建 XXX 模块" \
  --agent claude-code \
  --model claude-sonnet-4-6 \
  --watch   # 等待完成后输出结果

# 方式 3：直接 Python 调用
cd /home/txs/hermes && PYTHONPATH=/home/txs python3 -c "
from hermes.executor import TaskExecutor
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.config import DB_PATH

registry = TaskRegistry(DB_PATH)
executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))
task = executor.submit('你的需求描述', override='claude-code')
print(task)
"
```

## 自举开发流程

用 Hermes 开发新模块的 5 步 SOP：

1. **写需求** — 明确模块名、功能列表、接口签名、测试要求
2. **提交** — `submit.py "需求描述" --watch`
3. **验证** — 检查 worktree 中的文件和测试
4. **集成** — 复制到 `/home/txs/hermes/` 包内
5. **回归** — `cd /home/txs/hermes && python3 -m pytest tests/ -v`

### Prompt 模板

提交时在需求末尾追加测试要求，确保 Agent 自测：

```
创建 <module>.py，功能：...
同时创建 test_<module>.py，至少 N 个测试。运行所有测试确保通过。
```

## 穿测验证

```bash
# 全流程穿测（提交 3 个真实任务）
python3 ~/.openclaw/workspace/skills/hermes/scripts/e2e_test.py

# 单元测试（104 个）
cd /home/txs/hermes && python3 -m pytest tests/ -v --override-ini="addopts="
```

## 监控看板

```bash
# 启动 dashboard
python3 ~/.openclaw/workspace/skills/hermes/scripts/dashboard.py --port 8420

# API
curl http://localhost:8420/api/tasks | python3 -m json.tool
```

看板每 3 秒自动刷新，展示所有任务的实时状态。

## 组件清单

| 模块 | 用途 |
|------|------|
| executor.py | 核心编排（submit→route→run→retry→notify） |
| task_registry.py | SQLite 任务注册表（WAL + 状态机） |
| claude_runner.py | Claude Code 子进程（stream-json） |
| codex_runner.py | Codex 子进程（acpx） |
| router.py | 关键词路由（python→claude, api→codex） |
| retry.py | 失败分类 + 指数退避 + 熔断器 |
| outbox.py | 幂等通知（Gateway 飞书 / 日志 fallback） |
| reconciler.py | 崩溃恢复（stale task 检测 + 清理） |
| sandbox.py | 沙箱隔离（独立 HOME + git config + auth copy） |
| pr_manager.py | PR 创建/列表/合并（gh CLI） |
| review_pr.py | AI 代码审查（Claude + gh） |
| workflow_engine.py | 工作流引擎（管道 + 并行 + 重试） |
| check_agents.py | Agent 健康检查 |
| dashboard.py | 实时监控看板（HTTP + 自动刷新） |
| bin/hermes.py | CLI 入口 |

## 关键路径

```
代码:    /home/txs/hermes/
DB:      /home/txs/hermes-agent/tasks.db
Worktree:/home/txs/hermes-agent/worktrees/
日志:    /home/txs/hermes/logs/
测试仓库:/tmp/hermes-repo
Dashboard: http://localhost:8420
API:    http://localhost:8420/api/tasks
```

## 故障排查

| 问题 | 解决 |
|------|------|
| Claude Code "Not logged in" | sandbox.py 已处理（copy .claude.json 到隔离 HOME） |
| 熔断器打开（3 次失败） | 重启 executor 或等 5 分钟自动恢复 |
| worktree 丢失 | done 状态保留 worktree；失败才清理 |
| 测试 pytest 报错 | 加 `--override-ini="addopts="` 绕过项目级配置 |
| outbox 通知 404 | Gateway 未配置，fallback 到日志（正常） |
| Python 缓存旧代码 | `find /home/txs/hermes -name "__pycache__" -exec rm -rf {} +` |

## 架构详情

详细架构、状态机、数据流见 `references/architecture.md`（按需加载）。
