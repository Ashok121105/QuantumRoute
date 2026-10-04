"""Run a small real Aer/QAOA route comparison against the exact baseline."""

from quantum_route_optimisation import (
    Delivery,
    QAOAConfig,
    RouteCostWeights,
    TravelData,
    Vehicle,
    compare_classical_and_qaoa,
)


def main() -> None:
    vehicles = [Vehicle("van-1", 2, "depot", "depot", 0, 100)]
    deliveries = [
        Delivery("A", "A", 1, 0, 100),
        Delivery("B", "B", 1, 0, 100),
    ]
    distances_km = {
        ("depot", "A"): 2,
        ("A", "depot"): 2,
        ("depot", "B"): 3,
        ("B", "depot"): 3,
        ("A", "B"): 1,
        ("B", "A"): 5,
    }
    durations_min = {
        ("depot", "A"): 4,
        ("A", "depot"): 4,
        ("depot", "B"): 6,
        ("B", "depot"): 6,
        ("A", "B"): 2,
        ("B", "A"): 10,
    }
    comparison = compare_classical_and_qaoa(
        vehicles,
        deliveries,
        TravelData(distances_km, durations_min),
        RouteCostWeights(distance_cost_per_km=1),
        QAOAConfig(reps=1, maxiter=30, shots=1024, seed=7),
    )

    for label, routes, objective, distance, valid in (
        (
            "Classical exact",
            comparison.classical.routes,
            comparison.classical.total_cost,
            sum(route.distance_km for route in comparison.classical.routes),
            True,
        ),
        (
            "QAOA / Aer",
            comparison.quantum.routes,
            comparison.quantum.objective_value,
            comparison.quantum.total_distance_km,
            comparison.quantum.is_valid,
        ),
    ):
        print(f"{label}: objective/cost={objective:.2f}, distance={distance:.2f} km, valid={valid}")
        for route in routes:
            sequence = " -> ".join(route.plan.delivery_ids)
            print(f"  {route.plan.vehicle_id}: {sequence}; cost={route.total_cost:.2f}")

    gap = comparison.relative_gap_percent
    gap_text = "undefined (classical objective is zero)" if gap is None else f"{gap:+.2f}%"
    print(
        "Comparison: "
        f"QAOA minus exact cost={comparison.objective_delta:+.2f}; "
        f"relative gap={gap_text}; "
        f"same objective={comparison.quantum_matches_classical}"
    )
    print(
        "QAOA sample: "
        f"QUBO energy={comparison.quantum.qubo_energy:.2f}, "
        f"probability={comparison.quantum.sample_probability:.4f}, "
        f"unique samples={comparison.quantum.observed_unique_samples}"
    )


if __name__ == "__main__":
    main()