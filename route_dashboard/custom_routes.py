"""Location lookup and explainable scoring for point-to-point road routes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
from threading import Lock
from time import monotonic, sleep, time
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import folium
import pydeck as pdk

from .routing import OSRMRouteAlternative
from .scenario import FUEL_OPTIONS, canonical_fuel_type


GEOCODER_USER_AGENT = (
    "QuantumRoute/1.0 (https://github.com/Ashok121105/QuantumRoute)"
)
GEOCODER_MIN_INTERVAL_SECONDS = 1.1
GEOCODER_CACHE_TTL_SECONDS = 3600
_geocoder_lock = Lock()
_last_geocoder_request = 0.0
CUSTOM_ROUTE_COLORS = ("#22D3EE", "#3B82F6", "#A78BFA")
CARTO_DARK_MAP_STYLE = (
    "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
)


@dataclass(frozen=True)
class PlaceResult:
    display_name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class CustomRouteScore:
    route: OSRMRouteAlternative
    estimated_time_min: float
    vehicle_speed_adjusted: bool
    fuel_used: float | None
    fuel_unit: str | None
    fuel_cost: float | None
    driver_cost: float | None
    operating_cost: float | None
    tailpipe_co2_kg: float | None
    normalized_score: float


def search_places(query: str) -> tuple[tuple[PlaceResult, ...], str | None]:
    """Search Nominatim for real place results; empty searches do no network I/O."""
    cleaned = query.strip()
    if not cleaned:
        return (), "Enter a place, locality, or address to search."
    try:
        return _search_places_cached(
            cleaned.casefold(),
            cleaned,
            int(time() // GEOCODER_CACHE_TTL_SECONDS),
        ), None
    except _GeocoderRateLimited:
        return (), "Place search is temporarily rate-limited. Wait before trying again."
    except _GeocoderUnavailable:
        return (), "Place search is unavailable right now. Try again later or check your connection."
    except _GeocoderResponseError:
        return (), "The place-search service returned an invalid response."


class _GeocoderRateLimited(Exception):
    pass


class _GeocoderUnavailable(Exception):
    pass


class _GeocoderResponseError(Exception):
    pass


@lru_cache(maxsize=128)
def _search_places_cached(
    normalized_query: str,
    query: str,
    cache_bucket: int,
) -> tuple[PlaceResult, ...]:
    del normalized_query, cache_bucket
    global _last_geocoder_request
    with _geocoder_lock:
        wait = GEOCODER_MIN_INTERVAL_SECONDS - (monotonic() - _last_geocoder_request)
        if wait > 0:
            sleep(wait)
        params = urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "limit": "5",
                "addressdetails": "0",
            }
        )
        request = Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={
                "User-Agent": GEOCODER_USER_AGENT,
                "Accept": "application/json",
            },
        )
        _last_geocoder_request = monotonic()
        try:
            with urlopen(request, timeout=8.0) as response:
                import json

                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 429:
                raise _GeocoderRateLimited from None
            raise _GeocoderUnavailable from None
        except (URLError, TimeoutError, OSError):
            raise _GeocoderUnavailable from None
        except (UnicodeDecodeError, ValueError):
            raise _GeocoderResponseError from None

    if not isinstance(payload, list):
        raise _GeocoderResponseError
    results: list[PlaceResult] = []
    for item in payload:
        if not isinstance(item, dict):
            raise _GeocoderResponseError
        try:
            name = str(item["display_name"]).strip()
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _GeocoderResponseError from None
        if (
            not name
            or not isfinite(latitude)
            or not isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise _GeocoderResponseError
        results.append(PlaceResult(name, latitude, longitude))
    return tuple(results)


def custom_route_objectives(
    *,
    has_estimated_cost: bool,
    has_emissions: bool,
) -> tuple[str, ...]:
    objectives = ["Shortest Distance", "Fastest Estimated Time"]
    if has_estimated_cost:
        objectives.append("Lowest Estimated Cost")
    if has_emissions:
        objectives.append("Green / Lower Estimated Emissions")
    objectives.append("Balanced")
    return tuple(objectives)


def score_custom_route_alternatives(
    alternatives: Sequence[OSRMRouteAlternative],
    objective: str,
    *,
    fuel_type: str | None = None,
    fuel_consumption_per_km: float | None = None,
    fuel_price_per_unit: float | None = None,
    driver_cost_per_hour: float | None = None,
    max_speed_kmph: float | None = None,
) -> tuple[CustomRouteScore, ...]:
    """Rank provider-returned routes using only comparable, available inputs."""
    canonical_type = canonical_fuel_type(fuel_type)
    fuel = FUEL_OPTIONS.get(canonical_type or "")
    can_estimate_fuel = (
        fuel is not None
        and _valid_nonnegative(fuel_consumption_per_km)
        and float(fuel_consumption_per_km) > 0
    )
    can_estimate_cost = (
        can_estimate_fuel
        and _valid_nonnegative(fuel_price_per_unit)
        and _valid_nonnegative(driver_cost_per_hour)
    )
    can_apply_speed_limit = (
        _valid_nonnegative(max_speed_kmph)
        and float(max_speed_kmph) > 0
    )

    metrics: list[dict[str, float | None | bool]] = []
    for route in alternatives:
        estimated_time_min = route.duration_min
        if can_apply_speed_limit:
            minimum_time_at_speed_limit = (
                route.distance_km / float(max_speed_kmph) * 60.0
            )
            estimated_time_min = max(route.duration_min, minimum_time_at_speed_limit)
        fuel_used = (
            route.distance_km * float(fuel_consumption_per_km)
            if can_estimate_fuel
            else None
        )
        emissions = (
            fuel_used * fuel["tailpipe_co2_kg_per_unit"]
            if fuel_used is not None and fuel is not None
            else None
        )
        fuel_cost = (
            fuel_used * float(fuel_price_per_unit)
            if fuel_used is not None and _valid_nonnegative(fuel_price_per_unit)
            else None
        )
        driver_cost = (
            estimated_time_min / 60.0 * float(driver_cost_per_hour)
            if _valid_nonnegative(driver_cost_per_hour)
            else None
        )
        operating_cost = (
            fuel_cost + driver_cost
            if can_estimate_cost and fuel_cost is not None and driver_cost is not None
            else None
        )
        metrics.append(
            {
                "distance": route.distance_km,
                "time": estimated_time_min,
                "estimated_time": estimated_time_min,
                "vehicle_speed_adjusted": (
                    estimated_time_min > route.duration_min
                ),
                "cost": operating_cost,
                "emissions": emissions,
                "fuel": fuel_used,
                "fuel_cost": fuel_cost,
                "driver_cost": driver_cost,
            }
        )

    available_dimensions: list[tuple[str, float]] = []
    if objective == "Shortest Distance":
        available_dimensions = [("distance", 1.0)]
    elif objective == "Fastest Estimated Time":
        available_dimensions = [("time", 1.0)]
    elif objective == "Lowest Estimated Cost":
        available_dimensions = [("cost", 1.0)]
    elif objective == "Green / Lower Estimated Emissions":
        available_dimensions = [("emissions", 1.0)]
    elif objective == "Balanced":
        available_dimensions = [("distance", 0.5), ("time", 0.5)]
        if can_estimate_cost:
            available_dimensions.append(("cost", 0.25))
            available_dimensions[0] = ("distance", 0.375)
            available_dimensions[1] = ("time", 0.375)
        if can_estimate_fuel:
            available_dimensions.append(("emissions", 0.25))
            if can_estimate_cost:
                available_dimensions = [
                    (name, 0.25)
                    for name, _weight in (
                        ("distance", 0.25),
                        ("time", 0.25),
                        ("cost", 0.25),
                        ("emissions", 0.25),
                    )
                ]
            else:
                available_dimensions = [
                    ("distance", 0.375),
                    ("time", 0.375),
                    ("emissions", 0.25),
                ]
    else:
        raise ValueError(f"Unsupported custom route objective: {objective}")

    if not alternatives:
        return ()
    for name, _weight in available_dimensions:
        if any(item[name] is None for item in metrics):
            raise ValueError(f"{objective} cannot be scored: {name} data is incomplete.")
    score_values = [0.0] * len(metrics)
    for dimension, weight in available_dimensions:
        values = [float(item[dimension]) for item in metrics]
        minimum, maximum = min(values), max(values)
        span = maximum - minimum
        if span > 0:
            score_values = [
                score + weight * (value - minimum) / span
                for score, value in zip(
                    score_values,
                    values,
                )
            ]

    scored = [
        CustomRouteScore(
            route=route,
            estimated_time_min=float(metric["estimated_time"]),
            vehicle_speed_adjusted=bool(metric["vehicle_speed_adjusted"]),
            fuel_used=metric["fuel"],
            fuel_unit=fuel["unit"] if metric["fuel"] is not None and fuel is not None else None,
            fuel_cost=metric["fuel_cost"],
            driver_cost=metric["driver_cost"],
            operating_cost=metric["cost"],
            tailpipe_co2_kg=metric["emissions"],
            normalized_score=score_values[index],
        )
        for index, (route, metric) in enumerate(zip(alternatives, metrics))
    ]
    return tuple(sorted(scored, key=lambda item: (item.normalized_score, item.route.returned_order)))


def build_custom_route_map(
    origin: tuple[float, float],
    destination: tuple[float, float] | None,
    alternatives: Sequence[CustomRouteScore],
    selected_alternative_id: str | None,
    recommended_alternative_id: str | None = None,
    recommendation_available: bool = True,
) -> folium.Map:
    """Build a base-map view of selected endpoints and returned route geometries."""
    route_map = folium.Map(
        location=(
            ((origin[0] + destination[0]) / 2, (origin[1] + destination[1]) / 2)
            if destination is not None
            else origin
        ),
        zoom_start=7 if destination is not None else 13,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    folium.Marker(
        origin,
        tooltip="Origin",
        popup="Selected origin",
        icon=folium.Icon(color="blue", icon="play"),
    ).add_to(route_map)
    all_points: list[tuple[float, float]] = [origin]
    if destination is not None:
        folium.Marker(
            destination,
            tooltip="Destination",
            popup="Selected destination",
            icon=folium.Icon(color="purple", icon="stop"),
        ).add_to(route_map)
        all_points.append(destination)
    if (
        recommended_alternative_id is None
        and alternatives
        and recommendation_available
    ):
        recommended_alternative_id = min(
            alternatives,
            key=lambda item: (item.normalized_score, item.route.returned_order),
        ).route.alternative_id
    for index, scored in enumerate(alternatives):
        route = scored.route
        all_points.extend(route.geometry)
        selected = route.alternative_id == selected_alternative_id
        recommended = route.alternative_id == recommended_alternative_id
        color = CUSTOM_ROUTE_COLORS[index % len(CUSTOM_ROUTE_COLORS)]
        folium.PolyLine(
            route.geometry,
            color=color,
            weight=8 if recommended else 6 if selected else 4,
            opacity=1.0 if recommended or selected else 0.68,
            dash_array=None if recommended else "8 7",
            tooltip=(
                f"OSRM route {route.returned_order}"
                + (" · recommended" if recommended else "")
                + (" · selected" if selected else "")
            ),
        ).add_to(route_map)
    if destination is not None:
        route_map.fit_bounds(all_points, padding=(20, 20))
    return route_map


def build_custom_route_tilted_map(
    origin: tuple[float, float],
    destination: tuple[float, float] | None,
    alternatives: Sequence[CustomRouteScore],
    selected_alternative_id: str | None,
    recommended_alternative_id: str | None = None,
    recommendation_available: bool = True,
    origin_label: str = "Origin",
    destination_label: str = "Destination",
) -> pdk.Deck:
    """Build an interactive pitched map using only selected coordinates and route geometry."""
    all_points = [origin]
    if destination is not None:
        all_points.append(destination)
    for scored in alternatives:
        all_points.extend(scored.route.geometry)

    viewport: pdk.ViewState = pdk.data_utils.compute_view(
        [[longitude, latitude] for latitude, longitude in all_points]
    )
    view_state = pdk.ViewState(
        latitude=viewport.latitude,
        longitude=viewport.longitude,
        zoom=min(15, max(3, viewport.zoom)),
        pitch=55,
        bearing=0,
    )

    endpoint_rows = [
        {
            "position": [origin[1], origin[0]],
            "label": f"Origin: {origin_label}",
            "color": [34, 211, 238, 255],
        }
    ]
    if destination is not None:
        endpoint_rows.append(
            {
                "position": [destination[1], destination[0]],
                "label": f"Destination: {destination_label}",
                "color": [167, 139, 250, 255],
            }
        )

    if (
        recommended_alternative_id is None
        and alternatives
        and recommendation_available
    ):
        recommended_alternative_id = min(
            alternatives,
            key=lambda item: (
                item.normalized_score,
                item.route.returned_order,
            ),
        ).route.alternative_id

    route_rows = []
    recommended_rows = []
    route_labels = []
    for index, scored in enumerate(alternatives):
        route = scored.route
        path = [[longitude, latitude] for latitude, longitude in route.geometry]
        selected = route.alternative_id == selected_alternative_id
        recommended = route.alternative_id == recommended_alternative_id
        label = (
            f"Route {route.returned_order} · {route.distance_km:.1f} km · "
            f"OSRM estimated {route.duration_min:.0f} min"
        )
        route_rows.append(
            {
                "path": path,
                "color": _hex_color_rgba(
                    CUSTOM_ROUTE_COLORS[index % len(CUSTOM_ROUTE_COLORS)]
                ),
                "width": 7 if selected and not recommended else 5,
                "label": label
                + (" · recommended" if recommended else "")
                + (" · selected" if selected else ""),
            }
        )
        if recommended:
            recommended_rows.append({"path": path})
        if path:
            route_labels.append(
                {
                    "position": path[len(path) // 2],
                    "label": label,
                    "color": [241, 245, 249, 255],
                }
            )

    layers = [
        pdk.Layer(
            "PathLayer",
            data=recommended_rows,
            id="recommended-route-highlight",
            get_path="path",
            get_color=[52, 211, 153, 210],
            get_width=12,
            width_units="pixels",
            width_min_pixels=12,
            pickable=False,
        ),
        pdk.Layer(
            "PathLayer",
            data=route_rows,
            id="returned-osrm-routes",
            get_path="path",
            get_color="color",
            get_width="width",
            width_units="pixels",
            width_min_pixels=5,
            pickable=True,
            auto_highlight=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=endpoint_rows,
            id="selected-route-endpoints",
            get_position="position",
            get_fill_color="color",
            get_radius=90,
            radius_units="meters",
            radius_min_pixels=7,
            radius_max_pixels=13,
            line_color=[8, 17, 31, 255],
            line_width_min_pixels=2,
            stroked=True,
            pickable=True,
        ),
        pdk.Layer(
            "TextLayer",
            data=endpoint_rows + route_labels,
            id="route-and-location-labels",
            get_position="position",
            get_text="label",
            get_color="color",
            get_size=14,
            size_units="pixels",
            get_pixel_offset=[0, -16],
            get_text_anchor="middle",
            get_alignment_baseline="bottom",
            billboard=True,
            pickable=False,
        ),
    ]
    return pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style=CARTO_DARK_MAP_STYLE,
        tooltip={
            "text": "{label}",
            "style": {
                "backgroundColor": "#101D30",
                "color": "#F1F5F9",
            },
        },
    )


def _hex_color_rgba(color: str) -> list[int]:
    return [int(color[index : index + 2], 16) for index in (1, 3, 5)] + [255]


def route_recommendation_explanation(
    objective: str,
    selected: CustomRouteScore,
    recommended: CustomRouteScore,
    alternative_count: int,
) -> str:
    relation = (
        "It matches the recommended route."
        if selected.route.alternative_id == recommended.route.alternative_id
        else "It differs from the top-ranked route; your selection is shown on the map."
    )
    explanation = (
        f"Objective: {objective}. OSRM returned {alternative_count} distinct route(s); "
        f"route {recommended.route.returned_order} has the lowest normalized score "
        f"among those returned. Its score is {recommended.normalized_score:.3f}. "
        f"{relation}"
    )
    if selected.operating_cost is None:
        explanation += " Full operating cost is unavailable because the selected vehicle profile lacks cost inputs."
    if selected.tailpipe_co2_kg is None:
        explanation += " Emissions are unavailable because fuel type or consumption data is missing."
    if recommended.vehicle_speed_adjusted:
        explanation += (
            " The uploaded maximum-speed constraint raises its vehicle-adjusted "
            "time estimate above OSRM's primary duration."
        )
    return explanation


def _valid_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
        and float(value) >= 0
    )
