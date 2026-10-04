import unittest

from quantum_route_optimisation import (
    Delivery,
    InvalidQuantumSampleError,
    QAOAConfig,
    RouteCostWeights,
    TravelData,
    Vehicle,
    build_route_qubo,
    compare_classical_and_qaoa,
    decode_route_selection,
    generate_feasible_routes,
)


class QuantumOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vehicles = [Vehicle("van-1", 2, "depot", "depot", 0, 100)]
        self.deliveries = [
            Delivery("A", "A", 1, 0, 100),
            Delivery("B", "B", 1, 0, 100),
        ]
        distances = {
            ("depot", "A"): 2,
            ("A", "depot"): 2,
            ("depot", "B"): 3,
            ("B", "depot"): 3,
            ("A", "B"): 1,
            ("B", "A"): 5,
        }
        self.travel = TravelData(distances, distances)
        self.weights = RouteCostWeights(distance_cost_per_km=1)
        self.routes = generate_feasible_routes(
            self.vehicles, self.deliveries, self.travel, self.weights
        )
        self.formulation = build_route_qubo(self.routes, self.deliveries)

    def test_qubo_matches_route_cost_for_feasible_assignment(self) -> None:
        selected_route_index = next(
            index
            for index, route in enumerate(self.routes)
            if set(route.plan.delivery_ids) == {"A", "B"}
        )
        assignment = [0] * len(self.routes)
        assignment[selected_route_index] = 1

        energy = self.formulation.problem.objective.evaluate(assignment)
        self.assertEqual(energy, self.routes[selected_route_index].total_cost)

    def test_qubo_penalizes_uncovered_deliveries_and_maps_to_ising(self) -> None:
        empty_assignment = [0] * len(self.routes)
        energy = self.formulation.problem.objective.evaluate(empty_assignment)
        operator, offset = self.formulation.problem.to_ising()

        self.assertEqual(energy, 2 * self.formulation.penalty_weight)
        self.assertEqual(operator.num_qubits, len(self.routes))
        self.assertIsInstance(offset, float)

    def test_qubo_penalizes_two_routes_assigned_to_one_vehicle(self) -> None:
        assignment = [0] * len(self.routes)
        selected_indexes = [
            index
            for index, route in enumerate(self.routes)
            if len(route.plan.delivery_ids) == 1
        ]
        self.assertEqual(len(selected_indexes), 2)
        for index in selected_indexes:
            assignment[index] = 1

        energy = self.formulation.problem.objective.evaluate(assignment)
        selected_cost = sum(self.routes[index].total_cost for index in selected_indexes)

        self.assertEqual(energy, selected_cost + self.formulation.penalty_weight)

    def test_decoder_revalidates_and_rejects_incomplete_samples(self) -> None:
        selected_route_index = next(
            index
            for index, route in enumerate(self.routes)
            if set(route.plan.delivery_ids) == {"A", "B"}
        )
        assignment = [0] * len(self.routes)
        assignment[selected_route_index] = 1
        decoded = decode_route_selection(
            assignment,
            self.formulation,
            self.vehicles,
            self.deliveries,
            self.travel,
            self.weights,
        )
        self.assertEqual(decoded.total_cost, self.routes[selected_route_index].total_cost)
        self.assertEqual(decoded.total_distance_km, self.routes[selected_route_index].distance_km)

        with self.assertRaises(InvalidQuantumSampleError):
            decode_route_selection(
                [0] * len(self.routes),
                self.formulation,
                self.vehicles,
                self.deliveries,
                self.travel,
                self.weights,
            )

    def test_aer_qaoa_benchmark_returns_a_valid_comparable_sample(self) -> None:
        comparison = compare_classical_and_qaoa(
            self.vehicles,
            self.deliveries,
            self.travel,
            self.weights,
            QAOAConfig(reps=1, maxiter=20, shots=1024, seed=7),
        )
        quantum = comparison.quantum

        self.assertTrue(quantum.is_valid)
        self.assertEqual(quantum.objective_value, sum(route.total_cost for route in quantum.routes))
        self.assertEqual(
            quantum.total_distance_km,
            sum(route.distance_km for route in quantum.routes),
        )
        self.assertEqual(
            comparison.objective_delta,
            quantum.objective_value - comparison.classical.total_cost,
        )
        self.assertGreater(quantum.observed_unique_samples, 0)


if __name__ == "__main__":
    unittest.main()