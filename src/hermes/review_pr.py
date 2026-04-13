#!/usr/bin/env python3
"""Pull Request Code Review using Claude Code.

This module provides functions to automate code review for GitHub pull requests
using the Claude Code CLI tool and gh CLI.
"""

import subprocess
import json
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum


# Configure logging
logger = logging.getLogger(__name__)


class Severity(Enum):
    """Issue severity levels."""
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ReviewIssue:
    """Represents a code review issue."""
    severity: Severity
    file_path: Optional[str]
    line_number: Optional[int]
    message: str
    suggestion: Optional[str] = None


class ReviewPRError(Exception):
    """Base exception for Review PR errors."""
    pass


class CLIError(ReviewPRError):
    """Exception raised when CLI command fails."""
    pass


class ValidationError(ReviewPRError):
    """Exception raised when input validation fails."""
    pass


class ReviewParseError(ReviewPRError):
    """Exception raised when review output parsing fails."""
    pass


def _run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    input_text: Optional[str] = None,
    capture: bool = True
) -> Tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr.

    Args:
        cmd: Command and arguments to execute
        cwd: Working directory (optional)
        input_text: Text to pass to stdin (optional)
        capture: Whether to capture output

    Returns:
        Tuple of (exit_code, stdout, stderr)

    Raises:
        CLIError: If the command fails and capture is True
    """
    try:
        logger.debug(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            input=input_text,
            cwd=cwd,
            check=False
        )

        if capture and result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else str(result.stdout)
            raise CLIError(f"Command failed: {error_msg}")

        return result.returncode, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise CLIError(f"Command failed: {error_msg}") from e
    except FileNotFoundError as e:
        raise CLIError(f"Command not found: {cmd[0]}") from e


def _parse_review_output(output: str) -> Tuple[List[ReviewIssue], str]:
    """Parse Claude Code review output into structured issues.

    Args:
        output: Raw output from Claude Code review

    Returns:
        Tuple of (list of ReviewIssue, summary text)

    Raises:
        ReviewParseError: If parsing fails
    """
    issues = []
    summary_lines = []
    current_severity = None
    current_file = None
    current_line = None
    current_message = []
    current_suggestion = []

    try:
        lines = output.strip().split('\n')

        for line in lines:
            # Detect severity markers
            severity_match = re.match(r'^(BLOCKER|WARNING|INFO):\s*(.+)$', line)
            if severity_match:
                # Save previous issue if exists
                if current_severity and current_message:
                    issues.append(ReviewIssue(
                        severity=current_severity,
                        file_path=current_file,
                        line_number=current_line,
                        message='\n'.join(current_message).strip(),
                        suggestion='\n'.join(current_suggestion).strip() if current_suggestion else None
                    ))

                current_severity = Severity[severity_match.group(1)]
                current_message = [severity_match.group(2)]
                current_suggestion = []
                current_file = None
                current_line = None
                continue

            # Detect file/line references
            file_line_match = re.match(r'[@\s]([\w./-]+):(\d+)', line)
            if file_line_match and current_severity:
                current_file = file_line_match.group(1)
                current_line = int(file_line_match.group(2))
                continue

            # Detect suggestion blocks
            if line.strip().startswith('Suggestion:') or line.strip().startswith('->'):
                if current_severity:
                    current_suggestion.append(line.strip())
                continue

            # Add to current message if we have an active issue
            if current_severity and line.strip() and not severity_match:
                current_message.append(line)
            elif not current_severity and line.strip():
                summary_lines.append(line)

        # Save last issue
        if current_severity and current_message:
            issues.append(ReviewIssue(
                severity=current_severity,
                file_path=current_file,
                line_number=current_line,
                message='\n'.join(current_message).strip(),
                suggestion='\n'.join(current_suggestion).strip() if current_suggestion else None
            ))

        # If no structured issues found, treat entire output as summary
        if not issues and summary_lines:
            summary_lines = lines
        elif not issues and not summary_lines:
            summary_lines = lines

        summary = '\n'.join(summary_lines).strip()

        logger.info(f"Parsed {len(issues)} review issues")
        return issues, summary

    except Exception as e:
        raise ReviewParseError(f"Failed to parse review output: {e}") from e


def review_pr(
    repo_path: str,
    pr_number: int,
    model: str = 'claude-sonnet-4-6'
) -> Dict[str, Any]:
    """Perform code review on a pull request using Claude Code.

    Args:
        repo_path: Path to the repository
        pr_number: Pull request number
        model: Claude model to use for review

    Returns:
        Dictionary containing:
            - status: 'success' or 'error'
            - issues: List of ReviewIssue as dicts
            - summary: Review summary text
            - raw_output: Raw Claude output

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI or claude command fails
        ReviewParseError: If review output parsing fails
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError("Repository path cannot be empty")
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    logger.info(f"Starting review for PR #{pr_number} in {repo_path}")

    try:
        # Get PR diff
        logger.info("Fetching PR diff...")
        exit_code, diff_output, diff_err = _run_command(
            ["gh", "pr", "diff", str(pr_number)],
            cwd=repo_path
        )

        if not diff_output.strip():
            logger.warning(f"PR #{pr_number} has no diff to review")
            return {
                "status": "success",
                "issues": [],
                "summary": "No changes to review",
                "raw_output": ""
            }

        # Prepare review prompt
        review_prompt = f"""Review this pull request diff. Focus on:
1. Code quality and maintainability
2. Potential bugs or edge cases
3. Security concerns
4. Performance issues
5. Testing coverage

For each issue found, prefix with BLOCKER:, WARNING:, or INFO:
- BLOCKER: Critical issues that must be fixed
- WARNING: Important issues that should be addressed
- INFO: Minor suggestions or nitpicks

Format:
BLOCKER: Issue description
@file:line
Suggestion: Fix recommendation

"""

        # Run Claude Code review
        logger.info("Running Claude Code review...")
        exit_code, claude_output, claude_err = _run_command(
            ["claude", "--print", "--output-format", "stream-json"],
            cwd=repo_path,
            input_text=review_prompt + diff_output
        )

        # Parse review results
        issues, summary = _parse_review_output(claude_output)

        logger.info(f"Review complete: {len(issues)} issues found")

        return {
            "status": "success",
            "issues": [
                {
                    "severity": issue.severity.value,
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "message": issue.message,
                    "suggestion": issue.suggestion
                }
                for issue in issues
            ],
            "summary": summary,
            "raw_output": claude_output
        }

    except CLIError as e:
        logger.error(f"CLI error during review: {e}")
        raise
    except ReviewParseError as e:
        logger.error(f"Parse error during review: {e}")
        raise


