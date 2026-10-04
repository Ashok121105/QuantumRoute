"""Small, validated input and output models for route optimization."""

from dataclasses import dataclass
from math import isfinite
from typing import Mapping


def _require_nonnegative_finite(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")


@dataclass(frozen=True)
class Delivery:
    delivery_id: str
    location: str
    demand: float
    window_start_min: int
    window_end_min: int
    service_duration_min: int = 0

    def __post_init__(self) -> None:
        if not self.delivery_id or not self.location:
            raise ValueError("delivery_id and location must not be empty")
        _require_nonnegative_finite("demand", self.demand)
        if self.window_end_min < self.window_start_min:
            raise ValueError("window_end_min must be at or after window_start_min")
        if self.service_duration_min < 0:
            raise ValueError("service_duration_min must be non-negative")


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    capacity: float
    start_location: str
    end_location: str
    shift_start_min: int = 0
    shift_end_min: int = 1440

    def __post_init__(self) -> None:
        if not self.vehicle_id or not self.start_location or not self.end_location:
            raise ValueError("vehicle_id and route locations must not be empty")
        _require_nonnegative_finite("capacity", self.capacity)
        if self.shift_end_min < self.shift_start_min:
            raise ValueError("shift_end_min must be at or after shift_start_min")


@dataclass(frozen=True)
class RoutePlan:
    vehicle_id: str
    delivery_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.vehicle_id:
            raise ValueError("vehicle_id must not be empty")
        if not self.delivery_ids:
            raise ValueError("a route plan must contain at least one delivery")
        if len(set(self.delivery_ids)) != len(self.delivery_ids):
            raise ValueError("a route plan cannot visit a delivery more than once")


@dataclass(frozen=True)
class TravelData:
    distances_km: Mapping[tuple[str, str], float]
    durations_min: Mapping[tuple[str, str], float]

    def __post_init__(self) -> None:
        for name, values in (
            ("distances_km", self.distances_km),
            ("durations_min", self.durations_min),
        ):
            for arc, value in values.items():
                if len(arc) != 2 or not all(arc):
                    raise ValueError(f"{name} keys must be (origin, destination) pairs")
                _require_nonnegative_finite(name, value)


@dataclass(frozen=True)
class RouteCostWeights:
    distance_cost_per_km: float = 1.0
    time_cost_per_min: float = 0.0
    fixed_vehicle_cost: float = 0.0

    def __post_init__(self) -> None:
        _require_nonnegative_finite("distance_cost_per_km", self.distance_cost_per_km)
        _require_nonnegative_finite("time_cost_per_min", self.time_cost_per_min)
        _require_nonnegative_finite("fixed_vehicle_cost", self.fixed_vehicle_cost)


@dataclass(frozen=True)
class FeasibleRoute:
    plan: RoutePlan
    distance_km: float
    travel_time_min: float
    service_time_min: int
    waiting_time_min: float
    elapsed_time_min: float
    service_start_times: tuple[tuple[str, float], ...]
    total_cost: float