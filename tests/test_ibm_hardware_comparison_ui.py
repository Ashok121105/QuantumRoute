from dataclasses import replace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ibm_execution import (
    HardwareDryRunResult,
    HardwareDryRunStatus,
    HardwareJobState,
    decode_hardware_counts,
)
from ibm_qaoa_adapter import prepare_demo_qaoa_execution_package
from route_dashboard import ui
from route_dashboard.demo_mode import build_hackathon_demo_scenario, run_full_demo
from route_dashboard.scenario import ScenarioProblem
from ibm_quantum import IBMBackendMetadata


class IBMHardwareComparisonUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.demo_result = run_full_demo()
        caller_package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        cls.package = replace(caller_package, parameter_source="local_aer_optimized")
        cls.decode = decode_hardware_counts({"1010": 256}, cls.package)
        cls.backend = IBMBackendMetadata(
            name="ibm_fez",
            num_qubits=127,
            operational=True,
            status_message="active",
            pending_jobs=0,
            simulator=False,
        )
        cls.dry_run = HardwareDryRunResult(
            ready=True,
            status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
            message="mock dry run passed",
            backend=cls.backend,
            circuit_qubits=cls.package.required_qubits,
            shots=256,
            transpiled_circuit=cls.package.circuit,
            execution_package=cls.package,
        )
        cls.job_state = HardwareJobState(
            submission_attempted=True,
            job_id="mock-completed-job",
            backend_name="ibm_fez",
            shots=256,
            status="DONE",
            submitted_at="2026-10-04T00:00:00+00:00",
            message="completed",
            raw_counts=cls.decode.raw_counts,
            most_frequent_bitstring=cls.decode.most_frequent_bitstring,
            route_valid=cls.decode.valid_route,
            decoded_routes=cls.decode.routes,
            route_message=cls.decode.message,
            measurement_result_received=True,
        )

    def _render_demo(self, demo_result=None, job_state=None, dry_run=None):
        app = AppTest.from_file("app.py", default_timeout=300).run()
        if demo_result is not None:
            app.session_state["hackathon_demo_result"] = demo_result
        if job_state is not None:
            app.session_state["ibm_hardware_job_state"] = job_state
        if dry_run is not None:
            app.session_state["ibm_hardware_dry_run"] = dry_run
        app.radio[0].set_value("Demo Mode").run()
        return app

    @staticmethod
    def _visible_text(app) -> str:
        collections = (
            app.markdown,
            app.caption,
            app.success,
            app.warning,
            app.info,
            app.error,
            app.metric,
        )
        return " ".join(str(item.value) for collection in collections for item in collection)

    def test_completed_compatible_hardware_result_displays_all_three_methods(self) -> None:
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app = self._render_demo(
                self.demo_result,
                self.job_state,
                self.dry_run,
            )

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        visible = self._visible_text(app)
        for required in (
            "Quantum Execution Comparison",
            "CLASSICAL OPTIMIZATION",
            "LOCAL AER QAOA",
            "REAL IBM QUANTUM HARDWARE",
            "ibm_fez",
            "mock-completed-job",
            "256",
            "DONE",
            "passed feasibility validation",
            "matched for this demo case",
            "not evidence of quantum advantage",
            "stochastic",
        ):
            self.assertIn(required.casefold(), visible.casefold())
        self.assertTrue(
            any("Hardware measurement distribution" in item.label for item in app.expander)
        )
        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()
        refresh.assert_not_called()

    def test_demo_workflow_overview_and_execution_facts_are_actual_local_aer_values(self) -> None:
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app = self._render_demo(self.demo_result)

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
            self.assertIn(stage, visible)

        metrics = {item.label: str(item.value) for item in app.metric}
        self.assertEqual(
            metrics["Feasible route options / QUBO variables"],
            str(self.demo_result.baseline_candidate_count),
        )
        self.assertEqual(
            metrics["Feasibility validation"],
            "Passed" if self.demo_result.baseline_qaoa.valid else "Not passed",
        )
        self.assertEqual(
            metrics["Observed unique samples"],
            str(self.demo_result.baseline_qaoa.unique_samples),
        )
        self.assertEqual(
            metrics["Selected sample probability"],
            f"{self.demo_result.baseline_qaoa.sample_probability:.6f}",
        )
        result = self.demo_result.baseline_run.quantum_result
        self.assertIsNotNone(result)
        self.assertIn(f"{result.qubo_energy:.4f}", visible)
        self.assertIn(ui._format_money(result.objective_value), visible)
        self.assertIn(str(result.observed_unique_samples), visible)

        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()
        refresh.assert_not_called()

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
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
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
        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()
        refresh.assert_not_called()

    def test_hardware_metrics_are_computed_from_stored_decoded_routes(self) -> None:
        app = self._render_demo(self.demo_result, self.job_state, self.dry_run)
        self.assertFalse(app.exception, [str(item.value) for item in app.exception])

        summary = ui._route_summary_from_routes(self.job_state.decoded_routes)
        impact = ui.calculate_route_impact(self.demo_result.scenario, summary)
        displayed_metrics = [str(item.value) for item in app.metric]

        self.assertIn(f"{impact.distance_km:.2f} km", displayed_metrics)
        self.assertIn(ui._format_duration(impact.travel_time_min), displayed_metrics)
        display_money = ui._format_money(impact.cost)
        self.assertIn(display_money, displayed_metrics)
        self.assertIn(f"{impact.fuel_used:.3f} {impact.fuel_unit}", displayed_metrics)
        self.assertIn(f"{impact.tailpipe_co2_kg:.3f} kg", displayed_metrics)
        self.assertTrue(ui._completed_hardware_result_compatible(
            self.job_state,
            self.dry_run,
            self.demo_result,
        ))

    def test_no_completed_hardware_result_shows_graceful_empty_state(self) -> None:
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
        ):
            app = self._render_demo(self.demo_result)

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(
            any(
                item.value == "No completed IBM Quantum hardware result available for this session."
                for item in app.info
            )
        )
        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()

    def test_mismatched_scenario_is_not_combined_with_demo_metrics(self) -> None:
        mismatched_scenario = replace(
            self.demo_result.scenario,
            traffic_condition="Heavy",
        )
        mismatched_demo = replace(self.demo_result, scenario=mismatched_scenario)
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
        ):
            app = self._render_demo(mismatched_demo, self.job_state, self.dry_run)

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(
            any(
                "belongs to a different scenario" in item.value
                for item in app.warning
            )
        )
        self.assertFalse(
            any(
                "#### REAL IBM QUANTUM HARDWARE" in item.value
                for item in app.markdown
            )
        )
        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()

    def test_demo_page_load_has_no_ibm_api_or_job_calls_and_demo_remains_rendered(self) -> None:
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app = self._render_demo(self.demo_result)

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(any("QuantumRoute Demonstration Complete" in item.value for item in app.success))
        connect.assert_not_called()
        discover.assert_not_called()
        submit.assert_not_called()
        refresh.assert_not_called()

    def test_demo_presentation_stages_and_final_pipeline_render_in_order(self) -> None:
        app = self._render_demo(self.demo_result)
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
            "IBM Quantum hardware",
            "Sustainability analysis",
        ):
            self.assertIn(required.casefold(), markdown.casefold())
        self.assertIn("Normal → Heavy traffic".casefold(), visible.casefold())
        self.assertIn("no universal best route", visible.casefold())

    def test_demo_run_and_reset_work_without_touching_ibm_state(self) -> None:
        from ibm_execution import HardwareJobState

        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app = AppTest.from_file("app.py", default_timeout=300).run()
            app.radio[0].set_value("Demo Mode").run()
            app.session_state["ibm_hardware_job_state"] = HardwareJobState(
                job_id="existing-completed-job",
                backend_name="ibm_fez",
                shots=256,
                status="DONE",
            )
            next(
                button for button in app.button
                if button.key == "run_full_hackathon_demo"
            ).click().run()

            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            self.assertIsNotNone(app.session_state["hackathon_demo_result"])
            self.assertTrue(
                any("QuantumRoute Demonstration Complete" in item.value for item in app.success)
            )

            next(
                button for button in app.button
                if button.key == "reset_hackathon_demo"
            ).click().run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            with self.assertRaises(KeyError):
                app.session_state["hackathon_demo_result"]
            self.assertEqual(
                app.session_state["ibm_hardware_job_state"].job_id,
                "existing-completed-job",
            )
            connect.assert_not_called()
            discover.assert_not_called()
            submit.assert_not_called()
            refresh.assert_not_called()

    def test_all_workspaces_load_without_exceptions_or_ibm_calls(self) -> None:
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app = AppTest.from_file("app.py", default_timeout=300).run()
            for page in (
                "Dashboard",
                "Route Optimization",
                "Dynamic Traffic",
                "Fleet Disruption",
                "IBM Quantum Hardware",
                "Demo Mode",
                "Benchmark Mode",
            ):
                app.radio[0].set_value(page).run()
                self.assertFalse(
                    app.exception,
                    f"{page}: {[str(item.value) for item in app.exception]}",
                )
            connect.assert_not_called()
            discover.assert_not_called()
            submit.assert_not_called()
            refresh.assert_not_called()

    def test_ibm_workspace_remains_functional_and_offers_demo_comparison_notice(self) -> None:
        from ibm_quantum import IBMBackendDiscovery, IBMQuantumStatus
        discovery = IBMBackendDiscovery(
            connected=True,
            status=IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL,
            message="one backend",
            backends=(self.backend,),
        )
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["ibm_backend_discovery"] = discovery
        app.session_state["ibm_hardware_job_state"] = self.job_state
        app.session_state["ibm_hardware_dry_run"] = self.dry_run
        app.session_state["hackathon_demo_result"] = self.demo_result
        with (
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "discover_ibm_backends") as discover_mock,
            patch.object(ui, "refresh_hardware_job_status") as refresh,
        ):
            app.radio[0].set_value("IBM Quantum Hardware").run()

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(any("Comparison available in Demo Mode." == item.value for item in app.info))
        connect.assert_not_called()
        discover_mock.assert_not_called()
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
