#!/usr/bin/env python3
"""Tests for review_pr module using unittest.mock."""

import unittest
from unittest.mock import patch, Mock, MagicMock, call
import json
from review_pr import (
    review_pr,
    post_review_comment,
    post_inline_comment,
    get_review_status,
    auto_review,
    _run_command,
    _parse_review_output,
    ReviewIssue,
    Severity,
    ReviewPRError,
    CLIError,
    ValidationError,
    ReviewParseError
)


class TestRunCommand(unittest.TestCase):
    """Tests for _run_command helper function."""

    @patch('review_pr.subprocess.run')
    def test_run_command_success(self, mock_run):
        """Test successful command execution."""
        mock_run.return_value = Mock(
            stdout="output",
            stderr="",
            returncode=0
        )

        exit_code, stdout, stderr = _run_command(["echo", "test"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "output")
        mock_run.assert_called_once()

    @patch('review_pr.subprocess.run')
    def test_run_command_failure(self, mock_run):
        """Test command failure handling."""
        mock_run.return_value = Mock(
            stdout="",
            stderr="error occurred",
            returncode=1
        )

        with self.assertRaises(CLIError) as context:
            _run_command(["false"])

        self.assertIn("error occurred", str(context.exception))

    @patch('review_pr.subprocess.run')
    def test_run_command_with_input(self, mock_run):
        """Test command with stdin input."""
        mock_run.return_value = Mock(
            stdout="result",
            stderr="",
            returncode=0
        )

        exit_code, stdout, stderr = _run_command(
            ["cat"],
            input_text="test input"
        )

        self.assertEqual(stdout, "result")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[1]['input'], "test input")

    @patch('review_pr.subprocess.run')
    def test_run_command_with_cwd(self, mock_run):
        """Test command with working directory."""
        mock_run.return_value = Mock(
            stdout="output",
            stderr="",
            returncode=0
        )

        exit_code, stdout, stderr = _run_command(
            ["pwd"],
            cwd="/tmp"
        )

        self.assertEqual(stdout, "output")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[1]['cwd'], "/tmp")

    @patch('review_pr.subprocess.run')
    def test_run_command_not_found(self, mock_run):
        """Test command not found error."""
        mock_run.side_effect = FileNotFoundError("command not found")

        with self.assertRaises(CLIError) as context:
            _run_command(["nonexistent"])

        self.assertIn("not found", str(context.exception))


