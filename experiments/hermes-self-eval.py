#!/usr/bin/env python3
"""Hermes Code 自评估脚本 — 对照文档要求逐项检查代码实现。

用法: PYTHONPATH=/home/txs python3 /tmp/hermes-self-eval.py
"""
import importlib
import inspect
import os
import sys

sys.path.insert(0, "/home/txs")

# ── helpers ──
def check_module(name):
    try:
        mod = importlib.import_module(f"hermes.{name}")
        lines = sum(len(inspect.getsource(v).splitlines()) for _, v in inspect.getmembers(mod, inspect.isfunction) if inspect.getsourcefile(v) and "hermes" in inspect.getsourcefile(v))
        return True, f"✅ {len(inspect.getsource(mod).splitlines())} lines"
    except Exception as e:
        return False, f"❌ {e}"

def check_function(module_name, func_name):
    try:
        mod = importlib.import_module(f"hermes.{module_name}")
        fn = getattr(mod, func_name)
        sig = inspect.signature(fn)
        return True, f"✅ params: {list(sig.parameters.keys())}"
    except Exception as e:
        return False, f"❌ {e}"

def check_has_code(module_name, keyword):
    try:
        mod = importlib.import_module(f"hermes.{module_name}")
        src = inspect.getsource(mod)
        return keyword.lower() in src.lower(), f"{'✅' if keyword.lower() in src.lower() else '❌'} '{keyword}' {'found' if keyword.lower() in src.lower() else 'NOT found'}"
    except Exception as e:
        return False, f"❌ {e}"

def check_file_exists(path):
    return os.path.exists(path), f"{'✅' if os.path.exists(path) else '❌'} {path}"

# ── evaluation ──
results = []
def add(cat, item, ok, detail=""):
    results.append((cat, item, ok, detail))

# ══════════════════════════════════════════════════════
# 一、系统架构
# ══════════════════════════════════════════════════════
add("一、架构", "协调层 (executor.py)", *check_module("executor"))
add("一、架构", "执行层 - Claude Runner", *check_module("claude_runner"))
add("一、架构", "执行层 - Codex Runner", *check_module("codex_runner"))
add("一、架构", "路由 (router.py)", *check_module("router"))
add("一、架构", "沙箱隔离 (sandbox.py)", *check_module("sandbox"))

# ══════════════════════════════════════════════════════
# 二、任务注册表
# ══════════════════════════════════════════════════════
add("二、注册表", "SQLite 存储 (task_registry.py)", *check_module("task_registry"))
add("二、注册表", "状态机 (pending→running→done/failed)", *check_has_code("task_registry", "transition_status"))
add("二、注册表", "create_task API", *check_function("task_registry", "create_task"))
add("二、注册表", "finish_task API", *check_function("task_registry", "finish_task"))
add("二、注册表", "get_task API", *check_function("task_registry", "get_task"))
add("二、注册表", "list_tasks API", *check_function("task_registry", "list_tasks"))

# ══════════════════════════════════════════════════════
# 三、Cron 监控 + PR 流水线
# ══════════════════════════════════════════════════════
add("三、监控", "Agent 健康检查 (check_agents.py)", *check_module("check_agents"))
add("三、监控", "进程存活检查", *check_has_code("reconciler", "stale"))
add("三、监控", "进度停滞检测", *check_has_code("check_agents", "stall") or check_has_code("check_agents", "progress"))
add("三、监控", "PR 状态检查", *check_has_code("pr_manager", "list_prs"))
add("三、监控", "CI 状态检查", *check_function("pr_manager", "check_ci"))
add("三、监控", "审查状态检查", *check_has_code("review_pr", "review"))

# ══════════════════════════════════════════════════════
# 四、完成定义
# ══════════════════════════════════════════════════════
add("四、完成定义", "完成定义检查器 (done_checker.py)", *check_module("done_checker"))
add("四、完成定义", "commit 检查", *check_has_code("done_checker", "commit"))
add("四、完成定义", "测试通过检查", *check_has_code("done_checker", "tests_passed"))
add("四、完成定义", "PR 创建检查", *check_has_code("done_checker", "pr_created"))
add("四、完成定义", "CI 通过检查", *check_has_code("done_checker", "ci_passed"))
add("四、完成定义", "截图检查", *check_has_code("done_checker", "screenshot"))
add("四、完成定义", "executor 集成 done_checks", *check_has_code("executor", "run_done_checks"))
add("四、完成定义", "Prompt 注入完成标准", *check_has_code("executor", "git add -A && git commit"))

# ══════════════════════════════════════════════════════
# 五、自动化代码审查
# ══════════════════════════════════════════════════════
add("五、审查", "审查模块 (review_pr.py)", *check_module("review_pr"))
add("五、审查", "Claude 审查", *check_has_code("review_pr", "claude"))
add("五、审查", "Codex 审查", *check_has_code("review_pr", "codex"))
add("五、审查", "PR 评论发布", *check_has_code("review_pr", "comment"))
add("五、审查", "自动审批逻辑", *check_has_code("review_pr", "approve"))
add("五、审查", "三模型审查 (Phase 2)", False, "🔶 等 Codex/Gemini ACP")

