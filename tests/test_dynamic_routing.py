import unittest
from unittest.mock import patch

from quantum_route_optimisation import (
    FeasibleRoute,
    OptimizationResult,
    QAOAConfig,
    RouteCostWeights,
    RoutePlan,
    TravelData,
    Vehicle,
    generate_feasible_routes,
    optimize_classically,
)
from route_dashboard.dynamic import (
    calculate_impact_change,
    reoptimize_for_traffic,
    scenario_with_traffic,
)
from route_dashboard.map_view import build_route_map
from route_dashboard.optimization import (
    MAX_LOCAL_QAOA_VARIABLES,
    OptimizationRun,
    RouteSummary,
    optimize_scenario,
    validate_route_set,
)
from route_dashboard.routing import TRAFFIC_FACTORS, get_osrm_route_geometry
from route_dashboard.scenario import DeliveryStop, build_demo_scenario, build_scenario


def _tiny_scenario(*, use_osrm: bool = False):
    stops = (
        DeliveryStop("North", 37.781, -122.411, 1.0, 480, 900, 5),
        DeliveryStop("South", 37.768, -122.405, 1.0, 480, 900, 5),
    )
    return build_scenario(
        stops,
        vehicle_count=1,
        vehicle_capacity=2,
        depot_name="Depot",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1020,
        traffic_condition="Normal",
        fuel_type="Diesel",
        fuel_price_per_unit=1.10,
        driver_cost_per_hour=30,
        use_osrm=use_osrm,
    )


def _summary(routes: tuple[FeasibleRoute, ...]) -> RouteSummary:
    return RouteSummary(
        routes=routes,
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )


