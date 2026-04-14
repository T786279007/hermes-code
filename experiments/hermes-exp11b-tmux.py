#!/usr/bin/env python3
"""
🧪 实验 11b：tmux send-keys 中途干预（实测）
用真实的 tmux + Claude Code 验证 Elvis 方案中的 send-keys 干预。
"""
import json
import os
import subprocess
import sys
import time

TMUX = os.path.expanduser("~/.local/bin/tmux")
WORKDIR = "/tmp/hermes-exp11b"
SESSION = "hermes-test"


def setup():
    os.makedirs(WORKDIR, exist_ok=True)
    with open(f"{WORKDIR}/calc.py", "w") as f:
        f.write("""# Simple calculator with a bug
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a / b  # BUG: no zero division check
""")
    with open(f"{WORKDIR}/test_calc.py", "w") as f:
        f.write("""import pytest
from calc import add, subtract, multiply, divide

def test_add():
    assert add(2, 3) == 5

def test_subtract():
    assert subtract(10, 3) == 7

def test_multiply():
    assert multiply(4, 5) == 20

def test_divide():
    assert divide(10, 2) == 5

def test_divide_by_zero():
    assert divide(10, 0) == float('inf')
""")
    print("✅ 项目已创建（含 bug）")


def kill_tmux():
    subprocess.run([TMUX, "kill-session", "-t", SESSION], capture_output=True)
    time.sleep(0.5)


def tmux_send(session, pane, text, enter=True):
    """Send text to tmux pane"""
    cmd = [TMUX, "send-keys", "-t", f"{session}:{pane}", text]
    subprocess.run(cmd, capture_output=True)
    if enter:
        subprocess.run([TMUX, "send-keys", "-t", f"{session}:{pane}", "Enter"], capture_output=True)


def tmux_capture(session, pane, lines=50):
    """Capture pane content"""
    result = subprocess.run(
        [TMUX, "capture-pane", "-t", f"{session}:{pane}", "-p", "-S", f"-{lines}"],
        capture_output=True, text=True
    )
    return result.stdout


def test_tmux_send_keys_claude():
    """
    核心实验：在 tmux 中启动 Claude Code，运行中注入新指令
    """
    print("\n" + "=" * 60)
    print("📋 tmux send-keys + Claude Code 中途干预")
    print("=" * 60)
    
    kill_tmux()
    
    # Step 1: Start Claude Code in tmux
    task1 = "Add a power(base, exp) function to calc.py"
    cmd = f"claude --permission-mode bypassPermissions --print '{task1}'"
    
    # Create tmux session with the command
    subprocess.run([TMUX, "new-session", "-d", "-s", SESSION,
                   "-x", "200", "-y", "50",
                   "-c", WORKDIR, cmd], capture_output=True)
    
    print(f"  🚀 tmux 启动 Claude Code：{task1}")
    time.sleep(3)
    
    # Wait for Claude Code to finish task 1
    start = time.time()
    task1_done = False
    last_output = ""
    
    for i in range(60):  # max 60s wait
        output = tmux_capture(SESSION, 0)
        if output != last_output:
            new_lines = output[len(last_output):]
            if new_lines.strip():
                print(f"  [{time.time()-start:5.1f}s] {new_lines.strip()[:100]}")
            last_output = output
        
        # Check if Claude Code finished (look for prompt or end of output)
        if "Added" in output or "created" in output.lower() or "wrote" in output.lower():
            if time.time() - start > 5:  # give it at least 5s
                task1_done = True
                break
        
        # Check if process exited
        result = subprocess.run(
            [TMUX, "list-panes", "-t", SESSION, "-F", "#{pane_dead}"],
            capture_output=True, text=True
        )
        if "1" in result.stdout:
            task1_done = True
            break
        
        time.sleep(1)
    
    elapsed = time.time() - start
    
    if task1_done:
        print(f"  ✅ 任务 1 完成 ({elapsed:.1f}s)")
    else:
        print(f"  ⚠️ 任务 1 未完成 ({elapsed:.1f}s)")
    
    # Verify task 1 result
    with open(f"{WORKDIR}/calc.py") as f:
        content = f.read()
    t1_ok = "power" in content
    print(f"  {'✅' if t1_ok else '❌'} power 函数 {'已添加' if t1_ok else '未添加'}")
    
    # Step 2: send-keys to inject task 2
    # Since --print mode doesn't read stdin, we need to start a new claude instance
    print(f"\n  📝 tmux send-keys 注入任务 2...")
    task2 = "Fix the divide function to handle zero division. Run tests."
    tmux_send(SESSION, 0, f"claude --permission-mode bypassPermissions --print '{task2}'")
    
    time.sleep(3)
    
    # Wait for task 2
    start2 = time.time()
    last_output2 = tmux_capture(SESSION, 0)
    task2_done = False
    
    for i in range(120):  # max 120s
        output = tmux_capture(SESSION, 0)
        if output != last_output2:
            new_lines = output[len(last_output2):]
            if new_lines.strip():
                print(f"  [{time.time()-start2:5.1f}s] {new_lines.strip()[:100]}")
            last_output2 = output
        
        if "fixed" in output.lower() or "handle" in output.lower() and "zero" in output.lower():
            if time.time() - start2 > 5:
                task2_done = True
                break
        
        time.sleep(1)
    
    elapsed2 = time.time() - start2
    
    # Verify task 2
    with open(f"{WORKDIR}/calc.py") as f:
        content = f.read()
    t2_ok = "ZeroDivisionError" in content or "b == 0" in content or "zero" in content.lower()
    print(f"  {'✅' if t2_ok else '❌'} divide 零除处理 {'已修复' if t2_ok else '未修复'}")
    
    kill_tmux()
    
    return t1_ok, t2_ok, elapsed, elapsed2


