import json
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

import folium
import pandas as pd
from pathlib import Path
from streamlit.testing.v1 import AppTest

from route_dashboard import ui
from route_dashboard.custom_routes import (
    GEOCODER_USER_AGENT,
    PlaceResult,
    _search_places_cached,
    build_custom_route_map,
    build_custom_route_tilted_map,
    custom_route_objectives,
    route_recommendation_explanation,
    score_custom_route_alternatives,
    search_places,
)
from route_dashboard.fleet_upload import validate_fleet_upload
from route_dashboard.routing import OSRMRouteAlternative


def _route(identifier, order, points, distance, duration):
    return OSRMRouteAlternative(identifier, order, points, distance, duration)


class PlaceSearchTests(unittest.TestCase):
    def setUp(self):
        _search_places_cached.cache_clear()
        import route_dashboard.custom_routes as custom_routes

        custom_routes._last_geocoder_request = 0.0

    def tearDown(self):
        _search_places_cached.cache_clear()

    def test_empty_query_does_not_make_a_network_request(self):
        with patch("route_dashboard.custom_routes.urlopen") as request:
            places, error = search_places("  ")
        self.assertEqual(places, ())
        self.assertIn("Enter a place", error)
        request.assert_not_called()

    def test_success_is_validated_cached_and_uses_identifiable_user_agent(self):
        payload = json.dumps(
            [{"display_name": "Guntur, Andhra Pradesh", "lat": "16.3067", "lon": "80.4365"}]
        ).encode()
        with (
            patch("route_dashboard.custom_routes.time", return_value=3600.0),
            patch("route_dashboard.custom_routes.monotonic", return_value=20.0),
            patch("route_dashboard.custom_routes.urlopen", return_value=BytesIO(payload)) as request,
        ):
            first, error = search_places("Guntur")
            second, repeated_error = search_places("Guntur")
        self.assertIsNone(error)
        self.assertIsNone(repeated_error)
        self.assertEqual(first, second)
        self.assertEqual(first[0].display_name, "Guntur, Andhra Pradesh")
        self.assertAlmostEqual(first[0].latitude, 16.3067)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0].headers["User-agent"], GEOCODER_USER_AGENT)
        self.assertIn("nominatim.openstreetmap.org/search", request.call_args.args[0].full_url)

    def test_rate_limit_and_malformed_data_are_reported_without_raw_details(self):
        from urllib.error import HTTPError

        rate_limited = HTTPError("url", 429, "rate limited", {}, None)
        with (
            patch("route_dashboard.custom_routes.time", return_value=7200.0),
            patch("route_dashboard.custom_routes.monotonic", return_value=40.0),
            patch("route_dashboard.custom_routes.urlopen", side_effect=rate_limited),
        ):
            places, error = search_places("Vijayawada")
        self.assertEqual(places, ())
        self.assertIn("rate-limited", error)
        self.assertNotIn("Vijayawada", error)

        _search_places_cached.cache_clear()
        with (
            patch("route_dashboard.custom_routes.time", return_value=10800.0),
            patch("route_dashboard.custom_routes.monotonic", return_value=60.0),
            patch(
                "route_dashboard.custom_routes.urlopen",
                return_value=BytesIO(b'[{"display_name":"Place","lat":"999","lon":"1"}]'),
            ),
        ):
            places, error = search_places("Bad coordinates")
        self.assertEqual(places, ())
        self.assertIn("invalid response", error)


