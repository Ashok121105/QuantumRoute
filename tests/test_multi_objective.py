import unittest
from dataclasses import replace

from quantum_route_optimisation import (
    QAOAConfig,
    TravelData,
    Vehicle,
    build_route_qubo,
    generate_feasible_routes,
    optimize_classically,
)
from route_dashboard.dynamic import reoptimize_for_traffic
from route_dashboard.fleet_disruption import DeliveryPriority, VehicleStatus, reoptimize_fleet
from route_dashboard.objectives import (
    ObjectiveConfig,
    ObjectiveMetrics,
    ObjectiveName,
    ObjectiveScales,
    ObjectiveWeights,
    normalized_objective_value,
    objective_metrics_for_routes,
    objective_scales,
    optimize_with_objective,
    scenario_for_objective,
)
from route_dashboard.scenario import DeliveryStop, build_scenario


CONFIG = QAOAConfig(reps=1, maxiter=8, shots=512, seed=23)


def _tradeoff_scenario():
    stops = (
        DeliveryStop("A", 37.781, -122.411, 1.0, 480, 900, 5),
        DeliveryStop("B", 37.790, -122.411, 1.0, 480, 900, 5),
    )
    scenario = build_scenario(
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
        driver_cost_per_hour=20.0,
    )
    distances = {
        ("depot", "delivery-1"): 1.0,
        ("delivery-1", "delivery-2"): 28.0,
        ("delivery-2", "depot"): 1.0,
        ("depot", "delivery-2"): 1.0,
        ("delivery-2", "delivery-1"): 1.0,
        ("delivery-1", "depot"): 1.0,
    }
    durations = {
        ("depot", "delivery-1"): 1.0,
        ("delivery-1", "delivery-2"): 1.0,
        ("delivery-2", "depot"): 1.0,
        ("depot", "delivery-2"): 10.0,
        ("delivery-2", "delivery-1"): 10.0,
        ("delivery-1", "depot"): 10.0,
    }
    return replace(scenario, travel=TravelData(distances, durations))


def _plans_for_objective(scenario, name):
    scoring_scenario = scenario_for_objective(scenario, ObjectiveConfig.for_name(name))
    result = optimize_classically(
        scoring_scenario.vehicles,
        scoring_scenario.deliveries,
        scoring_scenario.travel,
        scoring_scenario.cost_weights,
    )
    return tuple(route.plan.delivery_ids for route in result.routes)


