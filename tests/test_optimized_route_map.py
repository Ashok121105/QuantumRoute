import unittest
from unittest.mock import call, patch

import folium

from quantum_route_optimisation import RoutePlan, evaluate_route
from route_dashboard.map_view import (
    OPTIMIZED_SEGMENT_COLORS,
    VEHICLE_COLORS,
    build_route_map,
)
from route_dashboard.optimization import OptimizationRun, RouteSummary
from route_dashboard.scenario import DeliveryStop, build_scenario


def _scenario(destination_count: int, vehicle_count: int = 1):
    stops = tuple(
        DeliveryStop(
            f"Destination {index}",
            37.78 + index * 0.005,
            -122.42 + index * 0.006,
            1.0,
            480,
            1200,
            5,
        )
        for index in range(1, destination_count + 1)
    )
    return build_scenario(
        stops,
        vehicle_count=vehicle_count,
        vehicle_capacity=float(destination_count),
        depot_name="Guntur",
        depot_latitude=37.7749,
        depot_longitude=-122.4194,
        shift_start_min=480,
        shift_end_min=1440,
        traffic_condition="Normal",
        fuel_type="Diesel",
        fuel_price_per_unit=1.10,
    )


def _run(scenario, route_specs):
    vehicle_by_id = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    routes = tuple(
        evaluate_route(
            vehicle_by_id[vehicle_id],
            RoutePlan(vehicle_id, delivery_ids),
            scenario.deliveries,
            scenario.travel,
            scenario.cost_weights,
        )
        for vehicle_id, delivery_ids in route_specs
    )
    summary = RouteSummary(
        routes=routes,
        total_cost=sum(route.total_cost for route in routes),
        total_distance_km=sum(route.distance_km for route in routes),
        total_travel_time_min=sum(route.travel_time_min for route in routes),
    )
    return OptimizationRun(
        classical=summary,
        quantum=None,
        quantum_result=None,
        quantum_error=None,
        objective_delta=None,
        relative_gap_percent=None,
    )