class CustomRouteScoringTests(unittest.TestCase):
    def setUp(self):
        self.alternatives = (
            _route("short-slow", 1, ((16.0, 80.0), (16.2, 80.2)), 10.0, 80.0),
            _route("long-fast", 2, ((16.0, 80.0), (16.3, 80.3)), 12.0, 20.0),
        )

    def test_distance_time_cost_and_green_objectives_use_returned_route_metrics(self):
        short = score_custom_route_alternatives(
            self.alternatives, "Shortest Distance"
        )
        fast = score_custom_route_alternatives(
            self.alternatives, "Fastest Estimated Time"
        )
        self.assertEqual(short[0].route.alternative_id, "short-slow")
        self.assertEqual(fast[0].route.alternative_id, "long-fast")

        kwargs = {
            "fuel_type": "Diesel",
            "fuel_consumption_per_km": 1.0,
            "fuel_price_per_unit": 1.0,
            "driver_cost_per_hour": 60.0,
        }
        cost = score_custom_route_alternatives(
            self.alternatives, "Lowest Estimated Cost", **kwargs
        )
        self.assertEqual(cost[0].route.alternative_id, "long-fast")
        self.assertAlmostEqual(cost[1].operating_cost, 90.0)
        self.assertAlmostEqual(cost[0].tailpipe_co2_kg, 12.0 * 2.68)

        green = score_custom_route_alternatives(
            self.alternatives,
            "Green / Lower Estimated Emissions",
            fuel_type="Diesel",
            fuel_consumption_per_km=1.0,
        )
        self.assertEqual(green[0].route.alternative_id, "short-slow")

    def test_unavailable_metrics_are_not_claimed_and_balanced_ties_are_stable(self):
        objectives = custom_route_objectives(
            has_estimated_cost=False,
            has_emissions=False,
        )
        self.assertEqual(
            objectives,
            ("Shortest Distance", "Fastest Estimated Time", "Balanced"),
        )
        equal_routes = (
            _route("one", 1, ((16.0, 80.0), (16.1, 80.1)), 5.0, 10.0),
            _route("two", 2, ((16.0, 80.0), (16.2, 80.2)), 5.0, 10.0),
        )
        balanced = score_custom_route_alternatives(equal_routes, "Balanced")
        self.assertEqual([item.route.alternative_id for item in balanced], ["one", "two"])
        self.assertTrue(all(item.operating_cost is None for item in balanced))
        self.assertTrue(all(item.tailpipe_co2_kg is None for item in balanced))

    def test_uploaded_maximum_speed_only_raises_vehicle_adjusted_time(self):
        scored = score_custom_route_alternatives(
            self.alternatives,
            "Fastest Estimated Time",
            driver_cost_per_hour=60.0,
            max_speed_kmph=5.0,
        )
        slow_long_route = next(
            item for item in scored if item.route.alternative_id == "long-fast"
        )
        self.assertEqual(slow_long_route.route.duration_min, 20.0)
        self.assertEqual(slow_long_route.estimated_time_min, 144.0)
        self.assertTrue(slow_long_route.vehicle_speed_adjusted)
        self.assertEqual(slow_long_route.driver_cost, 144.0)
        self.assertEqual(scored[0].route.alternative_id, "short-slow")
        self.assertIn(
            "maximum-speed constraint",
            route_recommendation_explanation(
                "Fastest Estimated Time",
                slow_long_route,
                slow_long_route,
                len(scored),
            ),
        )

    def test_values_from_validated_upload_drive_vehicle_cost_and_emissions(self):
        upload = validate_fleet_upload(
            "fleet.csv",
            (
                "company_id,vehicle_id,capacity,fuel_type,fuel_consumption_per_km,"
                "fuel_price_per_unit,driver_cost_per_hour\n"
                "Fleet A,van-1,1500,Diesel,0.12,1.8,20\n"
            ).encode(),
        )
        self.assertTrue(upload.valid, upload.errors)
        vehicle = upload.rows[0]
        route = _route("uploaded-profile-route", 1, ((16.0, 80.0), (16.1, 80.1)), 10.0, 30.0)
        scored = score_custom_route_alternatives(
            (route,),
            "Lowest Estimated Cost",
            fuel_type=vehicle["fuel_type"],
            fuel_consumption_per_km=vehicle["fuel_consumption_per_km"],
            fuel_price_per_unit=vehicle["fuel_price_per_unit"],
            driver_cost_per_hour=vehicle["driver_cost_per_hour"],
        )
        self.assertAlmostEqual(scored[0].fuel_used, 1.2)
        self.assertAlmostEqual(scored[0].fuel_cost, 2.16)
        self.assertAlmostEqual(scored[0].driver_cost, 10.0)
        self.assertAlmostEqual(scored[0].operating_cost, 12.16)
        self.assertAlmostEqual(scored[0].tailpipe_co2_kg, 3.216)

    def test_diesel_gasoline_petrol_and_electric_use_their_own_units_and_factors(self):
        route = _route(
            "fuel-unit-route",
            1,
            ((16.0, 80.0), (16.1, 80.1)),
            10.0,
            20.0,
        )
        expected = {
            "Diesel": (1.0, "L", 2.0, 2.68),
            "Gasoline": (1.0, "L", 2.0, 2.31),
            "Petrol": (1.0, "L", 2.0, 2.31),
            "Electric": (2.0, "kWh", 1.0, 0.0),
        }
        for fuel_type, (quantity, unit, fuel_cost, tailpipe_co2) in expected.items():
            with self.subTest(fuel_type=fuel_type):
                consumption = 0.2 if fuel_type == "Electric" else 0.1
                price = 0.5 if fuel_type == "Electric" else 2.0
                scored = score_custom_route_alternatives(
                    (route,),
                    "Lowest Estimated Cost",
                    fuel_type=fuel_type,
                    fuel_consumption_per_km=consumption,
                    fuel_price_per_unit=price,
                    driver_cost_per_hour=0.0,
                )[0]
                self.assertAlmostEqual(scored.fuel_used, quantity)
                self.assertEqual(scored.fuel_unit, unit)
                self.assertAlmostEqual(scored.fuel_cost, fuel_cost)
                self.assertAlmostEqual(scored.tailpipe_co2_kg, tailpipe_co2)
                self.assertAlmostEqual(scored.operating_cost, fuel_cost)

    def test_missing_inputs_and_unsupported_fuels_do_not_get_fabricated_metrics(self):
        route = self.alternatives[0]
        no_consumption = score_custom_route_alternatives(
            (route,),
            "Fastest Estimated Time",
            fuel_type="Diesel",
            fuel_price_per_unit=2.0,
            driver_cost_per_hour=10.0,
        )[0]
        self.assertIsNone(no_consumption.fuel_used)
        self.assertIsNone(no_consumption.fuel_unit)
        self.assertIsNone(no_consumption.fuel_cost)
        self.assertIsNone(no_consumption.tailpipe_co2_kg)
        self.assertIsNone(no_consumption.operating_cost)
        self.assertEqual(no_consumption.driver_cost, 80.0 / 60.0 * 10.0)

        no_electricity_price = score_custom_route_alternatives(
            (route,),
            "Fastest Estimated Time",
            fuel_type="Electric",
            fuel_consumption_per_km=0.2,
        )[0]
        self.assertEqual(no_electricity_price.fuel_used, 2.0)
        self.assertEqual(no_electricity_price.fuel_unit, "kWh")
        self.assertIsNone(no_electricity_price.fuel_cost)
        self.assertEqual(no_electricity_price.tailpipe_co2_kg, 0.0)
        self.assertIsNone(no_electricity_price.operating_cost)

        for unsupported in ("CNG", "LPG"):
            with self.subTest(fuel_type=unsupported):
                scored = score_custom_route_alternatives(
                    (route,),
                    "Fastest Estimated Time",
                    fuel_type=unsupported,
                    fuel_consumption_per_km=0.1,
                    fuel_price_per_unit=2.0,
                    driver_cost_per_hour=10.0,
                )[0]
                self.assertIsNone(scored.fuel_used)
                self.assertIsNone(scored.fuel_unit)
                self.assertIsNone(scored.fuel_cost)
                self.assertIsNone(scored.tailpipe_co2_kg)
                self.assertIsNone(scored.operating_cost)

    def test_map_displays_provider_geometries_and_selected_alternative(self):
        scored = score_custom_route_alternatives(
            self.alternatives,
            "Fastest Estimated Time",
        )
        route_map = build_custom_route_map(
            (16.0, 80.0),
            (16.2, 80.2),
            scored,
            "long-fast",
        )
        polylines = [
            layer
            for layer in route_map._children.values()
            if isinstance(layer, folium.PolyLine)
        ]
        self.assertEqual(len(polylines), 2)
        self.assertEqual(max(line.options["weight"] for line in polylines), 8)
        self.assertEqual(
            sorted(line.options["weight"] for line in polylines),
            [4, 8],
        )
        map_html = route_map.get_root().render()
        self.assertIn("recommended", map_html)
        self.assertIn("selected", map_html)
        nonrecommended_map = build_custom_route_map(
            (16.0, 80.0),
            (16.2, 80.2),
            scored,
            "short-slow",
        )
        nonrecommended_weights = [
            layer.options["weight"]
            for layer in nonrecommended_map._children.values()
            if isinstance(layer, folium.PolyLine)
        ]
        self.assertEqual(sorted(nonrecommended_weights), [6, 8])
        origin_map = build_custom_route_map((16.0, 80.0), None, (), None)
        markers = [
            layer
            for layer in origin_map._children.values()
            if isinstance(layer, folium.Marker)
        ]
        self.assertEqual(len(markers), 1)
        explanation = route_recommendation_explanation(
            "Fastest Estimated Time",
            scored[0],
            scored[0],
            len(scored),
        )
        self.assertIn("OSRM returned 2 distinct route(s)", explanation)

    def test_tilted_map_uses_real_route_geometry_and_highlights_recommendation(self):
        scored = score_custom_route_alternatives(
            self.alternatives,
            "Fastest Estimated Time",
        )
        tilted_map = build_custom_route_tilted_map(
            (16.0, 80.0),
            (16.2, 80.2),
            scored,
            "short-slow",
            "long-fast",
            origin_label="Actual origin",
            destination_label="Actual destination",
        )
        spec = json.loads(tilted_map.to_json())
        self.assertTrue(spec["views"][0]["controller"])
        self.assertEqual(spec["initialViewState"]["pitch"], 55)
        self.assertEqual(spec["mapStyle"], "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json")

        layers = {layer["id"]: layer for layer in spec["layers"]}
        endpoint_rows = layers["selected-route-endpoints"]["data"]
        self.assertEqual(
            [row["position"] for row in endpoint_rows],
            [[80.0, 16.0], [80.2, 16.2]],
        )
        self.assertEqual(
            [row["label"] for row in endpoint_rows],
            ["Origin: Actual origin", "Destination: Actual destination"],
        )

        route_rows = layers["returned-osrm-routes"]["data"]
        self.assertEqual(
            {
                row["label"].split(" · ")[0]: row["path"]
                for row in route_rows
            },
            {
                "Route 2": [[80.0, 16.0], [80.3, 16.3]],
                "Route 1": [[80.0, 16.0], [80.2, 16.2]],
            },
        )
        self.assertEqual(
            [row["color"] for row in route_rows],
            [[34, 211, 238, 255], [59, 130, 246, 255]],
        )
        self.assertEqual([row["width"] for row in route_rows], [5, 7])
        self.assertIn("recommended", route_rows[0]["label"])
        self.assertIn("selected", route_rows[1]["label"])
        self.assertEqual(
            layers["recommended-route-highlight"]["data"][0]["path"],
            route_rows[0]["path"],
        )

    def test_tilted_location_map_renders_one_selected_origin_without_routes(self):
        tilted_map = build_custom_route_tilted_map(
            (16.0, 80.0),
            None,
            (),
            None,
            origin_label="Searched origin",
        )
        spec = json.loads(tilted_map.to_json())
        layers = {layer["id"]: layer for layer in spec["layers"]}
        self.assertEqual(
            layers["selected-route-endpoints"]["data"],
            [
                {
                    "position": [80.0, 16.0],
                    "label": "Origin: Searched origin",
                    "color": [34, 211, 238, 255],
                }
            ],
        )
        self.assertEqual(layers["returned-osrm-routes"]["data"], [])


