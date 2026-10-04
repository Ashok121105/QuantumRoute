"""Traffic-only scenario changes, solver reruns, and operational impact metrics."""

from dataclasses import dataclass, replace

from quantum_route_optimisation import FeasibleRoute, QAOAConfig

from .optimization import (
    OptimizationRun,
    RouteSummary,
    optimize_scenario,
    validate_route_set,
)
from .routing import TRAFFIC_FACTORS, build_travel_data
from .objectives import ObjectiveConfig, optimize_with_objective
from .scenario import FUEL_OPTIONS, ScenarioProblem


@dataclass(frozen=True)
class RouteImpact:
    route_description: str
    distance_km: float
    travel_time_min: float
    cost: float
    fuel_used: float
    fuel_unit: str
    tailpipe_co2_kg: float


@dataclass(frozen=True)
class RouteImpactChange:
    before: RouteImpact
    after: RouteImpact
    distance_change_km: float
    travel_time_change_min: float
    cost_change: float
    fuel_change: float
    tailpipe_co2_change_kg: float


@dataclass(frozen=True)
class TrafficReoptimization:
    before_scenario: ScenarioProblem
    before_run: OptimizationRun
    after_scenario: ScenarioProblem
    after_run: OptimizationRun | None
    classical: RouteImpactChange | None
    quantum: RouteImpactChange | None
    error: str | None


def reoptimize_for_traffic(
    scenario: ScenarioProblem,
    current_run: OptimizationRun,
    traffic_condition: str,
    qaoa_config: QAOAConfig,
    objective_config: ObjectiveConfig | None = None,
) -> TrafficReoptimization:
    """Change only travel data, revalidate old/new routes, then rerun both solvers."""
    if traffic_condition not in TRAFFIC_FACTORS:
        raise ValueError(f"unsupported traffic condition: {traffic_condition}")

    updated_scenario = scenario_with_traffic(scenario, traffic_condition)
    selected_objective = objective_config or ObjectiveConfig.for_name(current_run.objective_name)
    try:
        validated_before = _revalidated_run(current_run, scenario)
        if objective_config is None and current_run.objective_name == "Cost Priority":
            updated_run = optimize_scenario(updated_scenario, qaoa_config)
        else:
            updated_run = optimize_with_objective(
                updated_scenario,
                selected_objective,
                qaoa_config,
            )
        validated_after = _revalidated_run(updated_run, updated_scenario)
    except Exception as error:
        return TrafficReoptimization(
            before_scenario=scenario,
            before_run=current_run,
            after_scenario=updated_scenario,
            after_run=None,
            classical=None,
            quantum=None,
            error=f"Re-optimization failed; the prior result remains active: {error}",
        )

    classical_change = calculate_impact_change(
        scenario,
        validated_before.classical,
        updated_scenario,
        validated_after.classical,
    )
    quantum_change = (
        calculate_impact_change(
            scenario,
            validated_before.quantum,
            updated_scenario,
            validated_after.quantum,
        )
        if validated_before.quantum is not None and validated_after.quantum is not None
        else None
    )
    return TrafficReoptimization(
        before_scenario=scenario,
        before_run=validated_before,
        after_scenario=updated_scenario,
        after_run=validated_after,
        classical=classical_change,
        quantum=quantum_change,
        error=None,
    )


def calculate_impact_change(
    before_scenario: ScenarioProblem,
    before_summary: RouteSummary,
    after_scenario: ScenarioProblem,
    after_summary: RouteSummary,
) -> RouteImpactChange:
    before = calculate_route_impact(before_scenario, before_summary)
    after = calculate_route_impact(after_scenario, after_summary)
    return RouteImpactChange(
        before=before,
        after=after,
        distance_change_km=after.distance_km - before.distance_km,
        travel_time_change_min=after.travel_time_min - before.travel_time_min,
        cost_change=after.cost - before.cost,
        fuel_change=after.fuel_used - before.fuel_used,
        tailpipe_co2_change_kg=after.tailpipe_co2_kg - before.tailpipe_co2_kg,
    )


def calculate_route_impact(
    scenario: ScenarioProblem, summary: RouteSummary
) -> RouteImpact:
    fuel = FUEL_OPTIONS[scenario.fuel_type]
    route_descriptions = []
    for route in summary.routes:
        stops = [scenario.location_names[delivery_id] for delivery_id in route.plan.delivery_ids]
        route_descriptions.append(
            f"{route.plan.vehicle_id}: "
            + " -> ".join([scenario.location_names["depot"], *stops, scenario.location_names["depot"]])
        )
    fuel_used = summary.total_distance_km * fuel["consumption_per_km"]
    return RouteImpact(
        route_description="; ".join(route_descriptions),
        distance_km=summary.total_distance_km,
        travel_time_min=summary.total_travel_time_min,
        cost=summary.total_cost,
        fuel_used=fuel_used,
        fuel_unit=fuel["unit"],
        tailpipe_co2_kg=fuel_used * fuel["tailpipe_co2_kg_per_unit"],
    )


def scenario_with_traffic(
    scenario: ScenarioProblem, traffic_condition: str
) -> ScenarioProblem:
    travel, data_source = build_travel_data(
        scenario.coordinates,
        traffic_condition,
        use_osrm=scenario.osrm_active,
    )
    if scenario.osrm_requested and not scenario.osrm_active:
        data_source = scenario.data_source
    return replace(
        scenario,
        travel=travel,
        traffic_condition=traffic_condition,
        data_source=data_source,
        osrm_active=data_source == "OSRM public table service",
    )


def _revalidated_run(
    run: OptimizationRun, scenario: ScenarioProblem
) -> OptimizationRun:
    classical_routes = validate_route_set(run.classical.routes, scenario)
    classical = _summary(classical_routes)
    quantum = (
        _summary(validate_route_set(run.quantum.routes, scenario))
        if run.quantum is not None
        else None
    )
    return replace(run, classical=classical, quantum=quantum)


def _summary(routes: tuple[FeasibleRoute, ...]) -> RouteSummary:
    return RouteSummary(
        routes=routes,
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )