#!/usr/bin/env python3
"""Tests for dual_review module."""

import unittest
from unittest.mock import patch, Mock, MagicMock
import time
import threading

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dual_review import (
    dual_review,
    dual_auto_review,
    _merge_issues,
    _max_severity,
    _build_summary,
    ReviewerResult,
    MergedIssue,
)


class TestMaxSeverity(unittest.TestCase):
    """Tests for _max_severity helper."""

    def test_blocker_vs_warning(self):
        self.assertEqual(_max_severity("BLOCKER", "WARNING"), "BLOCKER")

    def test_warning_vs_info(self):
        self.assertEqual(_max_severity("WARNING", "INFO"), "WARNING")

    def test_same_severity(self):
        self.assertEqual(_max_severity("BLOCKER", "BLOCKER"), "BLOCKER")

    def test_none_handling(self):
        self.assertEqual(_max_severity(None, "INFO"), "INFO")
        self.assertEqual(_max_severity("BLOCKER", None), "BLOCKER")

    def test_both_none(self):
        self.assertEqual(_max_severity(None, None), None)


class TestMergeIssues(unittest.TestCase):
    """Tests for _merge_issues function."""

    def test_merge_empty(self):
        result = _merge_issues([], [])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["consensus_count"], 0)

    def test_merge_claude_only(self):
        claude = [
            {"severity": "WARNING", "file_path": "a.py", "line_number": 10,
             "message": "W1", "suggestion": None}
        ]
        result = _merge_issues(claude, [])
        self.assertEqual(len(result["issues"]), 1)
        self.assertFalse(result["issues"][0]["consensus"])
        self.assertEqual(result["consensus_count"], 0)

    def test_merge_codex_only(self):
        codex = [
            {"severity": "INFO", "file_path": "b.py", "line_number": 20,
             "message": "I1", "suggestion": "fix"}
        ]
        result = _merge_issues([], codex)
        self.assertEqual(len(result["issues"]), 1)
        self.assertFalse(result["issues"][0]["consensus"])
        self.assertEqual(result["consensus_count"], 0)

    def test_merge_consensus(self):
        claude = [
            {"severity": "BLOCKER", "file_path": "c.py", "line_number": 30,
             "message": "Bug here", "suggestion": "Fix"}
        ]
        codex = [
            {"severity": "BLOCKER", "file_path": "c.py", "line_number": 30,
             "message": "Critical bug", "suggestion": "Patch it"}
        ]
        result = _merge_issues(claude, codex)
        self.assertEqual(len(result["issues"]), 1)
        self.assertTrue(result["issues"][0]["consensus"])
        self.assertEqual(result["consensus_count"], 1)
        self.assertEqual(result["issues"][0]["claude_severity"], "BLOCKER")
        self.assertEqual(result["issues"][0]["codex_severity"], "BLOCKER")
        self.assertEqual(result["issues"][0]["severity"], "BLOCKER")

    def test_merge_severity_escalation(self):
        """When Claude says WARNING but Codex says BLOCKER, result is BLOCKER."""
        claude = [
            {"severity": "WARNING", "file_path": "d.py", "line_number": 1,
             "message": "Meh", "suggestion": None}
        ]
        codex = [
            {"severity": "BLOCKER", "file_path": "d.py", "line_number": 1,
             "message": "Critical", "suggestion": "Fix"}
        ]
        result = _merge_issues(claude, codex)
        self.assertEqual(result["issues"][0]["severity"], "BLOCKER")

    def test_merge_multiple_issues_mixed(self):
        claude = [
            {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
             "message": "B1", "suggestion": None},
            {"severity": "WARNING", "file_path": "b.py", "line_number": 2,
             "message": "W1", "suggestion": None},
        ]
        codex = [
            {"severity": "WARNING", "file_path": "a.py", "line_number": 1,
             "message": "B1-codex", "suggestion": None},
            {"severity": "INFO", "file_path": "c.py", "line_number": 3,
             "message": "I1", "suggestion": None},
        ]
        result = _merge_issues(claude, codex)
        # a.py:1 matches (consensus), b.py:2 claude-only, c.py:3 codex-only
        self.assertEqual(len(result["issues"]), 3)
        self.assertEqual(result["consensus_count"], 1)

        # Consensus issues should sort first
        self.assertTrue(result["issues"][0]["consensus"])

    def test_merge_sorting_order(self):
        claude = [
            {"severity": "INFO", "file_path": "a.py", "line_number": 1,
             "message": "I1", "suggestion": None},
            {"severity": "BLOCKER", "file_path": "b.py", "line_number": 2,
             "message": "B1", "suggestion": None},
        ]
        codex = [
            {"severity": "BLOCKER", "file_path": "b.py", "line_number": 2,
             "message": "B1", "suggestion": None},
        ]
        result = _merge_issues(claude, codex)
        # Consensus blocker first
        self.assertTrue(result["issues"][0]["consensus"])
        self.assertEqual(result["issues"][0]["severity"], "BLOCKER")


