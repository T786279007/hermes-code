#!/usr/bin/env python3
"""
🧪 实验 10：PR 创建 + 合并
验证 Claude Code 能通过 gh CLI 创建 PR，Codex 能审查 PR。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import hermes_exp1_task_registry as reg

WORKDIR = "/tmp/hermes-exp10"
REPO = "T786279007/hermes-exp-test"
BRANCH = "exp/hermes-test-10"
PROXY = "http://127.0.0.1:7897"
GH = os.path.expanduser("~/.local/bin/gh")

def setup_repo():
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    
    # Clone with proxy
    env = {**os.environ, "https_proxy": PROXY, "http_proxy": PROXY}
    
    subprocess.run(["git", "clone", f"https://github.com/{REPO}.git", "."], env=env, capture_output=True)
    subprocess.run(["git", "config", "user.email", "zoe@hermes.local"], capture_output=True)
    subprocess.run(["git", "config", "user.name", "Zoe"], capture_output=True)
    
    # Create branch
    subprocess.run(["git", "checkout", "-b", BRANCH], capture_output=True)
    
    print(f"✅ Cloned {REPO} and created branch {BRANCH}")


def run_claude_create_pr():
    """让 Claude Code 修改代码 + 创建 PR"""
    task_text = (
        f"Step 1: Create README.md with '# Hermes Exp Test' and commit: git add -A && git commit -m 'init'\n"
        f"Step 2: Push main: git -c http.proxy={PROXY} push -u origin main\n"
        f"Step 3: Create branch: git checkout -b {BRANCH}\n"
        f"Step 4: Add a CONTRIBUTING.md with basic guidelines (3-5 bullet points)\n"
        f"Step 5: Commit: git add -A && git commit -m 'docs: add CONTRIBUTING.md'\n"
        f"Step 6: Push branch: git -c http.proxy={PROXY} push -u origin {BRANCH}\n"
        f"Step 7: Create PR: HTTPS_PROXY={PROXY} gh pr create --title 'docs: add contributing guidelines' --body 'Added CONTRIBUTING.md'\n\n"
        f"IMPORTANT: Always use git -c http.proxy={PROXY} for push operations.\n"
        f"Always use HTTPS_PROXY={PROXY} for gh commands."
    )
    
    PROGRESS = f"{WORKDIR}/.progress"
    runner_script = "/home/txs/hermes-agent/claude-task-runner.py"
    
    cmd = [
        "python3", runner_script,
        "--task", task_text,
        "--cwd", WORKDIR,
        "--progress", PROGRESS,
        "--timeout", "300",
    ]
    
    print(f"🚀 Starting Claude Code (PR creation)...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    last_pos = 0
    start = time.time()
    pr_events = []
    
    while proc.poll() is None:
        elapsed = time.time() - start
        if elapsed > 300:
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
                    summary = event.get("summary", "")
                    if any(kw in summary.lower() for kw in ["git", "push", "pr", "commit", "contributing"]):
                        print(f"  [{elapsed:5.1f}s] 🌿 {summary}")
                        pr_events.append(summary)
                
                elif etype == "result":
                    status = event.get("status", "unknown")
                    print(f"  [{elapsed:5.1f}s] {'✅' if status == 'success' else '❌'} Claude Code {status}")
        
        time.sleep(0.5)
    
    proc.wait()
    elapsed = time.time() - start
    return elapsed, proc.returncode, pr_events


def check_pr_exists():
    """Check if PR was created"""
    env = {**os.environ, "https_proxy": PROXY, "http_proxy": PROXY, "PATH": f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}"}
    result = subprocess.run(
        [GH, "pr", "list", "--head", BRANCH, "--json", "number,title,url,state", "--repo", REPO],
        capture_output=True, text=True, env=env
    )
    if result.returncode == 0 and result.stdout.strip():
        prs = json.loads(result.stdout)
        return prs[0] if prs else None
    return None


def cleanup_pr(pr_number):
    """Close PR and delete branch"""
    env = {**os.environ, "https_proxy": PROXY, "http_proxy": PROXY, "PATH": f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}"}
    subprocess.run([GH, "pr", "close", str(pr_number), "--repo", REPO], capture_output=True, env=env)
    subprocess.run(["git", "push", "origin", f"--delete", BRANCH], capture_output=True, cwd=WORKDIR)
    print(f"  🧹 Cleaned up PR #{pr_number} and branch")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 10：PR 创建")
    print("=" * 60)
    
    setup_repo()
    elapsed, rc, events = run_claude_create_pr()
    
    print(f"\n--- 验证 ---")
    pr = check_pr_exists()
    
    if pr:
        print(f"  ✅ PR 创建成功!")
        print(f"  #{pr['number']}: {pr['title']}")
        print(f"  URL: {pr['url']}")
        print(f"  State: {pr['state']}")
        
        # Cleanup
        cleanup_pr(pr["number"])
    else:
        print(f"  ⚠️ PR 未创建 (Claude Code rc={rc})")
        print(f"  PR 相关事件: {events}")
    
    print(f"  耗时: {elapsed:.1f}s")
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 10 完成 {'✅' if pr else '⚠️'}")
    print("=" * 60)
