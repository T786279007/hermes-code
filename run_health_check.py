#!/usr/bin/env python3

"""Simple script to run Hermes Agent health checks."""

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH
from task_registry import TaskRegistry
from check_agents import HealthChecker

def main():
    """Run health checks and print results."""
    print("🦞 Hermes Agent Health Check")
    print("=" * 40)
    
    # Initialize registry and health checker
    registry = TaskRegistry(DB_PATH)
    checker = HealthChecker(registry)
    
    # Run all checks
    results = checker.check()
    
    # Print results
    print("\n📊 Task Status:")
    for status, count in results["tasks"].items():
        print(f"  {status}: {count}")
    
    print(f"\n🤖 Running Agents: {len(results['agents'])}")
    for task_id, agent_info in results["agents"].items():
        alive = "✓" if agent_info["alive"] else "✗"
        elapsed = agent_info["elapsed_sec"]
        print(f"  {task_id[:8]}... PID:{agent_info['pid']} {alive} ({elapsed}s)")
    
    print(f"\n💾 Disk Usage: {results['system']['disk_used_gb']}GB / {results['system']['disk_total_gb']}GB ({results['system']['disk_percent']}%)")
    
    print(f"\n🗄️  Database: {'OK' if results['database']['integrity'] else 'ERROR'}")
    
    if results["stale"]["stale_tasks"]:
        print(f"\n⚠️  Stale Tasks: {len(results['stale']['stale_tasks'])}")
        for task in results["stale"]["stale_tasks"]:
            print(f"  {task['task_id'][:8]}... ({task['elapsed_sec']}s > {task['threshold_sec']}s)")
    
    if results["pr_status"]["open_prs"]:
        print(f"\n🔗 Open PRs: {len(results['pr_status']['open_prs'])}")
        for pr in results["pr_status"]["open_prs"]:
            status = "❌" if pr["ci_failed"] else "✓"
            print(f"  #{pr['number']} {status} {pr['title']}")
    
    if results["needs_attention"]:
        print("\n🚨 NEEDS ATTENTION!")
        if results["stale"]["stale_tasks"]:
            print(f"  - {len(results['stale']['stale_tasks'])} stale tasks")
        if results["pr_status"]["has_failed_ci"]:
            print("  - PR CI failures detected")
    else:
        print("\n✅ All systems normal")
    
    print("=" * 40)

if __name__ == "__main__":
    main()