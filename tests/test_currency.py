import unittest

from quantum_route_optimisation import optimize_classically
from route_dashboard.currency import (
    INR_PER_COST_UNIT,
    cost_units_to_inr,
    format_cost,
    format_inr,
    inr_to_cost_units,
)
from route_dashboard.map_view import build_route_map
from route_dashboard.optimization import OptimizationRun, RouteSummary
from route_dashboard.scenario import build_demo_scenario


class CurrencyDisplayTests(unittest.TestCase):
    def test_inr_formatter_uses_grouping_and_two_decimal_places(self) -> None:
        self.assertEqual(format_inr(4250), "₹4,250.00")
        self.assertEqual(format_inr(21.81), "₹21.81")

    def test_model_costs_are_formatted_in_inr(self) -> None:
        self.assertEqual(format_cost(21.81), "₹1,853.85")

    def test_inr_inputs_round_trip_through_existing_model_cost_units(self) -> None:
        fuel_price_inr = 1.10 * INR_PER_COST_UNIT
        driver_cost_inr = 25.0 * INR_PER_COST_UNIT

        self.assertEqual(inr_to_cost_units(fuel_price_inr), 1.10)
        self.assertEqual(inr_to_cost_units(driver_cost_inr), 25.0)
        self.assertEqual(cost_units_to_inr(1.10), fuel_price_inr)

    def test_cost_conversion_rejects_non_finite_amounts(self) -> None:
        for conversion in (inr_to_cost_units, cost_units_to_inr, format_inr):
            with self.subTest(conversion=conversion.__name__):
                with self.assertRaises(ValueError):
                    conversion(float("inf"))

    def test_demo_model_costs_remain_unchanged(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )

        self.assertEqual(scenario.cost_weights.distance_cost_per_km, 0.099)
        self.assertEqual(result.total_cost, sum(route.total_cost for route in result.routes))

    def test_route_map_popup_uses_inr(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        summary = RouteSummary(
            routes=result.routes,
            total_cost=result.total_cost,
            total_distance_km=sum(route.distance_km for route in result.routes),
            total_travel_time_min=sum(route.travel_time_min for route in result.routes),
        )
        run = OptimizationRun(
            classical=summary,
            quantum=None,
            quantum_result=None,
            quantum_error="not requested in test",
            objective_delta=None,
            relative_gap_percent=None,
        )

        map_view = build_route_map(scenario, run, show_quantum=False)

        map_html = map_view.map.get_root().render()
        self.assertTrue(
            any(format_cost(route.total_cost) in map_html for route in result.routes)
        )


if __name__ == "__main__":
    unittest.main()
