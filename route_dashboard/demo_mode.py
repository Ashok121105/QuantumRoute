"""Deterministic orchestration for the end-to-end hackathon demonstration."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, MutableMapping

from quantum_route_optimisation import QAOAConfig, generate_feasible_routes, optimize_classically

from .dynamic import (
    RouteImpact,
    TrafficReoptimization,
    calculate_route_impact,
    reoptimize_for_traffic,
)
from .fleet_disruption import (
    DeliveryPriority,
    FleetDisruptionResult,
    FleetMetrics,
    VehicleStatus,
    reoptimize_fleet,
)
from .objectives import (
    ObjectiveConfig,
    ObjectiveName,
    optimize_with_objective,
    scenario_for_objective,
)
from .optimization import (
    MAX_LOCAL_QAOA_VARIABLES,
    OptimizationRun,
    RouteSummary,
    validate_route_set,
)
from .scenario import DeliveryStop, ScenarioProblem, build_scenario


DEMO_QAOA_CONFIG = QAOAConfig(reps=1, maxiter=20, shots=2048, seed=42)
DEMO_TRAFFIC_CONDITION = "Heavy"
DEMO_BREAKDOWN_VEHICLE = "van-2"
DEMO_DELIVERY_PRIORITIES = {
    "delivery-1": DeliveryPriority.CRITICAL,
    "delivery-2": DeliveryPriority.HIGH,
}


class DemoStage(str, Enum):
    BASELINE = "Baseline Fleet"
    OBJECTIVES = "Objective Trade-offs"
    TRAFFIC = "Traffic Incident"
    FLEET = "Vehicle Disruption"
    SUSTAINABILITY = "Sustainability Summary"
    COMPLETE = "Final Demo Summary"


@dataclass(frozen=True)
class DemoQAOAStatus:
    executed: bool
    valid: bool
    message: str
    sample_probability: float | None = None
    unique_samples: int | None = None


@dataclass(frozen=True)
class DemoObjectiveResult:
    objective: ObjectiveName
    objective_value: float
    physical_summary: RouteSummary
    impact: RouteImpact


@dataclass(frozen=True)
class SustainabilitySummary:
    metrics: FleetMetrics
    objective_name: str
    baseline_deliveries_served: int
    cost_comparability_note: str


@dataclass(frozen=True)
class HackathonDemoResult:
    scenario: ScenarioProblem
    baseline_run: OptimizationRun
    baseline_candidate_count: int
    baseline_qaoa: DemoQAOAStatus
    objective_results: tuple[DemoObjectiveResult, ...]
    traffic_result: TrafficReoptimization
    fleet_result: FleetDisruptionResult
    sustainability: SustainabilitySummary
    completed_stages: tuple[DemoStage, ...]


ProgressCallback = Callable[[DemoStage], None]


def build_hackathon_demo_scenario() -> ScenarioProblem:
    """Build a fixed local-estimate scenario sized for reliable local QAOA."""
    stops = (
        DeliveryStop(
            "Civic Centre",
            37.7793,
            -122.4192,
            demand=1.0,
            window_start_min=540,
            window_end_min=630,
            service_duration_min=5,
        ),
        DeliveryStop(
            "Sunset Point",
            37.7599,
            -122.4944,
            demand=1.0,
            window_start_min=480,
            window_end_min=540,
            service_duration_min=5,
        ),
    )
    scenario = build_scenario(
        stops,
        vehicle_count=2,
        vehicle_capacity=2.0,
        depot_name="Mission Depot",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1020,
        traffic_condition="Normal",
        fuel_type="Diesel",
        fuel_price_per_unit=1.10,
        driver_cost_per_hour=25.0,
        use_osrm=False,
    )
    vehicles = tuple(
        replace(
            vehicle,
            capacity=capacity,
            shift_start_min=535 if index == 2 else vehicle.shift_start_min,
        )
        for index, (vehicle, capacity) in enumerate(
            zip(scenario.vehicles, (2.0, 1.0)),
            start=1,
        )
    )
    return replace(scenario, vehicles=vehicles)


def run_full_demo(
    qaoa_config: QAOAConfig = DEMO_QAOA_CONFIG,
    progress_callback: ProgressCallback | None = None,
) -> HackathonDemoResult:
    """Run each demonstration stage using existing optimization services."""
    scenario = build_hackathon_demo_scenario()
    cost_objective = ObjectiveConfig.for_name(ObjectiveName.COST)
    baseline_run = optimize_with_objective(scenario, cost_objective, qaoa_config)
    candidate_count = len(
        generate_feasible_routes(
            scenario.vehicles,
            scenario.deliveries,
            scenario.travel,
            scenario_for_objective(scenario, cost_objective).cost_weights,
        )
    )
    baseline_qaoa = qaoa_status(baseline_run, candidate_count)
    completed = [DemoStage.BASELINE]
    _notify(progress_callback, DemoStage.BASELINE)

    objective_results = run_objective_stage(scenario, cost_objective, baseline_run)
    completed.append(DemoStage.OBJECTIVES)
    _notify(progress_callback, DemoStage.OBJECTIVES)

    traffic_result = run_traffic_stage(scenario, baseline_run, qaoa_config, cost_objective)
    completed.append(DemoStage.TRAFFIC)
    _notify(progress_callback, DemoStage.TRAFFIC)

    fleet_result = run_fleet_stage(
        traffic_result.after_scenario,
        traffic_result.after_run,
        qaoa_config,
        cost_objective,
    )
    completed.append(DemoStage.FLEET)
    _notify(progress_callback, DemoStage.FLEET)

    sustainability = build_sustainability_summary(
        scenario,
        baseline_run,
        fleet_result,
    )
    completed.append(DemoStage.SUSTAINABILITY)
    _notify(progress_callback, DemoStage.SUSTAINABILITY)
    completed.append(DemoStage.COMPLETE)
    _notify(progress_callback, DemoStage.COMPLETE)

    return HackathonDemoResult(
        scenario=scenario,
        baseline_run=baseline_run,
        baseline_candidate_count=candidate_count,
        baseline_qaoa=baseline_qaoa,
        objective_results=objective_results,
        traffic_result=traffic_result,
        fleet_result=fleet_result,
        sustainability=sustainability,
        completed_stages=tuple(completed),
    )


def run_objective_stage(
    scenario: ScenarioProblem,
    baseline_objective: ObjectiveConfig | None = None,
    baseline_run: OptimizationRun | None = None,
) -> tuple[DemoObjectiveResult, ...]:
    """Compare existing classical results while keeping physical metrics separate."""
    results = []
    for objective in ObjectiveName:
        config = ObjectiveConfig.for_name(objective)
        if (
            objective is ObjectiveName.COST
            and baseline_run is not None
            and baseline_objective is not None
            and baseline_objective.name is objective
        ):
            objective_value = (
                baseline_run.classical_objective_value
                if baseline_run.classical_objective_value is not None
                else baseline_run.classical.total_cost
            )
            physical_summary = baseline_run.classical
        else:
            scoring_scenario = scenario_for_objective(scenario, config)
            result = optimize_classically(
                scoring_scenario.vehicles,
                scoring_scenario.deliveries,
                scoring_scenario.travel,
                scoring_scenario.cost_weights,
            )
            objective_value = result.total_cost
            routes = validate_route_set(result.routes, scenario)
            physical_summary = _summary(routes)
        impact = calculate_route_impact(scenario, physical_summary)
        results.append(
            DemoObjectiveResult(objective, objective_value, physical_summary, impact)
        )
    return tuple(results)


def run_traffic_stage(
    scenario: ScenarioProblem,
    baseline_run: OptimizationRun,
    qaoa_config: QAOAConfig,
    objective_config: ObjectiveConfig,
) -> TrafficReoptimization:
    result = reoptimize_for_traffic(
        scenario,
        baseline_run,
        DEMO_TRAFFIC_CONDITION,
        qaoa_config,
        objective_config,
    )
    if result.after_run is None:
        raise RuntimeError(result.error or "traffic re-optimization did not produce a result")
    return result


def run_fleet_stage(
    scenario: ScenarioProblem,
    current_run: OptimizationRun | None,
    qaoa_config: QAOAConfig,
    objective_config: ObjectiveConfig,
) -> FleetDisruptionResult:
    if current_run is None:
        raise ValueError("fleet disruption requires the completed traffic-stage result")
    result = reoptimize_fleet(
        scenario,
        current_run,
        {DEMO_BREAKDOWN_VEHICLE: VehicleStatus.UNAVAILABLE},
        DEMO_DELIVERY_PRIORITIES,
        DEMO_TRAFFIC_CONDITION,
        qaoa_config,
        objective_config,
    )
    if result.after_run is None:
        raise RuntimeError(result.error or "fleet disruption did not produce a result")
    return result


def build_sustainability_summary(
    scenario: ScenarioProblem,
    baseline_run: OptimizationRun,
    fleet_result: FleetDisruptionResult,
) -> SustainabilitySummary:
    if fleet_result.after_metrics is None or fleet_result.after_run is None:
        raise ValueError("sustainability summary requires validated fleet metrics")
    baseline_served = sum(
        len(route.plan.delivery_ids) for route in baseline_run.classical.routes
    )
    final_metrics = fleet_result.after_metrics
    if final_metrics.deliveries_served != baseline_served:
        comparison_note = (
            f"Operating cost is not directly comparable: deliveries served changed "
            f"from {baseline_served} to {final_metrics.deliveries_served}."
        )
    else:
        comparison_note = (
            "The same number of deliveries was served; these are measured operating "
            "metrics, not guaranteed savings."
        )
    return SustainabilitySummary(
        metrics=final_metrics,
        objective_name=fleet_result.after_run.objective_name,
        baseline_deliveries_served=baseline_served,
        cost_comparability_note=comparison_note,
    )


def qaoa_status(run: OptimizationRun, candidate_count: int) -> DemoQAOAStatus:
    if run.quantum is not None:
        quantum_result = run.quantum_result
        return DemoQAOAStatus(
            executed=True,
            valid=True,
            message="QAOA executed and returned a route set that passed feasibility validation.",
            sample_probability=(
                quantum_result.sample_probability if quantum_result is not None else None
            ),
            unique_samples=(
                quantum_result.observed_unique_samples if quantum_result is not None else None
            ),
        )
    error = run.quantum_error or "No QAOA result was returned."
    if candidate_count > MAX_LOCAL_QAOA_VARIABLES:
        return DemoQAOAStatus(
            executed=False,
            valid=False,
            message=(
                f"QAOA skipped: {candidate_count} variables exceed the existing "
                f"{MAX_LOCAL_QAOA_VARIABLES}-variable local Aer limit."
            ),
        )
    if "not run" in error.lower():
        return DemoQAOAStatus(False, False, f"QAOA skipped: {error}")
    return DemoQAOAStatus(True, False, f"QAOA ran but returned no valid route sample: {error}")


def reset_demo_state(state: MutableMapping) -> None:
    for key in ("hackathon_demo_result", "hackathon_demo_error", "hackathon_demo_stage"):
        state.pop(key, None)


def _summary(routes) -> RouteSummary:
    return RouteSummary(
        routes=tuple(routes),
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )


def _notify(callback: ProgressCallback | None, stage: DemoStage) -> None:
    if callback is not None:
        callback(stage)
