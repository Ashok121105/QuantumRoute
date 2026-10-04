"""Priority-aware fleet breakdown recovery using the existing route optimizers."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping

from quantum_route_optimisation import (
    Delivery,
    FeasibleRoute,
    QAOAConfig,
    RouteInfeasibleError,
    RoutePlan,
    Vehicle,
    evaluate_route,
    generate_feasible_routes,
)

from .dynamic import calculate_route_impact, scenario_with_traffic
from .optimization import (
    OptimizationRun,
    RouteSummary,
    validate_route_set,
)
from .objectives import (
    ObjectiveConfig,
    objective_scales,
    optimize_with_objective,
    scenario_for_objective,
)
from .routing import TRAFFIC_FACTORS, build_travel_data
from .scenario import FUEL_OPTIONS, DeliveryStop, ScenarioProblem, build_scenario


class VehicleStatus(str, Enum):
    AVAILABLE = "Available"
    UNAVAILABLE = "Unavailable / Breakdown"


class DeliveryPriority(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    NORMAL = "Normal"
    LOW = "Low"


PRIORITY_ORDER = (
    DeliveryPriority.CRITICAL,
    DeliveryPriority.HIGH,
    DeliveryPriority.NORMAL,
    DeliveryPriority.LOW,
)
DEFAULT_PRIORITY = DeliveryPriority.NORMAL
DEMO_DELIVERY_PRIORITIES = {
    "delivery-1": DeliveryPriority.CRITICAL,
    "delivery-2": DeliveryPriority.HIGH,
    "delivery-3": DeliveryPriority.NORMAL,
    "delivery-4": DeliveryPriority.LOW,
    "delivery-5": DeliveryPriority.NORMAL,
    "delivery-6": DeliveryPriority.CRITICAL,
    "delivery-7": DeliveryPriority.HIGH,
    "delivery-8": DeliveryPriority.LOW,
}


@dataclass(frozen=True)
class UnassignedDelivery:
    delivery_id: str
    priority: DeliveryPriority
    reason: str


@dataclass(frozen=True)
class DeliveryReassignment:
    delivery_id: str
    from_vehicle: str
    to_vehicle: str


@dataclass(frozen=True)
class FleetMetrics:
    vehicles_available: int
    deliveries_served: int
    deliveries_unassigned: int
    total_distance_km: float
    total_travel_time_min: float
    total_cost: float
    fuel_used: float
    fuel_unit: str
    tailpipe_co2_kg: float


@dataclass(frozen=True)
class FleetDisruptionResult:
    before_scenario: ScenarioProblem
    before_run: OptimizationRun
    after_scenario: ScenarioProblem
    optimization_scenario: ScenarioProblem
    after_run: OptimizationRun | None
    vehicle_statuses: Mapping[str, VehicleStatus]
    delivery_priorities: Mapping[str, DeliveryPriority]
    assigned_delivery_ids: tuple[str, ...]
    unassigned_deliveries: tuple[UnassignedDelivery, ...]
    reassignments: tuple[DeliveryReassignment, ...]
    before_metrics: FleetMetrics
    after_metrics: FleetMetrics | None
    feasibility_status: str
    qaoa_status: str
    error: str | None = None


def reoptimize_fleet(
    scenario: ScenarioProblem,
    current_run: OptimizationRun,
    vehicle_availability: Mapping[str, VehicleStatus | str],
    delivery_priorities: Mapping[str, DeliveryPriority | str],
    traffic_condition: str,
    qaoa_config: QAOAConfig,
    objective_config: ObjectiveConfig | None = None,
) -> FleetDisruptionResult:
    """Disable vehicles, preserve priority tiers, then rerun and revalidate both solvers."""
    if traffic_condition not in TRAFFIC_FACTORS:
        raise ValueError(f"unsupported traffic condition: {traffic_condition}")

    statuses = _normalize_availability(scenario.vehicles, vehicle_availability)
    priorities = _normalize_priorities(scenario.deliveries, delivery_priorities)
    selected_objective = objective_config or ObjectiveConfig.for_name(current_run.objective_name)
    validated_before = _revalidate_run(current_run, scenario)
    before_metrics = _metrics(
        scenario,
        validated_before.classical,
        len(scenario.vehicles),
        0,
    )
    updated_scenario = (
        scenario
        if scenario.traffic_condition == traffic_condition
        else scenario_with_traffic(scenario, traffic_condition)
    )
    available_vehicles = tuple(
        vehicle
        for vehicle in updated_scenario.vehicles
        if statuses[vehicle.vehicle_id] is VehicleStatus.AVAILABLE
    )
    priority_scenario = replace(updated_scenario, vehicles=available_vehicles)
    scales = objective_scales(priority_scenario)
    scoring_scenario = scenario_for_objective(
        priority_scenario,
        selected_objective,
        scales,
    )
    feasible_routes = generate_feasible_routes(
        scoring_scenario.vehicles,
        updated_scenario.deliveries,
        scoring_scenario.travel,
        scoring_scenario.cost_weights,
    )
    selected_routes = _select_priority_feasible_routes(
        updated_scenario.deliveries,
        available_vehicles,
        feasible_routes,
        priorities,
    )
    selected_ids = {
        delivery_id
        for route in selected_routes
        for delivery_id in route.plan.delivery_ids
    }
    accepted = [
        delivery for delivery in updated_scenario.deliveries if delivery.delivery_id in selected_ids
    ]
    accepted_ids = {delivery.delivery_id for delivery in accepted}
    final_deliveries = tuple(
        delivery for delivery in updated_scenario.deliveries if delivery.delivery_id in accepted_ids
    )
    optimization_scenario = replace(
        updated_scenario,
        vehicles=available_vehicles,
        deliveries=final_deliveries,
    )
    try:
        after_run = _optimize_selected_deliveries(
            optimization_scenario,
            selected_objective,
            qaoa_config,
            priority_scenario,
        )
        validated_after = _revalidate_run(after_run, optimization_scenario)
    except Exception as error:
        failure_reason = f"Fleet re-optimization failed: {type(error).__name__}: {error}"
        unassigned = tuple(
            UnassignedDelivery(delivery.delivery_id, priorities[delivery.delivery_id], failure_reason)
            for delivery in updated_scenario.deliveries
        )
        return FleetDisruptionResult(
            before_scenario=scenario,
            before_run=validated_before,
            after_scenario=updated_scenario,
            optimization_scenario=optimization_scenario,
            after_run=None,
            vehicle_statuses=statuses,
            delivery_priorities=priorities,
            assigned_delivery_ids=(),
            unassigned_deliveries=unassigned,
            reassignments=(),
            before_metrics=before_metrics,
            after_metrics=None,
            feasibility_status="Failed",
            qaoa_status="QAOA not run because fleet re-optimization failed.",
            error=failure_reason,
        )

    unassigned = tuple(
        UnassignedDelivery(
            delivery_id=delivery.delivery_id,
            priority=priorities[delivery.delivery_id],
            reason=_unassigned_reason(
                delivery,
                accepted,
                available_vehicles,
                updated_scenario,
            ),
        )
        for delivery in updated_scenario.deliveries
        if delivery.delivery_id not in accepted_ids
    )
    assigned_vehicle = {
        delivery_id: route.plan.vehicle_id
        for route in validated_after.classical.routes
        for delivery_id in route.plan.delivery_ids
    }
    previously_assigned_vehicle = {
        delivery_id: route.plan.vehicle_id
        for route in validated_before.classical.routes
        for delivery_id in route.plan.delivery_ids
    }
    unavailable_ids = {
        vehicle_id
        for vehicle_id, status in statuses.items()
        if status is VehicleStatus.UNAVAILABLE
    }
    reassignments = tuple(
        DeliveryReassignment(delivery_id, previous_vehicle, assigned_vehicle[delivery_id])
        for delivery_id, previous_vehicle in previously_assigned_vehicle.items()
        if previous_vehicle in unavailable_ids
        and delivery_id in assigned_vehicle
        and assigned_vehicle[delivery_id] != previous_vehicle
    )
    after_metrics = _metrics(
        updated_scenario,
        validated_after.classical,
        len(available_vehicles),
        len(unassigned),
    )
    qaoa_status = (
        validated_after.quantum_error
        if validated_after.quantum is None
        else "QAOA returned a valid route set and it was revalidated."
    )
    return FleetDisruptionResult(
        before_scenario=scenario,
        before_run=validated_before,
        after_scenario=updated_scenario,
        optimization_scenario=optimization_scenario,
        after_run=validated_after,
        vehicle_statuses=statuses,
        delivery_priorities=priorities,
        assigned_delivery_ids=tuple(assigned_vehicle),
        unassigned_deliveries=unassigned,
        reassignments=reassignments,
        before_metrics=before_metrics,
        after_metrics=after_metrics,
        feasibility_status="Complete" if not unassigned else "Partial - waitlist created",
        qaoa_status=qaoa_status or "QAOA did not return a valid route set.",
    )


def build_fleet_disruption_demo_scenario(
    traffic_condition: str = "Moderate",
) -> ScenarioProblem:
    """Build a deterministic 4-vehicle, 8-delivery demonstration scenario."""
    stops = (
        DeliveryStop("Union Square", 37.7879, -122.4074, 1.0, 480, 660),
        DeliveryStop("Civic Centre", 37.7793, -122.4192, 1.0, 500, 760),
        DeliveryStop("North Market", 37.7905, -122.4082, 1.0, 510, 780),
        DeliveryStop("Harbor Point", 37.7992, -122.3975, 1.0, 540, 840),
        DeliveryStop("Mission Bay", 37.7688, -122.3895, 1.0, 480, 900),
        DeliveryStop("South Park", 37.7817, -122.3942, 1.0, 525, 810),
        DeliveryStop("Chinatown", 37.7941, -122.4066, 1.0, 495, 735),
        DeliveryStop("Sunset Point", 37.7599, -122.4944, 1.0, 570, 900),
    )
    fuel = FUEL_OPTIONS["Diesel"]
    base = build_scenario(
        stops[:5],
        vehicle_count=4,
        vehicle_capacity=3.0,
        depot_name="Mission Depot",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1020,
        traffic_condition=traffic_condition,
        fuel_type="Diesel",
        fuel_price_per_unit=fuel["default_price"],
    )
    vehicles = tuple(
        Vehicle(
            vehicle_id=f"van-{index}",
            capacity=capacity,
            start_location="depot",
            end_location="depot",
            shift_start_min=480,
            shift_end_min=1020,
        )
        for index, capacity in enumerate((2.0, 2.0, 3.0, 2.0), start=1)
    )
    deliveries = tuple(
        Delivery(
            delivery_id=f"delivery-{index}",
            location=f"delivery-{index}",
            demand=stop.demand,
            window_start_min=stop.window_start_min,
            window_end_min=stop.window_end_min,
            service_duration_min=stop.service_duration_min,
        )
        for index, stop in enumerate(stops, start=1)
    )
    coordinates = {"depot": base.coordinates["depot"]}
    location_names = {"depot": base.location_names["depot"]}
    for index, stop in enumerate(stops, start=1):
        delivery_id = f"delivery-{index}"
        coordinates[delivery_id] = (stop.latitude, stop.longitude)
        location_names[delivery_id] = stop.destination
    travel, data_source = build_travel_data(coordinates, traffic_condition)
    return replace(
        base,
        vehicles=vehicles,
        deliveries=deliveries,
        travel=travel,
        coordinates=coordinates,
        location_names=location_names,
        data_source=data_source,
    )


def _normalize_availability(
    vehicles: tuple[Vehicle, ...],
    availability: Mapping[str, VehicleStatus | str],
) -> dict[str, VehicleStatus]:
    vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
    unknown = set(availability) - vehicle_ids
    if unknown:
        raise ValueError(f"availability contains unknown vehicles: {sorted(unknown)}")
    statuses = {}
    for vehicle in vehicles:
        raw_status = availability.get(vehicle.vehicle_id, VehicleStatus.AVAILABLE)
        try:
            statuses[vehicle.vehicle_id] = VehicleStatus(raw_status)
        except ValueError as error:
            raise ValueError(f"unsupported status for {vehicle.vehicle_id}: {raw_status}") from error
    return statuses


def _select_priority_feasible_routes(
    deliveries: tuple[Delivery, ...],
    vehicles: tuple[Vehicle, ...],
    feasible_routes: tuple[FeasibleRoute, ...],
    priorities: Mapping[str, DeliveryPriority],
) -> tuple[FeasibleRoute, ...]:
    """Maximize delivery counts by priority over the existing feasible route pool."""
    delivery_indexes = {
        delivery.delivery_id: index for index, delivery in enumerate(deliveries)
    }
    routes_by_vehicle: dict[str, list[tuple[FeasibleRoute, int]]] = {
        vehicle.vehicle_id: [] for vehicle in vehicles
    }
    for route in feasible_routes:
        route_mask = sum(1 << delivery_indexes[item] for item in route.plan.delivery_ids)
        routes_by_vehicle[route.plan.vehicle_id].append((route, route_mask))

    # Each state stores the lowest-cost route selection for a delivery bitmask.
    states: dict[int, tuple[float, tuple[FeasibleRoute, ...]]] = {0: (0.0, ())}
    for vehicle in vehicles:
        next_states = dict(states)
        for assigned_mask, (cost, selected_routes) in states.items():
            for route, route_mask in routes_by_vehicle[vehicle.vehicle_id]:
                if assigned_mask & route_mask:
                    continue
                combined_mask = assigned_mask | route_mask
                combined_cost = cost + route.total_cost
                combined_routes = selected_routes + (route,)
                existing = next_states.get(combined_mask)
                if existing is None or _route_selection_key(combined_cost, combined_routes) < _route_selection_key(*existing):
                    next_states[combined_mask] = (combined_cost, combined_routes)
        states = next_states

    def priority_key(state: tuple[int, tuple[float, tuple[FeasibleRoute, ...]]]):
        mask, (cost, routes) = state
        counts = tuple(
            sum(
                1
                for delivery in deliveries
                if priorities[delivery.delivery_id] is priority
                and mask & (1 << delivery_indexes[delivery.delivery_id])
            )
            for priority in PRIORITY_ORDER
        )
        signature = tuple(
            (route.plan.vehicle_id, route.plan.delivery_ids) for route in routes
        )
        return tuple(-count for count in counts), cost, signature

    _, (_, selected_routes) = min(states.items(), key=priority_key)
    return selected_routes


def _route_selection_key(
    cost: float,
    routes: tuple[FeasibleRoute, ...],
) -> tuple[float, tuple[tuple[str, tuple[str, ...]], ...]]:
    signature = tuple(
        (route.plan.vehicle_id, route.plan.delivery_ids) for route in routes
    )
    return cost, signature


def _normalize_priorities(
    deliveries: tuple[Delivery, ...],
    priorities: Mapping[str, DeliveryPriority | str],
) -> dict[str, DeliveryPriority]:
    delivery_ids = {delivery.delivery_id for delivery in deliveries}
    unknown = set(priorities) - delivery_ids
    if unknown:
        raise ValueError(f"priorities contain unknown deliveries: {sorted(unknown)}")
    normalized = {}
    for delivery in deliveries:
        raw_priority = priorities.get(delivery.delivery_id, DEFAULT_PRIORITY)
        try:
            normalized[delivery.delivery_id] = DeliveryPriority(raw_priority)
        except ValueError as error:
            raise ValueError(
                f"unsupported priority for {delivery.delivery_id}: {raw_priority}"
            ) from error
    return normalized


def _revalidate_run(run: OptimizationRun, scenario: ScenarioProblem) -> OptimizationRun:
    classical = _summary(validate_route_set(run.classical.routes, scenario))
    quantum = (
        _summary(validate_route_set(run.quantum.routes, scenario))
        if run.quantum is not None
        else None
    )
    return replace(run, classical=classical, quantum=quantum)


def _optimize_selected_deliveries(
    scenario: ScenarioProblem,
    objective_config: ObjectiveConfig,
    qaoa_config: QAOAConfig,
    normalization_scenario: ScenarioProblem,
) -> OptimizationRun:
    if scenario.deliveries:
        return optimize_with_objective(
            scenario,
            objective_config,
            qaoa_config,
            normalization_scenario,
        )
    empty_summary = RouteSummary((), 0.0, 0.0, 0.0)
    return OptimizationRun(
        classical=empty_summary,
        quantum=None,
        quantum_result=None,
        quantum_error="QAOA was not run because no deliveries were feasible for the remaining fleet.",
        objective_delta=None,
        relative_gap_percent=None,
        objective_name=objective_config.name.value,
        classical_objective_value=0.0,
        quantum_objective_value=None,
    )


def _summary(routes: tuple[FeasibleRoute, ...]) -> RouteSummary:
    return RouteSummary(
        routes=routes,
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )


def _metrics(
    scenario: ScenarioProblem,
    summary: RouteSummary,
    vehicles_available: int,
    deliveries_unassigned: int,
) -> FleetMetrics:
    impact = calculate_route_impact(scenario, summary)
    deliveries_served = sum(len(route.plan.delivery_ids) for route in summary.routes)
    return FleetMetrics(
        vehicles_available=vehicles_available,
        deliveries_served=deliveries_served,
        deliveries_unassigned=deliveries_unassigned,
        total_distance_km=impact.distance_km,
        total_travel_time_min=impact.travel_time_min,
        total_cost=impact.cost,
        fuel_used=impact.fuel_used,
        fuel_unit=impact.fuel_unit,
        tailpipe_co2_kg=impact.tailpipe_co2_kg,
    )


def _unassigned_reason(
    delivery: Delivery,
    accepted_deliveries: list[Delivery],
    available_vehicles: tuple[Vehicle, ...],
    scenario: ScenarioProblem,
) -> str:
    if not available_vehicles:
        return "No vehicles are available after the simulated breakdown."
    largest_capacity = max(vehicle.capacity for vehicle in available_vehicles)
    if delivery.demand > largest_capacity:
        return (
            f"Demand {delivery.demand:g} exceeds the largest remaining vehicle capacity "
            f"({largest_capacity:g})."
        )
    total_remaining_capacity = sum(vehicle.capacity for vehicle in available_vehicles)
    already_accepted_demand = sum(item.demand for item in accepted_deliveries)
    if already_accepted_demand + delivery.demand > total_remaining_capacity:
        return "Insufficient remaining fleet capacity after higher-priority deliveries."

    delivery_map = {item.delivery_id: item for item in scenario.deliveries}
    failures = []
    for vehicle in available_vehicles:
        try:
            evaluate_route(
                vehicle,
                RoutePlan(vehicle.vehicle_id, (delivery.delivery_id,)),
                delivery_map,
                scenario.travel,
                scenario.cost_weights,
            )
        except RouteInfeasibleError as error:
            failures.append(str(error))
    if failures and all("misses its time window" in failure for failure in failures):
        return "No available vehicle can meet this delivery's time window under current traffic."
    if failures and all("shift ends" in failure for failure in failures):
        return "No available vehicle can complete this delivery within its shift."
    return (
        "No feasible insertion remains within available vehicle capacity, shifts, and "
        "time windows after higher-priority assignments."
    )
