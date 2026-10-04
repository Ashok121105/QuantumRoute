import unittest
from dataclasses import replace

from quantum_route_optimisation import QAOAConfig, Vehicle
from route_dashboard.fleet_disruption import (
    DeliveryPriority,
    VehicleStatus,
    build_fleet_disruption_demo_scenario,
    reoptimize_fleet,
)
from route_dashboard.optimization import optimize_scenario
from route_dashboard.scenario import DeliveryStop, build_scenario


CONFIG = QAOAConfig(reps=1, maxiter=5, shots=256, seed=31)


def _scenario(
    stops: tuple[DeliveryStop, ...],
    capacities: tuple[float, ...],
    traffic: str = "Moderate",
):
    base = build_scenario(
        stops,
        vehicle_count=len(capacities),
        vehicle_capacity=max(capacities),
        depot_name="Depot",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1020,
        traffic_condition=traffic,
        fuel_type="Diesel",
        fuel_price_per_unit=1.10,
        driver_cost_per_hour=25,
    )
    vehicles = tuple(
        Vehicle(
            vehicle_id=f"van-{index}",
            capacity=capacity,
            start_location="depot",
            end_location="depot",
            shift_start_min=480,
            shift_end_min=1020,
        )
        for index, capacity in enumerate(capacities, start=1)
    )
    return replace(base, vehicles=vehicles)


def _balanced_stops() -> tuple[DeliveryStop, ...]:
    coordinates = (
        (37.7810, -122.4110),
        (37.7680, -122.4050),
        (37.7890, -122.4250),
        (37.7970, -122.4000),
    )
    return tuple(
        DeliveryStop(f"Order {index}", latitude, longitude, 1.0, 480, 900, 5)
        for index, (latitude, longitude) in enumerate(coordinates, start=1)
    )


