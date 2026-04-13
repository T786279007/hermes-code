#!/usr/bin/env python3
"""Pull Request Manager using gh CLI.

This module provides functions to manage GitHub pull requests via the gh CLI tool.
"""

import subprocess
import json
from typing import Optional, List, Dict, Any


class PRManagerError(Exception):
    """Base exception for PR Manager errors."""
    pass


class CLIError(PRManagerError):
    """Exception raised when gh CLI command fails."""
    pass


class ValidationError(PRManagerError):
    """Exception raised when input validation fails."""
    pass


def _run_gh_command(args: List[str]) -> Dict[str, Any]:
    """Run a gh CLI command and return the parsed JSON output.

    Args:
        args: Command arguments to pass to gh CLI

    Returns:
        Parsed JSON response as a dictionary

    Raises:
        CLIError: If the gh CLI command fails
    """
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout:
            return json.loads(result.stdout)
        return {}
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise CLIError(f"gh CLI command failed: {error_msg}") from e
    except json.JSONDecodeError as e:
        raise CLIError(f"Failed to parse gh CLI output: {e}") from e


def create_pr(
    title: str,
    body: str,
    base: str,
    head: Optional[str] = None,
    draft: bool = False,
    repo: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new pull request.

    Args:
        title: PR title
        body: PR description/body
        base: Target branch to merge into
        head: Source branch (defaults to current branch)
        draft: Whether to create as a draft PR
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing PR information

    Raises:
        ValidationError: If required parameters are invalid
        CLIError: If gh CLI command fails
    """
    if not title or not title.strip():
        raise ValidationError("PR title cannot be empty")
    if not base or not base.strip():
        raise ValidationError("Base branch cannot be empty")
    if not body or not body.strip():
        raise ValidationError("PR body cannot be empty")

    args = ["pr", "create", "--title", title, "--body", body, "--base", base]

    if head:
        args.extend(["--head", head])
    if draft:
        args.append("--draft")
    if repo:
        args.extend(["--repo", repo])

    args.extend(["--json", "number,title,state,headRefName,baseRefName,url"])

    return _run_gh_command(args)


def check_ci(pr_number: Optional[int] = None, repo: Optional[str] = None) -> Dict[str, Any]:
    """Check CI status for a pull request.

    Args:
        pr_number: PR number (defaults to current PR)
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing CI status information

    Raises:
        CLIError: If gh CLI command fails
    """
    args = ["pr", "checks", "--json", "name,status,conclusion,startedAt,completedAt"]

    if pr_number is not None:
        args.extend([str(pr_number)])
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh_command(args)

    # Calculate overall status
    if result is not None:
        checks = result if isinstance(result, list) else result.get("checks", [])
        total = len(checks)
        completed = sum(1 for c in checks if c.get("status") == "completed")
        failed = sum(1 for c in checks if c.get("conclusion") in ["failure", "cancelled"])

        if total == 0:
            status = "pending"
        elif failed > 0:
            status = "failure"
        elif completed == total:
            status = "success"
        else:
            status = "pending"

        return {
            "status": status,
            "total_checks": total,
            "completed_checks": completed,
            "failed_checks": failed,
            "checks": checks
        }

    return {"status": "unknown", "checks": []}


def list_prs(
    state: str = "open",
    limit: int = 30,
    head: Optional[str] = None,
    base: Optional[str] = None,
    repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List pull requests.

    Args:
        state: PR state to filter by (open, closed, merged, all)
        limit: Maximum number of PRs to return
        head: Filter by head branch
        base: Filter by base branch
        repo: Repository in format "owner/repo" (optional)

    Returns:
        List of dictionaries containing PR information

    Raises:
        ValidationError: If state parameter is invalid
        CLIError: If gh CLI command fails
    """
    valid_states = ["open", "closed", "merged", "all"]
    if state not in valid_states:
        raise ValidationError(
            f"Invalid state '{state}'. Must be one of: {', '.join(valid_states)}"
        )

    args = [
        "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,state,headRefName,baseRefName,author,createdAt,updatedAt,url"
    ]

    if head:
        args.extend(["--head", head])
    if base:
        args.extend(["--base", base])
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh_command(args)

    return result if isinstance(result, list) else result.get("pullRequests", [])


def merge_pr(
    pr_number: int,
    merge_method: str = "merge",
    delete_branch: bool = False,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    repo: Optional[str] = None
) -> Dict[str, Any]:
    """Merge a pull request.

    Args:
        pr_number: PR number to merge
        merge_method: Merge method (merge, squash, rebase)
        delete_branch: Whether to delete the branch after merging
        subject: Custom commit subject (for squash)
        body: Custom commit body (for squash)
        repo: Repository in format "owner/repo" (optional)

    Returns:
        Dictionary containing merge result information

    Raises:
        ValidationError: If parameters are invalid
        CLIError: If gh CLI command fails
    """
    valid_methods = ["merge", "squash", "rebase"]
    if merge_method not in valid_methods:
        raise ValidationError(
            f"Invalid merge method '{merge_method}'. Must be one of: {', '.join(valid_methods)}"
        )

    if pr_number <= 0:
        raise ValidationError("PR number must be positive")

    args = ["pr", "merge", str(pr_number), "--merge-method", merge_method]

    if delete_branch:
        args.append("--delete-branch")
    if subject:
        args.extend(["--subject", subject])
    if body:
        args.extend(["--body", body])
    if repo:
        args.extend(["--repo", repo])

    args.extend(["--json", "merged,mergedAt,mergedBy"])

    return _run_gh_command(args)
