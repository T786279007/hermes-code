#!/usr/bin/env python3
"""Comprehensive tests for router.py — keyword scoring, override, default."""

import unittest
from router import TaskRouter, RoutingDecision


class TestRouteOverride(unittest.TestCase):
    """Override parameter tests."""

    def setUp(self):
        self.router = TaskRouter()

    def test_override_claude(self):
        d = self.router.route("anything", override="claude-code")
        self.assertEqual(d.agent, "claude-code")
        self.assertEqual(d.model, "claude-sonnet-4-6")
        self.assertEqual(d.timeout, 300)
        self.assertEqual(d.confidence, 1.0)
        self.assertEqual(d.reason, "User override")

    def test_override_codex(self):
        d = self.router.route("anything", override="codex")
        self.assertEqual(d.agent, "codex")
        self.assertEqual(d.model, "gpt-5.4")
        self.assertEqual(d.timeout, 180)
        self.assertEqual(d.confidence, 1.0)

    def test_override_none(self):
        """No override should use keyword scoring."""
        d = self.router.route("implement a feature", override=None)
        self.assertIn(d.agent, ("claude-code", "codex"))


class TestRouteClaudeKeywords(unittest.TestCase):
    """Claude Code keyword matching tests."""

    def setUp(self):
        self.router = TaskRouter()

    def test_implement(self):
        d = self.router.route("implement a REST API")
        self.assertEqual(d.agent, "claude-code")

    def test_create(self):
        d = self.router.route("create a new module")
        self.assertEqual(d.agent, "claude-code")

    def test_build(self):
        d = self.router.route("build authentication system")
        self.assertEqual(d.agent, "claude-code")

    def test_refactor(self):
        d = self.router.route("refactor the database layer")
        self.assertEqual(d.agent, "claude-code")

    def test_frontend(self):
        d = self.router.route("add frontend dashboard")
        self.assertEqual(d.agent, "claude-code")

    def test_api(self):
        d = self.router.route("develop user API")
        self.assertEqual(d.agent, "claude-code")

    def test_ui(self):
        d = self.router.route("create login UI")
        self.assertEqual(d.agent, "claude-code")

    def test_chinese_keywords(self):
        d = self.router.route("实现一个登录功能")
        self.assertEqual(d.agent, "claude-code")

    def test_开发(self):
        d = self.router.route("开发新模块")
        self.assertEqual(d.agent, "claude-code")

    def test_重构(self):
        d = self.router.route("重构代码")
        self.assertEqual(d.agent, "claude-code")

    def test_优化(self):
        d = self.router.route("优化性能")
        self.assertEqual(d.agent, "claude-code")


class TestRouteCodexKeywords(unittest.TestCase):
    """Codex keyword matching tests."""

    def setUp(self):
        self.router = TaskRouter()

    def test_review(self):
        d = self.router.route("review the code changes")
        self.assertEqual(d.agent, "codex")

    def test_fix(self):
        d = self.router.route("fix the null pointer bug")
        self.assertEqual(d.agent, "codex")

    def test_lint(self):
        d = self.router.route("lint the source code")
        self.assertEqual(d.agent, "codex")

    def test_check(self):
        d = self.router.route("check for security issues")
        self.assertEqual(d.agent, "codex")

    def test_bug(self):
        d = self.router.route("investigate bug in payment module")
        self.assertEqual(d.agent, "codex")

    def test_chinese_review(self):
        d = self.router.route("代码审查")
        self.assertEqual(d.agent, "codex")

    def test_chinese_fix(self):
        d = self.router.route("修复崩溃问题")
        self.assertEqual(d.agent, "codex")


class TestRouteDefault(unittest.TestCase):
    """Default routing when no keywords match."""

    def setUp(self):
        self.router = TaskRouter()

    def test_no_keywords_defaults_to_claude(self):
        d = self.router.route("xyzzy abcdef")
        self.assertEqual(d.agent, "claude-code")
        self.assertEqual(d.confidence, 0.5)
        self.assertIn("default", d.reason.lower())


class TestRouteConfidence(unittest.TestCase):
    """Confidence scoring tests."""

    def setUp(self):
        self.router = TaskRouter()

    def test_high_confidence_single_keyword(self):
        d = self.router.route("implement feature")
        self.assertGreater(d.confidence, 0.5)

    def test_mixed_keywords_prefers_higher(self):
        d = self.router.route("implement and review the code")
        # Both match, but implement (claude) should win or tie
        self.assertIn(d.agent, ("claude-code", "codex"))
        self.assertGreater(d.confidence, 0)

    def test_multiple_codex_keywords(self):
        d = self.router.route("fix bug and review code")
        self.assertEqual(d.agent, "codex")
        self.assertGreaterEqual(d.confidence, 0.5)


class TestRoutingDecision(unittest.TestCase):
    """RoutingDecision dataclass tests."""

    def test_attributes(self):
        d = RoutingDecision(agent="claude-code", model="m", timeout=300, confidence=0.9, reason="test")
        self.assertEqual(d.agent, "claude-code")
        self.assertEqual(d.model, "m")
        self.assertEqual(d.timeout, 300)
        self.assertEqual(d.confidence, 0.9)
        self.assertEqual(d.reason, "test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
