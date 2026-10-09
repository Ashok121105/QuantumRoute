import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import folium
from streamlit.testing.v1 import AppTest

from quantum_route_optimisation import RouteCostWeights
from route_dashboard import ui
from route_dashboard.map_view import (
    ROAD_ALTERNATIVE_COLORS,
    build_route_map,
    rank_road_alternatives,
)
from route_dashboard.optimization import OptimizationRun, RouteSummary
from route_dashboard.objectives import ObjectiveName
from route_dashboard.routing import (
    OSRMRouteAlternative,
    _fetch_osrm_route_alternatives_cached,
    get_osrm_route_alternatives,
)
from route_dashboard.scenario import build_demo_scenario


def _route_payload(
    coordinates: list[list[list[float]]],
    distances: list[float] | None = None,
    durations: list[float] | None = None,
) -> bytes:
    distances = distances or [10000.0] * len(coordinates)
    durations = durations or [600.0] * len(coordinates)
    return json.dumps(
        {
            "code": "Ok",
            "routes": [
                {
                    "geometry": {"coordinates": points},
                    "distance": distance,
                    "duration": duration,
                }
                for points, distance, duration in zip(coordinates, distances, durations)
            ],
        }
    ).encode("utf-8")


def _alternative(
    number: int,
    points: tuple[tuple[float, float], ...],
    distance_km: float,
    duration_min: float,
) -> OSRMRouteAlternative:
    return OSRMRouteAlternative(
        f"road-{number}",
        number,
        points,
        distance_km,
        duration_min,
    )


class RoadAlternativeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        _fetch_osrm_route_alternatives_cached.cache_clear()

    def tearDown(self) -> None:
        _fetch_osrm_route_alternatives_cached.cache_clear()

    def test_parses_up_to_three_distinct_routes_with_stable_ids_and_order(self) -> None:
        body = _route_payload(
            [
                [[-122.42, 37.77], [-122.41, 37.78]],
                [[-122.42, 37.77], [-122.40, 37.78]],
                [[-122.42, 37.77], [-122.40, 37.79]],
                [[-122.42, 37.77], [-122.39, 37.79]],
            ],
            [9000, 10000, 11000, 12000],
            [480, 600, 720, 840],
        )
        with patch("route_dashboard.routing.urlopen", return_value=BytesIO(body)) as fetch:
            alternatives, error = get_osrm_route_alternatives(
                (37.77, -122.42),
                (37.78, -122.41),
                timeout_seconds=2.5,
            )

        self.assertIsNone(error)
        self.assertEqual(len(alternatives), 3)
        self.assertEqual([item.returned_order for item in alternatives], [1, 2, 3])
        self.assertEqual([item.distance_km for item in alternatives], [9.0, 10.0, 11.0])
        self.assertEqual([item.duration_min for item in alternatives], [8.0, 10.0, 12.0])
        self.assertEqual(len({item.alternative_id for item in alternatives}), 3)
        self.assertEqual(alternatives[0].geometry, ((37.77, -122.42), (37.78, -122.41)))
        request = fetch.call_args.args[0]
        self.assertIn("alternatives=2", request.full_url)
        self.assertEqual(fetch.call_args.kwargs["timeout"], 2.5)

        _fetch_osrm_route_alternatives_cached.cache_clear()
        with patch("route_dashboard.routing.urlopen", return_value=BytesIO(body)):
            repeated, _ = get_osrm_route_alternatives((37.77, -122.42), (37.78, -122.41))
        self.assertEqual(
            [item.alternative_id for item in alternatives],
            [item.alternative_id for item in repeated],
        )

    def test_identical_returned_geometry_is_not_presented_as_a_second_route(self) -> None:
        duplicate = [[-122.42, 37.77], [-122.41, 37.78]]
        body = _route_payload([duplicate, duplicate])
        with patch("route_dashboard.routing.urlopen", return_value=BytesIO(body)):
            alternatives, error = get_osrm_route_alternatives(
                (37.77, -122.42),
                (37.78, -122.41),
            )

        self.assertIsNone(error)
        self.assertEqual(len(alternatives), 1)

    def test_single_osrm_route_is_returned_without_inventing_others(self) -> None:
        body = _route_payload([[[-122.42, 37.77], [-122.41, 37.78]]])
        with patch("route_dashboard.routing.urlopen", return_value=BytesIO(body)):
            alternatives, error = get_osrm_route_alternatives(
                (37.77, -122.42),
                (37.78, -122.41),
            )

        self.assertIsNone(error)
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(alternatives[0].returned_order, 1)

    def test_zero_routes_malformed_response_and_timeout_are_reported(self) -> None:
        cases = (
            (_route_payload([]), None, ()),
            (b'{"code":"Ok","routes":[{"geometry":{},"distance":"bad"}]}', None, None),
            (b"[]", None, None),
            (None, TimeoutError("request timed out"), None),
        )
        for body, failure, expected_routes in cases:
            with self.subTest(body=body, failure=failure):
                _fetch_osrm_route_alternatives_cached.cache_clear()
                with patch(
                    "route_dashboard.routing.urlopen",
                    return_value=BytesIO(body) if body is not None else None,
                    side_effect=failure,
                ):
                    alternatives, error = get_osrm_route_alternatives(
                        (37.77, -122.42),
                        (37.78, -122.41),
                    )
                if expected_routes is not None:
                    self.assertEqual(alternatives, expected_routes)
                    self.assertIsNone(error)
                else:
                    self.assertEqual(alternatives, ())
                    self.assertTrue(error)

    def test_route_cache_reuses_entries_until_bounded_ttl_expires(self) -> None:
        response = BytesIO(
            _route_payload([[[-122.42, 37.77], [-122.41, 37.78]]])
        )
        with (
            patch("route_dashboard.routing.monotonic", side_effect=(10.0, 299.0, 300.0)),
            patch("route_dashboard.routing.urlopen", return_value=response) as fetch,
        ):
            get_osrm_route_alternatives((37.77, -122.42), (37.78, -122.41))
            get_osrm_route_alternatives((37.77, -122.42), (37.78, -122.41))
            get_osrm_route_alternatives((37.77, -122.42), (37.78, -122.41))

        self.assertEqual(fetch.call_count, 2)


class RoadAlternativeComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = build_demo_scenario()
        self.scenario = replace(
            self.scenario,
            cost_weights=RouteCostWeights(
                distance_cost_per_km=1.0,
                time_cost_per_min=0.0,
                fixed_vehicle_cost=0.0,
            ),
        )
        self.routes = (
            _alternative(
                1,
                ((37.77, -122.42), (37.78, -122.41)),
                10.0,
                8.0,
            ),
            _alternative(
                2,
                ((37.77, -122.42), (37.79, -122.40)),
                5.0,
                18.0,
            ),
        )

    def test_supported_objectives_rank_from_returned_metrics(self) -> None:
        expected_first = {
            ObjectiveName.COST: "road-2",
            ObjectiveName.TIME: "road-1",
            ObjectiveName.GREEN: "road-2",
            ObjectiveName.BALANCED: "road-2",
        }
        for objective, expected in expected_first.items():
            with self.subTest(objective=objective):
                ranked = rank_road_alternatives(
                    self.scenario,
                    objective.value,
                    self.routes,
                )
                self.assertEqual(ranked[0].route.alternative_id, expected)
                self.assertLessEqual(
                    ranked[0].objective_score,
                    ranked[1].objective_score,
                )

    def test_fuel_cost_operating_cost_and_traffic_time_use_scenario_formulas(self) -> None:
        ranked = rank_road_alternatives(
            self.scenario,
            ObjectiveName.COST.value,
            self.routes,
        )
        quick = next(item for item in ranked if item.route.alternative_id == "road-1")
        fuel = 10.0 * 0.09

        self.assertAlmostEqual(quick.estimated_time_min, 8.0 * 1.2)
        self.assertAlmostEqual(quick.fuel_used, fuel)
        self.assertAlmostEqual(quick.fuel_cost, fuel * self.scenario.fuel_price_per_unit)
        self.assertAlmostEqual(quick.operating_cost, 10.0)
        self.assertAlmostEqual(quick.tailpipe_co2_kg, fuel * 2.68)

    def test_map_uses_separate_colors_layers_and_prominent_selected_route(self) -> None:
        ranked = rank_road_alternatives(
            self.scenario,
            ObjectiveName.COST.value,
            self.routes,
        )
        recommended = ranked[0].route.alternative_id
        selected = ranked[-1].route.alternative_id
        run = OptimizationRun(
            classical=RouteSummary((), 0.0, 0.0, 0.0),
            quantum=None,
            quantum_result=None,
            quantum_error=None,
            objective_delta=None,
            relative_gap_percent=None,
        )
        view = build_route_map(
            self.scenario,
            run,
            show_quantum=False,
            road_alternatives=ranked,
            selected_road_alternative_id=selected,
        )
        groups = [
            child
            for child in view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name.startswith("Road alternative")
        ]
        self.assertEqual(len(groups), 2)
        colors = []
        selected_weight = None
        recommended_weight = None
        for group in groups:
            line = next(iter(group._children.values()))
            colors.append(line.options["color"])
            geometry = tuple(tuple(point) for point in line.locations)
            if geometry == next(
                item.route.geometry for item in ranked if item.route.alternative_id == selected
            ):
                selected_weight = line.options["weight"]
            if geometry == next(
                item.route.geometry for item in ranked if item.route.alternative_id == recommended
            ):
                recommended_weight = line.options["weight"]
        self.assertEqual(len(set(colors)), 2)
        self.assertTrue(set(colors).issubset(set(ROAD_ALTERNATIVE_COLORS)))
        self.assertEqual(selected_weight, 8)
        self.assertEqual(recommended_weight, 7)
        endpoint_group = next(
            child
            for child in view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name == "Road-leg start / end"
        )
        self.assertEqual(len(endpoint_group._children), 2)
        hidden_view = build_route_map(
            self.scenario,
            run,
            show_quantum=False,
            road_alternatives=ranked,
            selected_road_alternative_id=selected,
            show_road_alternatives=False,
        )
        hidden_groups = [
            child
            for child in hidden_view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name.startswith("Road alternative")
        ]
        self.assertTrue(all(not group.show for group in hidden_groups))
        hidden_endpoints = next(
            child
            for child in hidden_view.map._children.values()
            if isinstance(child, folium.FeatureGroup)
            and child.layer_name == "Road-leg start / end"
        )
        self.assertFalse(hidden_endpoints.show)
        rendered = view.map.get_root().render()
        self.assertIn("OSRM road alternatives", rendered)
        self.assertIn("coordinate estimates", view.geometry_status)
        self.assertIn("not road-route geometries", view.geometry_status)


