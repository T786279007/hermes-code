"""Claude Code plan generator — uses superpowers plugin to create dev plans."""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

PLAN_TIMEOUT = 300  # seconds


class PlanResult:
    """Result of a planning operation."""

    def __init__(self, plan: str, success: bool, error: str | None = None):
        self.plan = plan
        self.success = success
        self.error = error

    def __repr__(self) -> str:
        if self.success:
            return f"PlanResult(success=True, plan_length={len(self.plan)})"
        return f"PlanResult(success=False, error={self.error})"


def generate_plan(description: str, timeout: int = PLAN_TIMEOUT) -> PlanResult:
    """Use Claude Code with superpowers plugin to generate a development plan.

    Args:
        description: Task description to plan for.
        timeout: Maximum seconds to wait for plan generation.

    Returns:
        PlanResult with plan text or error.
    """
    prompt = (
        f"使用 superpowers 插件为以下需求做详细的开发规划：\n\n{description}\n\n"
        "请输出结构化的开发规划，包含：\n"
        "1. 需求分析\n"
        "2. 技术方案\n"
        "3. 实现步骤（分步骤列出）\n"
        "4. 需要注意的风险点\n"
        "5. 验收标准"
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--print",
        "--max-turns", "3",
        "--bare",
        "--model", "glm-5.1",
    ]

    logger.info("Starting plan generation for: %.80s", description)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=10)
            return PlanResult(
                plan="",
                success=False,
                error=f"Plan generation timed out after {timeout}s",
            )

        if proc.returncode != 0:
            error_msg = stderr.strip() or f"Exit code {proc.returncode}"
            logger.error("Plan generation failed: %s", error_msg)
            return PlanResult(plan="", success=False, error=error_msg)

        plan = stdout.strip()
        if not plan:
            return PlanResult(plan="", success=False, error="Empty plan output")

        logger.info("Plan generated successfully (%d chars)", len(plan))
        return PlanResult(plan=plan, success=True)

    except FileNotFoundError:
        return PlanResult(
            plan="",
            success=False,
            error="Claude Code CLI not found (command: claude)",
        )
    except Exception as e:
        logger.exception("Plan generation error")
        return PlanResult(plan="", success=False, error=str(e))
