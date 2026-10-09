"""Comparable exact-classical and QAOA benchmark for the same routing inputs."""

from dataclasses import dataclass
from typing import Sequence

from .classical import OptimizationResult, optimize_classically
from .models import Delivery, FeasibleRoute, RouteCostWeights, TravelData, Vehicle
from .qaoa import QAOAConfig, QuantumOptimizationResult, solve_qaoa


@dataclass(frozen=True)
class OptimizationComparison:
    classical: OptimizationResult
    quantum: QuantumOptimizationResult
    objective_delta: float
    relative_gap_percent: float | None

    @property
    def quantum_matches_classical(self) -> bool:
        return self.quantum.objective_value == self.classical.total_cost


def compare_classical_and_qaoa(
    vehicles: Sequence[Vehicle],
    deliveries: Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
    qaoa_config: QAOAConfig = QAOAConfig(),
    *,
    feasible_routes: Sequence[FeasibleRoute] | None = None,
) -> OptimizationComparison:
    """Run both optimizers on identical inputs and compare the route-cost objective."""
    classical = optimize_classically(
        vehicles,
        deliveries,
        travel,
        cost_weights,
        feasible_routes=feasible_routes,
    )
    quantum = solve_qaoa(
        vehicles,
        deliveries,
        travel,
        cost_weights,
        qaoa_config,
        feasible_routes=feasible_routes,
    )
    delta = quantum.objective_value - classical.total_cost
    relative_gap = (
        None
        if classical.total_cost == 0
        else delta / classical.total_cost * 100.0
    )
    return OptimizationComparison(
        classical=classical,
        quantum=quantum,
        objective_delta=delta,
        relative_gap_percent=relative_gap,
    )