class TestParseReviewOutput(unittest.TestCase):
    """Tests for _parse_review_output function."""

    def test_parse_blocker_issue(self):
        """Test parsing BLOCKER severity issue."""
        output = """BLOCKER: Potential null pointer dereference
@src/main.py:42
Suggestion: Add null check before dereferencing"""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.BLOCKER)
        self.assertEqual(issues[0].file_path, "src/main.py")
        self.assertEqual(issues[0].line_number, 42)
        self.assertIn("null pointer", issues[0].message)
        self.assertIn("null check", issues[0].suggestion)

    def test_parse_warning_issue(self):
        """Test parsing WARNING severity issue."""
        output = """WARNING: Unused variable 'temp'
@src/utils.py:15"""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.WARNING)
        self.assertEqual(issues[0].file_path, "src/utils.py")
        self.assertEqual(issues[0].line_number, 15)

    def test_parse_info_issue(self):
        """Test parsing INFO severity issue."""
        output = """INFO: Consider using list comprehension
@src/process.py:78"""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.INFO)

    def test_parse_multiple_issues(self):
        """Test parsing multiple issues of different severities."""
        output = """BLOCKER: Security vulnerability in SQL query
@src/db.py:100
Suggestion: Use parameterized queries

WARNING: Missing error handling
@src/api.py:25

INFO: Variable naming convention
@src/helpers.py:10"""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 3)
        self.assertEqual(issues[0].severity, Severity.BLOCKER)
        self.assertEqual(issues[1].severity, Severity.WARNING)
        self.assertEqual(issues[2].severity, Severity.INFO)

    def test_parse_issue_without_file_location(self):
        """Test parsing issue without file/line information."""
        output = """BLOCKER: General architecture concern

The current design may not scale well."""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 1)
        self.assertIsNone(issues[0].file_path)
        self.assertIsNone(issues[0].line_number)
        self.assertIn("architecture", issues[0].message)

    def test_parse_empty_output(self):
        """Test parsing empty output."""
        issues, summary = _parse_review_output("")

        self.assertEqual(len(issues), 0)
        self.assertEqual(summary, "")

    def test_parse_summary_only(self):
        """Test parsing output with only summary (no structured issues)."""
        output = """Overall the code looks good.
Consider adding more tests in the future."""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 0)
        self.assertIn("looks good", summary)

    def test_parse_multiline_message(self):
        """Test parsing issue with multi-line message."""
        output = """BLOCKER: Memory leak detected
@src/mem.py:50

The allocated memory is never freed.
This will cause issues over time.

Suggestion: Use context manager or explicit free"""

        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 1)
        self.assertIn("Memory leak", issues[0].message)
        self.assertIn("never freed", issues[0].message)

    def test_parse_malformed_severity(self):
        """Test parsing with malformed severity marker."""
        output = """INVALID: This is not a valid severity marker"""

        # Malformed severities are treated as summary text, not errors
        issues, summary = _parse_review_output(output)

        self.assertEqual(len(issues), 0)
        self.assertIn("INVALID", summary)


class TestReviewPR(unittest.TestCase):
    """Tests for review_pr function."""

    @patch('review_pr._run_command')
    def test_review_pr_success(self, mock_run):
        """Test successful PR review."""
        # Mock gh pr diff
        mock_run.side_effect = [
            (0, "diff --git a/file.py b/file.py\n+new line", ""),  # diff
            (0, "BLOCKER: Issue found\n@file.py:1", "")  # claude output
        ]

        result = review_pr("/path/to/repo", 123)

        self.assertEqual(result["status"], "success")
        self.assertIsInstance(result["issues"], list)
        self.assertEqual(mock_run.call_count, 2)

    @patch('review_pr._run_command')
    def test_review_pr_empty_diff(self, mock_run):
        """Test review with empty diff."""
        mock_run.return_value = (0, "", "")

        result = review_pr("/path/to/repo", 123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["issues"]), 0)
        self.assertEqual(result["summary"], "No changes to review")

    def test_review_pr_invalid_repo_path(self):
        """Test review with empty repository path."""
        with self.assertRaises(ValidationError) as context:
            review_pr("", 123)

        self.assertIn("path", str(context.exception).lower())

    def test_review_pr_invalid_pr_number(self):
        """Test review with invalid PR number."""
        with self.assertRaises(ValidationError):
            review_pr("/path", 0)

        with self.assertRaises(ValidationError):
            review_pr("/path", -1)

    @patch('review_pr._run_command')
    def test_review_pr_cli_error(self, mock_run):
        """Test review with CLI error."""
        mock_run.side_effect = CLIError("gh command failed")

        with self.assertRaises(CLIError):
            review_pr("/path/to/repo", 123)

    @patch('review_pr._run_command')
    def test_review_pr_custom_model(self, mock_run):
        """Test review with custom model."""
        mock_run.side_effect = [
            (0, "diff content", ""),
            (0, "INFO: Suggestion", "")
        ]

        result = review_pr("/path/to/repo", 123, model="claude-opus-4-6")

        self.assertEqual(result["status"], "success")
        # Check that claude command was called
        second_call = mock_run.call_args_list[1]
        self.assertIn("claude", second_call[0][0])


