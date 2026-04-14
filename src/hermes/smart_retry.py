"""Smart retry — analyzes failures and generates improved retry prompts.

When a task fails, instead of blindly retrying with the same prompt,
this module analyzes the failure context and generates a targeted
retry prompt that includes:
- Failure summary
- Files that were written (partial progress)
- Specific fix instructions based on failure classification
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def analyze_failure(task: dict) -> dict[str, str]:
    """Analyze a failed task and determine the likely cause.

    Args:
        task: Task dict with stderr_tail, exit_code, result, etc.

    Returns:
        Dict with:
            "cause": Human-readable failure cause
            "category": "timeout" | "test_failure" | "import_error" | "syntax_error" | "permission_error" | "unknown"
            "severity": "low" | "medium" | "high"
    """
    stderr = (task.get("stderr_tail", "") or "").lower()
    result = (task.get("result", "") or "").lower()
    exit_code = task.get("exit_code", -1)
    stderr_raw = task.get("stderr_tail", "") or ""

    # Timeout
    if exit_code == -1 or "timed out" in stderr or "timeout" in stderr:
        return {
            "cause": "执行超时，任务可能在复杂操作中卡住",
            "category": "timeout",
            "severity": "medium",
        }

    # Test failure
    test_patterns = ["failed", "error", "assertionerror", "test", "pytest", "unittest"]
    test_hits = sum(1 for p in test_patterns if p in stderr[-500:] or p in result[-500:])
    if test_hits >= 2:
        # Extract test failure details
        failed_tests = re.findall(r"(FAILED|ERROR) (.+?)(?:\n| -)", result[-2000:] + stderr[-2000:])
        if failed_tests:
            cause = "测试失败：" + "、".join(f"{t[1].strip()}" for t in failed_tests[:3])
        else:
            cause = "测试失败，部分断言未通过"
        return {
            "cause": cause,
            "category": "test_failure",
            "severity": "low",
        }

    # Import / module error
    if any(kw in stderr for kw in ("importerror", "modulenotfounderror", "cannot import", "no module named")):
        mod_match = re.search(r"no module named ['\"](.+?)['\"]", stderr_raw, re.IGNORECASE)
        mod = mod_match.group(1) if mod_match else "未知模块"
        return {
            "cause": f"缺少依赖模块: {mod}",
            "category": "import_error",
            "severity": "medium",
        }

    # Syntax error
    if "syntaxerror" in stderr or "syntax error" in stderr:
        return {
            "cause": "代码存在语法错误",
            "category": "syntax_error",
            "severity": "low",
        }

    # Permission error
    if "permission" in stderr or "denied" in stderr:
        return {
            "cause": "权限不足，可能需要不同的文件路径",
            "category": "permission_error",
            "severity": "high",
        }

    return {
        "cause": f"未知错误 (exit_code={exit_code})",
        "category": "unknown",
        "severity": "medium",
    }


def get_partial_progress(worktree: str | None) -> dict[str, list[str]]:
    """Check what files were written in the worktree (partial progress).

    Args:
        worktree: Path to the worktree.

    Returns:
        Dict with "files_written" and "files_test" lists.
    """
    if not worktree or not Path(worktree).exists():
        return {"files_written": [], "files_test": []}

    result = {"files_written": [], "files_test": []}

    try:
        # Get files changed vs base
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "--"],
            cwd=worktree, capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            for f in r.stdout.strip().split("\n"):
                if f.strip():
                    if "test_" in f or "_test." in f:
                        result["files_test"].append(f)
                    else:
                        result["files_written"].append(f)
    except Exception:
        pass

    # Fallback: list Python files in worktree
    if not result["files_written"]:
        try:
            for p in Path(worktree).glob("**/*.py"):
                name = str(p.relative_to(worktree))
                if name not in ("__init__.py",):
                    if "test_" in name or "_test." in name:
                        result["files_test"].append(name)
                    else:
                        result["files_written"].append(name)
        except Exception:
            pass

    return result


def generate_retry_prompt(
    original_description: str,
    task: dict,
    attempt: int,
) -> str:
    """Generate an improved retry prompt based on failure analysis.

    Args:
        original_description: Original task description.
        task: Failed task dict.
        attempt: Current attempt number (0-indexed).

    Returns:
        Improved prompt string to feed to the agent on retry.
    """
    analysis = analyze_failure(task)
    progress = get_partial_progress(task.get("worktree"))

    parts = [f"## 重试任务（第 {attempt + 1} 次尝试）\n"]

    # Original task
    parts.append(f"### 原始需求\n{original_description}\n")

    # Failure analysis
    parts.append(f"### 上次失败原因\n{analysis['cause']}\n")
    parts.append(f"**严重程度**: {analysis['severity']}")

    # Partial progress
    if progress["files_written"]:
        parts.append(f"\n### 已完成的文件\n{', '.join(progress['files_written'])}")
    if progress["files_test"]:
        parts.append(f"\n### 已有的测试\n{', '.join(progress['files_test'])}")

    # Category-specific fix instructions
    category = analysis["category"]
    parts.append("\n### 修复要求\n")

    if category == "timeout":
        parts.append(
            "上次执行超时。请：\n"
            "- 减少不必要的操作\n"
            "- 优先完成核心功能\n"
            "- 不要花太多时间在边缘 case 上\n"
            "- 确保代码简洁高效"
        )
    elif category == "test_failure":
        parts.append(
            "上次测试失败。请：\n"
            "- 仔细检查测试失败的具体断言\n"
            "- 修复代码逻辑而非修改测试\n"
            "- 确保所有测试通过后再提交\n"
            "- 运行 `python -m pytest tests/ -v` 验证"
        )
    elif category == "import_error":
        parts.append(
            "上次缺少依赖。请：\n"
            "- 只使用 Python 标准库\n"
            "- 不要假设第三方库已安装\n"
            "- 如果必须用，在代码中提供 fallback 实现"
        )
    elif category == "syntax_error":
        parts.append(
            "上次有语法错误。请：\n"
            "- 仔细检查代码语法\n"
            "- 写完每个函数后检查括号/冒号是否匹配\n"
            "- 运行 `python -c \"import ast; ast.parse(open('文件名').read())\"` 验证"
        )
    elif category == "permission_error":
        parts.append(
            "上次遇到权限问题。请：\n"
            "- 不要尝试修改系统文件\n"
            "- 所有操作限制在当前目录内\n"
            "- 不要使用 sudo 或提升权限"
        )
    else:
        parts.append(
            "上次执行失败，原因不明。请：\n"
            "- 重新理解需求，确保方向正确\n"
            "- 从头开始实现，不要依赖上次的状态\n"
            "- 保持代码简洁，避免过度设计"
        )

    parts.append(
        "\n完成后，运行所有测试确认通过，"
        "然后执行 `git add -A && git commit -m 'fix: retry #{attempt} - <brief>'`。"
    )

    return "\n".join(parts)
