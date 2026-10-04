import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ibm_quantum


class IBMQuantumConnectionTests(unittest.TestCase):
    @patch("ibm_quantum.load_dotenv")
    def test_missing_api_key_returns_safe_status(self, load_dotenv) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("ibm_quantum.importlib.import_module") as import_module,
        ):
            result = ibm_quantum.connect_ibm_quantum()

        self.assertFalse(result.connected)
        self.assertEqual(result.status, ibm_quantum.IBMQuantumStatus.NO_CREDENTIALS)
        self.assertIn("IBM_QUANTUM_API_KEY", result.message)
        self.assertIsNone(result.service)
        import_module.assert_not_called()
        load_dotenv.assert_called_once_with(
            dotenv_path=ibm_quantum._PROJECT_ENV_FILE,
            override=False,
        )

    @patch("ibm_quantum.load_dotenv")
    def test_missing_runtime_package_returns_install_guidance(self, load_dotenv) -> None:
        with (
            patch.dict(
                os.environ,
                {ibm_quantum.API_KEY_ENV_VAR: "mock-token-not-a-credential"},
                clear=True,
            ),
            patch(
                "ibm_quantum.importlib.import_module",
                side_effect=ModuleNotFoundError(
                    "package unavailable", name="qiskit_ibm_runtime"
                ),
            ),
        ):
            result = ibm_quantum.connect_ibm_quantum()

        self.assertFalse(result.connected)
        self.assertEqual(result.status, ibm_quantum.IBMQuantumStatus.RUNTIME_UNAVAILABLE)
        self.assertIn("qiskit-ibm-runtime", result.message)
        self.assertIsNone(result.service)

    @patch("ibm_quantum.load_dotenv")
    def test_initializes_current_service_without_submitting_a_job(self, load_dotenv) -> None:
        service = object()
        service_constructor = Mock(return_value=service)
        runtime = SimpleNamespace(QiskitRuntimeService=service_constructor)
        mock_token = "mock-token-not-a-credential"
        with (
            patch.dict(
                os.environ,
                {ibm_quantum.API_KEY_ENV_VAR: mock_token},
                clear=True,
            ),
            patch(
                "ibm_quantum.importlib.import_module",
                return_value=runtime,
            ),
        ):
            result = ibm_quantum.connect_ibm_quantum()

        self.assertTrue(result.connected)
        self.assertEqual(result.status, ibm_quantum.IBMQuantumStatus.SERVICE_CONNECTED)
        self.assertIs(result.service, service)
        self.assertIn("no quantum job was submitted", result.message)
        self.assertNotIn(mock_token, repr(result))
        service_constructor.assert_called_once_with(
            channel="ibm_quantum_platform",
            token=mock_token,
        )
        load_dotenv.assert_called_once()

    @patch("ibm_quantum.load_dotenv")
    def test_credential_errors_are_sanitized(self, load_dotenv) -> None:
        mock_token = "mock-token-not-a-credential"
        runtime = SimpleNamespace(
            QiskitRuntimeService=Mock(
                side_effect=RuntimeError(f"rejected token: {mock_token}")
            )
        )
        with (
            patch.dict(
                os.environ,
                {ibm_quantum.API_KEY_ENV_VAR: mock_token},
                clear=True,
            ),
            patch(
                "ibm_quantum.importlib.import_module",
                return_value=runtime,
            ),
        ):
            result = ibm_quantum.connect_ibm_quantum()

        self.assertFalse(result.connected)
        self.assertEqual(result.status, ibm_quantum.IBMQuantumStatus.SERVICE_ERROR)
        self.assertIsNone(result.service)
        self.assertIn("Check the configured credentials", result.message)
        self.assertNotIn(mock_token, result.message)
        self.assertNotIn(mock_token, repr(result))

    def test_backend_discovery_returns_safe_metadata_without_job_calls(self) -> None:
        backend_status = SimpleNamespace(
            operational=True,
            status_msg="active",
            pending_jobs=2,
        )
        backend = SimpleNamespace(
            name="ibm_example",
            num_qubits=127,
            simulator=False,
            status=Mock(return_value=backend_status),
            run=Mock(),
            job=Mock(),
        )
        service = SimpleNamespace(
            backends=Mock(return_value=[backend]),
            run=Mock(),
            job=Mock(),
            jobs=Mock(),
        )
        connection = ibm_quantum.IBMQuantumConnection(
            connected=True,
            message="connected",
            status=ibm_quantum.IBMQuantumStatus.SERVICE_CONNECTED,
            service=service,
        )

        result = ibm_quantum.discover_ibm_backends(connection)

        self.assertTrue(result.connected)
        self.assertEqual(
            result.status,
            ibm_quantum.IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL,
        )
        self.assertEqual(
            result.backends,
            (
                ibm_quantum.IBMBackendMetadata(
                    name="ibm_example",
                    num_qubits=127,
                    operational=True,
                    status_message="active",
                    pending_jobs=2,
                    simulator=False,
                ),
            ),
        )
        self.assertIn("connection successful", ibm_quantum.ibm_quantum_status_report(result))
        service.backends.assert_called_once_with()
        backend.status.assert_called_once_with()
        backend.run.assert_not_called()
        backend.job.assert_not_called()
        service.run.assert_not_called()
        service.job.assert_not_called()
        service.jobs.assert_not_called()

    def test_empty_backend_list_is_reported_distinctly(self) -> None:
        service = SimpleNamespace(backends=Mock(return_value=[]))
        connection = ibm_quantum.IBMQuantumConnection(
            connected=True,
            message="connected",
            status=ibm_quantum.IBMQuantumStatus.SERVICE_CONNECTED,
            service=service,
        )

        result = ibm_quantum.discover_ibm_backends(connection)

        self.assertEqual(
            result.status,
            ibm_quantum.IBMQuantumStatus.NO_ACCESSIBLE_BACKENDS,
        )
        self.assertIn("no accessible backends", ibm_quantum.ibm_quantum_status_report(result))

    def test_backend_discovery_reports_invalid_credentials_safely(self) -> None:
        class IBMNotAuthorizedError(RuntimeError):
            pass

        service = SimpleNamespace(
            backends=Mock(
                side_effect=IBMNotAuthorizedError("credential details must not escape")
            )
        )
        connection = ibm_quantum.IBMQuantumConnection(
            connected=True,
            message="connected",
            status=ibm_quantum.IBMQuantumStatus.SERVICE_CONNECTED,
            service=service,
        )

        result = ibm_quantum.discover_ibm_backends(connection)

        self.assertEqual(
            result.status,
            ibm_quantum.IBMQuantumStatus.INVALID_CREDENTIALS,
        )
        report = ibm_quantum.ibm_quantum_status_report(result)
        self.assertIn("credentials are invalid", report)
        self.assertNotIn("credential details", report)

    def test_service_connection_error_is_reported_without_exception_text(self) -> None:
        connection = ibm_quantum.IBMQuantumConnection(
            connected=False,
            message="sanitized error",
            status=ibm_quantum.IBMQuantumStatus.SERVICE_ERROR,
        )

        result = ibm_quantum.discover_ibm_backends(connection)

        self.assertFalse(result.connected)
        self.assertEqual(result.status, ibm_quantum.IBMQuantumStatus.SERVICE_ERROR)
        self.assertIn("service error", ibm_quantum.ibm_quantum_status_report(result))


if __name__ == "__main__":
    unittest.main()