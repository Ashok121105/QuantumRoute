import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from route_dashboard.history import SQLiteOperationHistory
from route_dashboard import ui
from route_dashboard.optimization import OptimizationRun, RouteSummary
from route_dashboard.scenario import build_demo_scenario


def _ui_record() -> dict[str, object]:
    timestamp = "2026-10-09T10:00:00+00:00"
    return {
        "run_id": "ui-history-run",
        "parent_run_id": None,
        "created_at": timestamp,
        "completed_at": timestamp,
        "status": "VALIDATED",
        "operation_type": "TEST_FIXTURE",
        "origin": "Depot A",
        "destination": "North Market",
        "traffic_level": "Moderate",
        "traffic_source": "SIMULATED",
        "objective": "Cost Priority",
        "inputs": {
            "origin": {"name": "Depot A", "latitude": 37.0, "longitude": -122.0},
            "destinations": [
                {
                    "name": "North Market",
                    "latitude": 37.1,
                    "longitude": -122.1,
                    "quantity": 1.0,
                    "time_window_start_min": 480,
                    "time_window_end_min": 600,
                    "service_duration_min": 5,
                }
            ],
            "vehicles": [
                {
                    "capacity": 2.0,
                    "shift_start_min": 480,
                    "shift_end_min": 1020,
                }
            ],
            "fuel": {
                "type": "Diesel",
                "price_per_unit_cost_units": 1.1,
                "cost_weights": {"time_cost_per_min": 0.4},
            },
            "traffic": {
                "level": "Moderate",
                "travel_data_source": "Local coordinate estimate",
            },
        },
        "outputs": {
            "feasibility": {"status": "VALIDATED", "failure_details": None},
            "solver_results": {
                "classical": {"method": "CLASSICAL", "status": "VALIDATED"},
                "qaoa_aer": {"method": "QAOA_AER", "status": "NOT_AVAILABLE"},
            },
            "ibm_hardware": {
                "status": "NOT_LINKED",
                "job_id": None,
                "backend": None,
                "shots": None,
                "verified_evidence_reference": None,
            },
        },
        "reoptimization": None,
        "source_metadata": {"project": "QuantumRoute"},
    }


class OperationHistoryUITests(unittest.TestCase):
    def test_opening_history_does_not_call_routing_or_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "history.sqlite3"
            history = SQLiteOperationHistory(database)
            record = _ui_record()
            self.assertTrue(history.save_completed_operation(record))
            script = Path(temporary_directory) / "history_app.py"
            script.write_text(
                "from route_dashboard.history import SQLiteOperationHistory\n"
                "from route_dashboard.history_ui import render_history_page\n"
                f"render_history_page(history=SQLiteOperationHistory({str(database)!r}))\n",
                encoding="utf-8",
            )
            with (
                patch("route_dashboard.routing.get_osrm_route_alternatives") as route_request,
                patch("route_dashboard.optimization.optimize_scenario") as optimize,
            ):
                app = AppTest.from_file(str(script), default_timeout=300).run()
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])
                self.assertTrue(
                    any("Depot A → North Market" in item.value for item in app.markdown)
                )
                next(
                    button
                    for button in app.button
                    if button.label == "Create a new plan from this history"
                ).click().run()
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])
                route_request.assert_not_called()
                optimize.assert_not_called()

            after = history.get_operation("ui-history-run")
            self.assertEqual(after["inputs"], record["inputs"])
            self.assertEqual(after["outputs"], record["outputs"])
            self.assertEqual(history.count_operations(), 1)
            self.assertEqual(
                app.session_state["history_plan_draft"],
                json.loads(json.dumps(record["inputs"])),
            )

    def test_sidebar_history_navigation_and_new_plan_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "history.sqlite3"
            history = SQLiteOperationHistory(database)
            history.save_completed_operation(_ui_record())
            app = AppTest.from_file("app.py", default_timeout=300).run()
            app.session_state["active_page"] = "History"
            with (
                patch(
                    "route_dashboard.history_ui.SQLiteOperationHistory",
                    return_value=history,
                ),
                patch.object(ui, "get_osrm_route_alternatives") as route_request,
                patch.object(ui, "optimize_scenario") as optimize_classical,
                patch.object(ui, "optimize_with_objective") as optimize_objective,
            ):
                app.run()
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])
                next(
                    button
                    for button in app.button
                    if button.label == "Create a new plan from this history"
                ).click().run()
                self.assertFalse(app.exception, [str(item.value) for item in app.exception])
                self.assertEqual(app.session_state["active_page"], "Route Optimization")
                self.assertTrue(
                    any("Historical inputs copied" in item.value for item in app.info)
                )
                route_request.assert_not_called()
                optimize_classical.assert_not_called()
                optimize_objective.assert_not_called()
            self.assertEqual(history.count_operations(), 1)

    def test_history_write_failure_is_visible_without_replacing_completed_run(self) -> None:
        completed_result = object()
        state: dict[str, object] = {"current_run": completed_result}
        streamlit_stub = SimpleNamespace(
            session_state=state,
            warning=lambda message: state.setdefault("shown_warning", message),
        )
        with (
            patch.object(ui, "st", streamlit_stub),
            patch.object(ui, "build_operation_record", return_value={"run_id": "disk-failure"}),
            patch.object(
                ui,
                "SQLiteOperationHistory",
                side_effect=OSError("disk full"),
            ),
        ):
            saved = ui._persist_completed_run(
                scenario=build_demo_scenario(),
                run=OptimizationRun(
                    classical=RouteSummary((), 0.0, 0.0, 0.0),
                    quantum=None,
                    quantum_result=None,
                    quantum_error=None,
                    objective_delta=None,
                    relative_gap_percent=None,
                ),
                run_id="disk-failure",
            )
        self.assertFalse(saved)
        self.assertIn("not saved to history", str(state["history_write_error"]))
        self.assertIn("disk full", str(state["shown_warning"]))
        self.assertIs(state["current_run"], completed_result)


if __name__ == "__main__":
    unittest.main()
