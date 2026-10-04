"""Normalized multi-objective profiles for existing route optimizers."""

from dataclasses import dataclass, replace
from enum import Enum
from math import isclose, isfinite

from quantum_route_optimisation import QAOAConfig, RouteCostWeights

from .optimization import OptimizationRun, RouteSummary, optimize_scenario, validate_route_set
from .scenario import FUEL_OPTIONS, ScenarioProblem


class ObjectiveName(str, Enum):
    COST = "Cost Priority"
    TIME = "Time Priority"
    GREEN = "Green Priority"
    BALANCED = "Balanced"


OBJECTIVE_DESCRIPTIONS = {
    ObjectiveName.COST: "Prioritize lower operating cost.",
    ObjectiveName.TIME: "Prioritize faster delivery.",
    ObjectiveName.GREEN: "Prioritize lower fuel consumption and CO2.",
    ObjectiveName.BALANCED: "Balance cost, travel time, and environmental impact.",
}


@dataclass(frozen=True)
class ObjectiveWeights:
    cost: float
    time: float
    green: float

    def __post_init__(self) -> None:
        values = (self.cost, self.time, self.green)
        if any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("objective weights must be finite and non-negative")
        if not isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("normalized objective weights must sum to 1")


@dataclass(frozen=True)
class ObjectiveConfig:
    name: ObjectiveName
    weights: ObjectiveWeights

    @classmethod
    def for_name(cls, name: ObjectiveName | str) -> "ObjectiveConfig":
        try:
            objective = ObjectiveName(name)
        except ValueError as error:
            raise ValueError(f"unsupported optimization objective: {name}") from error
        profiles = {
            ObjectiveName.COST: ObjectiveWeights(1.0, 0.0, 0.0),
            ObjectiveName.TIME: ObjectiveWeights(0.0, 1.0, 0.0),
            ObjectiveName.GREEN: ObjectiveWeights(0.0, 0.0, 1.0),
            ObjectiveName.BALANCED: ObjectiveWeights(1 / 3, 1 / 3, 1 / 3),
        }
        return cls(objective, profiles[objective])


@dataclass(frozen=True)
class ObjectiveScales:
    cost: float
    elapsed_time_min: float
    fuel: float
    co2_kg: float

    def __post_init__(self) -> None:
        if any(not isfinite(value) or value <= 0 for value in (
            self.cost,
            self.elapsed_time_min,
            self.fuel,
            self.co2_kg,
        )):
            raise ValueError("objective normalization scales must be finite and positive")


@dataclass(frozen=True)
class ObjectiveMetrics:
    cost: float
    elapsed_time_min: float
    fuel: float
    co2_kg: float


def objective_scales(scenario: ScenarioProblem) -> ObjectiveScales:
    """Use conservative scenario-wide bounds, independent of selected routes.

    Distance is bounded by the largest matrix arc times the maximum number of
    route legs across the fleet. Time is bounded by the sum of vehicle shifts.
    Fuel, CO2, and cost bounds are derived from those values and existing
    scenario coefficients. Ratios are therefore dimensionless and stable across
    candidate-route orderings.
    """
    delivery_count = len(scenario.deliveries)
    vehicle_count = max(1, len(scenario.vehicles))
    maximum_arc_distance = max(scenario.travel.distances_km.values(), default=0.0)
    distance_scale = max(
        1.0,
        maximum_arc_distance * (delivery_count + 1) * vehicle_count,
    )
    time_scale = max(
        1.0,
        sum(
            vehicle.shift_end_min - vehicle.shift_start_min
            for vehicle in scenario.vehicles
        ),
    )
    fuel = FUEL_OPTIONS[scenario.fuel_type]
    fuel_scale = max(1.0, distance_scale * fuel["consumption_per_km"])
    co2_scale = max(
        1.0,
        fuel_scale * fuel["tailpipe_co2_kg_per_unit"],
    )
    cost_scale = max(
        1.0,
        distance_scale * scenario.cost_weights.distance_cost_per_km
        + time_scale * scenario.cost_weights.time_cost_per_min
        + vehicle_count * scenario.cost_weights.fixed_vehicle_cost,
    )
    return ObjectiveScales(cost_scale, time_scale, fuel_scale, co2_scale)


