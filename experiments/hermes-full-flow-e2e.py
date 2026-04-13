#!/usr/bin/env python3
"""Hermes v2 Full-Flow E2E Test Suite.

Runs 3 real tasks through the complete Hermes pipeline:
  submit → registry → router → sandbox → claude_runner → worktree → commit → done

Verifies: status, exit_code, worktree preserved, git commit exists.
"""
import sys
import logging
import subprocess
import time

sys.path.insert(0, "/home/txs")

from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.executor import TaskExecutor
from hermes.config import DB_PATH, WORKTREE_BASE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)

registry = TaskRegistry(DB_PATH)
router = TaskRouter()
outbox = Outbox(registry)
reconciler = Reconciler(registry)
executor = TaskExecutor(registry, router, outbox, reconciler)

TASKS = [
    {
        "name": "T1: config_parser",
        "desc": (
            "Create config_parser.py that parses INI/YAML/JSON/TOML config files. "
            "Requirements: 1) parse_config(file_path) auto-detects format and returns dict "
            "2) validate_config(schema, config) validates against a schema dict "
            "3) merge_configs(base, override) deep-merges two dicts "
            "4) Create test_config_parser.py with 12+ tests. Run the tests."
        ),
        "agent": "claude-code",
    },
    {
        "name": "T2: http_client",
        "desc": (
            "Create http_client.py with retry, timeout, and circuit breaker. "
            "Requirements: 1) HttpClient class with get/post/put/delete methods "
            "2) Automatic retry with exponential backoff (configurable) "
            "3) Circuit breaker (closed/open/half-open states) "
            "4) Request/response logging "
            "5) Create test_http_client.py with 15+ tests using unittest.mock. Run the tests."
        ),
        "agent": "claude-code",
    },
    {
        "name": "T3: file_watcher",
        "desc": (
            "Create file_watcher.py that monitors directory changes. "
            "Requirements: 1) FileWatcher class with watch(path, patterns, callback) "
            "2) Support glob patterns for file matching "
            "3) Debounce rapid changes (configurable delay) "
            "4) Event types: created, modified, deleted "
            "5) Create test_file_watcher.py with 12+ tests. Run the tests."
        ),
        "agent": "claude-code",
    },
]

results = []

for i, task_def in enumerate(TASKS):
    name = task_def["name"]
    desc = task_def["desc"]
    agent = task_def["agent"]

    print(f"\n{'='*60}")
    print(f"  TASK {i+1}/3: {name}")
    print(f"{'='*60}")

    t0 = time.time()

    # Submit through Hermes executor
    task = executor.submit(desc, override=agent)

    elapsed = time.time() - t0
    task_id = task["id"]
    status = task["status"]
    exit_code = task.get("exit_code", "N/A")
    result = (task.get("result") or "")[:200]

    # Verify worktree
    wt_path = WORKTREE_BASE / task_id
    has_worktree = wt_path.exists()

    # Verify git commit
    has_commit = False
    commit_msg = ""
    if has_worktree:
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            has_commit = r.returncode == 0 and "feat:" in r.stdout
            commit_msg = r.stdout.strip()
        except Exception:
            pass

    # Run tests in worktree
    test_result = "SKIPPED"
    test_pass = 0
    test_total = 0
    if has_worktree:
        try:
            r = subprocess.run(
                ["python3", "-m", "pytest", "-v", "--override-ini=addopts=", "-q"],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            lines = r.stdout.strip().split("\n")
            for line in lines:
                if "passed" in line.lower():
                    test_result = "PASS"
                    parts = line.split()
                    for p in parts:
                        if "passed" in p:
                            test_pass = int(p.replace("passed", "").replace(",", "").strip())
                    for p in parts:
                        try:
                            test_total = int(p)
                            break
                        except ValueError:
                            pass
                    break
            if r.returncode != 0:
                test_result = f"FAIL: {r.stderr[-200:]}"
        except Exception as e:
            test_result = f"ERROR: {e}"

    result_data = {
        "name": name,
        "task_id": task_id[:40],
        "status": status,
        "exit_code": exit_code,
        "elapsed": f"{elapsed:.0f}s",
        "worktree": has_worktree,
        "commit": has_commit,
        "commit_msg": commit_msg,
        "test": test_result,
        "test_pass": test_pass,
        "test_total": test_total,
        "result_preview": result,
    }
    results.append(result_data)

    # Print result
    ok = "✅" if status == "done" and has_worktree else "❌"
    print(f"\n  {ok} {name}")
    print(f"     Status:    {status}")
    print(f"     Exit code: {exit_code}")
    print(f"     Time:      {elapsed:.0f}s")
    print(f"     Worktree:  {'✅' if has_worktree else '❌'} {wt_path}")
    print(f"     Commit:    {'✅' if has_commit else '❌'} {commit_msg}")
    print(f"     Tests:     {test_result}")
    print(f"     Preview:   {result}")

# Summary
print(f"\n{'='*60}")
print(f"  FULL-FLOW E2E SUMMARY")
print(f"{'='*60}")

all_pass = all(r["status"] == "done" for r in results)
all_wt = all(r["worktree"] for r in results)
all_commit = all(r["commit"] for r in results)
all_tests = all("PASS" in r["test"] for r in results)

print(f"  Tasks completed: {sum(1 for r in results if r['status']=='done')}/{len(results)}")
print(f"  Worktrees kept:  {sum(1 for r in results if r['worktree'])}/{len(results)}")
print(f"  Commits exist:   {sum(1 for r in results if r['commit'])}/{len(results)}")
print(f"  Tests passed:    {sum(1 for r in results if 'PASS' in r['test'])}/{len(results)}")
print(f"  Total test count: {sum(r['test_pass'] for r in results)}")

overall = "✅ ALL PASS" if (all_pass and all_wt and all_commit and all_tests) else "❌ FAILURES"
print(f"\n  OVERALL: {overall}")

# Now run the full hermes test suite
print(f"\n{'='*60}")
print(f"  HERMES UNIT TESTS (104 expected)")
print(f"{'='*60}")
r = subprocess.run(
    ["python3", "-m", "pytest", "tests/", "-v", "--override-ini=addopts="],
    cwd="/home/txs/hermes",
    capture_output=True,
    text=True,
    timeout=60,
)
# Get last 3 lines
lines = r.stdout.strip().split("\n")
for line in lines[-3:]:
    print(f"  {line}")

sys.exit(0 if overall == "✅ ALL PASS" else 1)