def post_review_comment(
    repo_path: str,
    pr_number: int,
    review_body: str
) -> Dict[str, Any]:
    """Post a review comment to a pull request.

    Args:
        repo_path: Path to the repository
        pr_number: Pull request number
        review_body: Review comment body (can include markdown)

    Returns:
        Dictionary containing:
            - status: 'success' or 'error'
            - comment_id: ID of posted comment
            - url: URL to the comment

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI command fails
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError("Repository path cannot be empty")
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")
    if not review_body or not review_body.strip():
        raise ValidationError("Review body cannot be empty")

    logger.info(f"Posting review comment to PR #{pr_number}")

    try:
        exit_code, output, stderr = _run_command(
            ["gh", "pr", "comment", str(pr_number), "--body", review_body],
            cwd=repo_path
        )

        # Extract comment URL from output
        url_match = re.search(r'https://github\.com/[^/]+/[^/]+/pull/\d+/comment/\w+', output)
        comment_url = url_match.group(0) if url_match else output.strip()

        logger.info(f"Review comment posted: {comment_url}")

        return {
            "status": "success",
            "comment_id": comment_url.split('/')[-1],
            "url": comment_url
        }

    except CLIError as e:
        logger.error(f"Failed to post review comment: {e}")
        raise


def post_inline_comment(
    repo_path: str,
    pr_number: int,
    body: str,
    file_path: str,
    line_number: int
) -> Dict[str, Any]:
    """Post an inline review comment on a specific line.

    Args:
        repo_path: Path to the repository
        pr_number: Pull request number
        body: Comment body
        file_path: Path to file in repo
        line_number: Line number to comment on

    Returns:
        Dictionary containing:
            - status: 'success' or 'error'
            - comment_id: ID of posted comment

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI command fails
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError("Repository path cannot be empty")
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")
    if not body or not body.strip():
        raise ValidationError("Comment body cannot be empty")
    if not file_path or not file_path.strip():
        raise ValidationError("File path cannot be empty")
    if line_number <= 0:
        raise ValidationError("Line number must be positive")

    logger.info(f"Posting inline comment to {file_path}:{line_number}")

    try:
        # Use gh API for inline comments
        comment_data = json.dumps({
            "body": body,
            "commit_id": "HEAD",
            "path": file_path,
            "line": line_number
        })

        exit_code, output, stderr = _run_command(
            ["gh", "api",
             f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
             "--input", "-"],
            cwd=repo_path,
            input_text=comment_data
        )

        result = json.loads(output)

        logger.info(f"Inline comment posted: ID {result.get('id')}")

        return {
            "status": "success",
            "comment_id": str(result.get("id")),
            "url": result.get("html_url", "")
        }

    except CLIError as e:
        logger.error(f"Failed to post inline comment: {e}")
        raise
    except json.JSONDecodeError as e:
        raise CLIError(f"Failed to parse API response: {e}") from e


