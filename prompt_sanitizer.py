"""Prompt sanitizer for Hermes agent inputs.

Validates and sanitizes user-provided prompts before passing to
Claude Code or Codex runners. Prevents prompt injection, command
injection, and other security risks.

References:
- §22 P0-4: Prompt sanitizer (dual-agent review consensus)
- §11: Zoe behavior rules for task dispatch
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns that suggest prompt injection attempts
_INJECTION_PATTERNS = [
    # Direct instruction override attempts
    r"(?i)ignore\s+(all\s+)?previous\s+instructions?",
    r"(?i)forget\s+(everything|all|your|the)\s+(instructions?|rules?|context)?",
    r"(?i)you\s+are\s+now\s+(a|an)\s+",
    r"(?i)system\s*:?\s*",
    r"(?i)pretend\s+(you\s+are|to\s+be)",
    r"(?i)roleplay\s+as\s+",
    r"(?i)new\s+instructions?\s*:",
    r"(?i)override\s+(the\s+)?(default|system|safety)",
    r"(?i)disable\s+(safety|security|filter|guard)",
    r"(?i)skip\s+(verification|validation|tests?)",
    # Shell/command injection via heredoc, pipes, backticks
    r"`[^`]*`",
    r"\$\([^)]*\)",
    r"<<\s*EOF",
    r"(?i)rm\s+(-rf\s+)?/",
    r"(?i)chmod\s+777",
    r"(?i)curl\s+.*\|\s*(bash|sh)",
    r"(?i)wget\s+.*\|\s*(bash|sh)",
    # Encoding tricks to bypass filters
    r"base64[:\s]",
    r"(?i)\\x[0-9a-f]{2}",
    r"(?i)\\u[0-9a-f]{4}",
    # Credential/access extraction
    r"(?i)(password|secret|token|api.?key|credential)",
    r"(?i)(\.env|\.ssh|\.aws|\.gpg|\.gnupg)",
    r"(?i)/etc/(passwd|shadow|hosts)",
]

# Compiled regex patterns
_COMPILED_PATTERNS = [re.compile(p) for p in _INJECTION_PATTERNS]

# Maximum prompt length (characters)
_MAX_PROMPT_LENGTH = 50_000

# Minimum prompt length for coding tasks
_MIN_CODING_PROMPT_LENGTH = 10


class SanitizationResult:
    """Result of prompt sanitization."""

    __slots__ = ("safe", "reason", "sanitized_prompt", "warnings")

    def __init__(
        self,
        safe: bool,
        reason: str = "",
        sanitized_prompt: str = "",
        warnings: list[str] | None = None,
    ):
        self.safe = safe
        self.reason = reason
        self.sanitized_prompt = sanitized_prompt
        self.warnings = warnings or []

    def __repr__(self) -> str:
        if self.safe:
            return f"SanitizationResult(safe=True, warnings={len(self.warnings)})"
        return f"SanitizationResult(safe=False, reason={self.reason!r})"


def _normalize_unicode(text: str) -> str:
    """Normalize unicode to prevent homoglyph attacks.

    Args:
        text: Input text.

    Returns:
        NFC-normalized text.
    """
    return unicodedata.normalize("NFC", text)


def _strip_control_chars(text: str) -> str:
    """Remove control characters except newline and tab.

    Args:
        text: Input text.

    Returns:
        Text with control characters removed.
    """
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _check_injection(prompt: str) -> list[str]:
    """Check for prompt injection patterns.

    Args:
        prompt: Normalized prompt text.

    Returns:
        List of matched pattern descriptions.
    """
    matches = []
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(prompt):
            # Extract a short description from the pattern
            desc = pattern.pattern[:60]
            matches.append(desc)
    return matches


def _validate_coding_task(prompt: str) -> list[str]:
    """Validate that prompt looks like a coding task.

    Args:
        prompt: Prompt text.

    Returns:
        List of warnings (empty if valid).
    """
    warnings = []

    # Check minimum length
    if len(prompt.strip()) < _MIN_CODING_PROMPT_LENGTH:
        warnings.append(f"Prompt very short ({len(prompt.strip())} chars), may not be a coding task")

    # Check for coding-related keywords
    coding_keywords = [
        "implement", "create", "write", "build", "add", "fix", "refactor",
        "test", "debug", "module", "function", "class", "api", "feature",
        "实现", "创建", "编写", "修复", "重构", "测试", "模块", "功能",
        "代码", "函数", "类", "接口",
    ]
    has_coding_keyword = any(kw in prompt.lower() for kw in coding_keywords)
    if not has_coding_keyword:
        warnings.append("No coding-related keywords detected in prompt")

    return warnings


def sanitize(prompt: str, strict: bool = False) -> SanitizationResult:
    """Sanitize and validate a user prompt for agent execution.

    Args:
        prompt: Raw user-provided prompt.
        strict: If True, reject prompts with any warnings.

    Returns:
        SanitizationResult with safety assessment.
    """
    if not prompt or not prompt.strip():
        return SanitizationResult(
            safe=False,
            reason="Empty prompt",
        )

    # Step 1: Normalize unicode
    normalized = _normalize_unicode(prompt)

    # Step 2: Strip control characters
    cleaned = _strip_control_chars(normalized)

    # Step 3: Check length
    if len(cleaned) > _MAX_PROMPT_LENGTH:
        return SanitizationResult(
            safe=False,
            reason=f"Prompt too long ({len(cleaned)} > {_MAX_PROMPT_LENGTH} chars)",
        )

    # Step 4: Check for injection patterns
    injection_matches = _check_injection(cleaned)
    if injection_matches:
        return SanitizationResult(
            safe=False,
            reason=f"Prompt injection detected ({len(injection_matches)} patterns matched)",
            warnings=injection_matches,
        )

    # Step 5: Validate as coding task
    coding_warnings = _validate_coding_task(cleaned)
    all_warnings = coding_warnings

    if strict and coding_warnings:
        return SanitizationResult(
            safe=False,
            reason="Strict mode: coding task validation failed",
            warnings=all_warnings,
        )

    # Step 6: Check for suspicious file paths
    suspicious_paths = [
        "/etc/", "/proc/", "/sys/", "/dev/",
        "~/.ssh/", "~/.aws/", "~/.gnupg/",
        "/root/", "/home/",
    ]
    path_warnings = []
    for path in suspicious_paths:
        if path in cleaned:
            path_warnings.append(f"References sensitive path: {path}")
    all_warnings.extend(path_warnings)

    logger.info(
        "Prompt sanitized: %d chars, %d warnings, safe=%s",
        len(cleaned),
        len(all_warnings),
        True,
    )

    return SanitizationResult(
        safe=True,
        sanitized_prompt=cleaned,
        warnings=all_warnings,
    )


def build_safe_prompt(
    raw_prompt: str,
    task_id: str,
    worktree: str,
    agent: str = "claude-code",
    strict: bool = False,
) -> tuple[bool, str]:
    """Build a safe, complete prompt for agent execution.

    Wraps the user prompt with safety constraints and context.

    Args:
        raw_prompt: Raw user-provided prompt.
        task_id: Task identifier.
        worktree: Working directory path.
        agent: Target agent ("claude-code" or "codex").
        strict: If True, reject prompts with any warnings.

    Returns:
        Tuple of (is_safe, final_prompt).
    """
    result = sanitize(raw_prompt, strict=strict)
    if not result.safe:
        logger.warning("Prompt rejected for task %s: %s", task_id, result.reason)
        return False, result.reason

    done_definition = """
## Completion Criteria (ALL must be satisfied)
1. Code passes local tests (run them)
2. git commit && git push
3. If any test fails, fix it before committing
"""

    if agent == "codex":
        done_definition += "4. Do NOT modify test files to make them pass\n"

    warnings_section = ""
    if result.warnings:
        warnings_section = f"\n## Warnings\n{chr(10).join('- ' + w for w in result.warnings)}\n"

    final_prompt = f"""## Task: {task_id}

{result.sanitized_prompt}

## Environment
- Working directory: {worktree}
- Agent: {agent}
{warnings_section}
{done_definition}
"""

    return True, final_prompt
