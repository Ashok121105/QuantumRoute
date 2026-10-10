from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from route_dashboard import ui


class FleetDisruptionUITests(unittest.TestCase):
    def test_dashboard_renders_before_and_after_fleet_metrics(self) -> None:
        before_metrics = SimpleNamespace(
            vehicles_available=2,
            deliveries_served=4,
            deliveries_unassigned=0,
            total_distance_km=42.0,
            total_travel_time_min=90.0,
            total_cost=18.0,
            fuel_used=5.0,
            fuel_unit="L",
            tailpipe_co2_kg=12.0,
        )
        after_metrics = SimpleNamespace(
            vehicles_available=1,
            deliveries_served=3,
            deliveries_unassigned=1,
            total_distance_km=35.0,
            total_travel_time_min=75.0,
            total_cost=16.0,
            fuel_used=4.0,
            fuel_unit="L",
            tailpipe_co2_kg=10.0,
        )
        disruption = SimpleNamespace(
            after_run=object(),
            after_metrics=after_metrics,
            before_metrics=before_metrics,
            after_scenario=SimpleNamespace(traffic_condition="Heavy"),
            optimization_scenario=object(),
        )

        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["active_page"] = "Dashboard"
        app.session_state["fleet_disruption_result"] = disruption
        with (
            patch.object(ui, "_render_waitlist"),
            patch.object(ui, "_render_route_map"),
            patch.object(ui, "_render_dashboard_route_details"),
            patch.object(ui, "_render_comparison"),
            patch.object(ui, "_render_fleet_view"),
        ):
            app.run()

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        markdown = [item.value for item in app.markdown]
        self.assertIn("#### Before disruption", markdown)
        self.assertIn("#### After disruption", markdown)
        self.assertEqual(
            [item.value for item in app.metric if item.label == "Vehicles available"],
            ["2", "1"],
        )


if __name__ == "__main__":
    unittest.main()