class TestPostReviewComment(unittest.TestCase):
    """Tests for post_review_comment function."""

    @patch('review_pr._run_command')
    def test_post_review_comment_success(self, mock_run):
        """Test successful review comment posting."""
        mock_run.return_value = (
            0,
            "https://github.com/owner/repo/pull/123/comment/456",
            ""
        )

        result = post_review_comment("/path/to/repo", 123, "LGTM!")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["comment_id"], "456")
        self.assertIn("github.com", result["url"])

    def test_post_review_comment_empty_body(self):
        """Test posting comment with empty body."""
        with self.assertRaises(ValidationError):
            post_review_comment("/path", 123, "")

    @patch('review_pr._run_command')
    def test_post_review_comment_cli_error(self, mock_run):
        """Test comment posting with CLI error."""
        mock_run.side_effect = CLIError("Authentication failed")

        with self.assertRaises(CLIError):
            post_review_comment("/path", 123, "Comment")


class TestPostInlineComment(unittest.TestCase):
    """Tests for post_inline_comment function."""

    @patch('review_pr._run_command')
    def test_post_inline_comment_success(self, mock_run):
        """Test successful inline comment posting."""
        mock_run.return_value = (
            0,
            json.dumps({"id": 789, "html_url": "https://github.com/repo/pull/1/comment/789"}),
            ""
        )

        result = post_inline_comment("/path", 1, "Fix this", "src/file.py", 42)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["comment_id"], "789")

    def test_post_inline_comment_invalid_params(self):
        """Test inline comment with invalid parameters."""
        with self.assertRaises(ValidationError):
            post_inline_comment("", 1, "Comment", "file.py", 1)

        with self.assertRaises(ValidationError):
            post_inline_comment("/path", 0, "Comment", "file.py", 1)

        with self.assertRaises(ValidationError):
            post_inline_comment("/path", 1, "", "file.py", 1)

        with self.assertRaises(ValidationError):
            post_inline_comment("/path", 1, "Comment", "", 1)

        with self.assertRaises(ValidationError):
            post_inline_comment("/path", 1, "Comment", "file.py", 0)

    @patch('review_pr._run_command')
    def test_post_inline_comment_invalid_json(self, mock_run):
        """Test inline comment with invalid JSON response."""
        mock_run.return_value = (0, "not json", "")

        with self.assertRaises(CLIError) as context:
            post_inline_comment("/path", 1, "Comment", "file.py", 1)

        self.assertIn("parse", str(context.exception).lower())

    @patch('review_pr._run_command')
    def test_post_inline_comment_fallback(self, mock_run):
        """Test that fallback path executes when primary call fails."""
        mock_run.side_effect = [
            CLIError("repos endpoint failed"),
            (
                0,
                json.dumps({"id": 987, "html_url": "https://github.com/repo/pull/1/comment/987"}),
                ""
            )
        ]

        result = post_inline_comment("/path", 1, "Fallback comment", "file.py", 10)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["comment_id"], "987")
        self.assertEqual(mock_run.call_count, 2)
        first_call_args = mock_run.call_args_list[0][0][0]
        second_call_args = mock_run.call_args_list[1][0][0]
        self.assertIn("repos/{owner}/{repo}", first_call_args[2])
        self.assertIn("pulls/1/comments", second_call_args[2])


