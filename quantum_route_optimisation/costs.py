"""Route objective calculation shared by classical and future QAOA solvers."""

from .models import RouteCostWeights


def calculate_route_cost(
    distance_km: float,
    elapsed_time_min: float,
    weights: RouteCostWeights,
) -> float:
    """Return weighted distance and elapsed-time cost plus the vehicle fixed cost."""
    return (
        distance_km * weights.distance_cost_per_km
        + elapsed_time_min * weights.time_cost_per_min
        + weights.fixed_vehicle_cost
    )