class RoadAlternativeUITests(unittest.TestCase):
    def test_requests_are_explicit_and_reruns_or_leg_changes_do_not_fetch(self) -> None:
        scenario = build_demo_scenario()
        route = OSRMRouteAlternative(
            "road-test",
            1,
            ((37.77, -122.42), (37.78, -122.41)),
            4.0,
            8.0,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_file = Path(temporary_directory) / "road_map_app.py"
            app_file.write_text(
                "from route_dashboard.ui import _render_route_map\n"
                "from route_dashboard.scenario import build_demo_scenario\n"
                "from route_dashboard.optimization import OptimizationRun, RouteSummary\n"
                "scenario = build_demo_scenario()\n"
                "run = OptimizationRun(RouteSummary((), 0.0, 0.0, 0.0), None, None, None, None, None)\n"
                "_render_route_map(scenario, run, None, key_prefix='road_test')\n",
                encoding="utf-8",
            )
            with (
                patch.object(ui, "get_osrm_route_alternatives", return_value=((route,), None)) as fetch,
                patch.object(ui, "st_folium"),
            ):
                app = AppTest.from_file(str(app_file), default_timeout=300).run()
                fetch.assert_not_called()

                app.run()
                fetch.assert_not_called()
                next(button for button in app.button if button.label == "Request OSRM alternatives").click().run()
                fetch.assert_called_once_with(
                    scenario.coordinates["depot"],
                    scenario.coordinates["delivery-1"],
                )

                app.run()
                fetch.assert_called_once()
                app.selectbox(key="road_test_road_origin").set_value("delivery-1").run()
                fetch.assert_called_once()
                app.selectbox(key="road_test_road_destination").set_value("delivery-2").run()
                fetch.assert_called_once()
                next(button for button in app.button if button.label == "Request OSRM alternatives").click().run()
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(
                    fetch.call_args.args,
                    (
                        scenario.coordinates["delivery-1"],
                        scenario.coordinates["delivery-2"],
                    ),
                )
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])
                self.assertTrue(
                    any(
                        "not live traffic" in item.value
                        for item in app.caption
                    )
                )

                fetch.return_value = ((), None)
                next(button for button in app.button if button.label == "Request OSRM alternatives").click().run()
                self.assertTrue(
                    any("OSRM returned no road routes" in item.value for item in app.info)
                )
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])

                fetch.return_value = ((), "request timed out")
                next(button for button in app.button if button.label == "Request OSRM alternatives").click().run()
                self.assertTrue(
                    any("request timed out" in item.value for item in app.warning)
                )
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])


if __name__ == "__main__":
    unittest.main()
