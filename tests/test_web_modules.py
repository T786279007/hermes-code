"""Tests for web modules: execution_log, command_queue, web_api."""

import os
import sys
import tempfile
import sqlite3
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from task_registry import TaskRegistry
from execution_log import ExecutionLog
from command_queue import CommandQueue


@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def registry(db_path):
    """Create a TaskRegistry with temp DB."""
    return TaskRegistry(db_path)


@pytest.fixture
def exec_log(registry):
    """Create an ExecutionLog."""
    return ExecutionLog(registry)


@pytest.fixture
def cmd_queue(registry):
    """Create a CommandQueue."""
    return CommandQueue(registry)


class TestExecutionLog:
    """Tests for ExecutionLog module."""

    def test_append_and_list(self, exec_log):
        exec_log.append("task-1", "Started", level="info", source="system")
        exec_log.append("task-1", "Warning!", level="warn")
        exec_log.append("task-1", "Error!", level="error", metadata={"code": 500})

        logs = exec_log.list_logs("task-1")
        assert len(logs) == 3
        assert logs[0]["message"] == "Started"
        assert logs[0]["level"] == "info"
        assert logs[0]["source"] == "system"
        assert logs[2]["metadata"] is not None

    def test_since_id(self, exec_log):
        for i in range(5):
            exec_log.append("task-2", f"msg-{i}")

        logs = exec_log.list_logs("task-2", since_id=3)
        assert len(logs) == 2
        assert logs[0]["message"] == "msg-3"

    def test_filter_by_level(self, exec_log):
        exec_log.append("task-3", "info msg", level="info")
        exec_log.append("task-3", "error msg", level="error")
        exec_log.append("task-3", "info msg2", level="info")

        errors = exec_log.list_logs("task-3", level="error")
        assert len(errors) == 1
        assert errors[0]["message"] == "error msg"

    def test_count(self, exec_log):
        exec_log.append("task-4", "a")
        exec_log.append("task-4", "b")
        assert exec_log.count_by_task("task-4") == 2
        assert exec_log.count_by_task("nonexistent") == 0

    def test_latest_id(self, exec_log):
        exec_log.append("task-5", "first")
        exec_log.append("task-5", "second")
        assert exec_log.get_latest_id("task-5") == 2

    def test_delete(self, exec_log):
        exec_log.append("task-6", "will be deleted")
        assert exec_log.count_by_task("task-6") == 1
        exec_log.delete_by_task("task-6")
        assert exec_log.count_by_task("task-6") == 0


class TestCommandQueue:
    """Tests for CommandQueue module."""

    def _create_task(self, registry):
        registry.create_task("task-cmd-1", "test task", "claude-code")

    def test_enqueue_and_list(self, registry, cmd_queue):
        self._create_task(registry)
        cmd_id = cmd_queue.enqueue("task-cmd-1", "inject", {"text": "hello"})
        assert cmd_id >= 1

        cmds = cmd_queue.list_commands("task-cmd-1")
        assert len(cmds) == 1
        assert cmds[0]["command"] == "inject"
        assert cmds[0]["status"] == "pending"

    def test_invalid_command(self, registry, cmd_queue):
        self._create_task(registry)
        with pytest.raises(ValueError, match="Invalid command"):
            cmd_queue.enqueue("task-cmd-1", "blow_up")

    def test_nonexistent_task(self, cmd_queue):
        with pytest.raises(ValueError, match="not found"):
            cmd_queue.enqueue("no-such-task", "cancel")

    def test_consume(self, registry, cmd_queue):
        self._create_task(registry)
        cmd_queue.enqueue("task-cmd-1", "cancel")
        cmd_queue.enqueue("task-cmd-1", "inject", {"text": "fix it"})

        pending = cmd_queue.consume("task-cmd-1")
        assert len(pending) == 2
        assert pending[0]["status"] == "pending"

        # Second consume should be empty
        empty = cmd_queue.consume("task-cmd-1")
        assert len(empty) == 0

    def test_mark_executed(self, registry, cmd_queue):
        self._create_task(registry)
        cmd_id = cmd_queue.enqueue("task-cmd-1", "cancel")
        cmd_queue.consume("task-cmd-1")
        assert cmd_queue.mark_executed(cmd_id, "cancelled OK")

    def test_has_pending(self, registry, cmd_queue):
        self._create_task(registry)
        assert not cmd_queue.has_pending("task-cmd-1")
        cmd_queue.enqueue("task-cmd-1", "cancel")
        assert cmd_queue.has_pending("task-cmd-1")
        cmd_queue.consume("task-cmd-1")
        assert not cmd_queue.has_pending("task-cmd-1")

    def test_all_valid_commands(self, cmd_queue):
        from command_queue import VALID_COMMANDS
        assert "cancel" in VALID_COMMANDS
        assert "inject" in VALID_COMMANDS
        assert "retry" in VALID_COMMANDS
        assert "pause" in VALID_COMMANDS


class TestTaskRegistryMigration:
    """Tests for new schema migration."""

    def test_new_tables_exist(self, registry):
        conn = registry._connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        conn.close()
        assert "execution_logs" in table_names
        assert "command_queue" in table_names
        assert "web_sessions" in table_names

    def test_new_columns_exist(self, registry):
        registry.create_task("migration-test", "test", "claude-code")
        task = registry.get_task("migration-test")
        assert "feishu_thread_id" in task
        assert "feishu_message_id" in task
        assert "web_submitted" in task
        assert "cmd_count" in task

    def test_idempotent_migration(self, registry):
        """Migration should be safe to run multiple times."""
        # Second init shouldn't fail
        from task_registry import TaskRegistry
        r2 = TaskRegistry(registry._db_path)
        assert r2 is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
