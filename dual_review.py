#!/usr/bin/env python3
"""Dual-model code review: run Claude Code + Codex in parallel and merge results."""

from __future__ import annotations

import logging
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from review_pr import (
    review_pr,
    codex_review_pr,
    post_review_comment,
    post_inline_comment,
    _run_command,
    ReviewIssue,
    Severity,
    CLIError,
    ValidationError,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewerResult:
    """Result from a single reviewer."""
    reviewer: str
    status: str  # 'success' | 'error'
    issues: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    model: str = ""
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


@dataclass
class MergedIssue:
    """An issue detected by one or both reviewers."""
    severities: Dict[str, str]  # reviewer -> severity
    file_path: Optional[str]
    line_number: Optional[int]
    message: str
    suggestion: Optional[str]
    consensus: bool  # True if both reviewers flagged it


def dual_review(
    repo_path: str,
    pr_number: int,
    claude_model: str = "claude-sonnet-4-6",
    codex_model: str = "gpt-5.4",
    timeout: int = 300,
) -> Dict[str, Any]:
    """Run Claude Code and Codex reviews in parallel and merge results.

    Args:
        repo_path: Path to the repository.
        pr_number: Pull request number.
        claude_model: Model for Claude Code review.
        codex_model: Model for Codex review.
        timeout: Max seconds per reviewer.

    Returns:
        Dictionary with:
            - status: 'success' | 'partial' | 'error'
            - claude: ReviewerResult dict
            - codex: ReviewerResult dict
            - merged_issues: List of merged issues
            - consensus_count: Number of issues both reviewers agree on
            - summary: Combined summary
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError("Repository path cannot be empty")
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    logger.info(
        "Starting dual review for PR #%d (claude=%s, codex=%s)",
        pr_number, claude_model, codex_model,
    )

    claude_result: ReviewerResult = ReviewerResult(reviewer="claude-code", status="error")
    codex_result: ReviewerResult = ReviewerResult(reviewer="codex", status="error")
    claude_error: List[str] = []
    codex_error: List[str] = []

    def _run_claude() -> None:
        import time
        start = time.time()
        try:
            r = review_pr(repo_path, pr_number, model=claude_model)
            claude_result.status = r.get("status", "error")
            claude_result.issues = r.get("issues", [])
            claude_result.summary = r.get("summary", "")
            claude_result.model = claude_model
        except Exception as e:
            claude_result.error = str(e)
            claude_error.append(str(e))
            logger.error("Claude review failed: %s", e)
        claude_result.elapsed_seconds = time.time() - start

    def _run_codex() -> None:
        import time
        start = time.time()
        try:
            r = codex_review_pr(repo_path, pr_number, model=codex_model)
            codex_result.status = r.get("status", "error")
            codex_result.issues = r.get("issues", [])
            codex_result.summary = r.get("summary", "")
            codex_result.model = codex_model
        except Exception as e:
            codex_result.error = str(e)
            codex_error.append(str(e))
            logger.error("Codex review failed: %s", e)
        codex_result.elapsed_seconds = time.time() - start

    # Run both in parallel
    claude_thread = threading.Thread(target=_run_claude, name="claude-review")
    codex_thread = threading.Thread(target=_run_codex, name="codex-review")

    claude_thread.start()
    codex_thread.start()

    claude_thread.join(timeout=timeout)
    codex_thread.join(timeout=timeout)

    # Handle timeouts
    if claude_thread.is_alive():
        claude_result.error = f"Timeout after {timeout}s"
        claude_error.append(f"Timeout after {timeout}s")
        claude_result.status = "error"
        logger.warning("Claude review timed out")
    if codex_thread.is_alive():
        codex_result.error = f"Timeout after {timeout}s"
        codex_error.append(f"Timeout after {timeout}s")
        codex_result.status = "error"
        logger.warning("Codex review timed out")

    # Merge results
    merged = _merge_issues(
        claude_result.issues if claude_result.status == "success" else [],
        codex_result.issues if codex_result.status == "success" else [],
    )

    # Determine overall status
    if claude_result.status == "success" and codex_result.status == "success":
        overall_status = "success"
    elif claude_result.status == "success" or codex_result.status == "success":
        overall_status = "partial"
    else:
        overall_status = "error"

    logger.info(
        "Dual review complete: status=%s, claude=%d issues (%.1fs), codex=%d issues (%.1fs), consensus=%d",
        overall_status,
        len(claude_result.issues), claude_result.elapsed_seconds,
        len(codex_result.issues), codex_result.elapsed_seconds,
        merged["consensus_count"],
    )

    return {
        "status": overall_status,
        "claude": {
            "reviewer": claude_result.reviewer,
            "status": claude_result.status,
            "model": claude_result.model,
            "issue_count": len(claude_result.issues),
            "summary": claude_result.summary,
            "elapsed_seconds": claude_result.elapsed_seconds,
            "error": claude_result.error,
        },
        "codex": {
            "reviewer": codex_result.reviewer,
            "status": codex_result.status,
            "model": codex_result.model,
            "issue_count": len(codex_result.issues),
            "summary": codex_result.summary,
            "elapsed_seconds": codex_result.elapsed_seconds,
            "error": codex_result.error,
        },
        "merged_issues": merged["issues"],
        "consensus_count": merged["consensus_count"],
        "summary": _build_summary(claude_result, codex_result, merged),
    }


def _merge_issues(
    claude_issues: List[Dict[str, Any]],
    codex_issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge issues from both reviewers, grouping by file+line when possible.

    Args:
        claude_issues: Issues from Claude Code.
        codex_issues: Issues from Codex.

    Returns:
        Dict with 'issues' (merged list) and 'consensus_count'.
    """
    merged: List[Dict[str, Any]] = []
    used_codex: set = set()

    # Build O(1) lookup index for codex issues by (file_path, line_number)
    codex_index: Dict[tuple, int] = {}
    for idx, coi in enumerate(codex_issues):
        key = (coi.get("file_path"), coi.get("line_number"))
        if key[0] and key[1]:
            codex_index[key] = idx
    consensus_count = 0

    for ci in claude_issues:
        # Try to find a matching Codex issue at the same file+line (O(1) via index)
        match_idx = None
        if ci.get("file_path") and ci.get("line_number"):
            key = (ci.get("file_path"), ci.get("line_number"))
            if key in codex_index and codex_index[key] not in used_codex:
                match_idx = codex_index[key]

        if match_idx is not None:
            coi = codex_issues[match_idx]
            used_codex.add(match_idx)
            consensus_count += 1
            merged.append({
                "consensus": True,
                "claude_severity": ci.get("severity"),
                "codex_severity": coi.get("severity"),
                "severity": _max_severity(ci.get("severity"), coi.get("severity")),
                "file_path": ci.get("file_path"),
                "line_number": ci.get("line_number"),
                "message": ci.get("message"),
                "codex_message": coi.get("message"),
                "suggestion": ci.get("suggestion") or coi.get("suggestion"),
            })
        else:
            merged.append({
                "consensus": False,
                "claude_severity": ci.get("severity"),
                "codex_severity": None,
                "severity": ci.get("severity"),
                "file_path": ci.get("file_path"),
                "line_number": ci.get("line_number"),
                "message": ci.get("message"),
                "suggestion": ci.get("suggestion"),
            })

    # Add unmatched Codex issues
    for idx, coi in enumerate(codex_issues):
        if idx not in used_codex:
            merged.append({
                "consensus": False,
                "claude_severity": None,
                "codex_severity": coi.get("severity"),
                "severity": coi.get("severity"),
                "file_path": coi.get("file_path"),
                "line_number": coi.get("line_number"),
                "message": coi.get("message"),
                "suggestion": coi.get("suggestion"),
            })

    # Sort: consensus first, then by severity
    severity_order = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}
    merged.sort(key=lambda x: (
        0 if x.get("consensus") else 1,
        severity_order.get(x.get("severity", "INFO"), 2),
    ))

    return {"issues": merged, "consensus_count": consensus_count}


