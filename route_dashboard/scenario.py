"""Translate editable dashboard values into the Day 1 and Day 2 data models."""

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from quantum_route_optimisation import (
    Delivery,
    RouteCostWeights,
    TravelData,
    Vehicle,
)

from .routing import TRAFFIC_FACTORS, build_travel_data


FUEL_OPTIONS = {
    "Diesel": {
        "unit": "L",
        "consumption_per_km": 0.09,
        "default_price": 1.10,
        "tailpipe_co2_kg_per_unit": 2.68,
    },
    "Gasoline": {
        "unit": "L",
        "consumption_per_km": 0.11,
        "default_price": 1.25,
        "tailpipe_co2_kg_per_unit": 2.31,
    },
    "Electric": {
        "unit": "kWh",
        "consumption_per_km": 0.20,
        "default_price": 0.18,
        "tailpipe_co2_kg_per_unit": 0.0,
    },
}
FUEL_TYPE_ALIASES = {"Petrol": "Gasoline"}
MAX_DASHBOARD_DELIVERIES = 5


def canonical_fuel_type(fuel_type: str | None) -> str | None:
    """Resolve supported display names and aliases to their calculation model."""
    if fuel_type is None:
        return None
    cleaned = fuel_type.strip().casefold()
    if not cleaned:
        return None
    for supported_type in FUEL_OPTIONS:
        if supported_type.casefold() == cleaned:
            return supported_type
    for alias, supported_type in FUEL_TYPE_ALIASES.items():
        if alias.casefold() == cleaned:
            return supported_type
    return None


@dataclass(frozen=True)
class DeliveryStop:
    destination: str
    latitude: float
    longitude: float
    demand: float
    window_start_min: int
    window_end_min: int
    service_duration_min: int = 5


@dataclass(frozen=True)
class ScenarioProblem:
    vehicles: tuple[Vehicle, ...]
    deliveries: tuple[Delivery, ...]
    travel: TravelData
    cost_weights: RouteCostWeights
    coordinates: dict[str, tuple[float, float]]
    location_names: dict[str, str]
    traffic_condition: str
    fuel_type: str
    fuel_price_per_unit: float
    fuel_unit: str
    data_source: str
    osrm_requested: bool
    osrm_active: bool


def build_scenario(
    stops: Sequence[DeliveryStop],
    *,
    vehicle_count: int,
    vehicle_capacity: float,
    depot_name: str,
    depot_latitude: float,
    depot_longitude: float,
    shift_start_min: int,
    shift_end_min: int,
    traffic_condition: str,
    fuel_type: str,
    fuel_price_per_unit: float,
    driver_cost_per_hour: float = 25.0,
    use_osrm: bool = False,
) -> ScenarioProblem:
    if vehicle_count < 1:
        raise ValueError("at least one vehicle is required")
    if not isfinite(vehicle_capacity) or vehicle_capacity <= 0:
        raise ValueError("vehicle capacity must be positive and finite")
    if not depot_name.strip():
        raise ValueError("depot name must not be empty")
    if shift_end_min < shift_start_min:
        raise ValueError("shift must end at or after it starts")
    if not stops:
        raise ValueError("at least one delivery destination is required")
    if len(stops) > MAX_DASHBOARD_DELIVERIES:
        raise ValueError(
            f"the dashboard supports at most {MAX_DASHBOARD_DELIVERIES} destinations "
            "because the exact baseline is intended for small instances"
        )
    if traffic_condition not in TRAFFIC_FACTORS:
        raise ValueError(f"unsupported traffic condition: {traffic_condition}")
    if fuel_type not in FUEL_OPTIONS:
        raise ValueError(f"unsupported fuel type: {fuel_type}")
    if not isfinite(fuel_price_per_unit) or fuel_price_per_unit < 0:
        raise ValueError("fuel price must be finite and non-negative")
    if not isfinite(driver_cost_per_hour) or driver_cost_per_hour < 0:
        raise ValueError("driver cost must be finite and non-negative")

    destination_names = [stop.destination.strip() for stop in stops]
    if any(not destination for destination in destination_names):
        raise ValueError("destination names must not be empty")
    if len({name.casefold() for name in destination_names}) != len(destination_names):
        raise ValueError("destination names must be unique")

    coordinates = {"depot": (depot_latitude, depot_longitude)}
    location_names = {"depot": depot_name.strip()}
    deliveries: list[Delivery] = []
    for index, (stop, destination) in enumerate(zip(stops, destination_names), start=1):
        delivery_id = f"delivery-{index}"
        coordinates[delivery_id] = (stop.latitude, stop.longitude)
        location_names[delivery_id] = destination
        deliveries.append(
            Delivery(
                delivery_id=delivery_id,
                location=delivery_id,
                demand=stop.demand,
                window_start_min=stop.window_start_min,
                window_end_min=stop.window_end_min,
                service_duration_min=stop.service_duration_min,
            )
        )

    travel, data_source = build_travel_data(
        coordinates,
        traffic_condition,
        use_osrm=use_osrm,
    )
    fuel = FUEL_OPTIONS[fuel_type]
    cost_weights = RouteCostWeights(
        distance_cost_per_km=fuel["consumption_per_km"] * fuel_price_per_unit,
        time_cost_per_min=driver_cost_per_hour / 60.0,
    )
    vehicles = tuple(
        Vehicle(
            vehicle_id=f"van-{index}",
            capacity=vehicle_capacity,
            start_location="depot",
            end_location="depot",
            shift_start_min=shift_start_min,
            shift_end_min=shift_end_min,
        )
        for index in range(1, vehicle_count + 1)
    )

    return ScenarioProblem(
        vehicles=vehicles,
        deliveries=tuple(deliveries),
        travel=travel,
        cost_weights=cost_weights,
        coordinates=coordinates,
        location_names=location_names,
        traffic_condition=traffic_condition,
        fuel_type=fuel_type,
        fuel_price_per_unit=fuel_price_per_unit,
        fuel_unit=fuel["unit"],
        data_source=data_source,
        osrm_requested=use_osrm,
        osrm_active=data_source == "OSRM public table service",
    )


def demo_stops() -> tuple[DeliveryStop, ...]:
    return (
        DeliveryStop("North Market", 37.7905, -122.4082, 1.0, 480, 690),
        DeliveryStop("Civic Centre", 37.7793, -122.4192, 2.0, 510, 780),
        DeliveryStop("Harbor Point", 37.7992, -122.3975, 1.0, 540, 840),
    )


def build_demo_scenario(traffic_condition: str = "Moderate") -> ScenarioProblem:
    fuel = FUEL_OPTIONS["Diesel"]
    return build_scenario(
        demo_stops(),
        vehicle_count=2,
        vehicle_capacity=2.0,
        depot_name="Mission Depot",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1020,
        traffic_condition=traffic_condition,
        fuel_type="Diesel",
        fuel_price_per_unit=fuel["default_price"],
    )