class FleetUploadValidationTests(unittest.TestCase):
    def test_csv_requires_vehicle_id_and_capacity_but_accepts_optional_fields(self):
        content = (
            "company,vehicle,vehicle_capacity,speed,fuel_type,fuel_efficiency,"
            "fuel_price,driver_hourly_cost,shift_start,shift_end,available_fuel\n"
            "Fleet A,van-1,1500,80,Diesel,0.12,1.8,20,480,1020,50\n"
        ).encode()
        result = validate_fleet_upload("fleet.csv", content)
        self.assertTrue(result.valid, result.errors)
        row = result.rows[0]
        self.assertEqual(row["company_id"], "Fleet A")
        self.assertEqual(row["vehicle_id"], "van-1")
        self.assertEqual(row["capacity"], 1500.0)
        self.assertEqual(row["max_speed_kmph"], 80.0)
        self.assertIsNone(row["available"])
        self.assertEqual(row["shift_start_min"], 480)
        self.assertEqual(row["available_fuel_quantity"], 50.0)

    def test_template_matches_supported_schema_and_preview_keeps_invalid_rows(self):
        from route_dashboard.fleet_upload import FLEET_TEMPLATE_COLUMNS

        template_path = Path(__file__).parents[1] / "docs" / "fleet-data-template.csv"
        self.assertEqual(
            tuple(template_path.read_text(encoding="utf-8").strip().split(",")),
            FLEET_TEMPLATE_COLUMNS,
        )
        result = validate_fleet_upload(
            "fleet.csv",
            (
                "company_id,vehicle_id,capacity,available\n"
                "Fleet A,van-1,not-a-number,maybe\n"
            ).encode(),
        )
        self.assertFalse(result.valid)
        self.assertEqual(len(result.preview_rows), 1)
        self.assertEqual(result.preview_rows[0]["capacity"], "not-a-number")
        self.assertTrue(any("capacity must be numeric" in error for error in result.errors))
        self.assertTrue(any("available must be" in error for error in result.errors))

    def test_invalid_rows_duplicates_unknown_columns_and_unsupported_files_fail_closed(self):
        missing_column = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id\nvan-1\n",
        )
        self.assertFalse(missing_column.valid)
        self.assertTrue(
            any("Missing required column(s): capacity" in error for error in missing_column.errors)
        )
        missing_values = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id,capacity,vehicle_type\n, ,Van\n",
        )
        self.assertFalse(missing_values.valid)
        self.assertTrue(any("Row 2: vehicle_id is required" in error for error in missing_values.errors))
        self.assertTrue(any("Row 2: capacity is required" in error for error in missing_values.errors))

        malformed = validate_fleet_upload("fleet.csv", b"\x00\xff")
        self.assertFalse(malformed.valid)
        self.assertTrue(any("could not be parsed" in error for error in malformed.errors))

        duplicate = (
            "vehicle_id,capacity\nvan-1,1\nvan-1,2\nvan-2,-1\n"
        ).encode()
        result = validate_fleet_upload("fleet.csv", duplicate)
        self.assertFalse(result.valid)
        self.assertEqual(result.rows, ())
        self.assertTrue(any("Row 3" in error and "unique" in error for error in result.errors))
        self.assertTrue(any("Row 4" in error and "capacity" in error for error in result.errors))

        unknown = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id,capacity,secret_column\nvan-1,1,private\n",
        )
        self.assertFalse(unknown.valid)
        self.assertTrue(any("Unsupported column" in error for error in unknown.errors))
        invalid_speed = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id,capacity,max_speed_kmph\nvan-1,1,0\n",
        )
        self.assertFalse(invalid_speed.valid)
        self.assertTrue(any("max_speed_kmph" in error for error in invalid_speed.errors))
        invalid_availability = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id,capacity,available\nvan-1,1,maybe\n",
        )
        self.assertFalse(invalid_availability.valid)
        self.assertTrue(any("available must be" in error for error in invalid_availability.errors))
        invalid_currency = validate_fleet_upload(
            "fleet.csv",
            b"vehicle_id,capacity,currency\nvan-1,1,dollars\n",
        )
        self.assertFalse(invalid_currency.valid)
        self.assertTrue(any("currency must be a 3-letter code" in error for error in invalid_currency.errors))
        unsupported = validate_fleet_upload("fleet.xls", b"anything")
        self.assertFalse(unsupported.valid)
        self.assertIn(".csv or .xlsx", unsupported.errors[0])

    def test_fuel_types_accept_petrol_alias_and_reject_cng_and_lpg_individually(self):
        for fuel_type in ("Diesel", "Gasoline", "Petrol", "Electric"):
            with self.subTest(fuel_type=fuel_type):
                result = validate_fleet_upload(
                    "fleet.csv",
                    (
                        "vehicle_id,capacity,fuel_type,fuel_consumption_per_km,"
                        "fuel_price_per_unit\n"
                        f"van-1,100,{fuel_type},0.1,2\n"
                    ).encode(),
                )
                self.assertTrue(result.valid, result.errors)
                self.assertEqual(result.rows[0]["fuel_type"], fuel_type)

        for fuel_type in ("CNG", "LPG"):
            with self.subTest(fuel_type=fuel_type):
                result = validate_fleet_upload(
                    "fleet.csv",
                    (
                        "vehicle_id,capacity,fuel_type\n"
                        f"van-1,100,{fuel_type}\n"
                    ).encode(),
                )
                self.assertFalse(result.valid)
                self.assertTrue(
                    any(
                        "unsupported fuel_type" in error
                        and "Petrol is accepted as a Gasoline alias" in error
                        and "CNG and LPG are not supported" in error
                        for error in result.errors
                    ),
                    result.errors,
                )

    def test_excel_upload_is_read_and_validated(self):
        buffer = BytesIO()
        pd.DataFrame(
            [{"vehicle_id": "van-x", "capacity": 250, "shift_end_min": 900}]
        ).to_excel(buffer, index=False, engine="openpyxl")
        result = validate_fleet_upload("company.xlsx", buffer.getvalue())
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.rows[0]["vehicle_id"], "van-x")
        self.assertEqual(result.rows[0]["capacity"], 250.0)


