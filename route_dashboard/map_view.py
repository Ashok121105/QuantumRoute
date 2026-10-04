"""Folium rendering for the currently selected optimizer routes."""

from dataclasses import dataclass
from html import escape

import folium

from .dynamic import TrafficReoptimization
from .currency import CurrencyDisplay, format_currency
from .optimization import OptimizationRun, RouteSummary
from .routing import get_osrm_route_geometry
from .scenario import ScenarioProblem


ROUTE_COLORS = ("#087f5b", "#e76f51", "#3a6ea5", "#b56576", "#6b705c")


@dataclass(frozen=True)
class RouteMapView:
    map: folium.Map
    geometry_status: str


def build_route_map(
    scenario: ScenarioProblem,
    run: OptimizationRun,
    show_classical: bool = True,
    show_quantum: bool = True,
    traffic_comparison: TrafficReoptimization | None = None,
    currency_display: CurrencyDisplay | None = None,
) -> RouteMapView:
    depot = scenario.coordinates["depot"]
    route_map = folium.Map(
        location=depot,
        zoom_start=13,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    folium.CircleMarker(
        depot,
        radius=10,
        color="#17324d",
        weight=2,
        fill=True,
        fill_color="#17324d",
        fill_opacity=1.0,
        tooltip=f"Depot | {escape(scenario.location_names['depot'])}",
    ).add_to(route_map)

    for delivery in scenario.deliveries:
        coordinate = scenario.coordinates[delivery.location]
        destination_name = escape(scenario.location_names[delivery.location])
        folium.CircleMarker(
            coordinate,
            radius=7,
            color="#263238",
            weight=2,
            fill=True,
            fill_color="#f4a261",
            fill_opacity=0.95,
            tooltip=destination_name,
            popup=(
                f"{destination_name}<br>"
                f"Demand: {delivery.demand:g}<br>"
                f"Window: {_format_time(delivery.window_start_min)}-"
                f"{_format_time(delivery.window_end_min)}"
            ),
        ).add_to(route_map)

    route_groups: list[tuple[str, RouteSummary, ScenarioProblem, bool]] = []
    if traffic_comparison and traffic_comparison.after_run is not None:
        before_scenario = traffic_comparison.before_scenario
        before_run = traffic_comparison.before_run
        if show_classical:
            route_groups.append(("Before | Classical", before_run.classical, before_scenario, True))
        if show_quantum and before_run.quantum is not None:
            route_groups.append(("Before | QAOA / Aer", before_run.quantum, before_scenario, True))
    if show_classical:
        route_groups.append(("After | Classical", run.classical, scenario, False))
    if show_quantum and run.quantum is not None:
        route_groups.append(("After | QAOA / Aer", run.quantum, scenario, False))

    geometry_modes: list[bool] = []
    geometry_errors: list[str] = []
    for label, summary, route_scenario, is_before in route_groups:
        feature_group = folium.FeatureGroup(name=label, show=True)
        for index, route in enumerate(summary.routes):
            color = ROUTE_COLORS[index % len(ROUTE_COLORS)]
            requested_coordinates = tuple(
                [depot]
                + [
                    route_scenario.coordinates[delivery_id]
                    for delivery_id in route.plan.delivery_ids
                ]
                + [depot]
            )
            route_coordinates = requested_coordinates
            used_osrm_geometry = False
            if route_scenario.osrm_active:
                road_geometry, geometry_error = get_osrm_route_geometry(requested_coordinates)
                if road_geometry is not None:
                    route_coordinates = road_geometry
                    used_osrm_geometry = True
                elif geometry_error:
                    geometry_errors.append(geometry_error)
            geometry_modes.append(used_osrm_geometry)
            route_cost = (
                currency_display.format_money(route.total_cost)
                if currency_display is not None
                else format_currency(route.total_cost, "USD")
            )
            destination_names = [
                escape(route_scenario.location_names[delivery_id])
                for delivery_id in route.plan.delivery_ids
            ]
            folium.PolyLine(
                route_coordinates,
                color=color,
                weight=5,
                opacity=0.9 if not is_before else 0.55,
                dash_array="8, 7" if is_before else None,
                tooltip=f"{label} | {route.plan.vehicle_id}: "
                + " -> ".join(destination_names),
                popup=(
                    f"{label} | {route.plan.vehicle_id}<br>"
                    f"{' -> '.join(destination_names)}<br>"
                    f"{route.distance_km:.1f} km | "
                    f"{route.travel_time_min:.0f} min | "
                    f"{route_cost}"
                ),
            ).add_to(feature_group)
        feature_group.add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
    if scenario.osrm_active and geometry_modes and all(geometry_modes):
        geometry_status = "OSRM road geometry shown for all mapped routes."
    elif scenario.osrm_active and any(geometry_modes):
        geometry_status = "OSRM road geometry shown where available; straight-line polylines used otherwise."
    elif scenario.osrm_active:
        detail = f" ({geometry_errors[0]})" if geometry_errors else ""
        geometry_status = f"OSRM road geometry unavailable; straight-line polylines shown{detail}."
    else:
        geometry_status = "Straight-line/local route visualization; OSRM road geometry is not active."
    return RouteMapView(route_map, geometry_status)


def _format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"