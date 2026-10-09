import unittest
from unittest.mock import patch

from quantum_route_optimisation import QAOAConfig
import quantum_route_optimisation.classical as classical_module
import quantum_route_optimisation.qaoa as qaoa_module
import route_dashboard.benchmark_suite as benchmark_module
import route_dashboard.optimization as optimization_module
from route_dashboard.benchmark_suite import generate_benchmark_scenarios, run_benchmark
from route_dashboard.optimization import optimize_scenario


class CandidateRouteReuseTests(unittest.TestCase):
    def test_dashboard_optimization_enumerates_candidates_once(self) -> None:
        scenario = generate_benchmark_scenarios()[0].problem
        original = optimization_module.generate_feasible_routes
        call_count = 0

        def counted(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original(*args, **kwargs)

        with (
            patch.object(optimization_module, "generate_feasible_routes", side_effect=counted),
            patch.object(classical_module, "generate_feasible_routes", wraps=classical_module.generate_feasible_routes) as classical_generate,
            patch.object(qaoa_module, "generate_feasible_routes", wraps=qaoa_module.generate_feasible_routes) as qaoa_generate,
        ):
            result = optimize_scenario(
                scenario,
                QAOAConfig(reps=1, maxiter=5, shots=128, seed=19),
            )

        self.assertEqual(call_count, 1)
        classical_generate.assert_not_called()
        qaoa_generate.assert_not_called()
        self.assertTrue(result.classical.routes)

    def test_benchmark_reuses_one_candidate_set_for_classical_and_qaoa(self) -> None:
        original = benchmark_module.generate_feasible_routes
        call_count = 0

        def counted(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original(*args, **kwargs)

        with (
            patch.object(benchmark_module, "generate_feasible_routes", side_effect=counted),
            patch.object(classical_module, "generate_feasible_routes", wraps=classical_module.generate_feasible_routes) as classical_generate,
            patch.object(qaoa_module, "generate_feasible_routes", wraps=qaoa_module.generate_feasible_routes) as qaoa_generate,
        ):
            result = run_benchmark(
                ["Small / 2 deliveries"],
                QAOAConfig(reps=1, maxiter=5, shots=128, seed=23),
            )[0]

        self.assertEqual(call_count, 1)
        classical_generate.assert_not_called()
        qaoa_generate.assert_not_called()
        self.assertTrue(result.classical_routes_valid)
        self.assertTrue(result.qaoa_routes_valid)


if __name__ == "__main__":
    unittest.main()