class OptimizedRouteMapTests(unittest.TestCase):
    def test_stop_order_segments_colors_origin_and_return_for_supported_sizes(self) -> None:
        for destination_count in (1, 2, 3, 4, 5):
            with self.subTest(destination_count=destination_count):
                scenario = _scenario(destination_count)
                delivery_ids = tuple(
                    f"delivery-{index}"
                    for index in range(destination_count, 0, -1)
                )
                run = _run(scenario, (("van-1", delivery_ids),))
                expected_coordinates = tuple(
                    scenario.coordinates[location_id]
                    for location_id in (
                        "depot",
                        *delivery_ids,
                        "depot",
                    )
                )
                with patch(
                    "route_dashboard.map_view.get_osrm_route_geometry",
                    side_effect=lambda points: (points, None),
                ) as get_geometry:
                    view = build_route_map(
                        scenario,
                        run,
                        show_quantum=False,
                        show_optimized_order=True,
                    )

                self.assertEqual(
                    get_geometry.call_args_list,
                    [
                        call((start, end))
                        for start, end in zip(
                            expected_coordinates,
                            expected_coordinates[1:],
                        )
                    ],
                )
                route_group = next(
                    child
                    for child in view.map._children.values()
                    if isinstance(child, folium.FeatureGroup)
                    and child.layer_name == "After | Classical"
                )
                segments = [
                    child
                    for child in route_group._children.values()
                    if isinstance(child, folium.PolyLine)
                ]
                self.assertEqual(len(segments), destination_count + 1)
                self.assertEqual(
                    [segment.options["color"] for segment in segments],
                    [
                        OPTIMIZED_SEGMENT_COLORS[index % len(OPTIMIZED_SEGMENT_COLORS)]
                        for index in range(destination_count + 1)
                    ],
                )
                if destination_count == 5:
                    self.assertEqual(
                        len({segment.options["color"] for segment in segments}),
                        6,
                    )
                self.assertEqual(
                    run.classical.total_distance_km,
                    sum(route.distance_km for route in run.classical.routes),
                )
                html = view.map.get_root().render()
                self.assertIn("Depot | Guntur", html)
                self.assertIn("Stop 1 | Destination", html)
                self.assertIn("Vehicle: van-1", html)
                self.assertIn("Optimized route legs", html)
                self.assertIn(
                    f"OSRM road geometry shown for all {destination_count + 1}",
                    view.geometry_status,
                )
                self.assertIn("fitBounds", view.map.get_root().render())

    def test_three_destinations_render_order_four_legs_geometry_and_bounds(self) -> None:
        scenario = _scenario(3)
        delivery_ids = ("delivery-3", "delivery-1", "delivery-2")
        run = _run(scenario, (("van-1", delivery_ids),))
        expected_legs = tuple(
            (
                scenario.coordinates[start],
                scenario.coordinates[end],
            )
            for start, end in zip(
                ("depot", *delivery_ids, "depot"),
                ("depot", *delivery_ids, "depot")[1:],
            )
        )
        with patch(
            "route_dashboard.map_view.get_osrm_route_geometry",
            side_effect=lambda points: (points, None),
        ) as get_geometry:
            view = build_route_map(
                scenario,
                run,
                show_quantum=False,
                show_optimized_order=True,
            )

        self.assertEqual(
            get_geometry.call_args_list,
            [call(leg) for leg in expected_legs],
        )
        route_group = next(
            child
            for child in view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name == "After | Classical"
        )
        segments = [
            child
            for child in route_group._children.values()
            if isinstance(child, folium.PolyLine)
        ]
        self.assertEqual(len(segments), 4)
        self.assertEqual(
            len({segment.options["color"] for segment in segments}),
            4,
        )
        rendered = view.map.get_root().render()
        for stop_number, delivery_id in enumerate(delivery_ids, start=1):
            self.assertIn(
                f"Stop {stop_number} | "
                f"{scenario.location_names[delivery_id]}",
                rendered,
            )
        self.assertIn("fitBounds", rendered)
        self.assertIn("Optimized route legs", rendered)
        self.assertIn("OSRM road geometry shown for all 4", view.geometry_status)

    def test_stop_markers_and_legs_follow_optimized_order_not_input_order(self) -> None:
        scenario = _scenario(4)
        ordered_ids = ("delivery-2", "delivery-4", "delivery-1", "delivery-3")
        run = _run(scenario, (("van-1", ordered_ids),))
        with patch(
            "route_dashboard.map_view.get_osrm_route_geometry",
            side_effect=lambda points: (points, None),
        ):
            view = build_route_map(
                scenario,
                run,
                show_quantum=False,
                show_optimized_order=True,
            )

        html = view.map.get_root().render()
        ordered_stop_text = (
            "Stop 1 | Destination 2",
            "Stop 2 | Destination 4",
            "Stop 3 | Destination 1",
            "Stop 4 | Destination 3",
        )
        positions = [html.index(text) for text in ordered_stop_text]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Segment 5", html)

    def test_multiple_vehicles_restart_stop_numbering_and_render_separately(self) -> None:
        scenario = _scenario(4, vehicle_count=2)
        run = _run(
            scenario,
            (
                ("van-1", ("delivery-1", "delivery-3")),
                ("van-2", ("delivery-2", "delivery-4")),
            ),
        )
        with patch(
            "route_dashboard.map_view.get_osrm_route_geometry",
            side_effect=lambda points: (points, None),
        ) as get_geometry:
            view = build_route_map(
                scenario,
                run,
                show_quantum=False,
                show_optimized_order=True,
            )

        self.assertEqual(get_geometry.call_count, 6)
        route_group = next(
            child
            for child in view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name == "After | Classical"
        )
        stop_markers = [
            child
            for child in route_group._children.values()
            if isinstance(child, folium.Marker)
        ]
        vehicle_marker_colors = {
            marker.icon.options["html"].split("border:2px solid ", 1)[1].split(";", 1)[0]
            for marker in stop_markers
        }
        self.assertEqual(
            vehicle_marker_colors,
            {VEHICLE_COLORS[0], VEHICLE_COLORS[1]},
        )
        html = view.map.get_root().render()
        self.assertIn("Stop 1 | Destination 1 | van-1", html)
        self.assertIn("Stop 1 | Destination 2 | van-2", html)
        self.assertIn("After | Classical · van-1", html)
        self.assertIn("After | Classical · van-2", html)

    def test_osrm_geometry_failure_omits_segments_and_keeps_matrix_metrics(self) -> None:
        scenario = _scenario(2)
        run = _run(
            scenario,
            (("van-1", ("delivery-2", "delivery-1")),),
        )
        totals_before = (
            run.classical.total_distance_km,
            run.classical.total_travel_time_min,
            run.classical.total_cost,
        )
        with patch(
            "route_dashboard.map_view.get_osrm_route_geometry",
            return_value=(None, "OSRM offline"),
        ) as get_geometry:
            view = build_route_map(
                scenario,
                run,
                show_quantum=False,
                show_optimized_order=True,
            )

        route_group = next(
            child
            for child in view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name == "After | Classical"
        )
        self.assertFalse(
            any(
                isinstance(child, folium.PolyLine)
                for child in route_group._children.values()
            )
        )
        self.assertEqual(get_geometry.call_count, 3)
        self.assertIn("OSRM road geometry unavailable for 3 of 3", view.geometry_status)
        self.assertIn("OSRM offline", view.geometry_status)
        self.assertEqual(
            totals_before,
            (
                run.classical.total_distance_km,
                run.classical.total_travel_time_min,
                run.classical.total_cost,
            ),
        )
        self.assertIn("road geometry unavailable", view.map.get_root().render())


if __name__ == "__main__":
    unittest.main()
