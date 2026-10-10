"""Folium rendering for the currently selected optimizer routes."""

from dataclasses import dataclass
from html import escape

import folium

from .dynamic import TrafficReoptimization
from .currency import format_cost
from .optimization import OptimizationRun, RouteSummary
from .objectives import (
    ObjectiveConfig,
    ObjectiveMetrics,
    normalized_objective_value,
    objective_scales,
)
from .routing import (
    OSRMRouteAlternative,
    TRAFFIC_FACTORS,
    get_osrm_route_geometry,
)
from .scenario import FUEL_OPTIONS, ScenarioProblem


ROUTE_COLORS = ("#3B82F6", "#A78BFA", "#22D3EE", "#34D399", "#60A5FA")
VEHICLE_COLORS = (
    "#60A5FA",
    "#FDBA74",
    "#5EEAD4",
    "#F9A8D4",
    "#BEF264",
    "#FDE047",
    "#C4B5FD",
    "#FDA4AF",
)
OPTIMIZED_SEGMENT_COLORS = (
    "#F87171",
    "#FBBF24",
    "#60A5FA",
    "#C084FC",
    "#FB923C",
    "#2DD4BF",
)
ROAD_ALTERNATIVE_COLORS = ("#22D3EE", "#3B82F6", "#A78BFA")


@dataclass(frozen=True)
class RouteMapView:
    map: folium.Map
    geometry_status: str


@dataclass(frozen=True)
class ScoredRoadAlternative:
    route: OSRMRouteAlternative
    estimated_time_min: float
    fuel_used: float
    fuel_cost: float
    operating_cost: float
    tailpipe_co2_kg: float
    objective_score: float