class TestGetReviewStatus(unittest.TestCase):
    """Tests for get_review_status function."""

    @patch('review_pr._run_command')
    def test_get_review_status_success(self, mock_run):
        """Test successful review status retrieval."""
        mock_data = {
            "reviews": [
                {"state": "APPROVED", "author": {"login": "user1"}},
                {"state": "CHANGES_REQUESTED", "author": {"login": "user2"}},
                {"state": "COMMENTED", "author": {"login": "user3"}}
            ],
            "comments": [
                {"id": 1, "body": "Comment 1"},
                {"id": 2, "body": "Comment 2"}
            ],
            "reviewRequests": [
                {"reviewer": {"login": "user4"}}
            ]
        }
        mock_run.return_value = (0, json.dumps(mock_data), "")

        result = get_review_status(123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["reviews"]), 3)
        self.assertEqual(result["stats"]["approved"], 1)
        self.assertEqual(result["stats"]["changes_requested"], 1)
        self.assertEqual(result["stats"]["commented"], 1)
        self.assertEqual(result["stats"]["total_comments"], 2)
        self.assertEqual(result["stats"]["pending_reviewers"], 1)

    def test_get_review_status_invalid_pr_number(self):
        """Test review status with invalid PR number."""
        with self.assertRaises(ValidationError):
            get_review_status(0)

        with self.assertRaises(ValidationError):
            get_review_status(-1)

    @patch('review_pr._run_command')
    def test_get_review_status_with_repo_path(self, mock_run):
        """Test review status with custom repo path."""
        mock_run.return_value = (0, json.dumps({"reviews": [], "comments": [], "reviewRequests": []}), "")

        result = get_review_status(123, repo_path="/custom/path")

        self.assertEqual(result["status"], "success")
        # Check that cwd was passed
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        self.assertEqual(call_kwargs['cwd'], "/custom/path")

    @patch('review_pr._run_command')
    def test_get_review_status_invalid_json(self, mock_run):
        """Test review status with invalid JSON response."""
        mock_run.return_value = (0, "invalid json", "")

        with self.assertRaises(CLIError) as context:
            get_review_status(123)

        self.assertIn("parse", str(context.exception).lower())


