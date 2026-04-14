# review_pr.py - Code Review Module

## Overview
Automated code review module for Hermes Agent system using Claude Code CLI and GitHub CLI.

## Features

### 1. **review_pr()** - Perform automated code review
```python
from review_pr import review_pr

result = review_pr(
    repo_path="/path/to/repo",
    pr_number=123,
    model="claude-sonnet-4-6"  # optional
)

# Returns:
{
    "status": "success",
    "issues": [
        {
            "severity": "BLOCKER",  # BLOCKER, WARNING, INFO
            "file_path": "src/file.py",
            "line_number": 42,
            "message": "Issue description",
            "suggestion": "Fix recommendation"
        }
    ],
    "summary": "Review summary text",
    "raw_output": "Full Claude output"
}
```

### 2. **post_review_comment()** - Post review comment
```python
from review_pr import post_review_comment

result = post_review_comment(
    repo_path="/path/to/repo",
    pr_number=123,
    review_body="## Review\n\nLGTM with minor suggestions."
)

# Returns:
{
    "status": "success",
    "comment_id": "456",
    "url": "https://github.com/owner/repo/pull/123/comment/456"
}
```

### 3. **post_inline_comment()** - Post inline comment
```python
from review_pr import post_inline_comment

result = post_inline_comment(
    repo_path="/path/to/repo",
    pr_number=123,
    body="Consider using list comprehension here",
    file_path="src/process.py",
    line_number=78
)
```
If the primary `gh api repos/{owner}/{repo}/pulls/<n>/comments` call raises `CLIError`, the module now logs the error and retries the relative `pulls/<n>/comments` endpoint so inline comments still land even when repository metadata is unavailable.

### 4. **get_review_status()** - Get PR review status
```python
from review_pr import get_review_status

result = get_review_status(
    pr_number=123,
    repo_path="/path/to/repo"  # optional
)

# Returns:
{
    "status": "success",
    "reviews": [...],
    "comments": [...],
    "review_requests": [...],
    "stats": {
        "total_reviews": 3,
        "approved": 1,
        "changes_requested": 1,
        "commented": 1,
        "pending": 0,
        "total_comments": 10,
        "pending_reviewers": 2
    }
}
```

### 5. **auto_review()** - One-click automated review
```python
from review_pr import auto_review

result = auto_review(
    repo_path="/path/to/repo",
    pr_number=123,
    auto_comment=True,   # Post review comments
    auto_approve=False   # Approve if no blockers
)

# Returns:
{
    "status": "success",
    "review_complete": True,  # False if BLOCKERs found
    "approved": False,         # True if auto-approved
    "issues": [...],
    "comments_posted": ["https://..."],
    "summary": {
        "blockers": 0,
        "warnings": 2,
        "infos": 1
    }
}
```

## Severity Levels

- **BLOCKER**: Critical issues that must be fixed before merge
- **WARNING**: Important issues that should be addressed
- **INFO**: Minor suggestions or nitpicks

## Exception Hierarchy

```
ReviewPRError
├── CLIError          # Command execution failures
├── ValidationError   # Invalid input parameters
└── ReviewParseError  # Output parsing failures
```

## Integration with pr_manager.py

```python
from pr_manager import list_prs, create_pr
from review_pr import auto_review

# List open PRs
prs = list_prs(state="open", limit=10)

# Auto-review each PR
for pr in prs:
    result = auto_review(
        repo_path="/path/to/repo",
        pr_number=pr["number"],
        auto_comment=True,
        auto_approve=False
    )
    print(f"PR {pr['number']}: {result['summary']}")
```

## Claude Review Output Format

Claude Code output should follow this format for best parsing:

```
BLOCKER: Issue description
@file.py:line
Suggestion: Fix recommendation

WARNING: Warning message
@file2.py:line

INFO: Minor suggestion
@file3.py:line
```

## Testing

```bash
# Run review_pr tests only
cd /home/txs/hermes
python3 -m pytest tests/test_review_pr.py -v

# Run all Hermes tests
python3 -m pytest tests/ -v
```

## Requirements

- `gh` CLI installed and authenticated
- `claude` CLI installed and configured
- Python 3.12+
- pytest (for testing)
