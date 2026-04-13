#!/usr/bin/env python3
"""Hermes E2E test — submit real tasks and verify the full pipeline.

Usage:
    python3 e2e_test.py [--count 2] [--tasks "task1|task2"]
"""
import argparse
import subprocess
import sys
import time

sys.path.insert(0, "/home/txs")

from hermes.executor import TaskExecutor
from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.config import DB_PATH, WORKTREE_BASE
from pathlib import Path

DEFAULT_TASKS = [
    "Create a Python module called temp_calc.py with functions: add, subtract, multiply, divide, power. Include type hints and docstrings. Create test_temp_calc.py with at least 10 tests. Run the tests.",
    "Create a Python module called text_utils.py with functions: slugify, truncate, word_count, extract_emails, mask_sensitive. Include type hints and error handling. Create test_text_utils.py with at least 10 tests. Run the tests.",
    "Create a Python module called date_utils.py with functions: format_date, parse_date, date_range, is_weekday, days_ago, days_until. Include type hints. Create test_date_utils.py with at least 10 tests. Run the tests.",
]


def main():
    parser = argparse.ArgumentParser(description="Hermes E2E test")
    parser.add_argument("--count", type=int, default=2, help="Number of tasks to submit")
    parser.add_argument("--tasks", type=str, default=None, help="Pipe-separated custom tasks")
    parser.add_argument("--timeout", type=int, default=600, help="Per-task timeout (default: 600)")
    args = parser.parse_args()

    tasks = args.tasks.split("|") if args.tasks else DEFAULT_TASKS[:args.count]

    registry = TaskRegistry(DB_PATH)
    executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))

    results = []
    for i, desc in enumerate(tasks):
        print(f"\n{'='*60}")
        print(f"  TASK {i+1}/{len(tasks)}")
        print(f"{'='*60}")

        t0 = time.time()
        task = executor.submit(desc, override="claude-code")
        elapsed = time.time() - t0

        tid = task["id"]
        status = task["status"]
        wt = Path(WORKTREE_BASE) / tid
        has_wt = wt.exists()

        # Check commit
        has_commit = False
        if has_wt:
            r = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=str(wt), capture_output=True, text=True, timeout=10
            )
            has_commit = r.returncode == 0 and "feat:" in r.stdout

        # Run tests
        test_out = "SKIP"
        if has_wt:
            r = subprocess.run(
                ["python3", "-m", "pytest", "-q", "--override-ini=addopts="],
                cwd=str(wt), capture_output=True, text=True, timeout=30
            )
            lines = r.stdout.strip().split("\n")
            test_out = lines[-1] if lines else "FAIL"

        ok = "✅" if status == "done" and has_wt else "❌"
        print(f"  {ok} {status} in {elapsed:.0f}s | wt={'✅' if has_wt else '❌'} commit={'✅' if has_commit else '❌'} | {test_out}")
        results.append(status == "done")

    # Unit tests
    print(f"\n{'='*60}\n  HERMES UNIT TESTS\n{'='*60}")
    r = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-q", "--override-ini=addopts="],
        cwd="/home/txs/hermes", capture_output=True, text=True, timeout=60
    )
    print(f"  {r.stdout.strip().split(chr(10))[-1] if r.stdout.strip() else 'FAIL'}")

    passed = sum(results)
    total = len(results)
    print(f"\n  E2E: {passed}/{total} tasks done")
    print(f"  Overall: {'✅ ALL PASS' if passed == total else '❌ FAILURES'}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
