import unittest
from unittest.mock import patch

from quantum_route_optimisation import QAOAConfig
from route_dashboard.optimization import optimize_scenario
from route_dashboard.routing import TRAFFIC_FACTORS, build_travel_data
from route_dashboard.scenario import build_demo_scenario, build_scenario, demo_stops


class DashboardScenarioTests(unittest.TestCase):
    def test_demo_builds_existing_models_and_complete_travel_matrix(self) -> None:
        scenario = build_demo_scenario()

        self.assertEqual(len(scenario.vehicles), 2)
        self.assertEqual(len(scenario.deliveries), 3)
        self.assertEqual(scenario.vehicles[0].start_location, "depot")
        self.assertEqual(scenario.deliveries[0].demand, 1.0)
        self.assertIn(("depot", "delivery-1"), scenario.travel.distances_km)
        self.assertEqual(scenario.data_source, "Local coordinate estimate")

    def test_traffic_changes_time_but_not_local_distance(self) -> None:
        light = build_demo_scenario("Light")
        heavy = build_demo_scenario("Heavy")
        arc = ("depot", "delivery-1")

        self.assertEqual(light.travel.distances_km[arc], heavy.travel.distances_km[arc])
        self.assertAlmostEqual(
            heavy.travel.durations_min[arc] / light.travel.durations_min[arc],
            TRAFFIC_FACTORS["Heavy"] / TRAFFIC_FACTORS["Light"],
        )

    def test_requested_osrm_failure_falls_back_to_local_estimates(self) -> None:
        coordinates = {"depot": (37.7749, -122.4194), "stop": (37.79, -122.41)}
        with patch("route_dashboard.routing._fetch_osrm_table", side_effect=OSError("offline")):
            travel, source = build_travel_data(coordinates, "Moderate", use_osrm=True)

        self.assertIn("Local estimate", source)
        self.assertIn("offline", source)
        self.assertGreater(travel.distances_km[("depot", "stop")], 0)

    def test_osrm_matrix_uses_location_ids_and_applies_traffic(self) -> None:
        coordinates = {"depot": (37.7749, -122.4194), "stop": (37.79, -122.41)}
        with patch(
            "route_dashboard.routing._fetch_osrm_table",
            return_value=([[0, 1500], [1600, 0]], [[0, 120], [180, 0]]),
        ):
            travel, source = build_travel_data(coordinates, "Heavy", use_osrm=True)

        self.assertEqual(source, "OSRM public table service")
        self.assertEqual(travel.distances_km[("depot", "stop")], 1.5)
        self.assertEqual(travel.durations_min[("depot", "stop")], 3.1)

    def test_fuel_price_and_driver_cost_feed_existing_cost_weights(self) -> None:
        scenario = build_scenario(
            demo_stops(),
            vehicle_count=1,
            vehicle_capacity=5,
            depot_name="Depot",
            depot_latitude=37.7749,
            depot_longitude=-122.4194,
            shift_start_min=480,
            shift_end_min=1020,
            traffic_condition="Moderate",
            fuel_type="Diesel",
            fuel_price_per_unit=2.0,
            driver_cost_per_hour=30.0,
        )

        self.assertAlmostEqual(scenario.cost_weights.distance_cost_per_km, 0.18)
        self.assertEqual(scenario.cost_weights.time_cost_per_min, 0.5)

    def test_demo_runs_both_optimizers_and_revalidates_routes(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_scenario(
            scenario,
            QAOAConfig(reps=1, maxiter=25, shots=1024, seed=7),
        )

        self.assertIsNone(result.quantum_error)
        self.assertIsNotNone(result.quantum)
        self.assertEqual(
            result.classical.total_cost,
            sum(route.total_cost for route in result.classical.routes),
        )
        for summary in (result.classical, result.quantum):
            self.assertIsNotNone(summary)
            served = [
                delivery_id
                for route in summary.routes
                for delivery_id in route.plan.delivery_ids
            ]
            self.assertCountEqual(served, [delivery.delivery_id for delivery in scenario.deliveries])

    def test_large_qubo_is_reported_without_running_local_qaoa(self) -> None:
        scenario = build_scenario(
            demo_stops(),
            vehicle_count=2,
            vehicle_capacity=4,
            depot_name="Depot",
            depot_latitude=37.7749,
            depot_longitude=-122.4194,
            shift_start_min=480,
            shift_end_min=1020,
            traffic_condition="Moderate",
            fuel_type="Diesel",
            fuel_price_per_unit=1.10,
        )
        with patch(
            "route_dashboard.optimization.compare_classical_and_qaoa"
        ) as benchmark:
            result = optimize_scenario(scenario, QAOAConfig(seed=7))

        benchmark.assert_not_called()
        self.assertIsNone(result.quantum)
        self.assertIn("30 variables", result.quantum_error)


if __name__ == "__main__":
    unittest.main()