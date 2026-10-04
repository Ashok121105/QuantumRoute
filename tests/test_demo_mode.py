import unittest
from dataclasses import replace

from route_dashboard.demo_mode import (
    DEMO_QAOA_CONFIG,
    DEMO_TRAFFIC_CONDITION,
    DemoStage,
    build_hackathon_demo_scenario,
    build_sustainability_summary,
    qaoa_status,
    reset_demo_state,
    run_full_demo,
)
from route_dashboard.fleet_disruption import DeliveryPriority, VehicleStatus
from route_dashboard.objectives import ObjectiveName
from route_dashboard.optimization import OptimizationRun, RouteSummary


class HackathonDemoModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_full_demo()

    def test_demo_scenario_loads_with_small_multi_vehicle_fleet(self) -> None:
        scenario = build_hackathon_demo_scenario()

        self.assertEqual(len(scenario.vehicles), 2)
        self.assertEqual(len(scenario.deliveries), 2)
        self.assertEqual(
            tuple(vehicle.capacity for vehicle in scenario.vehicles),
            (2.0, 1.0),
        )
        self.assertEqual(sum(delivery.demand for delivery in scenario.deliveries), 2.0)
        self.assertEqual(scenario.traffic_condition, "Normal")
        self.assertEqual(scenario.fuel_type, "Diesel")
        self.assertFalse(scenario.osrm_requested)
        self.assertLessEqual(self.result.baseline_candidate_count, 22)

    def test_demo_scenario_is_deterministic(self) -> None:
        first = build_hackathon_demo_scenario()
        second = build_hackathon_demo_scenario()

        self.assertEqual(first.vehicles, second.vehicles)
        self.assertEqual(first.deliveries, second.deliveries)
        self.assertEqual(first.travel.distances_km, second.travel.distances_km)
        self.assertEqual(first.travel.durations_min, second.travel.durations_min)
        self.assertEqual(first.location_names, second.location_names)

    def test_baseline_stage_runs_existing_classical_and_qaoa_services(self) -> None:
        baseline = self.result.baseline_run

        self.assertEqual(baseline.objective_name, ObjectiveName.COST.value)
        self.assertTrue(baseline.classical.routes)
        self.assertTrue(self.result.baseline_qaoa.executed)
        self.assertTrue(self.result.baseline_qaoa.valid)
        self.assertIsNotNone(baseline.quantum)
        self.assertEqual(baseline.quantum_result.observed_unique_samples, self.result.baseline_qaoa.unique_samples)
        self.assertIn(DemoStage.BASELINE, self.result.completed_stages)

    def test_objective_stage_runs_all_four_profiles_with_physical_metrics(self) -> None:
        rows = self.result.objective_results

        self.assertEqual({row.objective for row in rows}, set(ObjectiveName))
        for row in rows:
            self.assertGreater(row.physical_summary.total_distance_km, 0)
            self.assertGreaterEqual(row.impact.cost, 0)
            self.assertGreaterEqual(row.impact.fuel_used, 0)
            self.assertGreaterEqual(row.impact.tailpipe_co2_kg, 0)
        cost = next(row for row in rows if row.objective is ObjectiveName.COST)
        green = next(row for row in rows if row.objective is ObjectiveName.GREEN)
        self.assertNotEqual(
            tuple(route.plan for route in cost.physical_summary.routes),
            tuple(route.plan for route in green.physical_summary.routes),
        )
        self.assertIn(DemoStage.OBJECTIVES, self.result.completed_stages)

    def test_traffic_stage_reoptimizes_normal_to_heavy(self) -> None:
        result = self.result.traffic_result

        self.assertEqual(result.before_scenario.traffic_condition, "Normal")
        self.assertEqual(result.after_scenario.traffic_condition, DEMO_TRAFFIC_CONDITION)
        self.assertIsNotNone(result.after_run)
        self.assertIn(DemoStage.TRAFFIC, self.result.completed_stages)

    def test_fleet_stage_disables_vehicle_and_reassigns_delivery(self) -> None:
        result = self.result.fleet_result

        self.assertEqual(
            result.vehicle_statuses["van-2"],
            VehicleStatus.UNAVAILABLE,
        )
        self.assertGreaterEqual(len(result.reassignments), 1)
        self.assertEqual(result.after_metrics.vehicles_available, 1)
        self.assertEqual(result.after_metrics.deliveries_served, 2)
        self.assertFalse(result.unassigned_deliveries)
        self.assertIn(DemoStage.FLEET, self.result.completed_stages)

    def test_sustainability_summary_uses_actual_fleet_metrics(self) -> None:
        summary = self.result.sustainability
        metrics = self.result.fleet_result.after_metrics

        self.assertEqual(summary.metrics, metrics)
        self.assertEqual(summary.objective_name, self.result.fleet_result.after_run.objective_name)
        self.assertEqual(summary.baseline_deliveries_served, 2)
        self.assertIn("not guaranteed savings", summary.cost_comparability_note)
        self.assertIn(DemoStage.SUSTAINABILITY, self.result.completed_stages)

    def test_qaoa_status_never_claims_a_result_that_was_not_returned(self) -> None:
        status = qaoa_status(
            self.result.baseline_run,
            self.result.baseline_candidate_count,
        )

        self.assertEqual(status.executed, self.result.baseline_run.quantum is not None)
        self.assertEqual(status.valid, self.result.baseline_run.quantum is not None)
        if status.valid:
            self.assertIsNotNone(status.sample_probability)
            self.assertGreater(status.unique_samples, 0)

    def test_oversized_qaoa_status_is_reported_as_skipped(self) -> None:
        summary = RouteSummary((), 0.0, 0.0, 0.0)
        oversized = OptimizationRun(
            classical=summary,
            quantum=None,
            quantum_result=None,
            quantum_error="local Aer QAOA was not run: 23 route-selection variables; limit is 22",
            objective_delta=None,
            relative_gap_percent=None,
        )

        status = qaoa_status(oversized, 23)

        self.assertFalse(status.executed)
        self.assertFalse(status.valid)
        self.assertIn("skipped", status.message.lower())
        self.assertIn("23", status.message)
        self.assertIn("22", status.message)

    def test_reset_restores_initial_demo_state(self) -> None:
        state = {
            "hackathon_demo_result": self.result,
            "hackathon_demo_error": "old error",
            "hackathon_demo_stage": DemoStage.FLEET,
            "unrelated_state": "preserved",
        }

        reset_demo_state(state)

        self.assertNotIn("hackathon_demo_result", state)
        self.assertNotIn("hackathon_demo_error", state)
        self.assertNotIn("hackathon_demo_stage", state)
        self.assertEqual(state["unrelated_state"], "preserved")


if __name__ == "__main__":
    unittest.main()