def build_location_preview_map(
    origin: tuple[float, float],
    destinations: tuple[tuple[str, tuple[float, float]], ...],
    origin_label: str,
) -> folium.Map:
    """Show selected locations before route optimization has been run."""
    route_map = folium.Map(
        location=origin,
        zoom_start=12,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    folium.CircleMarker(
        origin,
        radius=10,
        color="#22D3EE",
        weight=2,
        fill=True,
        fill_color="#101D30",
        fill_opacity=1.0,
        tooltip=f"Origin | {escape(origin_label)}",
    ).add_to(route_map)

    bounds = [origin]
    for index, (label, coordinate) in enumerate(destinations, start=1):
        escaped_label = escape(label)
        folium.CircleMarker(
            coordinate,
            radius=7,
            color="#A78BFA",
            weight=2,
            fill=True,
            fill_color="#A78BFA",
            fill_opacity=0.95,
            tooltip=f"Destination {index} | {escaped_label}",
            popup=escaped_label,
        ).add_to(route_map)
        bounds.append(coordinate)

    if len(bounds) > 1:
        route_map.fit_bounds(bounds, padding=(20, 20))
    return route_map


def rank_road_alternatives(
    scenario: ScenarioProblem,
    objective_name: str,
    alternatives: tuple[OSRMRouteAlternative, ...],
) -> tuple[ScoredRoadAlternative, ...]:
    """Rank returned OSRM legs with the dashboard's normalized objective model."""
    objective = ObjectiveConfig.for_name(objective_name)
    scales = objective_scales(scenario)
    fuel = FUEL_OPTIONS[scenario.fuel_type]
    traffic_factor = TRAFFIC_FACTORS[scenario.traffic_condition]
    scored = []
    for route in alternatives:
        estimated_time = route.duration_min * traffic_factor
        fuel_used = route.distance_km * fuel["consumption_per_km"]
        fuel_cost = fuel_used * scenario.fuel_price_per_unit
        operating_cost = (
            route.distance_km * scenario.cost_weights.distance_cost_per_km
            + estimated_time * scenario.cost_weights.time_cost_per_min
        )
        co2 = fuel_used * fuel["tailpipe_co2_kg_per_unit"]
        score = normalized_objective_value(
            ObjectiveMetrics(
                cost=operating_cost,
                elapsed_time_min=estimated_time,
                fuel=fuel_used,
                co2_kg=co2,
            ),
            objective,
            scales,
        )
        scored.append(
            ScoredRoadAlternative(
                route,
                estimated_time,
                fuel_used,
                fuel_cost,
                operating_cost,
                co2,
                score,
            )
        )
    return tuple(sorted(scored, key=lambda item: (item.objective_score, item.route.returned_order)))


def build_route_map(
    scenario: ScenarioProblem,
    run: OptimizationRun,
    show_classical: bool = True,
    show_quantum: bool = True,
    traffic_comparison: TrafficReoptimization | None = None,
    road_alternatives: tuple[ScoredRoadAlternative, ...] = (),
    selected_road_alternative_id: str | None = None,
    show_road_alternatives: bool = True,
    show_optimized_order: bool = False,
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
        color="#22D3EE",
        weight=2,
        fill=True,
        fill_color="#101D30",
        fill_opacity=1.0,
        tooltip=f"Depot | {escape(scenario.location_names['depot'])}",
    ).add_to(route_map)

    if not show_optimized_order:
        for delivery in scenario.deliveries:
            coordinate = scenario.coordinates[delivery.location]
            destination_name = escape(scenario.location_names[delivery.location])
            folium.CircleMarker(
                coordinate,
                radius=7,
                color="#A78BFA",
                weight=2,
                fill=True,
                fill_color="#A78BFA",
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
    optimized_leg_count = 0
    optimized_geometry_count = 0
    optimized_legend_items: list[str] = []
    optimized_bounds: list[tuple[float, float]] = []
    for label, summary, route_scenario, is_before in route_groups:
        feature_group = folium.FeatureGroup(name=label, show=True)
        for index, route in enumerate(summary.routes):
            color = ROUTE_COLORS[index % len(ROUTE_COLORS)]
            vehicle_map = {
                vehicle.vehicle_id: vehicle for vehicle in route_scenario.vehicles
            }
            vehicle = vehicle_map[route.plan.vehicle_id]
            vehicle_order = sorted(vehicle_map)
            vehicle_color = VEHICLE_COLORS[
                vehicle_order.index(route.plan.vehicle_id) % len(VEHICLE_COLORS)
            ]
            delivery_map = {
                delivery.delivery_id: delivery
                for delivery in route_scenario.deliveries
            }
            route_origin = route_scenario.coordinates[vehicle.start_location]
            route_destination = route_scenario.coordinates[vehicle.end_location]
            requested_coordinates = tuple(
                [route_origin]
                + [
                    route_scenario.coordinates[delivery_map[delivery_id].location]
                    for delivery_id in route.plan.delivery_ids
                ]
                + [route_destination]
            )
            route_coordinates = requested_coordinates
            used_osrm_geometry = False
            if show_optimized_order:
                ordered_location_ids = (
                    vehicle.start_location,
                    *(
                        delivery_map[delivery_id].location
                        for delivery_id in route.plan.delivery_ids
                    ),
                    vehicle.end_location,
                )
                ordered_names = tuple(
                    route_scenario.location_names[location_id]
                    for location_id in ordered_location_ids
                )
                ordered_coordinates = tuple(
                    route_scenario.coordinates[location_id]
                    for location_id in ordered_location_ids
                )
                optimized_bounds.extend(ordered_coordinates)
                optimized_legend_items.append(
                    '<span style="display:inline-block;width:12px;height:12px;'
                    f"border:2px solid {vehicle_color};border-radius:50%;"
                    'vertical-align:middle;margin-right:5px"></span>'
                    f"<strong>{escape(label)} · {escape(route.plan.vehicle_id)}</strong>"
                )
                for stop_number, delivery_id in enumerate(
                    route.plan.delivery_ids, start=1
                ):
                    delivery = delivery_map[delivery_id]
                    destination_name = escape(
                        route_scenario.location_names[delivery.location]
                    )
                    folium.Marker(
                        route_scenario.coordinates[delivery.location],
                        tooltip=(
                            f"Stop {stop_number} | {destination_name} | "
                            f"{escape(route.plan.vehicle_id)}"
                        ),
                        popup=folium.Popup(
                            f"Stop {stop_number} — {destination_name}<br>"
                            f"Vehicle: {escape(route.plan.vehicle_id)}<br>"
                            f"Demand: {delivery.demand:g}<br>"
                            f"Window: {_format_time(delivery.window_start_min)}-"
                            f"{_format_time(delivery.window_end_min)}",
                            max_width=280,
                        ),
                        icon=folium.DivIcon(
                            html=(
                                '<div style="background:#151014;color:#F3E9DA;'
                                f"border:2px solid {vehicle_color};"
                                "border-radius:50%;width:30px;height:30px;"
                                "display:flex;align-items:center;justify-content:center;"
                                'font-weight:700;font-size:12px">'
                                f"{stop_number}</div>"
                            ),
                            icon_size=(30, 30),
                            icon_anchor=(15, 15),
                        ),
                    ).add_to(feature_group)
                    optimized_legend_items.append(
                        f"Stop {stop_number} — {destination_name}"
                    )

                for leg_index, (start_id, end_id) in enumerate(
                    zip(ordered_location_ids, ordered_location_ids[1:]),
                    start=1,
                ):
                    optimized_leg_count += 1
                    leg_coordinates = (
                        route_scenario.coordinates[start_id],
                        route_scenario.coordinates[end_id],
                    )
                    road_geometry, geometry_error = get_osrm_route_geometry(
                        leg_coordinates
                    )
                    if road_geometry is None:
                        if geometry_error:
                            geometry_errors.append(geometry_error)
                        optimized_legend_items.append(
                            f'<span style="color:#F3E9DA">Segment {leg_index} — '
                            f"{escape(ordered_names[leg_index - 1])} → "
                            f"{escape(ordered_names[leg_index])} "
                            "(road geometry unavailable)</span>"
                        )
                        continue

                    optimized_geometry_count += 1
                    optimized_bounds.extend(road_geometry)
                    leg_distance = route_scenario.travel.distances_km[
                        (start_id, end_id)
                    ]
                    leg_duration = route_scenario.travel.durations_min[
                        (start_id, end_id)
                    ]
                    segment_color = OPTIMIZED_SEGMENT_COLORS[
                        (leg_index - 1) % len(OPTIMIZED_SEGMENT_COLORS)
                    ]
                    start_name = escape(ordered_names[leg_index - 1])
                    end_name = escape(ordered_names[leg_index])
                    segment_description = (
                        f"Segment {leg_index} | {start_name} → {end_name}"
                    )
                    folium.PolyLine(
                        road_geometry,
                        color=segment_color,
                        weight=6,
                        opacity=0.95 if not is_before else 0.55,
                        dash_array="8, 7" if is_before else None,
                        tooltip=(
                            f"{label} | {route.plan.vehicle_id} | "
                            f"{segment_description}"
                        ),
                        popup=(
                            f"{escape(route.plan.vehicle_id)} | "
                            f"{segment_description}<br>"
                            f"{leg_distance:.1f} km | {leg_duration:.0f} min"
                        ),
                    ).add_to(feature_group)
                    optimized_legend_items.append(
                        '<span style="display:inline-block;width:16px;'
                        f'border-top:4px solid {segment_color};margin-right:5px"></span>'
                        f"{segment_description} — {leg_distance:.1f} km, "
                        f"{leg_duration:.0f} min"
                    )
                continue

            if route_scenario.osrm_active:
                road_geometry, geometry_error = get_osrm_route_geometry(requested_coordinates)
                if road_geometry is not None:
                    route_coordinates = road_geometry
                    used_osrm_geometry = True
                elif geometry_error:
                    geometry_errors.append(geometry_error)
            geometry_modes.append(used_osrm_geometry)
            route_cost = format_cost(route.total_cost)
            destination_names = [
                escape(
                    route_scenario.location_names[
                        delivery_map[delivery_id].location
                    ]
                )
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

    if road_alternatives:
        _add_road_alternative_layers(
            route_map,
            road_alternatives,
            selected_road_alternative_id,
            show_road_alternatives,
        )

    folium.LayerControl(collapsed=False).add_to(route_map)
    if show_optimized_order and optimized_bounds:
        route_map.fit_bounds(optimized_bounds, padding=(24, 24))
    if show_optimized_order and not optimized_leg_count:
        geometry_status = "No optimized route layer is currently selected."
    elif show_optimized_order:
        if optimized_geometry_count == optimized_leg_count:
            geometry_status = (
                f"OSRM road geometry shown for all {optimized_leg_count} optimized "
                "route segments. Segment metrics use the optimizer travel matrix."
            )
        else:
            geometry_status = (
                f"OSRM road geometry unavailable for "
                f"{optimized_leg_count - optimized_geometry_count} of "
                f"{optimized_leg_count} optimized route segments; missing segments "
                "are omitted. Route metrics remain from the optimizer travel matrix."
            )
            if geometry_errors:
                geometry_status += f" First OSRM error: {geometry_errors[0]}"
    elif scenario.osrm_active and geometry_modes and all(geometry_modes):
        geometry_status = "OSRM road geometry shown for all mapped routes."
    elif scenario.osrm_active and any(geometry_modes):
        geometry_status = "OSRM road geometry shown where available; straight-line polylines used otherwise."
    elif scenario.osrm_active:
        detail = f" ({geometry_errors[0]})" if geometry_errors else ""
        geometry_status = f"OSRM road geometry unavailable; straight-line polylines shown{detail}."
    else:
        geometry_status = (
            "Straight-line/local route visualization uses coordinate estimates; these are not "
            "road-route geometries. OSRM road geometry is not active for optimizer routes."
        )
    if road_alternatives:
        geometry_status += (
            " The separate selected-leg alternatives use returned OSRM road geometries."
        )
    if show_optimized_order and optimized_legend_items:
        legend = (
            '<div style="position:fixed;bottom:24px;left:24px;z-index:9999;'
            "max-width:340px;max-height:38vh;overflow:auto;"
            "background:rgba(21,16,20,.96);color:#F3E9DA;padding:10px 12px;"
            "border:1px solid #D8B866;border-radius:8px;font-size:12px\">"
            "<strong>Optimized route legs</strong><ul style=\"padding-left:8px;"
            f'margin:6px 0 0;list-style:none">{"".join(f"<li>{item}</li>" for item in optimized_legend_items)}</ul></div>'
        )
        from branca.element import Element

        route_map.get_root().html.add_child(Element(legend))
    return RouteMapView(route_map, geometry_status)


def _add_road_alternative_layers(
    route_map: folium.Map,
    alternatives: tuple[ScoredRoadAlternative, ...],
    selected_id: str | None,
    show_alternatives: bool,
) -> None:
    legend_items = []
    selected_geometry = next(
        (
            alternative.route.geometry
            for alternative in alternatives
            if alternative.route.alternative_id == selected_id
        ),
        alternatives[0].route.geometry,
    )
    endpoint_group = folium.FeatureGroup(
        name="Road-leg start / end",
        show=show_alternatives,
    )
    folium.CircleMarker(
        selected_geometry[0],
        radius=7,
        color="#22D3EE",
        weight=3,
        fill=True,
        fill_color="#3B82F6",
        fill_opacity=1.0,
        tooltip="Selected road leg start",
    ).add_to(endpoint_group)
    folium.CircleMarker(
        selected_geometry[-1],
        radius=7,
        color="#22D3EE",
        weight=3,
        fill=True,
        fill_color="#A78BFA",
        fill_opacity=1.0,
        tooltip="Selected road leg end",
    ).add_to(endpoint_group)
    endpoint_group.add_to(route_map)

    recommended_id = min(alternatives, key=lambda item: item.objective_score).route.alternative_id
    for alternative in sorted(alternatives, key=lambda item: item.route.returned_order):
        route = alternative.route
        selected = route.alternative_id == selected_id
        recommended = route.alternative_id == recommended_id
        color = ROAD_ALTERNATIVE_COLORS[(route.returned_order - 1) % len(ROAD_ALTERNATIVE_COLORS)]
        title = f"Road alternative {route.returned_order} ({route.alternative_id})"
        group = folium.FeatureGroup(
            name=(
                f"{title} | {route.distance_km:.1f} km | "
                f"{alternative.estimated_time_min:.0f} min"
            ),
            show=show_alternatives,
        )
        folium.PolyLine(
            route.geometry,
            color=color,
            weight=8 if selected else 7 if recommended else 5,
            opacity=1.0 if selected else 0.95 if recommended else 0.78,
            dash_array=None if selected or recommended else "5, 7",
            tooltip=(
                f"{title}"
                f"{' | Recommended' if recommended else ''}"
                f"{' | Selected' if selected else ''}"
            ),
            popup=(
                f"{escape(title)}<br>"
                f"{route.distance_km:.1f} km | "
                f"{alternative.estimated_time_min:.0f} min modeled estimate"
            ),
        ).add_to(group)
        group.add_to(route_map)
        legend_items.append(
            "<li>"
            f'<span style="display:inline-block;width:18px;border-top:4px '
            f'solid {color};margin-right:6px"></span>'
            f"{escape(title)} — {route.distance_km:.1f} km, "
            f"{alternative.estimated_time_min:.0f} min modeled"
            f"{' (recommended)' if recommended else ''}"
            f"{' (selected)' if selected else ''}</li>"
        )

    legend = (
        '<div style="position:fixed;bottom:28px;left:28px;z-index:9999;'
        "background:rgba(16,29,48,.96);color:#F1F5F9;padding:10px 12px;"
        'border:1px solid #45627F;border-radius:8px;font-size:12px">'
        "<strong>OSRM road alternatives</strong><ul style=\"padding-left:8px;"
        f'margin:6px 0 0;list-style:none">{"".join(legend_items)}</ul></div>'
    )
    from branca.element import Element

    route_map.get_root().html.add_child(Element(legend))


def _format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"