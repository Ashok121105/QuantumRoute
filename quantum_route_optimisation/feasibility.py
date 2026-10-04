"""Route capacity, travel, time-window, and shift feasibility checks."""

from collections.abc import Mapping, Sequence

from .costs import calculate_route_cost
from .models import (
    Delivery,
    FeasibleRoute,
    RouteCostWeights,
    RoutePlan,
    TravelData,
    Vehicle,
)


class RouteInfeasibleError(ValueError):
    """Raised when a proposed route violates its inputs or constraints."""


def evaluate_route(
    vehicle: Vehicle,
    plan: RoutePlan,
    deliveries: Mapping[str, Delivery] | Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
) -> FeasibleRoute:
    """Validate and score one ordered route, including its return leg."""
    if plan.vehicle_id != vehicle.vehicle_id:
        raise RouteInfeasibleError("route vehicle_id does not match the vehicle")

    delivery_map = (
        deliveries
        if isinstance(deliveries, Mapping)
        else {delivery.delivery_id: delivery for delivery in deliveries}
    )
    try:
        route_deliveries = [delivery_map[delivery_id] for delivery_id in plan.delivery_ids]
    except KeyError as error:
        raise RouteInfeasibleError(f"unknown delivery: {error.args[0]}") from error

    total_demand = sum(delivery.demand for delivery in route_deliveries)
    if total_demand > vehicle.capacity:
        raise RouteInfeasibleError(
            f"route demand {total_demand} exceeds vehicle capacity {vehicle.capacity}"
        )

    current_location = vehicle.start_location
    current_time = float(vehicle.shift_start_min)
    total_distance = 0.0
    total_travel_time = 0.0
    total_service_time = 0
    total_waiting_time = 0.0
    service_start_times: list[tuple[str, float]] = []

    for delivery in route_deliveries:
        distance, duration = _get_arc(travel, current_location, delivery.location)
        total_distance += distance
        total_travel_time += duration
        arrival_time = current_time + duration
        service_start = max(arrival_time, float(delivery.window_start_min))
        if service_start > delivery.window_end_min:
            raise RouteInfeasibleError(
                f"delivery {delivery.delivery_id} misses its time window"
            )

        total_waiting_time += service_start - arrival_time
        total_service_time += delivery.service_duration_min
        service_start_times.append((delivery.delivery_id, service_start))
        current_time = service_start + delivery.service_duration_min
        current_location = delivery.location

    distance, duration = _get_arc(travel, current_location, vehicle.end_location)
    total_distance += distance
    total_travel_time += duration
    current_time += duration
    elapsed_time = current_time - vehicle.shift_start_min
    if current_time > vehicle.shift_end_min:
        raise RouteInfeasibleError("route returns after the vehicle shift ends")

    return FeasibleRoute(
        plan=plan,
        distance_km=total_distance,
        travel_time_min=total_travel_time,
        service_time_min=total_service_time,
        waiting_time_min=total_waiting_time,
        elapsed_time_min=elapsed_time,
        service_start_times=tuple(service_start_times),
        total_cost=calculate_route_cost(total_distance, elapsed_time, cost_weights),
    )


def _get_arc(travel: TravelData, origin: str, destination: str) -> tuple[float, float]:
    arc = (origin, destination)
    try:
        distance = travel.distances_km[arc]
        duration = travel.durations_min[arc]
    except KeyError as error:
        raise RouteInfeasibleError(
            f"missing travel data for arc {origin!r} -> {destination!r}"
        ) from error
    return distance, duration