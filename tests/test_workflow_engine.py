"""
Comprehensive tests for the workflow engine.

Covers sequential execution, parallel execution, dependency resolution,
retry on failure, state persistence, and edge cases.
"""

import json
import os
import time
import unittest
from workflow_engine import Pipeline, Step, StepResult


class TestWorkflowEngine(unittest.TestCase):
    """Test cases for the workflow engine."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_state_file = "test_pipeline_state.json"
        # Clean up any existing test files
        if os.path.exists(self.test_state_file):
            os.remove(self.test_state_file)

    def tearDown(self):
        """Clean up test files."""
        if os.path.exists(self.test_state_file):
            os.remove(self.test_state_file)

    # Sequential execution tests

    def test_sequential_execution_single_step(self):
        """Test sequential execution of a single step."""
        pipeline = Pipeline("single_step")

        result_value = [42]

        def simple_step():
            return result_value[0]

        pipeline.add_step("step1", simple_step)
        results = pipeline.run(parallel=False)

        self.assertEqual(len(results), 1)
        self.assertEqual(results["step1"].status, "completed")
        self.assertEqual(results["step1"].result, 42)

    def test_sequential_execution_multiple_steps(self):
        """Test sequential execution of multiple steps."""
        pipeline = Pipeline("multi_step")
        execution_order = []

        def step1():
            execution_order.append("step1")
            return "a"

        def step2():
            execution_order.append("step2")
            return "b"

        def step3():
            execution_order.append("step3")
            return "c"

        pipeline.add_step("step1", step1)
        pipeline.add_step("step2", step2)
        pipeline.add_step("step3", step3)

        results = pipeline.run(parallel=False)

        self.assertEqual(len(results), 3)
        self.assertEqual(execution_order, ["step1", "step2", "step3"])
        for result in results.values():
            self.assertEqual(result.status, "completed")

    def test_sequential_execution_with_dependencies(self):
        """Test sequential execution respects dependencies."""
        pipeline = Pipeline("with_deps")
        execution_order = []

        def step_a():
            execution_order.append("a")
            return "a_result"

        def step_b():
            execution_order.append("b")
            return "b_result"

        def step_c():
            execution_order.append("c")
            return "c_result"

        pipeline.add_step("step_a", step_a)
        pipeline.add_step("step_b", step_b, depends_on=["step_a"])
        pipeline.add_step("step_c", step_c, depends_on=["step_a", "step_b"])

        results = pipeline.run(parallel=False)

        self.assertEqual(execution_order, ["a", "b", "c"])
        self.assertEqual(results["step_a"].result, "a_result")
        self.assertEqual(results["step_b"].result, "b_result")
        self.assertEqual(results["step_c"].result, "c_result")

    # Parallel execution tests

    def test_parallel_execution_independent_steps(self):
        """Test parallel execution of independent steps."""
        pipeline = Pipeline("parallel")
        execution_times = []

        def slow_step_1():
            start = time.time()
            time.sleep(0.1)
            execution_times.append(("step1", time.time() - start))
            return "result1"

        def slow_step_2():
            start = time.time()
            time.sleep(0.1)
            execution_times.append(("step2", time.time() - start))
            return "result2"

        def slow_step_3():
            start = time.time()
            time.sleep(0.1)
            execution_times.append(("step3", time.time() - start))
            return "result3"

        pipeline.add_step("step1", slow_step_1)
        pipeline.add_step("step2", slow_step_2)
        pipeline.add_step("step3", slow_step_3)

        start = time.time()
        results = pipeline.run(parallel=True, max_workers=3)
        total_time = time.time() - start

        # Should complete in ~0.1s (parallel) not ~0.3s (sequential)
        self.assertLess(total_time, 0.25)
        self.assertEqual(len(results), 3)
        for result in results.values():
            self.assertEqual(result.status, "completed")

    def test_parallel_execution_with_dependencies(self):
        """Test parallel execution respects dependencies."""
        pipeline = Pipeline("parallel_with_deps")
        execution_order = []

        def step_a():
            execution_order.append("a")
            return "a_result"

        def step_b():
            execution_order.append("b")
            return "b_result"

        def step_c():
            execution_order.append("c")
            return "c_result"

        def step_d():
            execution_order.append("d")
            return "d_result"

        # A must run first, B and C can run in parallel after A, D depends on B and C
        pipeline.add_step("step_a", step_a)
        pipeline.add_step("step_b", step_b, depends_on=["step_a"])
        pipeline.add_step("step_c", step_c, depends_on=["step_a"])
        pipeline.add_step("step_d", step_d, depends_on=["step_b", "step_c"])

        results = pipeline.run(parallel=True, max_workers=3)

        self.assertEqual(results["step_a"].status, "completed")
        self.assertEqual(results["step_b"].status, "completed")
        self.assertEqual(results["step_c"].status, "completed")
        self.assertEqual(results["step_d"].status, "completed")
        self.assertEqual(execution_order[0], "a")  # A must be first
        self.assertEqual(execution_order[-1], "d")  # D must be last

    # Dependency resolution tests

    def test_dependency_resolution_complex_graph(self):
        """Test dependency resolution with complex dependency graph."""
        pipeline = Pipeline("complex_deps")
        executed = []

        def step_a():
            executed.append("a")
            return "a"

        def step_b():
            executed.append("b")
            return "b"

        def step_c():
            executed.append("c")
            return "c"

        def step_d():
            executed.append("d")
            return "d"

        def step_e():
            executed.append("e")
            return "e"

        # A -> B -> D
        # A -> C -> D
        # E is independent
        pipeline.add_step("step_a", step_a)
        pipeline.add_step("step_b", step_b, depends_on=["step_a"])
        pipeline.add_step("step_c", step_c, depends_on=["step_a"])
        pipeline.add_step("step_d", step_d, depends_on=["step_b", "step_c"])
        pipeline.add_step("step_e", step_e)

        results = pipeline.run()

        self.assertTrue(all(r.status == "completed" for r in results.values()))
        # A must come before B, C, D
        self.assertLess(executed.index("a"), executed.index("b"))
        self.assertLess(executed.index("a"), executed.index("c"))
        # B and C must come before D
        self.assertLess(executed.index("b"), executed.index("d"))
        self.assertLess(executed.index("c"), executed.index("d"))

    def test_circular_dependency_detection(self):
        """Test that circular dependencies are detected."""
        pipeline = Pipeline("circular")

        def step_a():
            return "a"

        def step_b():
            return "b"

        pipeline.add_step("step_a", step_a, depends_on=["step_b"])
        pipeline.add_step("step_b", step_b, depends_on=["step_a"])

        with self.assertRaises(ValueError) as context:
            pipeline.run()

        self.assertIn("Circular dependency", str(context.exception))

    def test_nonexistent_dependency_raises_error(self):
        """Test that non-existent dependencies raise an error."""
        pipeline = Pipeline("nonexistent_dep")

        def step_a():
            return "a"

        pipeline.add_step("step_a", step_a, depends_on=["nonexistent_step"])

        with self.assertRaises(ValueError) as context:
            pipeline.run()

        self.assertIn("non-existent step", str(context.exception))

    # Retry on failure tests

    def test_retry_on_failure_success_after_retry(self):
        """Test that failed steps are retried and can succeed."""
        pipeline = Pipeline("retry_success")
        attempt_count = [0]

        def flaky_step():
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise ValueError("Not yet!")
            return "success"

        pipeline.add_step("flaky", flaky_step, max_retries=3)
        results = pipeline.run()

        self.assertEqual(results["flaky"].status, "completed")
        self.assertEqual(results["flaky"].result, "success")
        self.assertEqual(results["flaky"].retry_count, 2)
        self.assertEqual(attempt_count[0], 3)

    def test_retry_max_attempts_exceeded(self):
        """Test that retry stops after max attempts."""
        pipeline = Pipeline("retry_fail")

        def always_fail_step():
            raise ValueError("Always fails")

        pipeline.add_step("fail_step", always_fail_step, max_retries=2)
        results = pipeline.run()

        self.assertEqual(results["fail_step"].status, "failed")
        self.assertEqual(results["fail_step"].retry_count, 2)
        self.assertIn("Always fails", results["fail_step"].error)

    def test_zero_retries_no_retry(self):
        """Test that max_retries=0 means no retry on failure."""
        pipeline = Pipeline("no_retry")
        attempt_count = [0]

        def failing_step():
            attempt_count[0] += 1
            raise ValueError("Failed")

        pipeline.add_step("fail_once", failing_step, max_retries=0)
        results = pipeline.run()

        self.assertEqual(results["fail_once"].status, "failed")
        self.assertEqual(attempt_count[0], 1)  # Only attempted once

    def test_failed_step_marks_dependents_skipped(self):
        """Test that dependent steps are skipped when a step fails."""
        pipeline = Pipeline("skip_dependents")

        def step_a():
            return "a"

        def step_b():
            raise ValueError("B fails")

        def step_c():
            return "c"

        pipeline.add_step("step_a", step_a)
        pipeline.add_step("step_b", step_b, depends_on=["step_a"])
        pipeline.add_step("step_c", step_c, depends_on=["step_b"])

        results = pipeline.run()

        self.assertEqual(results["step_a"].status, "completed")
        self.assertEqual(results["step_b"].status, "failed")
        self.assertEqual(results["step_c"].status, "skipped")
        self.assertIn("failed dependency", results["step_c"].error)

    # State persistence tests

    def test_save_and_load_state(self):
        """Test saving and loading pipeline state."""
        pipeline = Pipeline("persist_test")

        def step_1():
            return "result1"

        def step_2():
            return "result2"

        pipeline.add_step("step1", step_1)
        pipeline.add_step("step2", step_2)

        # Run and save
        pipeline.run()
        pipeline.save_state(self.test_state_file)

        # Create new pipeline and load state
        new_pipeline = Pipeline("new_pipeline")
        new_pipeline.add_step("step1", step_1)
        new_pipeline.add_step("step2", step_2)
        new_pipeline.load_state(self.test_state_file)

        # Verify loaded state
        self.assertEqual(new_pipeline.get_step_status("step1"), "completed")
        self.assertEqual(new_pipeline.get_step_status("step2"), "completed")

    def test_save_state_includes_execution_history(self):
        """Test that saved state includes execution history."""
        pipeline = Pipeline("history_test")

        def step_func():
            return "result"

        pipeline.add_step("step1", step_func)
        pipeline.run()

        pipeline.save_state(self.test_state_file)

        # Load and check history
        with open(self.test_state_file, 'r') as f:
            state = json.load(f)

        self.assertIn("execution_history", state)
        self.assertGreater(len(state["execution_history"]), 0)
        self.assertEqual(state["execution_history"][0]["step"], "step1")
        self.assertEqual(state["execution_history"][0]["status"], "completed")

    def test_load_state_preserves_history(self):
        """Test that loading state preserves execution history."""
        pipeline = Pipeline("history_preserve")

        def step_func():
            return "result"

        pipeline.add_step("step1", step_func)
        pipeline.run()
        original_history = pipeline.get_execution_history()

        pipeline.save_state(self.test_state_file)

        # Create new pipeline and load
        new_pipeline = Pipeline("new")
        new_pipeline.add_step("step1", step_func)
        new_pipeline.load_state(self.test_state_file)

        loaded_history = new_pipeline.get_execution_history()
        self.assertEqual(len(loaded_history), len(original_history))
        self.assertEqual(loaded_history[0]["step"], "step1")

    # Execution history tests

    def test_execution_history_logging(self):
        """Test that execution history is logged."""
        pipeline = Pipeline("history_log")

        def step_a():
            return "a"

        def step_b():
            return "b"

        pipeline.add_step("step_a", step_a)
        pipeline.add_step("step_b", step_b)
        pipeline.run()

        history = pipeline.get_execution_history()

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["step"], "step_a")
        self.assertEqual(history[1]["step"], "step_b")
        for entry in history:
            self.assertIn("start_time", entry)
            self.assertIn("end_time", entry)
            self.assertIn("duration", entry)
            self.assertEqual(entry["status"], "completed")

    def test_clear_history(self):
        """Test clearing execution history."""
        pipeline = Pipeline("clear_history")

        def step_func():
            return "result"

        pipeline.add_step("step1", step_func)
        pipeline.run()

        self.assertGreater(len(pipeline.get_execution_history()), 0)

        pipeline.clear_history()

        self.assertEqual(len(pipeline.get_execution_history()), 0)

    # Edge cases

    def test_empty_pipeline(self):
        """Test running an empty pipeline."""
        pipeline = Pipeline("empty")
        results = pipeline.run()

        self.assertEqual(len(results), 0)

    def test_duplicate_step_name_raises_error(self):
        """Test that adding duplicate step names raises an error."""
        pipeline = Pipeline("duplicate")

        def step_func():
            return "result"

        pipeline.add_step("step1", step_func)

        with self.assertRaises(ValueError) as context:
            pipeline.add_step("step1", step_func)

        self.assertIn("already exists", str(context.exception))

    def test_get_step_status_nonexistent_raises_error(self):
        """Test getting status of non-existent step raises error."""
        pipeline = Pipeline("status_test")

        with self.assertRaises(ValueError) as context:
            pipeline.get_step_status("nonexistent")

        self.assertIn("not found", str(context.exception))

    def test_method_chaining_add_step(self):
        """Test that add_step supports method chaining."""
        pipeline = Pipeline("chaining")

        def step_func():
            return "result"

        result = (pipeline
                  .add_step("step1", step_func)
                  .add_step("step2", step_func)
                  .add_step("step3", step_func))

        self.assertIs(result, pipeline)
        self.assertEqual(len(pipeline.steps), 3)

    def test_step_result_contains_timing_info(self):
        """Test that step results contain timing information."""
        pipeline = Pipeline("timing")

        def slow_step():
            time.sleep(0.05)
            return "done"

        pipeline.add_step("slow", slow_step)
        results = pipeline.run()

        self.assertIsNotNone(results["slow"].start_time)
        self.assertIsNotNone(results["slow"].end_time)
        self.assertIsNotNone(results["slow"].duration)
        self.assertGreater(results["slow"].duration, 0.04)

    def test_parallel_with_max_workers_limit(self):
        """Test that max_workers limits parallel execution."""
        pipeline = Pipeline("worker_limit")
        concurrent_count = [0]
        max_concurrent = [0]

        def counting_step():
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            time.sleep(0.05)
            concurrent_count[0] -= 1
            return "done"

        # Add 5 steps but limit to 2 workers
        for i in range(5):
            pipeline.add_step(f"step{i}", counting_step)

        pipeline.run(parallel=True, max_workers=2)

        # Should not exceed 2 concurrent executions
        self.assertLessEqual(max_concurrent[0], 2)

    def test_failed_step_history_logging(self):
        """Test that failed steps are properly logged in history."""
        pipeline = Pipeline("fail_history")

        def failing_step():
            raise ValueError("Intentional failure")

        pipeline.add_step("fail_step", failing_step, max_retries=1)
        pipeline.run()

        history = pipeline.get_execution_history()

        self.assertEqual(len(history), 1)  # One execution with retry_count=1
        self.assertEqual(history[0]["status"], "failed")
        self.assertEqual(history[0]["retry_count"], 1)
        self.assertIsNotNone(history[0]["error"])


def run_tests():
    """Run all tests and print results."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestWorkflowEngine)

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)
