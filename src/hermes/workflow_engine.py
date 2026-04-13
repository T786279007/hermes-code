"""
Workflow Engine - A simple pipeline/workflow execution engine.

Supports sequential and parallel execution, step dependencies, retry logic,
state persistence, and execution history logging.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set


@dataclass
class StepResult:
    """Result of a step execution."""
    name: str
    status: str  # pending, running, completed, failed, skipped
    result: Optional[any] = None
    error: Optional[str] = None
    retry_count: int = 0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration: Optional[float] = None


class Step:
    """Represents a single step in the pipeline."""

    def __init__(self, name: str, func: Callable, depends_on: List[str] = None, max_retries: int = 0):
        self.name = name
        self.func = func
        self.depends_on = depends_on or []
        self.max_retries = max_retries
        self.status = "pending"
        self.retry_count = 0
        self.result = None
        self.error = None
        self.start_time = None
        self.end_time = None

    def to_dict(self) -> dict:
        """Convert step to dictionary for serialization."""
        return {
            "name": self.name,
            "status": self.status,
            "depends_on": self.depends_on,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "result": str(self.result) if self.result is not None else None,
            "error": self.error,
            "start_time": self.start_time,
            "end_time": self.end_time
        }

    def execute(self) -> StepResult:
        """Execute the step with retry logic."""
        self.status = "running"
        self.start_time = datetime.now().isoformat()
        start = time.time()

        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                self.retry_count = attempt
                result = self.func()
                self.result = result
                self.status = "completed"
                self.end_time = datetime.now().isoformat()
                duration = time.time() - start

                return StepResult(
                    name=self.name,
                    status="completed",
                    result=result,
                    retry_count=attempt,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    duration=duration
                )
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    time.sleep(1)  # Brief delay before retry
                    continue
                else:
                    self.status = "failed"
                    self.error = last_error
                    self.end_time = datetime.now().isoformat()
                    duration = time.time() - start

                    return StepResult(
                        name=self.name,
                        status="failed",
                        error=last_error,
                        retry_count=attempt,
                        start_time=self.start_time,
                        end_time=self.end_time,
                        duration=duration
                    )


class Pipeline:
    """Workflow pipeline engine supporting sequential and parallel execution."""

    def __init__(self, name: str):
        self.name = name
        self.steps: Dict[str, Step] = {}
        self.execution_history: List[Dict] = []
        self.lock = threading.Lock()

    def add_step(self, name: str, func: Callable, depends_on: List[str] = None, max_retries: int = 0) -> 'Pipeline':
        """
        Add a step to the pipeline.

        Args:
            name: Unique name for the step
            func: Callable to execute for this step
            depends_on: List of step names this step depends on
            max_retries: Maximum number of retry attempts on failure

        Returns:
            Self for method chaining
        """
        if name in self.steps:
            raise ValueError(f"Step '{name}' already exists in pipeline")

        self.steps[name] = Step(name, func, depends_on, max_retries)
        return self

    def _validate_dependencies(self) -> None:
        """Validate that all dependencies reference existing steps and no cycles exist."""
        for name, step in self.steps.items():
            for dep in step.depends_on:
                if dep not in self.steps:
                    raise ValueError(f"Step '{name}' depends on non-existent step '{dep}'")

        # Check for cycles
        visited = set()
        rec_stack = set()

        def has_cycle(step_name: str) -> bool:
            visited.add(step_name)
            rec_stack.add(step_name)

            for dep in self.steps[step_name].depends_on:
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(step_name)
            return False

        for step_name in self.steps:
            if step_name not in visited:
                if has_cycle(step_name):
                    raise ValueError(f"Circular dependency detected involving step '{step_name}'")

    def _get_ready_steps(self) -> List[Step]:
        """Get steps whose dependencies are all completed."""
        ready_steps = []
        for name, step in self.steps.items():
            if step.status == "pending":
                deps_completed = all(
                    self.steps[dep].status == "completed"
                    for dep in step.depends_on
                )
                if deps_completed:
                    ready_steps.append(step)
        return ready_steps

    def _log_execution(self, step_result: StepResult) -> None:
        """Log step execution result to history."""
        with self.lock:
            log_entry = {
                "pipeline": self.name,
                "step": step_result.name,
                "status": step_result.status,
                "start_time": step_result.start_time,
                "end_time": step_result.end_time,
                "duration": step_result.duration,
                "retry_count": step_result.retry_count,
                "error": step_result.error
            }
            self.execution_history.append(log_entry)

    def run(self, parallel: bool = False, max_workers: int = 4) -> Dict[str, StepResult]:
        """
        Execute the pipeline.

        Args:
            parallel: If True, execute independent steps in parallel
            max_workers: Maximum number of parallel workers

        Returns:
            Dictionary mapping step names to their execution results
        """
        self._validate_dependencies()

        # Reset all steps to pending
        for step in self.steps.values():
            step.status = "pending"
            step.retry_count = 0
            step.result = None
            step.error = None

        results = {}

        if not parallel:
            # Sequential execution
            while True:
                ready_steps = self._get_ready_steps()
                if not ready_steps:
                    break

                for step in ready_steps:
                    result = step.execute()
                    results[step.name] = result
                    self._log_execution(result)

                    if result.status == "failed":
                        # Mark dependent steps as skipped
                        self._mark_dependents_skipped(step.name, results)
        else:
            # Parallel execution
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                while True:
                    ready_steps = self._get_ready_steps()
                    if not ready_steps:
                        break

                    # Submit ready steps to executor
                    future_to_step = {
                        executor.submit(step.execute): step
                        for step in ready_steps
                    }

                    for future in as_completed(future_to_step):
                        step = future_to_step[future]
                        try:
                            result = future.result()
                            results[step.name] = result
                            self._log_execution(result)

                            if result.status == "failed":
                                self._mark_dependents_skipped(step.name, results)
                        except Exception as e:
                            # This shouldn't happen as execute() catches exceptions
                            error_result = StepResult(
                                name=step.name,
                                status="failed",
                                error=f"Unexpected error: {str(e)}"
                            )
                            results[step.name] = error_result
                            self._log_execution(error_result)
                            self._mark_dependents_skipped(step.name, results)

        return results

    def _mark_dependents_skipped(self, failed_step: str, results: Dict[str, StepResult]) -> None:
        """Mark all steps that depend on the failed step as skipped."""
        for step in self.steps.values():
            if step.status == "pending" and failed_step in step.depends_on:
                step.status = "skipped"
                skip_result = StepResult(
                    name=step.name,
                    status="skipped",
                    error=f"Skipped due to failed dependency: {failed_step}"
                )
                results[step.name] = skip_result
                self._log_execution(skip_result)

    def save_state(self, filepath: str) -> None:
        """Save pipeline state to JSON file."""
        state = {
            "name": self.name,
            "steps": {name: step.to_dict() for name, step in self.steps.items()},
            "execution_history": self.execution_history,
            "saved_at": datetime.now().isoformat()
        }

        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)

    def load_state(self, filepath: str) -> None:
        """Load pipeline state from JSON file."""
        with open(filepath, 'r') as f:
            state = json.load(f)

        self.name = state["name"]
        self.execution_history = state.get("execution_history", [])

        # Restore step states
        for step_data in state.get("steps", {}).values():
            if step_data["name"] in self.steps:
                step = self.steps[step_data["name"]]
                step.status = step_data["status"]
                step.retry_count = step_data.get("retry_count", 0)
                step.error = step_data.get("error")
                step.start_time = step_data.get("start_time")
                step.end_time = step_data.get("end_time")

    def get_execution_history(self) -> List[Dict]:
        """Get the execution history log."""
        return self.execution_history.copy()

    def clear_history(self) -> None:
        """Clear the execution history."""
        with self.lock:
            self.execution_history.clear()

    def get_step_status(self, step_name: str) -> str:
        """Get the current status of a step."""
        if step_name not in self.steps:
            raise ValueError(f"Step '{step_name}' not found in pipeline")
        return self.steps[step_name].status
