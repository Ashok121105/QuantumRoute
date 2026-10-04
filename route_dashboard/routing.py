"""Local-coordinate and optional OSRM travel-time matrix providers."""

import json
from functools import lru_cache
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quantum_route_optimisation import TravelData


TRAFFIC_FACTORS = {
    "Calm": 0.8,
    "Normal": 1.0,
    "Storm": 2.0,
    "Light": 1.0,
    "Moderate": 1.2,
    "Heavy": 1.55,
    "Severe": 2.0,
}
LOCAL_ROAD_FACTOR = 1.25
LOCAL_SPEED_KMPH = 32.0


def build_travel_data(
    coordinates: Mapping[str, tuple[float, float]],
    traffic_condition: str,
    use_osrm: bool = False,
    timeout_seconds: float = 4.0,
) -> tuple[TravelData, str]:
    """Build a complete directed matrix, falling back to local estimates on failure."""
    if traffic_condition not in TRAFFIC_FACTORS:
        raise ValueError(f"unsupported traffic condition: {traffic_condition}")
    _validate_coordinates(coordinates)

    if use_osrm:
        try:
            distances, durations = _fetch_osrm_table(coordinates, timeout_seconds)
            return (
                _travel_data_from_matrices(
                    distances,
                    durations,
                    tuple(coordinates),
                    traffic_condition,
                ),
                "OSRM public table service",
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
            local = _build_local_travel_data(coordinates, traffic_condition)
            return local, f"Local estimate (OSRM unavailable: {error})"

    return _build_local_travel_data(coordinates, traffic_condition), "Local coordinate estimate"


def _build_local_travel_data(
    coordinates: Mapping[str, tuple[float, float]], traffic_condition: str
) -> TravelData:
    locations = tuple(coordinates.items())
    distances: dict[tuple[str, str], float] = {}
    durations: dict[tuple[str, str], float] = {}
    traffic_factor = TRAFFIC_FACTORS[traffic_condition]

    for origin, origin_coordinates in locations:
        for destination, destination_coordinates in locations:
            distance = (
                _haversine_km(origin_coordinates, destination_coordinates)
                * LOCAL_ROAD_FACTOR
            )
            distances[(origin, destination)] = distance
            durations[(origin, destination)] = (
                distance / LOCAL_SPEED_KMPH * 60.0 * traffic_factor
            )

    return TravelData(distances, durations)


def _fetch_osrm_table(
    coordinates: Mapping[str, tuple[float, float]], timeout_seconds: float
) -> tuple[list[list[float | None]], list[list[float | None]]]:
    location_points = tuple(
        (location_id, float(latitude), float(longitude))
        for location_id, (latitude, longitude) in coordinates.items()
    )
    distances, durations, error = _fetch_osrm_table_cached(
        location_points,
        timeout_seconds,
    )
    if error:
        raise OSError(error)
    return [list(row) for row in distances], [list(row) for row in durations]


@lru_cache(maxsize=64)
def _fetch_osrm_table_cached(
    location_points: tuple[tuple[str, float, float], ...], timeout_seconds: float
) -> tuple[
    tuple[tuple[float | None, ...], ...],
    tuple[tuple[float | None, ...], ...],
    str | None,
]:
    coordinate_path = ";".join(
        f"{longitude},{latitude}" for _, latitude, longitude in location_points
    )
    request = Request(
        f"https://router.project-osrm.org/table/v1/driving/{coordinate_path}"
        "?annotations=distance,duration",
        headers={"User-Agent": "QuantumRouteDashboard/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "Ok":
            raise ValueError(f"OSRM returned {payload.get('code', 'an unknown error')}")
        distances = tuple(tuple(row) for row in payload["distances"])
        durations = tuple(tuple(row) for row in payload["durations"])
        return distances, durations, None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        return (), (), str(error)


def get_osrm_route_geometry(
    route_coordinates: tuple[tuple[float, float], ...], timeout_seconds: float = 4.0
) -> tuple[tuple[tuple[float, float], ...] | None, str | None]:
    """Return cached OSRM road geometry as (latitude, longitude) points when available."""
    coordinates = tuple(
        (float(latitude), float(longitude))
        for latitude, longitude in route_coordinates
    )
    geometry, error = _fetch_osrm_route_geometry_cached(coordinates, timeout_seconds)
    return (geometry or None), error


@lru_cache(maxsize=128)
def _fetch_osrm_route_geometry_cached(
    route_coordinates: tuple[tuple[float, float], ...], timeout_seconds: float
) -> tuple[tuple[tuple[float, float], ...], str | None]:
    coordinate_path = ";".join(
        f"{longitude},{latitude}" for latitude, longitude in route_coordinates
    )
    request = Request(
        f"https://router.project-osrm.org/route/v1/driving/{coordinate_path}"
        "?overview=full&geometries=geojson&steps=false",
        headers={"User-Agent": "QuantumRouteDashboard/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ValueError(f"OSRM route geometry unavailable: {payload.get('code', 'no route')}")
        raw_coordinates = payload["routes"][0]["geometry"]["coordinates"]
        geometry = tuple(
            (float(latitude), float(longitude))
            for longitude, latitude in raw_coordinates
        )
        if len(geometry) < 2 or any(
            not isfinite(latitude)
            or not isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            for latitude, longitude in geometry
        ):
            raise ValueError("OSRM returned invalid route geometry")
        return geometry, None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return (), str(error)


def _travel_data_from_matrices(
    distance_matrix: list[list[float | None]],
    duration_matrix: list[list[float | None]],
    location_ids: tuple[str, ...],
    traffic_condition: str,
) -> TravelData:
    size = len(distance_matrix)
    if size == 0 or len(duration_matrix) != size or len(location_ids) != size:
        raise ValueError("OSRM returned matrices with inconsistent dimensions")

    distance_values: dict[tuple[str, str], float] = {}
    duration_values: dict[tuple[str, str], float] = {}
    for origin_index in range(size):
        if len(distance_matrix[origin_index]) != size or len(duration_matrix[origin_index]) != size:
            raise ValueError("OSRM returned matrices with inconsistent dimensions")
        for destination_index in range(size):
            distance_m = distance_matrix[origin_index][destination_index]
            duration_s = duration_matrix[origin_index][destination_index]
            if distance_m is None or duration_s is None:
                raise ValueError("OSRM returned an unreachable location pair")
            distance_km = float(distance_m) / 1000.0
            duration_min = float(duration_s) / 60.0 * TRAFFIC_FACTORS[traffic_condition]
            if not isfinite(distance_km) or not isfinite(duration_min):
                raise ValueError("OSRM returned a non-finite travel value")
            arc = (location_ids[origin_index], location_ids[destination_index])
            distance_values[arc] = distance_km
            duration_values[arc] = duration_min

    return TravelData(distance_values, duration_values)


def _validate_coordinates(coordinates: Mapping[str, tuple[float, float]]) -> None:
    if not coordinates:
        raise ValueError("at least one coordinate is required")
    for location_id, coordinate in coordinates.items():
        if not location_id or len(coordinate) != 2:
            raise ValueError("locations require an id and (latitude, longitude)")
        latitude, longitude = coordinate
        if not isfinite(latitude) or not -90 <= latitude <= 90:
            raise ValueError(f"invalid latitude for {location_id}")
        if not isfinite(longitude) or not -180 <= longitude <= 180:
            raise ValueError(f"invalid longitude for {location_id}")


def _haversine_km(
    origin: tuple[float, float], destination: tuple[float, float]
) -> float:
    latitude_1, longitude_1 = map(radians, origin)
    latitude_2, longitude_2 = map(radians, destination)
    latitude_delta = latitude_2 - latitude_1
    longitude_delta = longitude_2 - longitude_1
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(latitude_1) * cos(latitude_2) * sin(longitude_delta / 2) ** 2
    )
    return 6371.0088 * 2 * asin(sqrt(haversine))