class CustomRoutePlannerUITests(unittest.TestCase):
    def test_selected_fuel_profile_explains_petrol_units_and_electric_emissions(self):
        with tempfile.TemporaryDirectory() as directory:
            app_file = Path(directory) / "fuel_profile_app.py"
            app_file.write_text(
                "import streamlit as st\n"
                "from route_dashboard.ui import _render_custom_fuel_profile_details\n"
                "st.session_state.setdefault('vehicle', {'fuel_type': 'Electric'})\n"
                "_render_custom_fuel_profile_details(st.session_state['vehicle'])\n",
                encoding="utf-8",
            )
            app = AppTest.from_file(str(app_file), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertTrue(
                any(
                    "zero tailpipe CO2 is not a grid-emissions" in item.value
                    and "grid emissions are unavailable" in item.value
                    for item in app.caption
                )
            )

            app.session_state["vehicle"] = {"fuel_type": "Petrol"}
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(
                any(
                    "Petrol (calculated using the Gasoline model)" in item.value
                    and "L/km" in item.value
                    and "price is per L" in item.value
                    for item in app.caption
                )
            )

    def test_fleet_upload_requires_acceptance_and_excludes_unavailable_vehicle(self):
        content = (
            "company_id,vehicle_id,vehicle_type,capacity,max_speed_kmph,available,"
            "fuel_type,fuel_consumption_per_km,fuel_price_per_unit,currency,"
            "driver_cost_per_hour,shift_start_min,shift_end_min,available_fuel_quantity\n"
            "Fleet A,van-1,Electric van,2000,50,true,Petrol,0.1,1.5,USD,30,480,1020,100\n"
            "Fleet A,van-2,Truck,1500,40,false,Diesel,0.2,1.8,USD,25,480,1020,80\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            app_file = Path(directory) / "custom_fleet_app.py"
            app_file.write_text(
                "from route_dashboard.ui import _render_custom_fleet_profile\n"
                "import streamlit as st\n"
                "selected = _render_custom_fleet_profile()\n"
                "st.write(selected)\n",
                encoding="utf-8",
            )
            app = AppTest.from_file(str(app_file), default_timeout=30).run()
            app.file_uploader[0].upload(
                "fleet.csv",
                content,
                "text/csv",
            ).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.label == "Accept uploaded fleet data" for item in app.button))
            self.assertNotIn("custom_fleet_rows", app.session_state)
            self.assertTrue(
                any(
                    "Uploaded data preview" in item.value
                    for item in app.markdown
                )
            )

            next(button for button in app.button if button.label == "Accept uploaded fleet data").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["custom_fleet_rows"]), 2)
            self.assertTrue(any("1 vehicle(s) explicitly marked unavailable" in item.value for item in app.info))
            self.assertEqual(app.selectbox[1].value, "van-1")
            self.assertEqual(len(app.selectbox[1].options), 1)
            self.assertIn("van-1", app.selectbox[1].options[0])
            self.assertTrue(any("Uploaded driver shift" in item.value for item in app.caption))
            self.assertTrue(
                any(
                    "Uploaded fuel type: Petrol (calculated using the Gasoline model)"
                    in item.value
                    and "L/km" in item.value
                    for item in app.caption
                )
            )

    def test_place_search_and_route_request_are_explicit(self):
        place = PlaceResult("Example origin", 16.0, 80.0)
        destination = PlaceResult("Example destination", 16.2, 80.2)
        route = _route(
            "road-ui",
            1,
            ((16.0, 80.0), (16.1, 80.1), (16.2, 80.2)),
            20.0,
            45.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            app_file = Path(directory) / "custom_route_app.py"
            app_file.write_text(
                "from route_dashboard.ui import _render_custom_route_planner, _render_page_header\n"
                "_render_page_header('Custom Route Planner', None)\n"
                "_render_custom_route_planner()\n",
                encoding="utf-8",
            )
            with (
                patch.object(
                    ui,
                    "search_places",
                    side_effect=[((place,), None), ((destination,), None), ((), None)],
                ) as geocode,
                patch.object(ui, "get_osrm_route_alternatives", return_value=((route,), None)) as request_routes,
                patch.object(ui, "st_folium") as render_map,
            ):
                app = AppTest.from_file(str(app_file), default_timeout=30).run()
                self.assertFalse(app.exception)
                geocode.assert_not_called()
                request_routes.assert_not_called()

                next(button for button in app.button if button.label == "Search origin").click().run()
                self.assertFalse(app.exception)
                origin_map = render_map.call_args.args[0]
                self.assertEqual(
                    sum(
                        isinstance(layer, folium.Marker)
                        for layer in origin_map._children.values()
                    ),
                    1,
                )
                request_routes.assert_not_called()
                next(button for button in app.button if button.label == "Search destination").click().run()
                self.assertFalse(app.exception)
                locations_map = render_map.call_args.args[0]
                self.assertEqual(
                    sum(
                        isinstance(layer, folium.Marker)
                        for layer in locations_map._children.values()
                    ),
                    2,
                )
                geocode.assert_called()
                request_routes.assert_not_called()

                next(
                    button
                    for button in app.button
                    if button.label == "Compare OSRM road alternatives"
                ).click().run()
                self.assertFalse(app.exception)
                request_routes.assert_called_once_with((16.0, 80.0), (16.2, 80.2))
                self.assertGreaterEqual(len(app.dataframe), 1)
                map_view = next(radio for radio in app.radio if radio.label == "Map view")
                map_view.set_value("Tilted 3D (flat-map perspective)").run()
                self.assertFalse(app.exception)
                self.assertTrue(
                    any("camera-pitched perspective" in item.value for item in app.caption)
                )
                map_view = next(radio for radio in app.radio if radio.label == "Map view")
                map_view.set_value("2D (Folium)").run()
                self.assertFalse(app.exception)
                self.assertGreaterEqual(render_map.call_count, 2)
                next(button for button in app.button if button.label == "Search origin").click().run()
                self.assertFalse(app.exception)
                self.assertNotIn("custom_origin", app.session_state)
                self.assertTrue(
                    any("No matching origin" in item.value for item in app.info)
                )
                request_routes.assert_called_once()


class ThemeWorkspaceSmokeTests(unittest.TestCase):
    def test_all_workspaces_render_without_invoking_ibm_execution(self):
        pages = {
            "Dashboard": "Operations overview",
            "Route Optimization": "Route optimization",
            "Dynamic Traffic": "Dynamic traffic",
            "Fleet Disruption": "Fleet disruption",
            "IBM Quantum Hardware": "IBM Quantum Hardware",
            "Demo Mode": "Demo Mode",
            "Custom Route Planner": "Custom Route Planner",
            "Benchmark Mode": "Benchmark laboratory",
            "History": "Operation history",
        }
        with (
            patch.object(
                ui,
                "discover_ibm_backends",
                side_effect=AssertionError("Backend discovery must remain explicit."),
            ) as discover,
            patch.object(
                ui,
                "connect_ibm_quantum",
                side_effect=AssertionError("The IBM account must not be contacted."),
            ) as connect,
            patch.object(
                ui,
                "dry_run_optimized_qaoa_execution",
                side_effect=AssertionError("No IBM hardware preflight is requested."),
            ) as preflight,
            patch.object(
                ui,
                "submit_confirmed_hardware_job",
                side_effect=AssertionError("No IBM hardware job may be submitted."),
            ) as submit,
        ):
            app = AppTest.from_file("app.py", default_timeout=30).run()
            for page, expected_title in pages.items():
                if page != "Dashboard":
                    app.session_state["active_page"] = page
                    app.run()
                self.assertFalse(
                    app.exception,
                    f"{page} failed to render: {[str(item.value) for item in app.exception]}",
                )
                self.assertTrue(
                    any(item.value == expected_title for item in app.title),
                    f"{page} did not render its expected page title.",
                )

        discover.assert_not_called()
        connect.assert_not_called()
        preflight.assert_not_called()
        submit.assert_not_called()
