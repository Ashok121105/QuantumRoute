"""Exact route-selection baseline for small delivery instances."""

from dataclasses import dataclass
from itertools import permutations
from typing import Sequence

from .feasibility import RouteInfeasibleError, evaluate_route
from .models import (
    Delivery,
    FeasibleRoute,
    RouteCostWeights,
    RoutePlan,
    TravelData,
    Vehicle,
)


class NoFeasibleSolutionError(ValueError):
    """Raised when deliveries cannot be covered by feasible vehicle routes."""


@dataclass(frozen=True)
class OptimizationResult:
    routes: tuple[FeasibleRoute, ...]
    total_cost: float


def generate_feasible_routes(
    vehicles: Sequence[Vehicle],
    deliveries: Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
) -> tuple[FeasibleRoute, ...]:
    """Enumerate feasible ordered routes; intended for small hackathon instances."""
    _validate_unique_ids(vehicles, deliveries)
    routes: list[FeasibleRoute] = []
    delivery_ids = tuple(delivery.delivery_id for delivery in deliveries)

    for vehicle in vehicles:
        for route_size in range(1, len(delivery_ids) + 1):
            for delivery_order in permutations(delivery_ids, route_size):
                plan = RoutePlan(vehicle.vehicle_id, delivery_order)
                try:
                    routes.append(
                        evaluate_route(vehicle, plan, deliveries, travel, cost_weights)
                    )
                except RouteInfeasibleError:
                    continue
    return tuple(routes)


def optimize_classically(
    vehicles: Sequence[Vehicle],
    deliveries: Sequence[Delivery],
    travel: TravelData,
    cost_weights: RouteCostWeights,
) -> OptimizationResult:
    """Minimize route cost with exact set partitioning over feasible routes."""
    _validate_unique_ids(vehicles, deliveries)
    if not deliveries:
        return OptimizationResult(routes=(), total_cost=0.0)
    if not vehicles:
        raise NoFeasibleSolutionError("at least one vehicle is required")

    feasible_routes = generate_feasible_routes(vehicles, deliveries, travel, cost_weights)
    delivery_ids = frozenset(delivery.delivery_id for delivery in deliveries)
    routes_by_delivery = {
        delivery_id: tuple(
            route for route in feasible_routes if delivery_id in route.plan.delivery_ids
        )
        for delivery_id in delivery_ids
    }

    best_routes: tuple[FeasibleRoute, ...] | None = None
    best_cost = float("inf")

    def search(
        uncovered: frozenset[str],
        used_vehicles: frozenset[str],
        selected: tuple[FeasibleRoute, ...],
        current_cost: float,
    ) -> None:
        nonlocal best_routes, best_cost
        if not uncovered:
            ordered = tuple(sorted(selected, key=lambda route: route.plan.vehicle_id))
            signature = _route_signature(ordered)
            best_signature = _route_signature(best_routes) if best_routes is not None else ()
            if current_cost < best_cost or (
                current_cost == best_cost and signature < best_signature
            ):
                best_routes = ordered
                best_cost = current_cost
            return
        if current_cost > best_cost:
            return

        eligible_by_delivery = {
            delivery_id: tuple(
                route
                for route in routes_by_delivery[delivery_id]
                if route.plan.vehicle_id not in used_vehicles
                and set(route.plan.delivery_ids).issubset(uncovered)
            )
            for delivery_id in uncovered
        }
        next_delivery = min(eligible_by_delivery, key=lambda item: len(eligible_by_delivery[item]))
        for route in eligible_by_delivery[next_delivery]:
            route_deliveries = frozenset(route.plan.delivery_ids)
            search(
                uncovered - route_deliveries,
                used_vehicles | {route.plan.vehicle_id},
                selected + (route,),
                current_cost + route.total_cost,
            )

    search(delivery_ids, frozenset(), (), 0.0)
    if best_routes is None:
        raise NoFeasibleSolutionError("no feasible set of routes covers all deliveries")
    return OptimizationResult(routes=best_routes, total_cost=best_cost)


def _validate_unique_ids(
    vehicles: Sequence[Vehicle], deliveries: Sequence[Delivery]
) -> None:
    vehicle_ids = [vehicle.vehicle_id for vehicle in vehicles]
    delivery_ids = [delivery.delivery_id for delivery in deliveries]
    if len(vehicle_ids) != len(set(vehicle_ids)):
        raise ValueError("vehicle_id values must be unique")
    if len(delivery_ids) != len(set(delivery_ids)):
        raise ValueError("delivery_id values must be unique")


def _route_signature(routes: tuple[FeasibleRoute, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((route.plan.vehicle_id, route.plan.delivery_ids) for route in routes)