def get_review_status(pr_number: int, repo_path: Optional[str] = None) -> Dict[str, Any]:
    """Get review and comment status for a pull request.

    Args:
        pr_number: Pull request number
        repo_path: Path to repository (optional, uses current dir if None)

    Returns:
        Dictionary containing:
            - status: 'success' or 'error'
            - reviews: List of review objects
            - comments: Count and list of comments
            - review_requests: List of requested reviewers
            - stats: Review statistics

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI command fails
    """
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    logger.info(f"Fetching review status for PR #{pr_number}")

    try:
        exit_code, output, stderr = _run_command(
            ["gh", "pr", "view", str(pr_number),
             "--json", "reviews,comments,reviewRequests"],
            cwd=repo_path
        )

        data = json.loads(output)

        reviews = data.get("reviews", [])
        comments = data.get("comments", [])
        review_requests = data.get("reviewRequests", [])

        # Calculate statistics
        approved = sum(1 for r in reviews if r.get("state") == "APPROVED")
        changes_requested = sum(1 for r in reviews if r.get("state") == "CHANGES_REQUESTED")
        commented = sum(1 for r in reviews if r.get("state") == "COMMENTED")
        pending = sum(1 for r in reviews if r.get("state") == "PENDING")

        stats = {
            "total_reviews": len(reviews),
            "approved": approved,
            "changes_requested": changes_requested,
            "commented": commented,
            "pending": pending,
            "total_comments": len(comments),
            "pending_reviewers": len(review_requests)
        }

        logger.info(f"Review status: {approved} approved, {changes_requested} changes requested")

        return {
            "status": "success",
            "reviews": reviews,
            "comments": comments,
            "review_requests": review_requests,
            "stats": stats
        }

    except CLIError as e:
        logger.error(f"Failed to get review status: {e}")
        raise
    except json.JSONDecodeError as e:
        raise CLIError(f"Failed to parse review status: {e}") from e


