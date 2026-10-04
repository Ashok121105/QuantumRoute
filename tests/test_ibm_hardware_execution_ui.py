from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from ibm_execution import HardwareDryRunResult, HardwareDryRunStatus, HardwareJobState
from ibm_quantum import (
    IBMBackendDiscovery,
    IBMBackendMetadata,
    IBMQuantumConnection,
    IBMQuantumStatus,
)
from route_dashboard import ui
from route_dashboard.optimization import RouteSummary


class IBMHardwareExecutionUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery = IBMBackendDiscovery(
            connected=True,
            status=IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL,
            message="one mock backend",
            backends=(
                IBMBackendMetadata(
                    name="ibm_mock_backend",
                    num_qubits=127,
                    operational=True,
                    status_message="active",
                    pending_jobs=0,
                    simulator=False,
                ),
            ),
        )
        self.connection = IBMQuantumConnection(
            connected=True,
            message="connected",
            status=IBMQuantumStatus.SERVICE_CONNECTED,
            service=object(),
        )
        self.package = SimpleNamespace(
            problem=SimpleNamespace(route_variable_count=4),
            qaoa_reps=1,
            parameter_source="local_aer_optimized",
            circuit=object(),
        )
        self.dry_run = HardwareDryRunResult(
            ready=True,
            status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
            message="dry run passed",
            backend=self.discovery.backends[0],
            circuit_qubits=5,
            shots=256,
            transpiled_circuit=object(),
            execution_package=self.package,
        )

    def _open_ibm_workspace(self):
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["ibm_backend_discovery"] = self.discovery
        app.radio[0].set_value("IBM Quantum Hardware").run()
        return app

    def test_workspace_load_does_not_connect_discover_or_submit(self) -> None:
        with (
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "connect_ibm_quantum") as connect,
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
        ):
            app = AppTest.from_file("app.py", default_timeout=300).run()
            self.assertTrue(
                any("IBM Quantum Hardware" in option for option in app.radio[0].options)
            )
            app.radio[0].set_value("IBM Quantum Hardware").run()

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        self.assertTrue(
            any(item.value == "IBM Quantum Hardware" for item in app.title)
        )
        self.assertTrue(any("No job submitted yet" in item.value for item in app.info))
        discover.assert_not_called()
        connect.assert_not_called()
        submit.assert_not_called()

    def test_dry_run_is_user_triggered_and_does_not_submit(self) -> None:
        parameters = object()
        local_run = SimpleNamespace(classical=RouteSummary((), 0.0, 0.0, 0.0), quantum=None)
        with (
            patch.object(ui, "connect_ibm_quantum", return_value=self.connection) as connect,
            patch.object(ui, "optimize_demo_qaoa_parameters", return_value=parameters) as optimize,
            patch.object(ui, "dry_run_optimized_qaoa_execution", return_value=self.dry_run) as dry_run,
            patch.object(ui, "optimize_with_objective", return_value=local_run),
            patch.object(ui, "submit_confirmed_hardware_job") as submit,
        ):
            app = self._open_ibm_workspace()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            connect.assert_not_called()
            optimize.assert_not_called()
            dry_run.assert_not_called()
            submit.assert_not_called()

            next(button for button in app.button if button.label == "Run Hardware Dry-Run").click().run()

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        connect.assert_called_once_with()
        optimize.assert_called_once()
        dry_run.assert_called_once_with(
            self.connection,
            "ibm_mock_backend",
            parameters,
            256,
        )
        submit.assert_not_called()
        confirmation_text = " ".join(item.value for item in app.warning)
        self.assertIn("REAL IBM Quantum job", confirmation_text)
        self.assertIn("queue and execution time may vary", confirmation_text)
        submit_button = next(
            button for button in app.button
            if button.label == "Submit ONE REAL IBM Quantum Job"
        )
        self.assertTrue(submit_button.disabled)

    def test_submission_requires_confirmation_and_is_only_user_triggered(self) -> None:
        app = AppTest.from_file("app.py", default_timeout=300).run()
        app.session_state["ibm_backend_discovery"] = self.discovery
        app.session_state["ibm_hardware_dry_run"] = self.dry_run
        app.session_state["ibm_hardware_dry_run_backend"] = "ibm_mock_backend"
        app.session_state["ibm_hardware_dry_run_shots"] = 256
        app.session_state["ibm_hardware_job_state"] = HardwareJobState()
        state_after_submit = HardwareJobState(
            submission_attempted=True,
            job_id="mock-job-from-ui",
            backend_name="ibm_mock_backend",
            shots=256,
            status="SUBMITTED",
            message="REAL IBM QUANTUM HARDWARE job submitted once.",
        )
        with (
            patch.object(ui, "connect_ibm_quantum", return_value=self.connection) as connect,
            patch.object(
                ui,
                "submit_confirmed_hardware_job",
                return_value=state_after_submit,
            ) as submit,
            patch.object(ui, "discover_ibm_backends") as discover,
        ):
            app.radio[0].set_value("IBM Quantum Hardware").run()
            self.assertFalse(app.exception, [str(item.value) for item in app.exception])
            connect.assert_not_called()
            submit.assert_not_called()

            checkbox = next(item for item in app.checkbox if "I understand this submits" in item.label)
            checkbox.set_value(True).run()
            connect.assert_not_called()
            submit.assert_not_called()

            next(
                button for button in app.button
                if button.label == "Submit ONE REAL IBM Quantum Job"
            ).click().run()

        self.assertFalse(app.exception, [str(item.value) for item in app.exception])
        connect.assert_called_once_with()
        submit.assert_called_once()
        self.assertTrue(submit.call_args.kwargs["confirmed"])
        self.assertEqual(app.session_state["ibm_hardware_job_state"].job_id, "mock-job-from-ui")
        discover.assert_not_called()

    def test_connection_exception_text_never_reaches_the_ui(self) -> None:
        secret_marker = "IBM_QUANTUM_API_KEY=mock-secret-marker"
        with (
            patch.object(ui, "connect_ibm_quantum", side_effect=RuntimeError(secret_marker)),
            patch.object(ui, "discover_ibm_backends") as discover,
            patch.object(ui, "optimize_demo_qaoa_parameters") as optimize,
        ):
            app = self._open_ibm_workspace()
            next(button for button in app.button if button.label == "Run Hardware Dry-Run").click().run()

        visible_text = " ".join(
            item.value
            for collection in (app.error, app.warning, app.info, app.caption)
            for item in collection
        )
        self.assertNotIn(secret_marker, visible_text)
        self.assertTrue(any("No hardware job was submitted" in item.value for item in app.error))
        discover.assert_not_called()
        optimize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