class DynamicRoutingTests(unittest.TestCase):
    def test_traffic_change_recalculates_duration_without_changing_local_distance(self) -> None:
        before = _tiny_scenario()
        after = scenario_with_traffic(before, "Storm")
        arc = ("depot", "delivery-1")

        self.assertEqual(tuple(TRAFFIC_FACTORS), ("Calm", "Normal", "Storm", "Light", "Moderate", "Heavy", "Severe"))
        self.assertEqual(before.travel.distances_km[arc], after.travel.distances_km[arc])
        self.assertAlmostEqual(
            after.travel.durations_min[arc] / before.travel.durations_min[arc],
            TRAFFIC_FACTORS["Storm"] / TRAFFIC_FACTORS["Normal"],
        )
        self.assertEqual(after.traffic_condition, "Storm")

    def test_traffic_incident_reruns_and_revalidates_both_optimizers(self) -> None:
        scenario = _tiny_scenario()
        config = QAOAConfig(reps=1, maxiter=10, shots=512, seed=19)
        before_run = optimize_scenario(scenario, config)

        comparison = reoptimize_for_traffic(scenario, before_run, "Heavy", config)

        self.assertIsNone(comparison.error)
        self.assertIsNotNone(comparison.after_run)
        self.assertEqual(comparison.after_scenario.traffic_condition, "Heavy")
        self.assertTrue(
            validate_route_set(
                comparison.after_run.classical.routes,
                comparison.after_scenario,
            )
        )
        if comparison.after_run.quantum is not None:
            self.assertTrue(
                validate_route_set(
                    comparison.after_run.quantum.routes,
                    comparison.after_scenario,
                )
            )
        else:
            self.assertTrue(comparison.after_run.quantum_error)
        self.assertIsNotNone(comparison.classical)
        self.assertIsNotNone(comparison.quantum)

        map_view = build_route_map(
            comparison.after_scenario,
            comparison.after_run,
            traffic_comparison=comparison,
        )
        map_html = map_view.map.get_root().render()
        self.assertIn("Before | Classical", map_html)
        self.assertIn("After | Classical", map_html)
        self.assertIn("Straight-line/local route visualization", map_view.geometry_status)

    def test_before_after_deltas_are_actual_after_minus_before_values(self) -> None:
        scenario = _tiny_scenario()
        changed = scenario_with_traffic(scenario, "Heavy")
        classical_before = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        classical_after = optimize_classically(
            changed.vehicles,
            changed.deliveries,
            changed.travel,
            changed.cost_weights,
        )
        before_routes = validate_route_set(classical_before.routes, scenario)
        after_routes = validate_route_set(classical_after.routes, changed)

        change = calculate_impact_change(
            scenario,
            _summary(before_routes),
            changed,
            _summary(after_routes),
        )

        self.assertAlmostEqual(
            change.distance_change_km,
            change.after.distance_km - change.before.distance_km,
        )
        self.assertAlmostEqual(
            change.travel_time_change_min,
            change.after.travel_time_min - change.before.travel_time_min,
        )
        self.assertAlmostEqual(change.cost_change, change.after.cost - change.before.cost)
        self.assertAlmostEqual(change.fuel_change, change.after.fuel_used - change.before.fuel_used)
        self.assertAlmostEqual(
            change.tailpipe_co2_change_kg,
            change.after.tailpipe_co2_kg - change.before.tailpipe_co2_kg,
        )

    def test_multivehicle_routes_respect_capacity_and_unique_delivery_assignment(self) -> None:
        scenario = build_demo_scenario()
        result = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        routes = validate_route_set(result.routes, scenario)
        delivery_map = {delivery.delivery_id: delivery for delivery in scenario.deliveries}
        vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}

        assigned = [delivery_id for route in routes for delivery_id in route.plan.delivery_ids]
        self.assertCountEqual(assigned, [delivery.delivery_id for delivery in scenario.deliveries])
        self.assertEqual(len({route.plan.vehicle_id for route in routes}), len(routes))
        for route in routes:
            demand = sum(delivery_map[item].demand for item in route.plan.delivery_ids)
            self.assertLessEqual(demand, vehicle_map[route.plan.vehicle_id].capacity)

    def test_duplicate_delivery_and_duplicate_vehicle_routes_are_rejected(self) -> None:
        scenario = _tiny_scenario()
        candidates = generate_feasible_routes(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        north_only = next(route for route in candidates if route.plan.delivery_ids == ("delivery-1",))
        south_only = next(route for route in candidates if route.plan.delivery_ids == ("delivery-2",))

        with self.assertRaisesRegex(ValueError, "does not serve every delivery exactly once"):
            validate_route_set((north_only, north_only), scenario)
        with self.assertRaisesRegex(ValueError, "assigns a vehicle more than once"):
            validate_route_set((north_only, south_only), scenario)

    def test_osrm_fallback_is_reused_during_traffic_change(self) -> None:
        with patch("route_dashboard.routing._fetch_osrm_table", side_effect=OSError("offline")) as fetch:
            scenario = _tiny_scenario(use_osrm=True)
            updated = scenario_with_traffic(scenario, "Storm")

        fetch.assert_called_once()
        self.assertTrue(scenario.osrm_requested)
        self.assertFalse(scenario.osrm_active)
        self.assertFalse(updated.osrm_active)
        self.assertIn("OSRM unavailable", updated.data_source)

    def test_osrm_geometry_is_used_only_when_returned(self) -> None:
        points = ((37.7749, -122.4194), (37.781, -122.411))
        with patch(
            "route_dashboard.routing._fetch_osrm_route_geometry_cached",
            return_value=(points, None),
        ):
            geometry, error = get_osrm_route_geometry(points)

        self.assertEqual(geometry, points)
        self.assertIsNone(error)

    def test_invalid_optimizer_result_is_reported_without_substitution(self) -> None:
        scenario = _tiny_scenario()
        routes = generate_feasible_routes(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        valid_routes = validate_route_set(
            (next(route for route in routes if len(route.plan.delivery_ids) == 2),),
            scenario,
        )
        valid_run = OptimizationRun(
            classical=_summary(valid_routes),
            quantum=None,
            quantum_result=None,
            quantum_error="not requested in test baseline",
            objective_delta=None,
            relative_gap_percent=None,
        )
        invalid_run = OptimizationRun(
            classical=_summary((valid_routes[0], valid_routes[0])),
            quantum=None,
            quantum_result=None,
            quantum_error="invalid test result",
            objective_delta=None,
            relative_gap_percent=None,
        )

        with patch("route_dashboard.dynamic.optimize_scenario", return_value=invalid_run):
            comparison = reoptimize_for_traffic(
                scenario,
                valid_run,
                "Storm",
                QAOAConfig(reps=1, maxiter=5, shots=128, seed=11),
            )

        self.assertIsNone(comparison.after_run)
        self.assertIsNone(comparison.classical)
        self.assertIn("prior result remains active", comparison.error)
        self.assertIn("exactly once", comparison.error)


if __name__ == "__main__":
    unittest.main()