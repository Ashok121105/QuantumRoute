import unittest
from datetime import time
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from route_dashboard.map_view import build_location_preview_map


class GuidedWorkflowUITests(unittest.TestCase):
    def test_guided_workflow_is_the_default_and_requires_company_details(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertEqual(app.session_state["active_page"], "Guided Workflow")
        self.assertEqual(app.session_state["guided_step"], 0)
        self.assertTrue(any(item.label == "Company name *" for item in app.text_input))

        next_button = next(button for button in app.button if button.label == "Next")
        next_button.click().run()
        self.assertEqual(app.session_state["guided_step"], 0)
        self.assertTrue(
            any("Complete the required company fields" in item.value for item in app.error)
        )

    def test_company_values_survive_step_navigation(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        values = {
            "Company name *": "Northstar Logistics",
            "Company ID *": "NS-01",
            "Branch / dispatch location *": "Rotterdam",
        }
        for label, value in values.items():
            next(item for item in app.text_input if item.label == label).set_value(value).run()
        next(button for button in app.button if button.label == "Next").click().run()
        self.assertEqual(app.session_state["guided_step"], 1)
        self.assertEqual(app.session_state["company_name"], "Northstar Logistics")
        self.assertEqual(app.session_state["company_id"], "NS-01")

        next(button for button in app.button if button.label == "Back").click().run()
        self.assertEqual(app.session_state["guided_step"], 0)
        self.assertEqual(app.session_state["company_dispatch_location"], "Rotterdam")

    def test_map_preview_shows_selected_locations_without_routing_request(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["guided_step"] = 3
        app.session_state["custom_origin"] = {
            "label": "Origin",
            "latitude": 51.5,
            "longitude": -0.1,
            "source": "test",
        }
        app.session_state["guided_destination_ids"] = [0, 4]
        app.session_state["guided_destination_location_0"] = {
            "label": "Destination One",
            "latitude": 53.5,
            "longitude": -2.2,
            "source": "test",
        }
        app.session_state["guided_destination_location_4"] = {
            "label": "Destination Two",
            "latitude": 52.5,
            "longitude": -1.2,
            "source": "test",
        }
        with (
            patch("route_dashboard.ui.get_osrm_route_alternatives") as request,
            patch(
                "route_dashboard.ui.build_location_preview_map",
                wraps=build_location_preview_map,
            ) as build_map,
        ):
            app.run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        request.assert_not_called()
        self.assertEqual(len(build_map.call_args.args[1]), 2)
        self.assertTrue(any("all 2 selected destination(s)" in item.value for item in app.caption))

    def test_destinations_can_be_added_removed_and_capped_at_five(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["guided_step"] = 2
        app.run()

        next(button for button in app.button if button.label == "+ Add Destination").click().run()
        self.assertEqual(app.session_state["guided_destination_ids"], [0, 1])
        self.assertTrue(
            any("Search Destination 2" in item.label for item in app.text_input)
        )
        next(button for button in app.button if button.label == "Remove").click().run()
        self.assertEqual(app.session_state["guided_destination_ids"], [0])

        app.session_state["guided_destination_ids"] = [0, 1, 2, 3, 4]
        app.session_state["guided_destination_next_id"] = 5
        app.run()
        self.assertFalse(any(button.label == "+ Add Destination" for button in app.button))
        self.assertTrue(any("maximum of 5 destinations" in item.value for item in app.caption))

    def test_delivery_values_survive_back_and_next_navigation(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["guided_step"] = 2
        app.session_state["custom_origin"] = {
            "label": "Depot",
            "latitude": 51.5,
            "longitude": -0.1,
            "source": "test",
        }
        app.session_state["guided_destination_ids"] = [0, 1]
        for destination_id, name, latitude, longitude in (
            (0, "First Stop", 53.5, -2.2),
            (1, "Second Stop", 52.5, -1.2),
        ):
            app.session_state[f"guided_destination_location_{destination_id}"] = {
                "label": name,
                "latitude": latitude,
                "longitude": longitude,
                "source": "test",
            }
            app.session_state[f"guided_destination_name_{destination_id}"] = name
            app.session_state[f"guided_destination_demand_{destination_id}"] = destination_id + 1.0
            app.session_state[f"guided_destination_window_start_{destination_id}"] = time(8, 0)
            app.session_state[f"guided_destination_window_end_{destination_id}"] = time(17, 0)
            app.session_state[f"guided_destination_service_{destination_id}"] = 5
        app.run()

        next(button for button in app.button if button.label == "Next").click().run()
        self.assertEqual(app.session_state["guided_step"], 3)
        next(button for button in app.button if button.label == "Back").click().run()
        self.assertEqual(app.session_state["guided_step"], 2)
        self.assertEqual(app.session_state["guided_destination_ids"], [0, 1])
        self.assertEqual(app.session_state["guided_destination_name_0"], "First Stop")
        self.assertEqual(app.session_state["guided_destination_name_1"], "Second Stop")
        self.assertEqual(app.session_state["guided_destination_demand_1"], 2.0)

    def test_optimize_step_passes_every_destination_into_existing_runner(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["guided_step"] = 4
        app.session_state["custom_origin"] = {
            "label": "Depot",
            "latitude": 51.5,
            "longitude": -0.1,
            "source": "test",
        }
        app.session_state["guided_destination_ids"] = [0, 3]
        for destination_id, name, latitude, longitude in (
            (0, "First Stop", 53.5, -2.2),
            (3, "Second Stop", 52.5, -1.2),
        ):
            app.session_state[f"guided_destination_location_{destination_id}"] = {
                "label": name,
                "latitude": latitude,
                "longitude": longitude,
                "source": "test",
            }
            app.session_state[f"guided_destination_name_{destination_id}"] = name
            app.session_state[f"guided_destination_demand_{destination_id}"] = 1.5
            app.session_state[f"guided_destination_window_start_{destination_id}"] = time(8, 30)
            app.session_state[f"guided_destination_window_end_{destination_id}"] = time(16, 0)
            app.session_state[f"guided_destination_service_{destination_id}"] = 10
        app.session_state["guided_vehicle_count"] = 2
        app.session_state["guided_vehicle_capacity"] = 3.0
        app.session_state["guided_shift_start"] = time(8, 0)
        app.session_state["guided_shift_end"] = time(17, 0)
        app.run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])

        passed_values: dict[str, object] = {}

        def capture_runner(
            values: dict[str, object],
            _qaoa_config: object,
            _use_osrm: bool,
        ) -> None:
            passed_values.update(values)
            st.session_state["run_error"] = None
            st.session_state["current_run"] = {"scenario": object(), "run": object()}

        with patch("route_dashboard.ui._run_from_form", side_effect=capture_runner):
            next(
                button
                for button in app.button
                if button.label == "Optimize all destinations"
            ).click().run()

        rows = passed_values["delivery_rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["destination"] for row in rows], ["First Stop", "Second Stop"])
        self.assertEqual([row["demand"] for row in rows], [1.5, 1.5])
        self.assertEqual([row["service_minutes"] for row in rows], [10, 10])
        self.assertEqual(passed_values["vehicle_count"], 2)
        self.assertEqual(passed_values["capacity"], 3.0)

    def test_final_results_render_assignments_for_every_destination(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["guided_step"] = 5
        app.session_state["custom_origin"] = {
            "label": "Depot",
            "latitude": 51.5,
            "longitude": -0.1,
            "source": "test",
        }
        app.session_state["guided_destination_ids"] = [0, 1]
        delivery_values = (
            ("delivery-1", "First Stop", 53.5, -2.2),
            ("delivery-2", "Second Stop", 52.5, -1.2),
        )
        for destination_id, (delivery_id, name, latitude, longitude) in enumerate(
            delivery_values
        ):
            app.session_state[f"guided_destination_location_{destination_id}"] = {
                "label": name,
                "latitude": latitude,
                "longitude": longitude,
                "source": "test",
            }
            app.session_state[f"guided_destination_name_{destination_id}"] = name
            app.session_state[f"guided_destination_demand_{destination_id}"] = 1.0
            app.session_state[f"guided_destination_window_start_{destination_id}"] = time(8, 0)
            app.session_state[f"guided_destination_window_end_{destination_id}"] = time(17, 0)
            app.session_state[f"guided_destination_service_{destination_id}"] = 5
        deliveries = tuple(
            SimpleNamespace(
                delivery_id=delivery_id,
                location=delivery_id,
                demand=1.0,
                window_start_min=480,
                window_end_min=1020,
            )
            for delivery_id, _, _, _ in delivery_values
        )
        route = SimpleNamespace(
            plan=SimpleNamespace(
                vehicle_id="van-1",
                delivery_ids=tuple(delivery.delivery_id for delivery in deliveries),
            )
        )
        scenario = SimpleNamespace(
            vehicles=(
                SimpleNamespace(
                    vehicle_id="van-1",
                    start_location="depot",
                    end_location="depot",
                ),
            ),
            deliveries=deliveries,
            location_names={
                "depot": "Guntur",
                **{
                    delivery_id: name
                    for delivery_id, name, _, _ in delivery_values
                },
            },
            travel=SimpleNamespace(
                distances_km={
                    ("depot", "delivery-1"): 10.0,
                    ("delivery-1", "delivery-2"): 20.0,
                    ("delivery-2", "depot"): 30.0,
                },
                durations_min={
                    ("depot", "delivery-1"): 12.0,
                    ("delivery-1", "delivery-2"): 24.0,
                    ("delivery-2", "depot"): 36.0,
                },
            ),
        )
        run = SimpleNamespace(
            classical=SimpleNamespace(routes=(route,)),
            quantum=None,
        )
        app.session_state["guided_run"] = {"scenario": scenario, "run": run}
        app.session_state["guided_optimization_signature"] = "current"
        app.run()

        with (
            patch("route_dashboard.ui._guided_optimization_signature", return_value="current"),
            patch("route_dashboard.ui._render_results"),
            patch("route_dashboard.ui._render_route_map") as render_route_map,
            patch("route_dashboard.ui._render_historical_ibm_evidence"),
        ):
            app.run()
        self.assertTrue(render_route_map.call_args.kwargs["show_optimized_order"])
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(
            any(
                "Assignments for all 2 destinations" in item.value
                for item in app.markdown
            )
        )
        self.assertTrue(
            any("Optimized Delivery Order" in item.value for item in app.markdown)
        )
        self.assertTrue(
            any("0. Guntur — Start" in item.value for item in app.markdown)
        )
        self.assertTrue(
            any("1. First Stop" in item.value for item in app.markdown)
        )
        self.assertTrue(
            any("2. Second Stop" in item.value for item in app.markdown)
        )
        self.assertTrue(
            any("3. Guntur — Return" in item.value for item in app.markdown)
        )
        assignments = app.dataframe[0].value
        self.assertEqual(assignments["Destination"].tolist(), ["First Stop", "Second Stop"])
        self.assertEqual(assignments["Vehicle"].tolist(), ["van-1", "van-1"])
        legs = app.dataframe[1].value
        self.assertEqual(legs["Distance (km)"].tolist(), [10.0, 20.0, 30.0])
        self.assertEqual(legs["Travel time (min)"].tolist(), [12.0, 24.0, 36.0])


if __name__ == "__main__":
    unittest.main()