class MultiObjectiveTests(unittest.TestCase):
    def test_objective_profiles_are_normalized_and_named(self) -> None:
        for objective in ObjectiveName:
            config = ObjectiveConfig.for_name(objective)
            self.assertEqual(config.name, objective)
            self.assertAlmostEqual(sum((config.weights.cost, config.weights.time, config.weights.green)), 1.0)

        self.assertEqual(ObjectiveConfig.for_name("Cost Priority").weights, ObjectiveWeights(1, 0, 0))
        self.assertEqual(ObjectiveConfig.for_name("Time Priority").weights, ObjectiveWeights(0, 1, 0))
        self.assertEqual(ObjectiveConfig.for_name("Green Priority").weights, ObjectiveWeights(0, 0, 1))

    def test_invalid_weight_values_and_non_normalized_weights_are_rejected(self) -> None:
        for values in ((-0.1, 0.6, 0.5), (float("nan"), 0.5, 0.5), (0.2, 0.2, 0.2)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ObjectiveWeights(*values)

    def test_metric_normalization_is_dimensionless_and_deterministic(self) -> None:
        config = ObjectiveConfig.for_name(ObjectiveName.BALANCED)
        scales = ObjectiveScales(cost=100, elapsed_time_min=200, fuel=20, co2_kg=40)
        metrics = ObjectiveMetrics(cost=50, elapsed_time_min=100, fuel=10, co2_kg=20)

        self.assertAlmostEqual(normalized_objective_value(metrics, config, scales), 0.5)
        self.assertEqual(
            normalized_objective_value(metrics, config, scales),
            normalized_objective_value(metrics, config, scales),
        )

    def test_cost_time_green_and_balanced_can_select_different_tradeoffs(self) -> None:
        scenario = _tradeoff_scenario()
        cost_plan = _plans_for_objective(scenario, ObjectiveName.COST)
        time_plan = _plans_for_objective(scenario, ObjectiveName.TIME)
        green_plan = _plans_for_objective(scenario, ObjectiveName.GREEN)
        balanced_plan = _plans_for_objective(scenario, ObjectiveName.BALANCED)

        self.assertEqual(cost_plan, (("delivery-1", "delivery-2"),))
        self.assertEqual(time_plan, (("delivery-1", "delivery-2"),))
        self.assertEqual(cost_plan, time_plan)
        self.assertEqual(green_plan, (("delivery-2", "delivery-1"),))
        self.assertEqual(balanced_plan, (("delivery-2", "delivery-1"),))
        self.assertNotEqual(cost_plan, green_plan)

    def test_qubo_linear_objective_uses_selected_objective_route_costs(self) -> None:
        scenario = _tradeoff_scenario()
        scoring_scenario = scenario_for_objective(
            scenario,
            ObjectiveConfig.for_name(ObjectiveName.TIME),
        )
        routes = generate_feasible_routes(
            scoring_scenario.vehicles,
            scoring_scenario.deliveries,
            scoring_scenario.travel,
            scoring_scenario.cost_weights,
        )
        qubo = build_route_qubo(routes, scoring_scenario.deliveries)
        full_route_index = next(
            index for index, route in enumerate(routes)
            if route.plan.delivery_ids == ("delivery-1", "delivery-2")
        )
        assignment = [0] * len(routes)
        assignment[full_route_index] = 1

        self.assertAlmostEqual(
            qubo.problem.objective.evaluate(assignment),
            routes[full_route_index].total_cost,
        )
        self.assertLess(
            routes[full_route_index].total_cost,
            next(route.total_cost for route in routes if route.plan.delivery_ids == ("delivery-2", "delivery-1")),
        )

    def test_supported_size_runs_qaoa_and_keeps_physical_metrics(self) -> None:
        scenario = _tradeoff_scenario()
        config = ObjectiveConfig.for_name(ObjectiveName.TIME)
        run = optimize_with_objective(scenario, config, CONFIG)

        self.assertEqual(run.objective_name, ObjectiveName.TIME.value)
        self.assertIsNotNone(run.quantum)
        self.assertAlmostEqual(run.objective_delta, run.quantum_objective_value - run.classical_objective_value)
        self.assertAlmostEqual(
            run.classical_objective_value,
            normalized_objective_value(
                objective_metrics_for_routes(scenario, run.classical.routes),
                config,
                objective_scales(scenario),
            ),
        )
        self.assertAlmostEqual(
            run.classical.total_cost,
            sum(route.total_cost for route in run.classical.routes),
        )
        self.assertAlmostEqual(
            run.quantum_objective_value,
            run.quantum_result.objective_value,
        )

    def test_dynamic_traffic_reoptimization_preserves_selected_objective(self) -> None:
        scenario = _tradeoff_scenario()
        objective = ObjectiveConfig.for_name(ObjectiveName.GREEN)
        before = optimize_with_objective(scenario, objective, CONFIG)
        comparison = reoptimize_for_traffic(
            scenario,
            before,
            "Heavy",
            CONFIG,
            objective,
        )

        self.assertIsNone(comparison.error)
        self.assertEqual(comparison.after_run.objective_name, ObjectiveName.GREEN.value)
        self.assertIsNotNone(comparison.after_run.classical_objective_value)
        self.assertEqual(comparison.after_scenario.traffic_condition, "Heavy")

    def test_fleet_disruption_reoptimization_preserves_selected_objective(self) -> None:
        scenario = _tradeoff_scenario()
        scenario = replace(
            scenario,
            vehicles=(
                Vehicle("van-1", 2.0, "depot", "depot", 480, 1020),
                Vehicle("van-2", 2.0, "depot", "depot", 480, 1020),
            ),
        )
        objective = ObjectiveConfig.for_name(ObjectiveName.GREEN)
        before = optimize_with_objective(scenario, objective, CONFIG)
        result = reoptimize_fleet(
            scenario,
            before,
            {"van-1": VehicleStatus.UNAVAILABLE},
            {"delivery-1": DeliveryPriority.CRITICAL, "delivery-2": DeliveryPriority.HIGH},
            "Normal",
            CONFIG,
            objective,
        )

        self.assertIsNone(result.error)
        self.assertEqual(result.after_run.objective_name, ObjectiveName.GREEN.value)
        self.assertEqual(result.after_metrics.deliveries_served, 2)
        self.assertEqual(result.after_metrics.deliveries_unassigned, 0)
        self.assertTrue(
            all(route.plan.vehicle_id == "van-2" for route in result.after_run.classical.routes)
        )
        self.assertGreaterEqual(len(result.reassignments), 1)

    def test_cost_priority_preserves_existing_optimizer_routes_and_metrics(self) -> None:
        scenario = _tradeoff_scenario()
        original = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        run = optimize_with_objective(
            scenario,
            ObjectiveConfig.for_name(ObjectiveName.COST),
            CONFIG,
        )

        self.assertEqual(
            tuple(route.plan for route in original.routes),
            tuple(route.plan for route in run.classical.routes),
        )
        self.assertAlmostEqual(run.classical.total_cost, original.total_cost)


if __name__ == "__main__":
    unittest.main()
