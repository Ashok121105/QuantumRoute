import unittest

from quantum_route_optimisation import QAOAConfig
from route_dashboard.benchmark_suite import run_benchmark
from route_dashboard.ui import _benchmark_chart


class BenchmarkChartUITests(unittest.TestCase):
    def test_small_benchmark_chart_uses_readable_light_theme_colors(self) -> None:
        results = run_benchmark(
            ["Small / 2 deliveries"],
            QAOAConfig(reps=1, maxiter=8, shots=256, seed=7),
        )

        chart_spec = _benchmark_chart(results, "objective", "Objective / cost").to_dict()

        self.assertEqual(chart_spec["mark"]["type"], "point")
        self.assertEqual(
            chart_spec["encoding"]["color"]["scale"]["range"],
            ["#2B6A4B", "#7B1E2D"],
        )
        self.assertEqual(chart_spec["config"]["axis"]["labelColor"], "#2B0D19")
        self.assertEqual(chart_spec["config"]["axis"]["titleColor"], "#2B0D19")
        self.assertEqual(chart_spec["config"]["axis"]["gridColor"], "#D8B98E")
        self.assertEqual(chart_spec["config"]["legend"]["labelColor"], "#2B0D19")
        self.assertEqual(chart_spec["config"]["legend"]["titleColor"], "#2B0D19")


if __name__ == "__main__":
    unittest.main()