class FleetDisruptionTests(unittest.TestCase):
    def test_unavailable_vehicle_is_excluded_from_after_routes(self) -> None:
        scenario = _scenario(_balanced_stops()[:2], (2.0, 2.0))
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-1": VehicleStatus.UNAVAILABLE},
            {},
            "Moderate",
            CONFIG,
        )

        self.assertEqual(result.vehicle_statuses["van-1"], VehicleStatus.UNAVAILABLE)
        self.assertEqual(tuple(vehicle.vehicle_id for vehicle in result.optimization_scenario.vehicles), ("van-2",))
        self.assertTrue(
            all(route.plan.vehicle_id != "van-1" for route in result.after_run.classical.routes)
        )

    def test_priorities_are_applied_lexicographically_under_capacity_shortage(self) -> None:
        scenario = _scenario(_balanced_stops(), (2.0, 2.0))
        baseline = optimize_scenario(scenario, CONFIG)
        priorities = {
            "delivery-1": DeliveryPriority.CRITICAL,
            "delivery-2": DeliveryPriority.HIGH,
            "delivery-3": DeliveryPriority.NORMAL,
            "delivery-4": DeliveryPriority.LOW,
        }
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-2": VehicleStatus.UNAVAILABLE},
            priorities,
            "Moderate",
            CONFIG,
        )

        assigned = set(result.assigned_delivery_ids)
        self.assertTrue({"delivery-1", "delivery-2"}.issubset(assigned))
        self.assertEqual({item.delivery_id for item in result.unassigned_deliveries}, {"delivery-3", "delivery-4"})
        self.assertEqual(
            {item.priority for item in result.unassigned_deliveries},
            {DeliveryPriority.NORMAL, DeliveryPriority.LOW},
        )

    def test_four_vehicle_breakdown_reassigns_routes_and_respects_capacities(self) -> None:
        scenario = build_fleet_disruption_demo_scenario()
        baseline = optimize_scenario(scenario, CONFIG)
        priorities = {
            "delivery-1": DeliveryPriority.CRITICAL,
            "delivery-2": DeliveryPriority.HIGH,
            "delivery-3": DeliveryPriority.NORMAL,
            "delivery-4": DeliveryPriority.LOW,
            "delivery-5": DeliveryPriority.NORMAL,
            "delivery-6": DeliveryPriority.CRITICAL,
            "delivery-7": DeliveryPriority.HIGH,
            "delivery-8": DeliveryPriority.LOW,
        }
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-2": VehicleStatus.UNAVAILABLE},
            priorities,
            "Moderate",
            CONFIG,
        )

        self.assertEqual(len(scenario.vehicles), 4)
        self.assertEqual(len(scenario.deliveries), 8)
        self.assertEqual(result.after_metrics.vehicles_available, 3)
        self.assertEqual(result.after_metrics.deliveries_served, 7)
        self.assertEqual(result.after_metrics.deliveries_unassigned, 1)
        self.assertGreaterEqual(len({delivery.window_end_min for delivery in scenario.deliveries}), 4)
        self.assertEqual(result.unassigned_deliveries[0].priority, DeliveryPriority.LOW)
        self.assertTrue(
            {"delivery-1", "delivery-2", "delivery-6", "delivery-7"}.issubset(
                result.assigned_delivery_ids
            )
        )
        self.assertGreaterEqual(len(result.reassignments), 1)
        vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in result.optimization_scenario.vehicles}
        delivery_map = {delivery.delivery_id: delivery for delivery in result.optimization_scenario.deliveries}
        for route in result.after_run.classical.routes:
            demand = sum(delivery_map[item].demand for item in route.plan.delivery_ids)
            self.assertLessEqual(demand, vehicle_map[route.plan.vehicle_id].capacity)

    def test_capacity_waitlist_reason_is_explicit(self) -> None:
        scenario = build_fleet_disruption_demo_scenario()
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-2": VehicleStatus.UNAVAILABLE},
            {"delivery-8": DeliveryPriority.LOW},
            "Moderate",
            CONFIG,
        )

        self.assertEqual(len(result.unassigned_deliveries), 1)
        self.assertEqual(result.unassigned_deliveries[0].delivery_id, "delivery-8")
        self.assertIn("capacity", result.unassigned_deliveries[0].reason.lower())
        self.assertEqual(result.unassigned_deliveries[0].priority, DeliveryPriority.LOW)

    def test_time_window_violation_is_reported_as_waitlist_reason(self) -> None:
        stops = (
            DeliveryStop("Flexible", 37.781, -122.411, 1.0, 480, 900, 5),
            DeliveryStop("Tight window", 37.790, -122.411, 1.0, 480, 486, 5),
        )
        scenario = _scenario(stops, (2.0, 2.0), "Moderate")
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {},
            {"delivery-2": DeliveryPriority.LOW},
            "Heavy",
            CONFIG,
        )

        waitlisted = next(item for item in result.unassigned_deliveries if item.delivery_id == "delivery-2")
        self.assertIn("time window", waitlisted.reason.lower())
        self.assertEqual(result.after_scenario.traffic_condition, "Heavy")

    def test_before_after_metrics_are_computed_from_validated_routes(self) -> None:
        scenario = build_fleet_disruption_demo_scenario()
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-2": VehicleStatus.UNAVAILABLE},
            {},
            "Moderate",
            CONFIG,
        )

        before_distance = sum(route.distance_km for route in result.before_run.classical.routes)
        after_distance = sum(route.distance_km for route in result.after_run.classical.routes)
        self.assertAlmostEqual(result.before_metrics.total_distance_km, before_distance)
        self.assertAlmostEqual(result.after_metrics.total_distance_km, after_distance)
        self.assertAlmostEqual(
            result.after_metrics.total_cost,
            sum(route.total_cost for route in result.after_run.classical.routes),
        )
        self.assertGreaterEqual(result.before_metrics.deliveries_served, result.after_metrics.deliveries_served)

    def test_heavy_traffic_and_vehicle_breakdown_are_applied_together(self) -> None:
        scenario = _scenario(_balanced_stops()[:2], (2.0, 2.0), "Moderate")
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-1": VehicleStatus.UNAVAILABLE},
            {},
            "Heavy",
            CONFIG,
        )

        self.assertEqual(result.after_scenario.traffic_condition, "Heavy")
        self.assertEqual(result.after_metrics.vehicles_available, 1)
        self.assertEqual(result.after_metrics.deliveries_served, 2)
        self.assertTrue(
            all(route.plan.vehicle_id == "van-2" for route in result.after_run.classical.routes)
        )

    def test_supported_problem_size_runs_existing_qaoa(self) -> None:
        scenario = _scenario(_balanced_stops()[:2], (2.0,))
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(scenario, baseline, {}, {}, "Moderate", CONFIG)

        self.assertIsNotNone(result.after_run.quantum)
        self.assertIsNone(result.after_run.quantum_error)
        self.assertIn("valid route set", result.qaoa_status)

    def test_oversized_problem_keeps_classical_and_reports_qaoa_limit(self) -> None:
        scenario = build_fleet_disruption_demo_scenario()
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(
            scenario,
            baseline,
            {"van-2": VehicleStatus.UNAVAILABLE},
            {},
            "Moderate",
            CONFIG,
        )

        self.assertIsNotNone(result.after_run.classical)
        self.assertIsNone(result.after_run.quantum)
        self.assertIn("variables", result.qaoa_status)
        self.assertIn("limit", result.qaoa_status)

    def test_no_breakdown_preserves_existing_classical_optimizer_result(self) -> None:
        scenario = _scenario(_balanced_stops()[:2], (2.0,))
        baseline = optimize_scenario(scenario, CONFIG)
        result = reoptimize_fleet(scenario, baseline, {}, {}, "Moderate", CONFIG)

        self.assertEqual(result.before_run.classical.total_cost, result.after_run.classical.total_cost)
        self.assertEqual(
            tuple(route.plan for route in result.before_run.classical.routes),
            tuple(route.plan for route in result.after_run.classical.routes),
        )


if __name__ == "__main__":
    unittest.main()