class TestAutoReview(unittest.TestCase):
    """Tests for auto_review function."""

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_with_blockers(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review with blocker issues."""
        mock_review.return_value = {
            "status": "success",
            "issues": [
                {
                    "severity": "BLOCKER",
                    "file_path": "src/file.py",
                    "line_number": 10,
                    "message": "Critical issue",
                    "suggestion": "Fix it"
                }
            ],
            "summary": "Review complete"
        }
        mock_post_comment.return_value = {"url": "https://github.com/comment/1"}

        result = auto_review("/path", 123, auto_comment=True, auto_approve=False)

        self.assertFalse(result["approved"])
        self.assertFalse(result["review_complete"])
        self.assertEqual(result["summary"]["blockers"], 1)
        self.assertTrue(len(result["comments_posted"]) > 0)

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_approve_without_blockers(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review approves when no blockers."""
        mock_review.return_value = {
            "status": "success",
            "issues": [
                {
                    "severity": "INFO",
                    "file_path": "src/file.py",
                    "line_number": 5,
                    "message": "Minor suggestion",
                    "suggestion": None
                }
            ],
            "summary": "Good code"
        }
        mock_post_comment.return_value = {"url": "https://github.com/comment/1"}
        mock_run.return_value = (0, "Approved", "")

        result = auto_review("/path", 123, auto_comment=True, auto_approve=True)

        self.assertTrue(result["approved"])
        self.assertTrue(result["review_complete"])
        self.assertEqual(result["summary"]["blockers"], 0)

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_no_comments(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review without posting comments."""
        mock_review.return_value = {
            "status": "success",
            "issues": [
                {"severity": "WARNING", "file_path": None, "line_number": None,
                 "message": "Warning", "suggestion": None}
            ],
            "summary": "Review"
        }

        result = auto_review("/path", 123, auto_comment=False, auto_approve=False)

        self.assertEqual(len(result["comments_posted"]), 0)
        mock_post_comment.assert_not_called()

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_inline_comments(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review posts inline comments."""
        mock_review.return_value = {
            "status": "success",
            "issues": [
                {
                    "severity": "WARNING",
                    "file_path": "src/file.py",
                    "line_number": 20,
                    "message": "Check this",
                    "suggestion": "Use better approach"
                }
            ],
            "summary": "Review"
        }
        mock_post_comment.return_value = {"url": "https://github.com/comment/main"}
        mock_inline.return_value = {"url": "https://github.com/comment/inline"}

        result = auto_review("/path", 123, auto_comment=True, auto_approve=False)

        # Should have both main and inline comment
        self.assertTrue(len(result["comments_posted"]) >= 1)
        mock_inline.assert_called_once()

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_mixed_severities(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review with mixed severity issues."""
        mock_review.return_value = {
            "status": "success",
            "issues": [
                {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
                 "message": "B1", "suggestion": None},
                {"severity": "WARNING", "file_path": "b.py", "line_number": 2,
                 "message": "W1", "suggestion": None},
                {"severity": "WARNING", "file_path": "c.py", "line_number": 3,
                 "message": "W2", "suggestion": None},
                {"severity": "INFO", "file_path": "d.py", "line_number": 4,
                 "message": "I1", "suggestion": None}
            ],
            "summary": "Multiple issues"
        }
        mock_post_comment.return_value = {"url": "https://github.com/comment/1"}

        result = auto_review("/path", 123, auto_comment=True, auto_approve=False)

        self.assertEqual(result["summary"]["blockers"], 1)
        self.assertEqual(result["summary"]["warnings"], 2)
        self.assertEqual(result["summary"]["infos"], 1)
        self.assertFalse(result["review_complete"])

    @patch('review_pr._run_command')
    @patch('review_pr.post_inline_comment')
    @patch('review_pr.post_review_comment')
    @patch('review_pr.review_pr')
    def test_auto_review_approve_failure(
        self, mock_review, mock_post_comment, mock_inline, mock_run
    ):
        """Test auto review handles approval failure gracefully."""
        mock_review.return_value = {
            "status": "success",
            "issues": [],
            "summary": "Clean code"
        }
        mock_run.side_effect = CLIError("Approval failed")

        result = auto_review("/path", 123, auto_comment=False, auto_approve=True)

        # Should not crash, but approval should fail
        self.assertFalse(result["approved"])

    def test_auto_review_invalid_params(self):
        """Test auto review with invalid parameters."""
        with self.assertRaises(ValidationError):
            auto_review("", 123)

        with self.assertRaises(ValidationError):
            auto_review("/path", 0)

    @patch('review_pr.review_pr')
    def test_auto_review_review_failure(self, mock_review):
        """Test auto review when review function fails."""
        mock_review.side_effect = CLIError("Review failed")

        with self.assertRaises(CLIError):
            auto_review("/path", 123)


class TestReviewIssue(unittest.TestCase):
    """Tests for ReviewIssue dataclass."""

    def test_review_issue_creation(self):
        """Test creating a ReviewIssue."""
        issue = ReviewIssue(
            severity=Severity.BLOCKER,
            file_path="src/file.py",
            line_number=42,
            message="Test issue",
            suggestion="Fix it"
        )

        self.assertEqual(issue.severity, Severity.BLOCKER)
        self.assertEqual(issue.file_path, "src/file.py")
        self.assertEqual(issue.line_number, 42)
        self.assertEqual(issue.message, "Test issue")
        self.assertEqual(issue.suggestion, "Fix it")

    def test_review_issue_without_suggestion(self):
        """Test ReviewIssue without optional suggestion."""
        issue = ReviewIssue(
            severity=Severity.WARNING,
            file_path=None,
            line_number=None,
            message="Warning message"
        )

        self.assertIsNone(issue.file_path)
        self.assertIsNone(issue.line_number)
        self.assertIsNone(issue.suggestion)


class TestExceptions(unittest.TestCase):
    """Tests for custom exceptions."""

    def test_exception_hierarchy(self):
        """Test exception class hierarchy."""
        error = ReviewPRError("test")
        self.assertIsInstance(error, Exception)

        cli_error = CLIError("cli error")
        self.assertIsInstance(cli_error, ReviewPRError)

        validation_error = ValidationError("validation error")
        self.assertIsInstance(validation_error, ReviewPRError)

        parse_error = ReviewParseError("parse error")
        self.assertIsInstance(parse_error, ReviewPRError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