def _max_severity(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the higher severity of two severity strings."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    order = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}
    return a if order[a] <= order[b] else b


def _build_summary(
    claude: ReviewerResult,
    codex: ReviewerResult,
    merged: Dict[str, Any],
) -> str:
    """Build a human-readable combined summary.

    Args:
        claude: Claude Code review result.
        codex: Codex review result.
        merged: Merged issues dict.

    Returns:
        Multi-line summary string.
    """
    parts = [
        f"## Dual Review Summary",
        f"",
        f"| Reviewer | Status | Issues | Time |",
        f"|----------|--------|--------|------|",
        f"| Claude Code ({claude.model}) | {claude.status} | {len(claude.issues)} | {claude.elapsed_seconds:.1f}s |",
        f"| Codex ({codex.model}) | {codex.status} | {len(codex.issues)} | {codex.elapsed_seconds:.1f}s |",
        f"",
        f"**Consensus issues:** {merged['consensus_count']}",
        f"**Total unique issues:** {len(merged['issues'])}",
    ]

    if claude.error:
        parts.append(f"\n⚠️ Claude error: {claude.error}")
    if codex.error:
        parts.append(f"\n⚠️ Codex error: {codex.error}")

    return "\n".join(parts)


def dual_auto_review(
    repo_path: str,
    pr_number: int,
    claude_model: str = "claude-sonnet-4-6",
    codex_model: str = "gpt-5.4",
    auto_comment: bool = True,
    auto_approve: bool = False,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Run dual review and optionally post comments / approve.

    Args:
        repo_path: Path to the repository.
        pr_number: Pull request number.
        claude_model: Model for Claude Code.
        codex_model: Model for Codex.
        auto_comment: Whether to post merged review as a comment.
        auto_approve: Whether to approve if no consensus blockers.
        timeout: Max seconds per reviewer.

    Returns:
        Full dual review result plus comment/approval info.
    """
    # Run dual review
    result = dual_review(
        repo_path, pr_number,
        claude_model=claude_model,
        codex_model=codex_model,
        timeout=timeout,
    )

    comments_posted: List[str] = []
    approved = False

    if auto_comment and result["merged_issues"]:
        comment_parts = [
            "## 🤖🤖 Dual-Model Automated Code Review",
            "",
            result["summary"],
            "",
        ]

        # Group by consensus
        consensus = [i for i in result["merged_issues"] if i.get("consensus")]
        claude_only = [i for i in result["merged_issues"]
                       if not i.get("consensus") and i.get("claude_severity")]
        codex_only = [i for i in result["merged_issues"]
                      if not i.get("consensus") and i.get("codex_severity")]

        if consensus:
            comment_parts.append("### ✅ Consensus (both reviewers agree)")
            for issue in consensus:
                loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                tag = f"[{issue['claude_severity']}/{issue['codex_severity']}]"
                comment_parts.append(f"- **{tag}{loc}** {issue['message']}")
                if issue.get('suggestion'):
                    comment_parts.append(f"  - 💡 {issue['suggestion']}")
            comment_parts.append("")

        if claude_only:
            comment_parts.append("### 🔵 Claude Code only")
            for issue in claude_only:
                loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                comment_parts.append(f"- **[{issue['claude_severity']}]{loc}** {issue['message']}")
                if issue.get('suggestion'):
                    comment_parts.append(f"  - 💡 {issue['suggestion']}")
            comment_parts.append("")

        if codex_only:
            comment_parts.append("### 🟢 Codex only")
            for issue in codex_only:
                loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                comment_parts.append(f"- **[{issue['codex_severity']}]{loc}** {issue['message']}")
                if issue.get('suggestion'):
                    comment_parts.append(f"  - 💡 {issue['suggestion']}")
            comment_parts.append("")

        try:
            comment_result = post_review_comment(
                repo_path, pr_number, "\n".join(comment_parts)
            )
            comments_posted.append(comment_result.get("url", ""))
        except CLIError as e:
            logger.warning("Failed to post dual review comment: %s", e)

    # Approve if no consensus blockers
    consensus_blockers = [
        i for i in result["merged_issues"]
        if i.get("consensus") and i.get("severity") == "BLOCKER"
    ]

    if auto_approve and not consensus_blockers:
        try:
            _run_command(
                ["gh", "pr", "review", str(pr_number), "--approve"],
                cwd=repo_path,
            )
            approved = True
            logger.info("Auto-approved PR #%d (no consensus blockers)", pr_number)
        except CLIError as e:
            logger.warning("Failed to auto-approve PR: %s", e)

    result["approved"] = approved
    result["comments_posted"] = comments_posted
    result["review_complete"] = len(consensus_blockers) == 0

    return result