class TestBuildSummary(unittest.TestCase):
    """Tests for _build_summary function."""

    def test_basic_summary(self):
        claude = ReviewerResult(
            reviewer="claude-code", status="success", model="claude-sonnet-4-6",
            elapsed_seconds=12.3,
        )
        codex = ReviewerResult(
            reviewer="codex", status="success", model="gpt-5.4",
            elapsed_seconds=8.1,
        )
        merged = {"issues": [], "consensus_count": 0}
        summary = _build_summary(claude, codex, merged)
        self.assertIn("Dual Review Summary", summary)
        self.assertIn("claude-sonnet-4-6", summary)
        self.assertIn("gpt-5.4", summary)

    def test_summary_with_errors(self):
        claude = ReviewerResult(
            reviewer="claude-code", status="error", model="claude-sonnet-4-6",
            elapsed_seconds=300.0, error="Timeout after 300s",
        )
        codex = ReviewerResult(
            reviewer="codex", status="success", model="gpt-5.4",
            elapsed_seconds=15.0,
        )
        merged = {"issues": [], "consensus_count": 0}
        summary = _build_summary(claude, codex, merged)
        self.assertIn("Timeout", summary)


class TestDualReview(unittest.TestCase):
    """Tests for dual_review function."""

    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_dual_review_both_success(self, mock_claude, mock_codex):
        """Test both reviewers succeed."""
        mock_claude.return_value = {
            "status": "success",
            "issues": [
                {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
                 "message": "B1", "suggestion": "Fix"}
            ],
            "summary": "Claude found 1 issue",
        }
        mock_codex.return_value = {
            "status": "success",
            "reviewer": "codex",
            "model": "gpt-5.4",
            "issues": [
                {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
                 "message": "B1", "suggestion": "Fix"}
            ],
            "summary": "Codex found 1 issue",
        }

        result = dual_review("/path/to/repo", 123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["claude"]["status"], "success")
        self.assertEqual(result["codex"]["status"], "success")
        self.assertEqual(result["claude"]["issue_count"], 1)
        self.assertEqual(result["codex"]["issue_count"], 1)
        self.assertEqual(result["consensus_count"], 1)

    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_dual_review_claude_fails(self, mock_claude, mock_codex):
        """Test Claude fails, Codex succeeds."""
        mock_claude.side_effect = Exception("Claude not available")
        mock_codex.return_value = {
            "status": "success",
            "reviewer": "codex",
            "model": "gpt-5.4",
            "issues": [
                {"severity": "WARNING", "file_path": "x.py", "line_number": 5,
                 "message": "W1", "suggestion": None}
            ],
            "summary": "Codex only",
        }

        result = dual_review("/path/to/repo", 123)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["claude"]["status"], "error")
        self.assertEqual(result["codex"]["status"], "success")
        self.assertEqual(result["consensus_count"], 0)

    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_dual_review_both_fail(self, mock_claude, mock_codex):
        """Test both reviewers fail."""
        mock_claude.side_effect = Exception("Claude error")
        mock_codex.side_effect = Exception("Codex error")

        result = dual_review("/path/to/repo", 123)

        self.assertEqual(result["status"], "error")

    def test_dual_review_invalid_params(self):
        from dual_review import ValidationError
        with self.assertRaises(ValidationError):
            dual_review("", 123)
        with self.assertRaises(ValidationError):
            dual_review("/path", 0)

    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_dual_review_empty_diff(self, mock_claude, mock_codex):
        """Test both reviewers find nothing."""
        mock_claude.return_value = {
            "status": "success", "issues": [],
            "summary": "No changes to review",
        }
        mock_codex.return_value = {
            "status": "success", "reviewer": "codex", "model": "gpt-5.4",
            "issues": [], "summary": "No changes to review",
        }

        result = dual_review("/path/to/repo", 123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["merged_issues"]), 0)
        self.assertEqual(result["consensus_count"], 0)


