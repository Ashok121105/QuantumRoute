"""Qiskit Optimization formulation for selecting feasible vehicle routes."""

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from qiskit_optimization import QuadraticProgram

from .models import Delivery, FeasibleRoute


@dataclass(frozen=True)
class RouteQubo:
    problem: QuadraticProgram
    routes: tuple[FeasibleRoute, ...]
    variable_names: tuple[str, ...]
    delivery_ids: tuple[str, ...]
    penalty_weight: float


def build_route_qubo(
    feasible_routes: Sequence[FeasibleRoute],
    deliveries: Sequence[Delivery],
) -> RouteQubo:
    """Build a binary QUBO over the provided feasible vehicle-route options.

    Route cost is the linear objective. Squared exact-cover penalties ensure each
    delivery is served once; pairwise penalties prevent a vehicle from receiving
    more than one selected route.
    """
    routes = tuple(feasible_routes)
    delivery_ids = tuple(delivery.delivery_id for delivery in deliveries)
    if not delivery_ids:
        raise ValueError("at least one delivery is required to build a route QUBO")
    if len(set(delivery_ids)) != len(delivery_ids):
        raise ValueError("delivery_id values must be unique")
    if not routes:
        raise ValueError("at least one feasible route option is required")

    known_deliveries = set(delivery_ids)
    for route in routes:
        if not isfinite(route.total_cost) or route.total_cost < 0:
            raise ValueError("route costs must be finite and non-negative")
        unknown = set(route.plan.delivery_ids) - known_deliveries
        if unknown:
            raise ValueError(f"route contains unknown deliveries: {sorted(unknown)}")

    routes_by_delivery = {
        delivery_id: tuple(
            index
            for index, route in enumerate(routes)
            if delivery_id in route.plan.delivery_ids
        )
        for delivery_id in delivery_ids
    }
    uncovered = [delivery_id for delivery_id, indexes in routes_by_delivery.items() if not indexes]
    if uncovered:
        raise ValueError(f"no route option covers deliveries: {uncovered}")

    # All route costs are non-negative, so this exceeds the cost of any selection.
    penalty = sum(route.total_cost for route in routes) + 1.0
    linear = {index: route.total_cost for index, route in enumerate(routes)}
    quadratic: dict[tuple[int, int], float] = {}
    constant = penalty * len(delivery_ids)

    for indexes in routes_by_delivery.values():
        for index in indexes:
            linear[index] -= penalty
        for left_position, left in enumerate(indexes):
            for right in indexes[left_position + 1 :]:
                _add_quadratic(quadratic, left, right, 2.0 * penalty)

    routes_by_vehicle: dict[str, list[int]] = {}
    for index, route in enumerate(routes):
        routes_by_vehicle.setdefault(route.plan.vehicle_id, []).append(index)
    for indexes in routes_by_vehicle.values():
        for left_position, left in enumerate(indexes):
            for right in indexes[left_position + 1 :]:
                _add_quadratic(quadratic, left, right, penalty)

    problem = QuadraticProgram("feasible_route_selection")
    variable_names = tuple(f"route_{index:04d}" for index in range(len(routes)))
    for name in variable_names:
        problem.binary_var(name)
    problem.minimize(constant=constant, linear=linear, quadratic=quadratic)
    return RouteQubo(problem, routes, variable_names, delivery_ids, penalty)


def _add_quadratic(
    coefficients: dict[tuple[int, int], float], left: int, right: int, value: float
) -> None:
    key = (min(left, right), max(left, right))
    coefficients[key] = coefficients.get(key, 0.0) + value