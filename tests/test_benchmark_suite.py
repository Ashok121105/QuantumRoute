import unittest
from unittest.mock import patch

from quantum_route_optimisation import QAOAConfig
from route_dashboard.benchmark_suite import (
    calculate_objective_gaps,
    generate_benchmark_scenarios,
    run_benchmark,
)


class BenchmarkSuiteTests(unittest.TestCase):
    def test_scenario_generation_is_deterministic(self) -> None:
        first = generate_benchmark_scenarios()
        second = generate_benchmark_scenarios()

        self.assertEqual(
            [(case.name, case.seed, case.problem) for case in first],
            [(case.name, case.seed, case.problem) for case in second],
        )
        self.assertEqual([len(case.problem.deliveries) for case in first], [2, 3, 4, 5])

    def test_objective_gaps_handle_skipped_and_zero_baselines(self) -> None:
        self.assertEqual(calculate_objective_gaps(100.0, 110.0), (10.0, 10.0))
        self.assertEqual(calculate_objective_gaps(100.0, None), (None, None))
        gap, relative = calculate_objective_gaps(0.0, 2.0)
        self.assertEqual(gap, 2.0)
        self.assertIsNone(relative)

    def test_qaoa_is_skipped_above_existing_variable_limit(self) -> None:
        with patch("route_dashboard.benchmark_suite.solve_qaoa") as qaoa:
            result = run_benchmark(
                ["Large / 4 deliveries"],
                QAOAConfig(reps=1, maxiter=5, shots=128, seed=23),
            )[0]

        qaoa.assert_not_called()
        self.assertEqual(result.route_variable_count, 32)
        self.assertFalse(result.qaoa_executed)
        self.assertEqual(result.qaoa_status, "Skipped (variable limit)")
        self.assertIsNone(result.qaoa_objective)
        self.assertIsNone(result.qaoa_runtime_seconds)
        self.assertTrue(result.classical_routes_valid)
        self.assertIsNone(result.qaoa_routes_valid)

    def test_tiny_benchmark_runs_real_classical_and_qaoa_solvers(self) -> None:
        result = run_benchmark(
            ["Small / 2 deliveries"],
            QAOAConfig(reps=1, maxiter=8, shots=256, seed=7),
        )[0]

        self.assertEqual(result.scenario_name, "Small / 2 deliveries")
        self.assertEqual(result.scenario_seed, 101)
        self.assertEqual(result.qaoa_seed, 108)
        self.assertEqual(result.route_variable_count, 4)
        self.assertTrue(result.qaoa_executed)
        self.assertEqual(result.qaoa_status, "Executed")
        self.assertTrue(result.classical_routes_valid)
        self.assertTrue(result.qaoa_routes_valid)
        self.assertGreater(result.classical_runtime_seconds, 0)
        self.assertGreater(result.qaoa_runtime_seconds, 0)
        self.assertIsNotNone(result.qaoa_sample_probability)
        self.assertGreater(result.qaoa_unique_samples, 0)
        self.assertAlmostEqual(
            result.objective_gap,
            result.qaoa_objective - result.classical_objective,
        )


if __name__ == "__main__":
    unittest.main()