def auto_review(
    repo_path: str,
    pr_number: int,
    auto_comment: bool = True,
    auto_approve: bool = False
) -> Dict[str, Any]:
    """Automatically review a pull request.

    Args:
        repo_path: Path to the repository
        pr_number: Pull request number
        auto_comment: Whether to post review comments
        auto_approve: Whether to approve PR if no blockers

    Returns:
        Dictionary containing:
            - status: 'success' or 'error'
            - review_complete: Boolean indicating if review is complete
            - approved: Boolean indicating if PR was approved
            - issues: List of issues found
            - comments_posted: List of posted comment URLs

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI or claude command fails
        ReviewParseError: If review output parsing fails
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError("Repository path cannot be empty")
    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    logger.info(f"Starting auto-review for PR #{pr_number}")

    comments_posted = []
    approved = False

    try:
        # Step 1: Perform review
        review_result = review_pr(repo_path, pr_number)
        issues = review_result.get("issues", [])

        # Count issues by severity
        blockers = [i for i in issues if i["severity"] == "BLOCKER"]
        warnings = [i for i in issues if i["severity"] == "WARNING"]
        infos = [i for i in issues if i["severity"] == "INFO"]

        # Step 2: Post comments if enabled
        if auto_comment and (blockers or warnings or infos):
            # Build review comment body
            comment_parts = [
                f"## 🤖 Automated Code Review",
                f"",
                f"**Summary:** {len(blockers)} blocker(s), {len(warnings)} warning(s), {len(infos)} info(s)",
                f""
            ]

            # Group issues by severity
            if blockers:
                comment_parts.append("### 🚫 Blockers")
                for issue in blockers:
                    loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                    comment_parts.append(f"- **{loc}** {issue['message']}")
                    if issue.get('suggestion'):
                        comment_parts.append(f"  - Suggestion: {issue['suggestion']}")
                comment_parts.append("")

            if warnings:
                comment_parts.append("### ⚠️ Warnings")
                for issue in warnings:
                    loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                    comment_parts.append(f"- **{loc}** {issue['message']}")
                    if issue.get('suggestion'):
                        comment_parts.append(f"  - Suggestion: {issue['suggestion']}")
                comment_parts.append("")

            if infos:
                comment_parts.append("### ℹ️ Info")
                for issue in infos:
                    loc = f" `{issue['file_path']}:{issue['line_number']}`" if issue.get('file_path') else ""
                    comment_parts.append(f"- **{loc}** {issue['message']}")
                comment_parts.append("")

            comment_body = '\n'.join(comment_parts)

            comment_result = post_review_comment(repo_path, pr_number, comment_body)
            comments_posted.append(comment_result.get("url", ""))

            # Post inline comments for issues with file/line info
            for issue in issues:
                if issue.get('file_path') and issue.get('line_number'):
                    inline_body = f"**{issue['severity']}**: {issue['message']}"
                    if issue.get('suggestion'):
                        inline_body += f"\n\nSuggestion: {issue['suggestion']}"

                    try:
                        inline_result = post_inline_comment(
                            repo_path,
                            pr_number,
                            inline_body,
                            issue['file_path'],
                            issue['line_number']
                        )
                        comments_posted.append(inline_result.get("url", ""))
                    except CLIError as e:
                        logger.warning(f"Failed to post inline comment: {e}")

        # Step 3: Approve if enabled and no blockers
        if auto_approve and not blockers:
            try:
                exit_code, output, stderr = _run_command(
                    ["gh", "pr", "review", str(pr_number), "--approve"],
                    cwd=repo_path
                )
                approved = True
                logger.info(f"Auto-approved PR #{pr_number}")
            except CLIError as e:
                logger.warning(f"Failed to auto-approve PR: {e}")

        review_complete = not blockers

        logger.info(f"Auto-review complete: approved={approved}, complete={review_complete}")

        return {
            "status": "success",
            "review_complete": review_complete,
            "approved": approved,
            "issues": issues,
            "comments_posted": comments_posted,
            "summary": {
                "blockers": len(blockers),
                "warnings": len(warnings),
                "infos": len(infos)
            }
        }

    except (CLIError, ReviewParseError) as e:
        logger.error(f"Auto-review failed: {e}")
        raise
