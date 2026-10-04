import unittest

from quantum_route_optimisation import (
    Delivery,
    NoFeasibleSolutionError,
    RouteCostWeights,
    TravelData,
    Vehicle,
    generate_feasible_routes,
    optimize_classically,
)


class ClassicalOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vehicles = [
            Vehicle("van-1", 2, "depot", "depot"),
            Vehicle("van-2", 2, "depot", "depot"),
        ]
        self.deliveries = [
            Delivery("A", "A", 1, 0, 100),
            Delivery("B", "B", 1, 0, 100),
            Delivery("C", "C", 1, 0, 100),
        ]
        distances = {
            ("depot", "A"): 1,
            ("A", "depot"): 1,
            ("depot", "B"): 1,
            ("B", "depot"): 1,
            ("depot", "C"): 4,
            ("C", "depot"): 4,
            ("A", "B"): 1,
            ("B", "A"): 1,
            ("A", "C"): 8,
            ("C", "A"): 8,
            ("B", "C"): 8,
            ("C", "B"): 8,
        }
        self.travel = TravelData(
            distances_km=distances,
            durations_min=distances,
        )
        self.weights = RouteCostWeights(distance_cost_per_km=1)

    def test_route_pool_contains_only_feasible_routes(self) -> None:
        routes = generate_feasible_routes(
            self.vehicles, self.deliveries, self.travel, self.weights
        )
        self.assertTrue(routes)
        self.assertTrue(all(len(route.plan.delivery_ids) <= 2 for route in routes))

    def test_optimizer_covers_every_delivery_once_with_distinct_vehicles(self) -> None:
        result = optimize_classically(
            self.vehicles, self.deliveries, self.travel, self.weights
        )

        covered = [
            delivery_id
            for route in result.routes
            for delivery_id in route.plan.delivery_ids
        ]
        self.assertCountEqual(covered, ["A", "B", "C"])
        self.assertEqual(len({route.plan.vehicle_id for route in result.routes}), 2)
        self.assertEqual(result.total_cost, 11)

    def test_infeasible_instance_raises_clear_error(self) -> None:
        one_small_vehicle = [Vehicle("van-1", 1, "depot", "depot")]
        with self.assertRaises(NoFeasibleSolutionError):
            optimize_classically(
                one_small_vehicle, self.deliveries, self.travel, self.weights
            )


if __name__ == "__main__":
    unittest.main()