from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qiskit import QuantumCircuit

from ibm_quantum import IBMQuantumConnection, IBMQuantumStatus
import ibm_execution


class IBMHardwareDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = SimpleNamespace(
            name="ibm_mock_backend",
            num_qubits=5,
            simulator=False,
            operation_names=("cx", "measure", "rz", "sx"),
            status=Mock(
                return_value=SimpleNamespace(
                    operational=True,
                    status_msg="active",
                    pending_jobs=1,
                )
            ),
            run=Mock(),
            submit=Mock(),
        )
        self.service = SimpleNamespace(
            backend=Mock(return_value=self.backend),
            run=Mock(),
            submit=Mock(),
            job=Mock(),
            jobs=Mock(),
        )
        self.connection = IBMQuantumConnection(
            connected=True,
            message="connected",
            status=IBMQuantumStatus.SERVICE_CONNECTED,
            service=self.service,
        )

    def test_valid_dry_run_checks_backend_and_transpiles_without_job_calls(self) -> None:
        circuit = QuantumCircuit(3)
        transpiled = QuantumCircuit(3)
        with patch("ibm_execution.transpile", return_value=transpiled) as transpile:
            result = ibm_execution.dry_run_hardware_execution(
                self.connection,
                "ibm_mock_backend",
                circuit,
                shots=512,
            )

        self.assertTrue(result.ready)
        self.assertEqual(result.status, ibm_execution.HardwareDryRunStatus.READY_FOR_CONFIRMATION)
        self.assertEqual(result.circuit_qubits, 3)
        self.assertEqual(result.shots, 512)
        self.assertTrue(result.confirmation_required)
        self.assertIs(result.transpiled_circuit, transpiled)
        self.assertEqual(result.backend.name, "ibm_mock_backend")
        self.assertEqual(result.backend.supported_operations, ("cx", "measure", "rz", "sx"))
        self.service.backend.assert_called_once_with("ibm_mock_backend")
        self.backend.status.assert_called_once_with()
        transpile.assert_called_once_with(
            circuit,
            backend=self.backend,
            optimization_level=1,
        )
        self.backend.run.assert_not_called()
        self.backend.submit.assert_not_called()
        self.service.run.assert_not_called()
        self.service.submit.assert_not_called()
        self.service.job.assert_not_called()
        self.service.jobs.assert_not_called()

    def test_missing_credentials_prevent_backend_lookup(self) -> None:
        connection = IBMQuantumConnection(
            connected=False,
            message="missing credentials",
            status=IBMQuantumStatus.NO_CREDENTIALS,
        )
        with patch("ibm_execution.transpile") as transpile:
            result = ibm_execution.dry_run_hardware_execution(
                connection,
                "ibm_mock_backend",
                QuantumCircuit(3),
            )

        self.assertFalse(result.ready)
        self.assertEqual(
            result.status,
            ibm_execution.HardwareDryRunStatus.CONNECTION_UNAVAILABLE,
        )
        self.assertIsNone(result.backend)
        transpile.assert_not_called()

    def test_backend_selection_is_required(self) -> None:
        with patch("ibm_execution.transpile") as transpile:
            result = ibm_execution.dry_run_hardware_execution(
                self.connection,
                None,
                QuantumCircuit(3),
            )

        self.assertEqual(result.status, ibm_execution.HardwareDryRunStatus.INVALID_REQUEST)
        self.service.backend.assert_not_called()
        transpile.assert_not_called()

    def test_unavailable_backend_is_rejected_without_leaking_service_error(self) -> None:
        self.service.backend.side_effect = RuntimeError("private provider response")
        result = ibm_execution.dry_run_hardware_execution(
            self.connection,
            "removed_backend",
            QuantumCircuit(3),
        )

        self.assertEqual(
            result.status,
            ibm_execution.HardwareDryRunStatus.BACKEND_UNAVAILABLE,
        )
        self.assertNotIn("private provider response", result.message)

    def test_non_operational_backend_is_rejected_before_transpilation(self) -> None:
        self.backend.status.return_value = SimpleNamespace(operational=False)
        with patch("ibm_execution.transpile") as transpile:
            result = ibm_execution.dry_run_hardware_execution(
                self.connection,
                "ibm_mock_backend",
                QuantumCircuit(3),
            )

        self.assertEqual(
            result.status,
            ibm_execution.HardwareDryRunStatus.BACKEND_NOT_OPERATIONAL,
        )
        transpile.assert_not_called()

    def test_shot_and_circuit_size_limits_are_enforced(self) -> None:
        with patch("ibm_execution.transpile") as transpile:
            too_many_shots = ibm_execution.dry_run_hardware_execution(
                self.connection,
                "ibm_mock_backend",
                QuantumCircuit(3),
                shots=ibm_execution.MAX_SHOTS + 1,
            )
            too_many_qubits = ibm_execution.dry_run_hardware_execution(
                self.connection,
                "ibm_mock_backend",
                QuantumCircuit(ibm_execution.MAX_FIRST_DEMO_QUBITS + 1),
            )

        self.assertEqual(too_many_shots.status, ibm_execution.HardwareDryRunStatus.INVALID_REQUEST)
        self.assertEqual(too_many_qubits.status, ibm_execution.HardwareDryRunStatus.INVALID_REQUEST)
        self.service.backend.assert_not_called()
        transpile.assert_not_called()

    def test_transpilation_failure_is_reported_without_job_calls(self) -> None:
        with patch("ibm_execution.transpile", side_effect=ValueError("unsupported gate")):
            result = ibm_execution.dry_run_hardware_execution(
                self.connection,
                "ibm_mock_backend",
                QuantumCircuit(3),
            )

        self.assertEqual(
            result.status,
            ibm_execution.HardwareDryRunStatus.CIRCUIT_UNSUPPORTED,
        )
        self.assertNotIn("unsupported gate", result.message)
        self.backend.run.assert_not_called()
        self.service.submit.assert_not_called()

    def test_future_execution_policy_uses_bounded_safe_defaults(self) -> None:
        self.assertEqual(ibm_execution.DEFAULT_SHOTS, 256)
        self.assertEqual(ibm_execution.MAX_SHOTS, 1024)
        self.assertEqual(ibm_execution.MIN_CIRCUIT_QUBITS, 1)
        self.assertEqual(ibm_execution.MAX_FIRST_DEMO_QUBITS, 5)
        self.assertEqual(ibm_execution.DEFAULT_JOB_TIMEOUT_SECONDS, 300)
        self.assertEqual(ibm_execution.JOB_STATUS_POLL_INTERVAL_SECONDS, 10)
        self.assertEqual(ibm_execution.MAX_CONCURRENT_EXECUTION_REQUESTS, 1)
        self.assertFalse(ibm_execution.AUTOMATIC_RETRIES_ENABLED)
        self.assertTrue(ibm_execution.EXPLICIT_USER_CONFIRMATION_REQUIRED)


if __name__ == "__main__":
    unittest.main()