#!/usr/bin/env python3
"""
🧪 实验 7：代码审查流程
验证 Codex 能对 git diff 进行结构化审查，输出审查意见。
"""
import json
import os
import subprocess
import sys
import time

WORKDIR = "/tmp/hermes-exp6"  # Reuse exp6 repo


def get_diff():
    """获取 feature 分支相对 main 的 diff"""
    os.chdir(WORKDIR)
    result = subprocess.run(
        ["git", "diff", "main...feature/calculator"],
        capture_output=True, text=True
    )
    return result.stdout


def run_codex_review(diff_text):
    """让 Codex 审查 diff"""
    # Write diff to file
    diff_file = f"{WORKDIR}/review_input.diff"
    with open(diff_file, "w") as f:
        f.write(diff_text)
    
    prompt = (
        "Review the following git diff. Provide a structured review:\n"
        "1. **Summary**: What changes were made\n"
        "2. **Issues**: Any bugs, security concerns, or code quality issues (with severity: HIGH/MEDIUM/LOW)\n"
        "3. **Suggestions**: Improvement recommendations\n"
        "4. **Verdict**: APPROVE or REQUEST_CHANGES\n\n"
        f"Diff:\n```\n{diff_text}\n```"
    )
    
    task_file = f"{WORKDIR}/review_task.txt"
    with open(task_file, "w") as f:
        f.write(prompt)
    
    print(f"🚀 Starting Codex review...")
    
    cmd = [
        "codex", "exec", "--yolo",
        prompt,
    ]
    
    start = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=WORKDIR)
    
    output_lines = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            output_lines.append(line)
            # Print summary lines
            if any(kw in line for kw in ["Summary", "Issue", "Suggestion", "Verdict", "APPROVE", "REQUEST"]):
                print(f"  💬 {line[:120]}")
    
    proc.wait()
    elapsed = time.time() - start
    
    full_output = "\n".join(output_lines)
    return full_output, elapsed, proc.returncode


def run_claude_review(diff_text):
    """让 Claude Code 审查 diff（对比）"""
    prompt = (
        "Review this git diff. Output ONLY a JSON object with these fields:\n"
        "- summary: one sentence\n"
        "- issues: array of {severity: HIGH/MEDIUM/LOW, file: string, line: string, description: string}\n"
        "- suggestions: array of strings\n"
        "- verdict: APPROVE or REQUEST_CHANGES\n\n"
        f"```\n{diff_text}\n```"
    )
    
    print(f"\n🚀 Starting Claude Code review...")
    
    cmd = [
        "claude",
        "--permission-mode", "bypassPermissions",
        "--print",
        "--output-format", "stream-json",
        prompt,
    ]
    
    start = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=WORKDIR)
    
    result_text = ""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "result":
                result_text = event.get("result", "")
        except:
            continue
    
    proc.wait()
    elapsed = time.time() - start
    
    return result_text, elapsed, proc.returncode


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 实验 7：代码审查流程")
    print("=" * 60)
    
    # Get diff
    diff_text = get_diff()
    if not diff_text:
        print("❌ No diff found")
        sys.exit(1)
    
    print(f"\n--- Diff ({len(diff_text)} chars) ---")
    for line in diff_text.split("\n")[:20]:
        print(f"  {line}")
    print(f"  ... ({len(diff_text.split(chr(10)))} lines total)")
    
    # Run Codex review
    codex_output, codex_time, codex_rc = run_codex_review(diff_text)
    
    # Run Claude review
    claude_output, claude_time, claude_rc = run_claude_review(diff_text)
    
    # Summary
    print(f"\n--- 审查结果对比 ---")
    print(f"  Codex: {codex_time:.1f}s (rc={codex_rc})")
    print(f"  Claude: {claude_time:.1f}s (rc={claude_rc})")
    
    if "APPROVE" in codex_output.upper():
        print(f"  Codex 审查结论: ✅ APPROVE")
    elif "REQUEST" in codex_output.upper():
        print(f"  Codex 审查结论: ⚠️ REQUEST_CHANGES")
    else:
        print(f"  Codex 审查结论: ❓ 无法判断")
    
    # Try to parse Claude JSON output
    try:
        # Extract JSON from Claude output
        json_start = claude_output.find("{")
        json_end = claude_output.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            claude_json = json.loads(claude_output[json_start:json_end])
            print(f"  Claude 审查结论: {'✅' if claude_json.get('verdict') == 'APPROVE' else '⚠️'} {claude_json.get('verdict')}")
            print(f"  Claude 发现问题: {len(claude_json.get('issues', []))} 个")
            for issue in claude_json.get("issues", []):
                print(f"    [{issue.get('severity')}] {issue.get('description', '')[:60]}")
    except Exception as e:
        print(f"  Claude 输出解析失败: {e}")
        print(f"  Claude 原始输出: {claude_output[:300]}")
    
    # Verify both completed
    ok = codex_rc == 0 and claude_rc == 0
    
    print("\n" + "=" * 60)
    print(f"🧪 实验 7 完成 {'✅' if ok else '⚠️'}")
    print(f"  {'Codex' if codex_rc == 0 else 'Codex ❌'} + {'Claude' if claude_rc == 0 else 'Claude ❌'} 双模型审查验证")
    print("=" * 60)
