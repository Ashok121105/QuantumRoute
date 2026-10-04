import unittest

from quantum_route_optimisation import (
    Delivery,
    RouteCostWeights,
    RouteInfeasibleError,
    RoutePlan,
    TravelData,
    Vehicle,
    evaluate_route,
)


class RouteFeasibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vehicle = Vehicle("van-1", 5, "depot", "depot", 0, 100)
        self.delivery = Delivery("order-1", "A", 4, 10, 20, 5)
        self.travel = TravelData(
            distances_km={("depot", "A"): 3, ("A", "depot"): 3},
            durations_min={("depot", "A"): 5, ("A", "depot"): 5},
        )

    def test_feasible_route_waits_for_window_and_calculates_cost(self) -> None:
        result = evaluate_route(
            self.vehicle,
            RoutePlan("van-1", ("order-1",)),
            [self.delivery],
            self.travel,
            RouteCostWeights(distance_cost_per_km=2, time_cost_per_min=0.5),
        )

        self.assertEqual(result.service_start_times, (("order-1", 10),))
        self.assertEqual(result.waiting_time_min, 5)
        self.assertEqual(result.distance_km, 6)
        self.assertEqual(result.elapsed_time_min, 20)
        self.assertEqual(result.total_cost, 22)

    def test_capacity_excess_is_rejected(self) -> None:
        vehicle = Vehicle("van-1", 3, "depot", "depot")
        with self.assertRaisesRegex(RouteInfeasibleError, "exceeds vehicle capacity"):
            evaluate_route(
                vehicle,
                RoutePlan("van-1", ("order-1",)),
                [self.delivery],
                self.travel,
                RouteCostWeights(),
            )

    def test_missed_delivery_window_is_rejected(self) -> None:
        delivery = Delivery("order-1", "A", 1, 0, 4)
        with self.assertRaisesRegex(RouteInfeasibleError, "misses its time window"):
            evaluate_route(
                self.vehicle,
                RoutePlan("van-1", ("order-1",)),
                [delivery],
                self.travel,
                RouteCostWeights(),
            )

    def test_return_after_shift_is_rejected(self) -> None:
        vehicle = Vehicle("van-1", 5, "depot", "depot", 0, 12)
        with self.assertRaisesRegex(RouteInfeasibleError, "shift ends"):
            evaluate_route(
                vehicle,
                RoutePlan("van-1", ("order-1",)),
                [self.delivery],
                self.travel,
                RouteCostWeights(),
            )


if __name__ == "__main__":
    unittest.main()