import unittest
from dataclasses import replace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from route_dashboard import ui
from route_dashboard.demo_mode import run_full_demo


class IBMHardwareComparisonUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.demo_result = run_full_demo()

    @staticmethod
    def _visible_text(app: AppTest) -> str:
        collections = (
            app.markdown,
            app.caption,
            app.success,
            app.warning,
            app.info,
            app.error,
            app.metric,
        )
        return " ".join(
            str(item.value)
            for collection in collections
            for item in collection
        )

    def _render_demo(self, demo_result=None) -> AppTest:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["hackathon_demo_result"] = (
            self.demo_result if demo_result is None else demo_result
        )
        app.radio[0].set_value("Demo Mode").run()
        return app

    def test_demo_mode_never_connects_to_or_submits_ibm_hardware(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.radio[0].set_value("Demo Mode").run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        visible_info = " ".join(item.value for item in app.info)
        self.assertIn("does not connect to IBM Quantum", visible_info)
        labels = {button.label for button in app.button}
        self.assertFalse(
            any(
                word in label.casefold()
                for label in labels
                for word in ("submit", "refresh job", "discover ibm", "hardware dry-run")
            )
        )
        self.assertFalse(hasattr(ui, "submit_confirmed_hardware_job"))
        self.assertFalse(hasattr(ui, "connect_ibm_quantum"))

    def test_historical_hardware_record_is_not_presented_as_current_demo_result(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.radio[0].set_value("Demo Mode").run()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        text = " ".join(
            str(item.value)
            for collection in (app.markdown, app.caption, app.info, app.success)
            for item in collection
        )
        self.assertIn("historical demonstration", text)
        self.assertNotIn("REAL IBM QUANTUM HARDWARE RESULT", text)
        self.assertNotIn("mock-completed-job", text)

    def test_demo_workflow_overview_and_execution_facts_are_actual_local_aer_values(self) -> None:
        app = self._render_demo()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        visible = self._visible_text(app)

        for stage in (
            "Workflow overview",
            "not a live execution trace",
            "Delivery inputs and constraints",
            "Classical feasibility",
            "QUBO formulation",
            "QAOA",
            "Qiskit Aer simulation",
            "Candidate decoding",
            "Feasibility validation",
            "Route results",
            "not IBM hardware execution",
            "separate from the IBM Quantum Hardware workspace",
            "not evidence of quantum advantage",
        ):
            self.assertIn(stage.casefold(), visible.casefold())
        self.assertIn("Local Qiskit Aer executed", visible)

        metrics = {item.label: str(item.value) for item in app.metric}
        status = self.demo_result.baseline_qaoa
        quantum_result = self.demo_result.baseline_run.quantum_result
        self.assertIsNotNone(quantum_result)
        self.assertEqual(
            metrics["Feasible route options / QUBO variables"],
            str(self.demo_result.baseline_candidate_count),
        )
        self.assertEqual(
            metrics["Feasibility validation"],
            "Passed" if status.valid else "Not passed",
        )
        self.assertEqual(metrics["Observed unique samples"], str(status.unique_samples))
        self.assertEqual(
            metrics["Selected sample probability"],
            f"{status.sample_probability:.6f}",
        )
        self.assertIn(f"{quantum_result.qubo_energy:.4f}", visible)
        self.assertIn(ui._format_money(quantum_result.objective_value), visible)
        self.assertIn(str(quantum_result.observed_unique_samples), visible)
        self.assertIn("historical demonstration", visible)

    def test_demo_workflow_shows_unavailable_values_when_local_aer_was_skipped(self) -> None:
        unavailable_demo = replace(
            self.demo_result,
            baseline_run=replace(
                self.demo_result.baseline_run,
                quantum=None,
                quantum_result=None,
            ),
            baseline_qaoa=replace(
                self.demo_result.baseline_qaoa,
                executed=False,
                valid=False,
                message="Local QAOA was skipped.",
                sample_probability=None,
                unique_samples=None,
            ),
        )
        app = self._render_demo(unavailable_demo)
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])

        metrics = {item.label: str(item.value) for item in app.metric}
        self.assertEqual(metrics["Feasibility validation"], "Unavailable")
        self.assertEqual(metrics["Observed unique samples"], "Unavailable")
        self.assertEqual(metrics["Selected sample probability"], "Unavailable")
        self.assertIn(
            "QUBO energy, selected objective value, and decoded route details are unavailable",
            self._visible_text(app),
        )
        self.assertIn("Local Qiskit Aer not run", self._visible_text(app))
        self.assertIn("historical demonstration", self._visible_text(app))

    def test_demo_presentation_stages_and_final_pipeline_render_in_order(self) -> None:
        app = self._render_demo()
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])

        markdown = " ".join(item.value for item in app.markdown)
        visible = self._visible_text(app)
        headings = (
            "01 / BASELINE",
            "02 / QUANTUM OPTIMIZATION",
            "03 / DYNAMIC TRAFFIC",
            "04 / FLEET DISRUPTION",
            "05 / SUSTAINABILITY + OBJECTIVE TRADE-OFFS",
            "06 / Quantum Execution Comparison",
            "07 / FINAL SUMMARY",
        )
        positions = [markdown.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        for required in (
            "QUBO formulation",
            "Qiskit Aer simulation",
            "Candidate decoding",
            "Feasibility validation",
            "QuantumRoute — From Classical Routing to Real Quantum Hardware",
            "Sustainability analysis",
        ):
            self.assertIn(required.casefold(), markdown.casefold())
        self.assertIn("Normal → Heavy traffic".casefold(), visible.casefold())
        self.assertIn("no universal best route", visible.casefold())
        self.assertIn("separate, historical demonstration", visible.casefold())

    def test_demo_run_and_reset_work_without_touching_ibm_state(self) -> None:
        with (
            patch.object(ui, "run_full_demo", return_value=self.demo_result) as run_demo,
            patch.object(ui, "_persist_demo_history") as persist_history,
            patch.object(
                ui,
                "discover_ibm_backends",
                side_effect=AssertionError("Demo Mode must not discover IBM backends."),
            ) as discover,
        ):
            app = AppTest.from_file("app.py", default_timeout=300).run()
            app.radio[0].set_value("Demo Mode").run()
            app.session_state["ibm_hardware_job_state"] = {
                "job_id": "existing-historical-job",
                "status": "DONE",
            }
            next(
                button
                for button in app.button
                if button.key == "run_full_hackathon_demo"
            ).click().run()

            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            self.assertIsNotNone(app.session_state["hackathon_demo_result"])
            self.assertTrue(
                any(
                    "QuantumRoute Demonstration Complete" in item.value
                    for item in app.success
                )
            )
            self.assertTrue(
                any("historical demonstration" in item.value for item in app.caption)
            )
            run_demo.assert_called_once()
            persist_history.assert_called_once_with(self.demo_result)

            next(
                button
                for button in app.button
                if button.key == "reset_hackathon_demo"
            ).click().run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            with self.assertRaises(KeyError):
                app.session_state["hackathon_demo_result"]
            self.assertEqual(
                app.session_state["ibm_hardware_job_state"]["job_id"],
                "existing-historical-job",
            )
            run_demo.assert_called_once()
            discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