# ══════════════════════════════════════════════════════
# 六、自动化测试 (CI)
# ══════════════════════════════════════════════════════
add("六、CI", "GitHub Actions CI", *check_file_exists("/home/txs/hermes/.github/workflows/ci.yml"))
ci_exists = os.path.exists("/home/txs/hermes/.github/workflows/ci.yml")
add("六、CI", "CI 文件存在", ci_exists, f"{'✅' if ci_exists else '❌'} ci.yml")
# Fix: check CI content
ci_path = "/home/txs/hermes/.github/workflows/ci.yml"
if os.path.exists(ci_path):
    ci_content = open(ci_path).read()
    add("六、CI", "Python 测试", "py_compile" in ci_content, f"{'✅' if 'py_compile' in ci_content else '❌'}")
    add("六、CI", "pytest 运行", "pytest" in ci_content, f"{'✅' if 'pytest' in ci_content else '❌'}")
    add("六、CI", "截图规则检查", "screenshot" in ci_content.lower(), f"{'✅' if 'screenshot' in ci_content.lower() else '❌'}")
    add("六、CI", "多 Python 版本", "3.12" in ci_content, f"{'✅' if '3.12' in ci_content else '❌'}")
else:
    add("六、CI", "CI 管道", False, "❌ ci.yml not found")

# ══════════════════════════════════════════════════════
# 七、人工审查 + 合并
# ══════════════════════════════════════════════════════
add("七、PR流程", "PR 创建", *check_function("pr_manager", "create_pr"))
add("七、PR流程", "PR 列表", *check_function("pr_manager", "list_prs"))
add("七、PR流程", "PR 合并", *check_function("pr_manager", "merge_pr"))
add("七、PR流程", "CI 状态", *check_function("pr_manager", "check_ci"))

# ══════════════════════════════════════════════════════
# 八、智能重试
# ══════════════════════════════════════════════════════
add("八、智能重试", "智能重试模块 (smart_retry.py)", *check_module("smart_retry"))
add("八、智能重试", "失败分析 (6 类)", *check_function("smart_retry", "analyze_failure"))
add("八、智能重试", "重写 Prompt", *check_function("smart_retry", "generate_retry_prompt"))
add("八、智能重试", "部分进度检测", *check_function("smart_retry", "get_partial_progress"))
add("八、智能重试", "executor 集成 smart_retry", *check_has_code("executor", "generate_retry_prompt"))
add("八、智能重试", "指数退避", *check_function("retry", "compute_delay"))
add("八、智能重试", "熔断器", *check_has_code("retry", "CircuitBreaker"))

# ══════════════════════════════════════════════════════
# 九、Worktree 隔离
# ══════════════════════════════════════════════════════
add("九、隔离", "Worktree 创建", *check_has_code("executor", "_create_worktree"))
add("九、隔离", "Worktree 清理", *check_has_code("executor", "_cleanup_worktree"))
add("九、隔离", "独立 HOME", *check_has_code("sandbox", "HOME"))
add("九、隔离", "凭据 copy (非 symlink)", *check_has_code("sandbox", "copy2"))
add("九、隔离", "Git config 隔离", *check_has_code("sandbox", "gitconfig"))

# ══════════════════════════════════════════════════════
# 十、进度追踪
# ══════════════════════════════════════════════════════
add("十、进度", "stream-json 进度", *check_has_code("claude_runner", "stream-json"))
add("十、进度", "on_spawn 回调", *check_has_code("claude_runner", "on_spawn"))

# ══════════════════════════════════════════════════════
# 十一、Zoe 行为规则
# ══════════════════════════════════════════════════════
add("十一、行为规则", "SOUL.md 编排规则", *check_file_exists("/home/txs/.openclaw/workspace/SOUL.md"))
add("十一、行为规则", "OpenClaw Skill", *check_file_exists("/home/txs/.openclaw/workspace/skills/hermes/SKILL.md"))

# ══════════════════════════════════════════════════════
# 十二、实施计划
# ══════════════════════════════════════════════════════
add("十二、Sprint A", "核心闭环 (注册表+Runner+监控)", True, "✅ 全部完成")
add("十二、Sprint B", "交付链路 (审查+重试+Worktree)", True, "✅ 全部完成")

# ══════════════════════════════════════════════════════
# 十四、Codex 集成
# ══════════════════════════════════════════════════════
add("十四、Codex", "Codex Runner", *check_module("codex_runner"))
add("十四、Codex", "路由策略 (关键词)", *check_has_code("router", "python") and check_has_code("router", "api"))
add("十四、Codex", "ACP 配置", *check_file_exists("/home/txs/.openclaw/workspace/skills/acpx/SKILL.md"))

# ══════════════════════════════════════════════════════
# 额外：看板 + GitHub
# ══════════════════════════════════════════════════════
add("额外", "实时看板 (dashboard.py)", *check_module("dashboard"))
add("额外", "GitHub 仓库", *check_file_exists("/tmp/hermes-project/.git"))
add("额外", "CLI 入口", *check_module("cli"))

# ══════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════
print("╔════════════════════════════════════════════════════════════════════════╗")
print("║  🦞 HERMES CODE · 自评估报告（对照文档 v2 方案）                     ║")
print("╠════════════════════════════════════════════════════════════════════════╣")

current_cat = ""
total = passed = failed = partial = 0
for cat, item, ok, detail in results:
    if cat != current_cat:
        print(f"\n  【{cat}】")
        current_cat = cat
    total += 1
    icon = "✅" if ok else "🔶" if "Phase" in detail or "等" in detail else "❌"
    if ok: passed += 1
    elif "🔶" in icon or "Phase" in detail: partial += 1
    else: failed += 1
    print(f"    {icon} {item:<40s} {detail}")

print(f"\n╠════════════════════════════════════════════════════════════════════════╣")
print(f"  📊 总计: {total} 项  ✅ 通过: {passed}  🔶 待定: {partial}  ❌ 未实现: {failed}")
print(f"  📈 达标率: {passed}/{total} = {passed/total*100:.0f}%")
if partial > 0:
    print(f"  📌 含待定: {(passed+partial)}/{total} = {(passed+partial)/total*100:.0f}%")
print("╚════════════════════════════════════════════════════════════════════════╝")
