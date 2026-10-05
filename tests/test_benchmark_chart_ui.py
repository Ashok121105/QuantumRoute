import unittest

from quantum_route_optimisation import QAOAConfig
from route_dashboard.benchmark_suite import run_benchmark
from route_dashboard.ui import _benchmark_chart


class BenchmarkChartUITests(unittest.TestCase):
    def test_small_benchmark_chart_uses_readable_dark_theme_colors(self) -> None:
        results = run_benchmark(
            ["Small / 2 deliveries"],
            QAOAConfig(reps=1, maxiter=8, shots=256, seed=7),
        )

        chart_spec = _benchmark_chart(results, "objective", "Objective / cost").to_dict()

        self.assertEqual(chart_spec["mark"]["type"], "point")
        self.assertEqual(
            chart_spec["encoding"]["color"]["scale"]["range"],
            ["#D6B15D", "#A0445C"],
        )
        self.assertEqual(chart_spec["config"]["axis"]["labelColor"], "#F2EBDD")
        self.assertEqual(chart_spec["config"]["axis"]["titleColor"], "#F2EBDD")
        self.assertEqual(chart_spec["config"]["axis"]["gridColor"], "#49353B")
        self.assertEqual(chart_spec["config"]["legend"]["labelColor"], "#F2EBDD")
        self.assertEqual(chart_spec["config"]["legend"]["titleColor"], "#F2EBDD")


if __name__ == "__main__":
    unittest.main()
