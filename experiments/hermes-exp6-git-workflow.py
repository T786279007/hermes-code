#!/usr/bin/env python3
"""
🧪 实验 6：Agent 自动创建 git branch + commit
验证 Claude Code 能在 worktree 中自动创建分支、提交代码。
不依赖 gh CLI，验证 git 操作部分。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg

WORKDIR = "/tmp/hermes-exp6"
PROGRESS = f"{WORKDIR}/.progress"
TASK_ID = "exp6-git-workflow"
BRANCH = "feature/calculator"


def setup_repo():
    """创建测试仓库"""
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    
    # Init repo with main branch
    subprocess.run(["git", "init", "-b", "main"], capture_output=True)
    subprocess.run(["git", "config", "user.email", "zoe@hermes.local"], capture_output=True)
    subprocess.run(["git", "config", "user.name", "Zoe"], capture_output=True)
    
    # Create initial README
    with open(f"{WORKDIR}/README.md", "w") as f:
        f.write("# Test Project\n\nA test project for Hermes experiments.\n")
    subprocess.run(["git", "add", "."], capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], capture_output=True)
    
    print(f"✅ Repo initialized at {WORKDIR}")


def run_claude_git_task():
    """让 Claude Code 在新分支上创建功能并提交"""
    # Init registry
    if reg.DB_PATH.exists():
        reg.DB_PATH.unlink()
    reg.init_db()
    
    reg.create_task(
        id=TASK_ID,
        agent="claude-code",
        model="glm-5-turbo",
        description="在 feature 分支上创建计算器模块",
        prompt="创建计算器",
        worktree=WORKDIR,
        branch=BRANCH,
    )
    reg.transition_status(TASK_ID, reg.STATUS_RUNNING)
    
    task_text = (
        "You are working in a git repository. Do the following steps:\n"
        "1. Create a new branch called 'feature/calculator' from main\n"
        "2. Create calculator.py with add, subtract, multiply, divide functions\n"
        "3. Create test_calculator.py with comprehensive tests\n"
        "4. Run the tests and ensure they all pass\n"
        "5. Add all files and commit with message 'feat: add calculator module'\n"
        "6. Show me the git log"
    )
    
    runner_script = "/home/txs/hermes-agent/claude-task-runner.py"
    cmd = [
        "python3", runner_script,
        "--task", task_text,
        "--cwd", WORKDIR,
        "--progress", PROGRESS,
        "--timeout", "180",
    ]
    
    print(f"🚀 Starting Claude Code...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    last_pos = 0
    tool_count = 0
    start = time.time()
    
    while proc.poll() is None:
        elapsed = time.time() - start
        if elapsed > 180:
            proc.kill()
            break
        
        if os.path.exists(PROGRESS):
            with open(PROGRESS, "r") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                last_pos = f.tell()
            
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except:
                    continue
                
                etype = event.get("type")
                if etype == "tool":
                    tool_count = event.get("tool_num", tool_count)
                    summary = event.get("summary", "")
                    reg.update_progress(TASK_ID, tool_count=tool_count, last_event=summary)
                    if "git" in summary.lower() or "commit" in summary.lower() or "branch" in summary.lower():
                        print(f"  [{elapsed:5.1f}s] 🌿 {summary}")
                    else:
                        print(f"  [{elapsed:5.1f}s] {summary}")
                
                elif etype == "result":
                    status = event.get("status", "unknown")
                    duration_ms = event.get("duration_ms", 0)
                    reg.update_progress(TASK_ID, last_event=f"{'✅' if status=='success' else '❌'} ({duration_ms/1000:.1f}s)")
        
        time.sleep(0.5)
    
    proc.wait()
    elapsed = time.time() - start
    
    # Check git state
    print(f"\n--- Git 状态检查 ---")
    
    # Current branch
    result = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=WORKDIR)
    current_branch = result.stdout.strip()
    print(f"  当前分支: {current_branch}")
    
    # Git log
    result = subprocess.run(["git", "log", "--oneline", "-5"], capture_output=True, text=True, cwd=WORKDIR)
    print(f"  Git log:\n{result.stdout}")
    
    # Files
    files = [f for f in os.listdir(WORKDIR) if not f.startswith(".") and f != "__pycache__"]
    print(f"  文件: {files}")
    
    # Verify
    print(f"\n--- 验证 ---")
    ok = True
    
    if current_branch == BRANCH:
        print(f"  ✅ 分支正确: {BRANCH}")
    else:
        print(f"  ⚠️ 分支: {current_branch} (期望: {BRANCH})")
    
    if "calculator.py" in files:
        print(f"  ✅ calculator.py 已创建")
    else:
        print(f"  ❌ calculator.py 未创建")
        ok = False
    
    if "test_calculator.py" in files:
        print(f"  ✅ test_calculator.py 已创建")
    else:
        print(f"  ❌ test_calculator.py 未创建")
        ok = False
    
    log_lines = result.stdout.strip().split("\n")
    if any("calculator" in l.lower() or "feat" in l.lower() for l in log_lines):
        print(f"  ✅ Git commit 包含功能描述")
    else:
        print(f"  ⚠️ 未找到功能 commit")
    
    # Update registry
    if proc.returncode == 0:
        reg.transition_status(TASK_ID, reg.STATUS_DONE)
        reg.update_checks(TASK_ID, checks_pr_created=False)  # No gh, so no PR
    
    t = reg.get_task(TASK_ID)
    print(f"\n--- 注册表 ---")
    print(f"  状态: {t['status']}")
    print(f"  工具调用: {t['progress_tool_count']}")
    print(f"  耗时: {elapsed:.1f}s")
    
    return ok, elapsed


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 6：Claude Code 自动 git workflow")
    print("=" * 60)
    
    setup_repo()
    ok, elapsed = run_claude_git_task()
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 6 完成 {'✅' if ok else '⚠️'}")
    print("=" * 60)