def test_tmux_send_keys_ctrl_c():
    """
    测试 Ctrl+C 中断正在运行的命令
    """
    print("\n" + "=" * 60)
    print("📋 tmux send-keys Ctrl+C 中断")
    print("=" * 60)
    
    kill_tmux()
    
    # Start a long-running process
    subprocess.run([TMUX, "new-session", "-d", "-s", SESSION,
                   "-x", "200", "-y", "50",
                   "-c", WORKDIR, "sleep 100"], capture_output=True)
    time.sleep(1)
    
    # Verify it's running
    output = tmux_capture(SESSION, 0)
    running = "sleep" in output
    print(f"  {'✅' if running else '❌'} sleep 100 已启动")
    
    # Send Ctrl+C
    subprocess.run([TMUX, "send-keys", "-t", f"{SESSION}:0", "C-c"], capture_output=True)
    time.sleep(1)
    
    # Verify it was interrupted
    output = tmux_capture(SESSION, 0)
    interrupted = "sleep" not in output or "Terminated" in output or "$" in output
    print(f"  {'✅' if interrupted else '❌'} Ctrl+C 中断 {'成功' if interrupted else '失败'}")
    
    kill_tmux()
    return interrupted


def test_tmux_vs_exec_pty():
    """
    对比测试：tmux send-keys vs exec pty send-keys
    """
    print("\n" + "=" * 60)
    print("📋 对比：tmux send-keys vs exec pty send-keys")
    print("=" * 60)
    
    print("  ┌──────────────────┬──────────────┬──────────────┬──────────────┐")
    print("  │ 能力             │ tmux         │ exec pty     │ 优势方       │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 安装需求         │ 需安装       │ 内置         │ exec pty     │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 发送文本         │ ✅ send-keys │ ✅ write     │ 平手         │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 发送回车         │ ✅ Enter     │ ✅ submit    │ 平手         │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ Ctrl+C           │ ✅ C-c       │ ✅ hex 03    │ 平手         │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 读取输出         │ capture-pane │ ✅ log       │ exec pty     │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 多窗口/面板      │ ✅ 强项      │ ❌           │ tmux         │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 断开后重连       │ ✅ attach    │ ❌           │ tmux         │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ 编程控制         │ ⚠️ CLI only  │ ✅ API       │ exec pty     │")
    print("  ├──────────────────┼──────────────┼──────────────┼──────────────┤")
    print("  │ Agent 编排集成   │ ⚠️ 需封装    │ ✅ 原生      │ exec pty     │")
    print("  └──────────────────┴──────────────┴──────────────┴──────────────┘")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 11b：tmux send-keys 实测")
    print("=" * 60)
    
    os.environ["LD_LIBRARY_PATH"] = f"{os.path.expanduser('~/.local/lib')}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    
    setup()
    
    t1, t2, e1, e2 = test_tmux_send_keys_claude()
    ctrl_c = test_tmux_send_keys_ctrl_c()
    test_tmux_vs_exec_pty()
    
    print("\n" + "=" * 60)
    print("📊 实验结果")
    print("=" * 60)
    print(f"  Claude 任务 1（power 函数）：{'✅' if t1 else '❌'} {e1:.0f}s")
    print(f"  Claude 任务 2（零除修复）：{'✅' if t2 else '❌'} {e2:.0f}s")
    print(f"  Ctrl+C 中断：{'✅' if ctrl_c else '❌'}")
    
    total_ok = t1 and t2 and ctrl_c
    print(f"\n  {'✅ 全部通过' if total_ok else '⚠️ 部分通过'}")
    
    kill_tmux()
