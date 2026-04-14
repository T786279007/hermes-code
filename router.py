"""Score-based task routing to select agent, model, and timeout."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of task routing: which agent to use and why."""

    agent: str
    model: str
    timeout: int
    confidence: float
    reason: str


class TaskRouter:
    """Route tasks to Claude Code or Codex based on keyword scoring."""

    CLAUDE_KEYWORDS = [
        "implement", "create", "build", "refactor", "add", "frontend",
        "feature", "api", "ui", "实现", "开发", "创建", "编写", "重构",
        "前端", "添加", "新增", "修改", "优化",
    ]
    CODEX_KEYWORDS = [
        "review", "fix", "bug", "check", "lint", "审查", "代码审查",
        "修复", "检查",
    ]

    def route(self, description: str, override: str | None = None) -> RoutingDecision:
        """Route a task to an agent based on keyword scoring.

        Args:
            description: Natural-language task description.
            override: If set to 'claude-code' or 'codex', skip scoring.

        Returns:
            RoutingDecision with selected agent, model, timeout, confidence, reason.
        """
        if override in ("claude-code", "codex"):
            agent = override
            if agent == "claude-code":
                return RoutingDecision(
                    agent="claude-code",
                    model="claude-sonnet-4-6",
                    timeout=300,
                    confidence=1.0,
                    reason="User override",
                )
            return RoutingDecision(
                agent="codex",
                model="gpt-5.4",
                timeout=180,
                confidence=1.0,
                reason="User override",
            )

        desc_lower = description.lower()
        claude_score = 0
        codex_score = 0

        for kw in self.CLAUDE_KEYWORDS:
            if re.search(re.escape(kw), desc_lower):
                claude_score += 1

        for kw in self.CODEX_KEYWORDS:
            if re.search(re.escape(kw), desc_lower):
                codex_score += 1

        total = claude_score + codex_score
        if total == 0:
            # Default to Claude Code for unknown tasks
            logger.info("No keywords matched, defaulting to claude-code")
            return RoutingDecision(
                agent="claude-code",
                model="claude-sonnet-4-6",
                timeout=300,
                confidence=0.5,
                reason="Default (no keywords matched)",
            )

        if claude_score >= codex_score:
            confidence = claude_score / total
            logger.info(
                "Routed to claude-code (score=%d/%d, confidence=%.2f)",
                claude_score, total, confidence,
            )
            return RoutingDecision(
                agent="claude-code",
                model="claude-sonnet-4-6",
                timeout=300,
                confidence=confidence,
                reason=f"Claude keywords matched {claude_score}/{total}",
            )

        confidence = codex_score / total
        logger.info(
            "Routed to codex (score=%d/%d, confidence=%.2f)",
            codex_score, total, confidence,
        )
        return RoutingDecision(
            agent="codex",
            model="gpt-5.4",
            timeout=180,
            confidence=confidence,
            reason=f"Codex keywords matched {codex_score}/{total}",
        )