def normalized_objective_value(
    metrics: ObjectiveMetrics,
    config: ObjectiveConfig,
    scales: ObjectiveScales,
) -> float:
    weights = config.weights
    green_value = 0.5 * (metrics.fuel / scales.fuel + metrics.co2_kg / scales.co2_kg)
    return (
        weights.cost * metrics.cost / scales.cost
        + weights.time * metrics.elapsed_time_min / scales.elapsed_time_min
        + weights.green * green_value
    )


def route_cost_weights_for_objective(
    scenario: ScenarioProblem,
    config: ObjectiveConfig,
    scales: ObjectiveScales | None = None,
) -> RouteCostWeights:
    """Translate normalized metrics into additive coefficients used by both solvers."""
    scales = scales or objective_scales(scenario)
    weights = config.weights
    fuel = FUEL_OPTIONS[scenario.fuel_type]
    green_distance_coefficient = 0.5 * weights.green * (
        fuel["consumption_per_km"] / scales.fuel
        + fuel["consumption_per_km"]
        * fuel["tailpipe_co2_kg_per_unit"]
        / scales.co2_kg
    )
    return RouteCostWeights(
        distance_cost_per_km=(
            weights.cost * scenario.cost_weights.distance_cost_per_km / scales.cost
            + green_distance_coefficient
        ),
        time_cost_per_min=(
            weights.cost * scenario.cost_weights.time_cost_per_min / scales.cost
            + weights.time / scales.elapsed_time_min
        ),
        fixed_vehicle_cost=(
            weights.cost * scenario.cost_weights.fixed_vehicle_cost / scales.cost
        ),
    )


def scenario_for_objective(
    scenario: ScenarioProblem,
    config: ObjectiveConfig,
    scales: ObjectiveScales | None = None,
) -> ScenarioProblem:
    """Return a scoring copy; physical scenario costs remain untouched."""
    return replace(
        scenario,
        cost_weights=route_cost_weights_for_objective(scenario, config, scales),
    )


def optimize_with_objective(
    scenario: ScenarioProblem,
    config: ObjectiveConfig,
    qaoa_config: QAOAConfig,
    normalization_scenario: ScenarioProblem | None = None,
) -> OptimizationRun:
    """Use existing classical/QAOA solvers, preserving physical route metrics."""
    scales = objective_scales(normalization_scenario or scenario)
    scoring_scenario = scenario_for_objective(scenario, config, scales)
    scored_run = optimize_scenario(scoring_scenario, qaoa_config)
    physical_classical = validate_route_set(scored_run.classical.routes, scenario)
    physical_quantum = (
        validate_route_set(scored_run.quantum.routes, scenario)
        if scored_run.quantum is not None
        else None
    )
    classical_objective_value = scored_run.classical.total_cost
    quantum_objective_value = (
        scored_run.quantum.total_cost if scored_run.quantum is not None else None
    )
    return replace(
        scored_run,
        classical=_summary(physical_classical),
        quantum=(
            _summary(physical_quantum) if physical_quantum is not None else None
        ),
        objective_name=config.name.value,
        classical_objective_value=classical_objective_value,
        quantum_objective_value=quantum_objective_value,
    )


def objective_metrics_for_routes(
    scenario: ScenarioProblem,
    routes,
) -> ObjectiveMetrics:
    fuel = FUEL_OPTIONS[scenario.fuel_type]
    distance = sum(route.distance_km for route in routes)
    # FeasibleRoute elapsed time includes driving, waiting, and service.
    elapsed = sum(route.elapsed_time_min for route in routes)
    fuel_used = distance * fuel["consumption_per_km"]
    return ObjectiveMetrics(
        cost=sum(route.total_cost for route in routes),
        elapsed_time_min=elapsed,
        fuel=fuel_used,
        co2_kg=fuel_used * fuel["tailpipe_co2_kg_per_unit"],
    )


def _summary(routes) -> RouteSummary:
    return RouteSummary(
        routes=tuple(routes),
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )
