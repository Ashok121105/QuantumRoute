"""Run and revalidate the existing classical and QAOA optimizers."""

from dataclasses import dataclass, replace
from typing import Sequence

from quantum_route_optimisation import (
    FeasibleRoute,
    InvalidQuantumSampleError,
    NoFeasibleQuantumSampleError,
    OptimizationComparison,
    OptimizationResult,
    QAOAConfig,
    QuantumOptimizationResult,
    RoutePlan,
    compare_classical_and_qaoa,
    evaluate_route,
    generate_feasible_routes,
    optimize_classically,
)

from .scenario import ScenarioProblem


MAX_LOCAL_QAOA_VARIABLES = 22


@dataclass(frozen=True)
class RouteSummary:
    routes: tuple[FeasibleRoute, ...]
    total_cost: float
    total_distance_km: float
    total_travel_time_min: float


@dataclass(frozen=True)
class OptimizationRun:
    classical: RouteSummary
    quantum: RouteSummary | None
    quantum_result: QuantumOptimizationResult | None
    quantum_error: str | None
    objective_delta: float | None
    relative_gap_percent: float | None
    objective_name: str = "Cost Priority"
    classical_objective_value: float | None = None
    quantum_objective_value: float | None = None


def optimize_scenario(
    scenario: ScenarioProblem, qaoa_config: QAOAConfig
) -> OptimizationRun:
    """Use the shared benchmark, then independently revalidate display routes."""
    route_options = generate_feasible_routes(
        scenario.vehicles,
        scenario.deliveries,
        scenario.travel,
        scenario.cost_weights,
    )
    if len(route_options) > MAX_LOCAL_QAOA_VARIABLES:
        classical = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        validated_classical = validate_route_set(classical.routes, scenario)
        return OptimizationRun(
            classical=_summarize(validated_classical),
            quantum=None,
            quantum_result=None,
            quantum_error=(
                f"local Aer QAOA was not run: the route-selection QUBO has "
                f"{len(route_options)} variables; the dashboard limit is "
                f"{MAX_LOCAL_QAOA_VARIABLES}. Reduce deliveries or tighten capacity."
            ),
            objective_delta=None,
            relative_gap_percent=None,
        )

    try:
        comparison = compare_classical_and_qaoa(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
            qaoa_config,
        )
    except (NoFeasibleQuantumSampleError, InvalidQuantumSampleError) as error:
        classical = optimize_classically(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        validated_classical = validate_route_set(
            classical.routes,
            scenario,
        )
        return OptimizationRun(
            classical=_summarize(validated_classical),
            quantum=None,
            quantum_result=None,
            quantum_error=str(error),
            objective_delta=None,
            relative_gap_percent=None,
        )

    classical_routes = validate_route_set(comparison.classical.routes, scenario)
    quantum_routes = validate_route_set(comparison.quantum.routes, scenario)
    classical_result = OptimizationResult(
        classical_routes,
        sum(route.total_cost for route in classical_routes),
    )
    quantum_result = replace(
        comparison.quantum,
        routes=quantum_routes,
        objective_value=sum(route.total_cost for route in quantum_routes),
        total_distance_km=sum(route.distance_km for route in quantum_routes),
    )
    validated_comparison = OptimizationComparison(
        classical=classical_result,
        quantum=quantum_result,
        objective_delta=quantum_result.objective_value - classical_result.total_cost,
        relative_gap_percent=(
            None
            if classical_result.total_cost == 0
            else (quantum_result.objective_value - classical_result.total_cost)
            / classical_result.total_cost
            * 100.0
        ),
    )
    return OptimizationRun(
        classical=_summarize(validated_comparison.classical.routes),
        quantum=_summarize(validated_comparison.quantum.routes),
        quantum_result=validated_comparison.quantum,
        quantum_error=None,
        objective_delta=validated_comparison.objective_delta,
        relative_gap_percent=validated_comparison.relative_gap_percent,
    )


def validate_route_set(
    routes: Sequence[FeasibleRoute], scenario: ScenarioProblem
) -> tuple[FeasibleRoute, ...]:
    vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    delivery_map = {delivery.delivery_id: delivery for delivery in scenario.deliveries}
    covered: list[str] = []
    used_vehicles: list[str] = []
    validated: list[FeasibleRoute] = []

    for route in routes:
        if route.plan.vehicle_id not in vehicle_map:
            raise ValueError(f"optimizer returned unknown vehicle {route.plan.vehicle_id}")
        used_vehicles.append(route.plan.vehicle_id)
        covered.extend(route.plan.delivery_ids)
        validated.append(
            evaluate_route(
                vehicle_map[route.plan.vehicle_id],
                RoutePlan(route.plan.vehicle_id, route.plan.delivery_ids),
                delivery_map,
                scenario.travel,
                scenario.cost_weights,
            )
        )

    required_deliveries = {delivery.delivery_id for delivery in scenario.deliveries}
    if len(covered) != len(set(covered)) or set(covered) != required_deliveries:
        raise ValueError("optimizer route set does not serve every delivery exactly once")
    if len(used_vehicles) != len(set(used_vehicles)):
        raise ValueError("optimizer route set assigns a vehicle more than once")
    return tuple(sorted(validated, key=lambda route: route.plan.vehicle_id))


def _summarize(routes: Sequence[FeasibleRoute]) -> RouteSummary:
    return RouteSummary(
        routes=tuple(routes),
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )