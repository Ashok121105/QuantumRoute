import unittest
from types import SimpleNamespace

from quantum_route_optimisation import QAOAConfig
from route_dashboard.benchmark_suite import run_benchmark
from route_dashboard.custom_routes import score_custom_route_alternatives
from route_dashboard.currency import cost_units_to_inr
from route_dashboard.dynamic import RouteImpact, RouteImpactChange
from route_dashboard.routing import OSRMRouteAlternative
from route_dashboard.ui import (
    _benchmark_chart,
    _custom_route_comparison_chart,
    _demo_objective_tradeoff_chart,
    _demo_traffic_impact_chart,
    _selected_vehicle_sustainability_chart,
)


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
            ["#3B82F6", "#A78BFA"],
        )
        self.assertEqual(chart_spec["config"]["axis"]["labelColor"], "#A7B5C8")
        self.assertEqual(chart_spec["config"]["axis"]["titleColor"], "#F1F5F9")
        self.assertEqual(chart_spec["config"]["axis"]["gridColor"], "#263B55")
        self.assertEqual(chart_spec["config"]["legend"]["labelColor"], "#F1F5F9")
        self.assertEqual(chart_spec["config"]["legend"]["titleColor"], "#F1F5F9")

    def test_custom_route_chart_uses_only_returned_routes_and_available_metrics(self) -> None:
        alternatives = (
            OSRMRouteAlternative(
                "route-a", 1, ((16.0, 80.0), (16.1, 80.1)), 10.0, 20.0
            ),
            OSRMRouteAlternative(
                "route-b", 2, ((16.0, 80.0), (16.2, 80.2)), 12.0, 25.0
            ),
        )
        scored = score_custom_route_alternatives(alternatives, "Shortest Distance")
        chart = _custom_route_comparison_chart(scored, "route-a")

        rows = _chart_rows(chart)
        self.assertEqual({row["Category"] for row in rows}, {"Route 1", "Route 2"})
        self.assertEqual(
            {row["Series"] for row in rows},
            {"Recommended", "Alternative"},
        )
        route_spec = chart.to_dict()["spec"]
        self.assertEqual(
            route_spec["encoding"]["color"]["scale"]["domain"],
            ["Recommended", "Alternative"],
        )
        self.assertEqual(
            route_spec["encoding"]["color"]["scale"]["range"],
            ["#34D399", "#3B82F6"],
        )
        self.assertEqual(
            {row["Metric"] for row in rows},
            {"Distance (km)", "OSRM estimated duration (min)"},
        )

        single = _custom_route_comparison_chart(scored[:1], "route-a")
        self.assertEqual({row["Category"] for row in _chart_rows(single)}, {"Route 1"})
        self.assertIsNone(_custom_route_comparison_chart((), None))

    def test_vehicle_chart_omits_unavailable_calculated_metrics(self) -> None:
        route = OSRMRouteAlternative(
            "route-a", 1, ((16.0, 80.0), (16.1, 80.1)), 10.0, 20.0
        )
        selected = score_custom_route_alternatives((route,), "Shortest Distance")[0]
        chart = _selected_vehicle_sustainability_chart(
            {"vehicle_id": "van-1", "capacity": 1500},
            selected,
        )

        rows = _chart_rows(chart)
        self.assertEqual(
            {row["Metric"] for row in rows},
            {"Uploaded capacity (units)"},
        )
        self.assertEqual(rows[0]["Value"], 1500.0)

        calculated = score_custom_route_alternatives(
            (route,),
            "Lowest Estimated Cost",
            fuel_type="Diesel",
            fuel_consumption_per_km=0.09,
            fuel_price_per_unit=2.0,
            driver_cost_per_hour=60.0,
        )[0]
        calculated_chart = _selected_vehicle_sustainability_chart(
            {"vehicle_id": "van-1", "capacity": 1500},
            calculated,
        )
        calculated_rows = {
            row["Metric"]: row["Value"] for row in _chart_rows(calculated_chart)
        }
        self.assertEqual(calculated_rows["Estimated fuel (L)"], calculated.fuel_used)
        self.assertEqual(
            calculated_rows["Estimated operating cost (uploaded units)"],
            calculated.operating_cost,
        )
        self.assertEqual(
            calculated_rows["Estimated tailpipe CO2 (kg)"],
            calculated.tailpipe_co2_kg,
        )

    def test_demo_traffic_and_objective_charts_use_supplied_classical_impacts(self) -> None:
        before = RouteImpact("Before", 10.0, 20.0, 5.0, 1.0, "L", 2.0)
        after = RouteImpact("After", 12.0, 30.0, 7.0, 1.2, "L", 2.4)
        traffic_chart = _demo_traffic_impact_chart(
            RouteImpactChange(before, after, 2.0, 10.0, 2.0, 0.2, 0.4)
        )
        traffic_rows = _chart_rows(traffic_chart)
        self.assertEqual(
            {
                (row["Category"], row["Metric"], row["Value"])
                for row in traffic_rows
            },
            {
                ("Before", "Estimated travel time (min)", 20.0),
                ("Before", "Estimated route cost (₹)", cost_units_to_inr(5.0)),
                ("After", "Estimated travel time (min)", 30.0),
                ("After", "Estimated route cost (₹)", cost_units_to_inr(7.0)),
            },
        )

        objectives = [
            SimpleNamespace(
                objective=SimpleNamespace(value="Cost Priority"),
                impact=before,
            ),
            SimpleNamespace(
                objective=SimpleNamespace(value="Green Priority"),
                impact=after,
            ),
        ]
        objective_rows = _chart_rows(_demo_objective_tradeoff_chart(objectives))
        self.assertEqual(
            {row["Category"] for row in objective_rows},
            {"Cost Priority", "Green Priority"},
        )
        self.assertEqual(
            {row["Metric"] for row in objective_rows},
            {
                "Estimated route cost (₹)",
                "Estimated travel time (min)",
                "Route distance (km)",
                "Estimated tailpipe CO2 (kg)",
            },
        )
        self.assertEqual(
            len(objective_rows),
            len(objectives) * 4,
        )


def _chart_rows(chart) -> list[dict[str, object]]:
    chart_spec = chart.to_dict()
    return next(iter(chart_spec["datasets"].values()))


if __name__ == "__main__":
    unittest.main()
