#!/usr/bin/env python3
"""
🧪 实验 8：自动化测试约束
验证 Agent Prompt 约束能强制 Agent 运行测试并确保通过。
这是完成定义的核心部分。
"""
import json
import os
import subprocess
import sys
import time

WORKDIR = "/tmp/hermes-exp8"


def setup_repo():
    os.makedirs(WORKDIR, exist_ok=True)
    os.chdir(WORKDIR)
    subprocess.run(["git", "init", "-b", "main"], capture_output=True)
    subprocess.run(["git", "config", "user.email", "zoe@hermes.local"], capture_output=True)
    subprocess.run(["git", "config", "user.name", "Zoe"], capture_output=True)
    
    # Create a deliberately buggy module
    with open(f"{WORKDIR}/string_utils.py", "w") as f:
        f.write("""# String utility functions (DELIBERATELY BUGGY - Agent must fix)

def reverse_string(s):
    # BUG: doesn't handle None
    return s[::-1]

def is_palindrome(s):
    # BUG: case-sensitive, doesn't strip spaces
    return s == s[::-1]

def count_vowels(s):
    # BUG: doesn't handle uppercase
    return sum(1 for c in s if c in 'aeiou')

def capitalize_words(s):
    # BUG: doesn't handle multiple spaces
    return ' '.join(w.capitalize() for w in s.split())
""")
    
    # Create tests that expose the bugs
    with open(f"{WORKDIR}/test_string_utils.py", "w") as f:
        f.write("""import pytest
from string_utils import reverse_string, is_palindrome, count_vowels, capitalize_words

def test_reverse_normal():
    assert reverse_string("hello") == "olleh"

def test_reverse_empty():
    assert reverse_string("") == ""

def test_reverse_none():
    # This should handle None gracefully
    assert reverse_string(None) == ""

def test_palindrome_normal():
    assert is_palindrome("racecar") == True

def test_palindrome_case_insensitive():
    # Should be case-insensitive
    assert is_palindrome("RaceCar") == True

def test_palindrome_with_spaces():
    # Should ignore spaces
    assert is_palindrome("A man a plan a canal Panama") == True

def test_count_vowels_lowercase():
    assert count_vowels("hello") == 2

def test_count_vowels_uppercase():
    # Should count uppercase vowels too
    assert count_vowels("HELLO") == 2

def test_count_vowels_mixed():
    assert count_vowels("Hello World") == 3

def test_capitalize_words_normal():
    assert capitalize_words("hello world") == "Hello World"

def test_capitalize_words_multiple_spaces():
    # Should preserve or normalize multiple spaces
    assert capitalize_words("hello  world") == "Hello  World"
""")
    
    subprocess.run(["git", "add", "."], capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial: buggy string utils"], capture_output=True)
    print("✅ Buggy repo initialized")


def run_agent_with_test_constraint():
    """用 Claude Code 修复 bug 并确保测试通过"""
    
    # Constraint prompt (what Zoe would generate)
    prompt = (
        "IMPORTANT CONSTRAINTS:\n"
        "- You MUST run ALL tests in test_string_utils.py before finishing\n"
        "- ALL tests MUST pass. If any test fails, fix the code and re-run\n"
        "- Do NOT modify the test file. Only fix string_utils.py\n"
        "- Create a venv with pytest if needed\n"
        "- After all tests pass, commit with message 'fix: resolve string_utils bugs'\n\n"
        "The file string_utils.py has known bugs. The tests in test_string_utils.py expose them.\n"
        "Fix the bugs so ALL tests pass."
    )
    
    PROGRESS = f"{WORKDIR}/.progress"
    
    runner_script = "/home/txs/hermes-agent/claude-task-runner.py"
    cmd = [
        "python3", runner_script,
        "--task", prompt,
        "--cwd", WORKDIR,
        "--progress", PROGRESS,
        "--timeout", "180",
    ]
    
    print(f"🚀 Starting Claude Code with test constraint...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    last_pos = 0
    test_runs = []
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
                    summary = event.get("summary", "")
                    print(f"  [{elapsed:5.1f}s] {summary}")
                    
                    if "test" in summary.lower() or "pytest" in summary.lower():
                        test_runs.append({"time": elapsed, "summary": summary})
                
                elif etype == "result":
                    status = event.get("status", "unknown")
                    print(f"  [{elapsed:5.1f}s] {'✅' if status == 'success' else '❌'} 完成")
        
        time.sleep(0.5)
    
    proc.wait()
    elapsed = time.time() - start
    
    # Now manually run tests to verify
    print(f"\n--- 手动验证测试结果 ---")
    result = subprocess.run(
        ["python3", "-m", "venv", ".venv"],
        capture_output=True, cwd=WORKDIR
    )
    subprocess.run(
        [".venv/bin/pip", "install", "-q", "pytest"],
        capture_output=True, cwd=WORKDIR
    )
    result = subprocess.run(
        [".venv/bin/python", "-m", "pytest", "-v", "test_string_utils.py"],
        capture_output=True, text=True, cwd=WORKDIR
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
    
    # Git log
    git_result = subprocess.run(
        ["git", "log", "--oneline", "-3"],
        capture_output=True, text=True, cwd=WORKDIR
    )
    print(f"Git log:\n{git_result.stdout}")
    
    # Verify
    passed = result.returncode == 0
    print(f"\n--- 验证 ---")
    print(f"  测试 {'✅ 全部通过' if passed else '❌ 有失败'}")
    print(f"  测试运行次数: {len(test_runs)}")
    print(f"  Claude Code 耗时: {elapsed:.1f}s")
    print(f"  有 commit: {'✅' if 'fix' in git_result.stdout.lower() else '⚠️'}")
    
    return passed, elapsed, test_runs


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 8：自动化测试约束")
    print("=" * 60)
    
    setup_repo()
    passed, elapsed, test_runs = run_agent_with_test_constraint()
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 8 完成 {'✅' if passed else '⚠️'}")
    print(f"  测试{'全部通过' if passed else '未全部通过'}")
    print(f"  Agent 主动运行测试 {len(test_runs)} 次")
    print("=" * 60)
