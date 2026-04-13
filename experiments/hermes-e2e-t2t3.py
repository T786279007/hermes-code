#!/usr/bin/env python3
"""Hermes v2 Full-Flow E2E - Tasks 2 & 3."""
import sys, logging, subprocess, time
sys.path.insert(0, "/home/txs")

from hermes.task_registry import TaskRegistry
from hermes.router import TaskRouter
from hermes.outbox import Outbox
from hermes.reconciler import Reconciler
from hermes.executor import TaskExecutor
from hermes.config import DB_PATH, WORKTREE_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

registry = TaskRegistry(DB_PATH)
executor = TaskExecutor(registry, TaskRouter(), Outbox(registry), Reconciler(registry))

# Also verify T1 result
t1 = registry.get_task("feat-create-config-parser-py-that-p-20260413-162714")
wt1 = WORKTREE_BASE / t1["id"]
print(f"T1 config_parser: status={t1['status']} exit={t1.get('exit_code')} worktree={wt1.exists()}")
if wt1.exists():
    r = subprocess.run(["git","log","--oneline","-1"], cwd=str(wt1), capture_output=True, text=True, timeout=10)
    print(f"  commit: {r.stdout.strip()}")
    r = subprocess.run(["python3","-m","pytest","-q","--override-ini=addopts="], cwd=str(wt1), capture_output=True, text=True, timeout=30)
    print(f"  tests: {r.stdout.strip().split(chr(10))[-1] if r.stdout.strip() else 'FAIL'}")

TASKS = [
    ("T2: http_client",
     "Create http_client.py with retry, timeout, and circuit breaker. Requirements: "
     "1) HttpClient class with get/post/put/delete methods 2) Automatic retry with exponential backoff "
     "3) Circuit breaker (closed/open/half-open states) 4) Request/response logging "
     "5) Create test_http_client.py with 15+ tests using unittest.mock. Run the tests."),
    ("T3: file_watcher",
     "Create file_watcher.py that monitors directory changes. Requirements: "
     "1) FileWatcher class with watch(path, patterns, callback) 2) Support glob patterns "
     "3) Debounce rapid changes 4) Event types: created, modified, deleted "
     "5) Create test_file_watcher.py with 12+ tests. Run the tests."),
]

results = [t1["status"]]  # start with T1

for name, desc in TASKS:
    print(f"\n{'='*60}\n  {name}\n{'='*60}")
    t0 = time.time()
    task = executor.submit(desc, override="claude-code")
    elapsed = time.time() - t0
    tid = task["id"]
    status = task["status"]
    wt_path = WORKTREE_BASE / tid
    has_wt = wt_path.exists()
    has_commit = False
    if has_wt:
        r = subprocess.run(["git","log","--oneline","-1"], cwd=str(wt_path), capture_output=True, text=True, timeout=10)
        has_commit = r.returncode == 0 and "feat:" in r.stdout
    test_out = ""
    if has_wt:
        r = subprocess.run(["python3","-m","pytest","-q","--override-ini=addopts="], cwd=str(wt_path), capture_output=True, text=True, timeout=30)
        test_out = r.stdout.strip().split("\n")[-1] if r.stdout.strip() else "FAIL"
    results.append(status)
    ok = "✅" if status == "done" else "❌"
    print(f"  {ok} {name}: {status} in {elapsed:.0f}s | wt={'✅' if has_wt else '❌'} commit={'✅' if has_commit else '❌'} | {test_out}")

# Summary
print(f"\n{'='*60}\n  FULL-FLOW E2E SUMMARY\n{'='*60}")
names = ["T1: config_parser", "T2: http_client", "T3: file_watcher"]
for n, s in zip(names, results):
    print(f"  {'✅' if s=='done' else '❌'} {n}: {s}")
print(f"\n  Overall: {'✅ ALL PASS' if all(s=='done' for s in results) else '❌ FAILURES'}")

# Unit tests
print(f"\n{'='*60}\n  HERMES UNIT TESTS\n{'='*60}")
r = subprocess.run(["python3","-m","pytest","tests/","-q","--override-ini=addopts="], cwd="/home/txs/hermes", capture_output=True, text=True, timeout=60)
print(f"  {r.stdout.strip().split(chr(10))[-1] if r.stdout.strip() else 'FAIL'}")