class TestDualAutoReview(unittest.TestCase):
    """Tests for dual_auto_review function."""

    @patch('dual_review.post_review_comment')
    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_auto_review_posts_comment(self, mock_claude, mock_codex, mock_comment):
        """Test auto review posts merged comment."""
        mock_claude.return_value = {
            "status": "success",
            "issues": [
                {"severity": "WARNING", "file_path": "a.py", "line_number": 1,
                 "message": "W1", "suggestion": "fix"}
            ],
            "summary": "Claude: 1 warning",
        }
        mock_codex.return_value = {
            "status": "success", "reviewer": "codex", "model": "gpt-5.4",
            "issues": [
                {"severity": "INFO", "file_path": "b.py", "line_number": 2,
                 "message": "I1", "suggestion": None}
            ],
            "summary": "Codex: 1 info",
        }
        mock_comment.return_value = {"url": "https://github.com/comment/1"}

        result = dual_auto_review("/path", 123, auto_comment=True, auto_approve=False)

        self.assertTrue(len(result["comments_posted"]) > 0)
        mock_comment.assert_called_once()

    @patch('dual_review.post_review_comment')
    @patch('dual_review._run_command')
    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_auto_approve_no_blockers(self, mock_claude, mock_codex, mock_run, mock_comment):
        """Test auto approve when no consensus blockers."""
        mock_claude.return_value = {
            "status": "success",
            "issues": [
                {"severity": "WARNING", "file_path": "a.py", "line_number": 1,
                 "message": "W1", "suggestion": None}
            ],
            "summary": "OK",
        }
        mock_codex.return_value = {
            "status": "success", "reviewer": "codex", "model": "gpt-5.4",
            "issues": [],
            "summary": "OK",
        }
        mock_comment.return_value = {"url": "https://github.com/comment/1"}
        mock_run.return_value = (0, "Approved", "")

        result = dual_auto_review("/path", 123, auto_comment=True, auto_approve=True)

        self.assertTrue(result["approved"])
        self.assertTrue(result["review_complete"])

    @patch('dual_review.post_review_comment')
    @patch('dual_review.codex_review_pr')
    @patch('dual_review.review_pr')
    def test_no_approve_with_consensus_blocker(self, mock_claude, mock_codex, mock_comment):
        """Test no auto approve when there's a consensus blocker."""
        mock_claude.return_value = {
            "status": "success",
            "issues": [
                {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
                 "message": "Critical", "suggestion": None}
            ],
            "summary": "Blocker",
        }
        mock_codex.return_value = {
            "status": "success", "reviewer": "codex", "model": "gpt-5.4",
            "issues": [
                {"severity": "BLOCKER", "file_path": "a.py", "line_number": 1,
                 "message": "Critical", "suggestion": None}
            ],
            "summary": "Blocker",
        }
        mock_comment.return_value = {"url": "https://github.com/comment/1"}

        result = dual_auto_review("/path", 123, auto_comment=True, auto_approve=True)

        self.assertFalse(result["approved"])
        self.assertFalse(result["review_complete"])


class TestReviewerResult(unittest.TestCase):
    """Tests for ReviewerResult dataclass."""

    def test_defaults(self):
        r = ReviewerResult(reviewer="test", status="success")
        self.assertEqual(r.issues, [])
        self.assertEqual(r.summary, "")
        self.assertEqual(r.model, "")
        self.assertIsNone(r.error)
        self.assertEqual(r.elapsed_seconds, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
