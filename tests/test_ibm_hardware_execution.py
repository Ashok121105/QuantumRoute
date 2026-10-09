from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qiskit import QuantumCircuit
from qiskit.circuit import Parameter

import ibm_execution
from ibm_qaoa_adapter import prepare_demo_qaoa_execution_package
from ibm_execution import (
    HardwareDryRunResult,
    HardwareDryRunStatus,
    HardwareJobState,
    decode_hardware_counts,
    dry_run_hardware_execution,
    dry_run_optimized_qaoa_execution,
    refresh_hardware_job_status,
    submit_confirmed_hardware_job,
)
from ibm_quantum import IBMBackendMetadata, IBMQuantumConnection, IBMQuantumStatus
from ibm_qaoa_parameters import optimize_demo_qaoa_parameters
from quantum_route_optimisation.qaoa import QAOAConfig


class IBMHardwareExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameter_result = optimize_demo_qaoa_parameters(
            QAOAConfig(reps=1, maxiter=8, shots=256, seed=42)
        )

    def setUp(self) -> None:
        ibm_execution._active_job_id = None
        ibm_execution._submission_outcome_unknown = False
        manifest_directory = tempfile.TemporaryDirectory()
        self.addCleanup(manifest_directory.cleanup)
        manifest_patch = patch(
            "ibm_hardware_provenance.MANIFEST_DIRECTORY",
            Path(manifest_directory.name),
        )
        manifest_patch.start()
        self.addCleanup(manifest_patch.stop)
        progress_patch = patch(
            "ibm_hardware_provenance.PROGRESS_DIRECTORY",
            Path(manifest_directory.name) / "progress",
        )
        progress_patch.start()
        self.addCleanup(progress_patch.stop)
        self.backend = SimpleNamespace(
            name="ibm_mock_backend",
            num_qubits=127,
            simulator=False,
            status=Mock(return_value=SimpleNamespace(operational=True)),
            run=Mock(),
            submit=Mock(),
        )
        self.service = SimpleNamespace(
            backend=Mock(return_value=self.backend),
            job=Mock(),
            run=Mock(),
            submit=Mock(),
            jobs=Mock(),
        )
        self.connection = IBMQuantumConnection(
            connected=True,
            message="connected",
            status=IBMQuantumStatus.SERVICE_CONNECTED,
            service=self.service,
        )

    def _dry_run(self, *, shots: int = 256) -> HardwareDryRunResult:
        package = self.parameter_result.execution_package
        return HardwareDryRunResult(
            ready=True,
            status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
            message="dry run passed",
            backend=IBMBackendMetadata(
                name="ibm_mock_backend",
                num_qubits=127,
                operational=True,
                status_message="active",
                pending_jobs=0,
                simulator=False,
            ),
            circuit_qubits=package.circuit.num_qubits,
            shots=shots,
            transpiled_circuit=package.circuit,
            execution_package=package,
        )

    def _runtime_mock(self, sampler: Mock):
        return SimpleNamespace(SamplerV2=Mock(return_value=sampler))

    def test_no_submission_without_explicit_confirmation(self) -> None:
        sampler = Mock()
        with patch(
            "ibm_execution.importlib.import_module",
            return_value=self._runtime_mock(sampler),
        ) as load_runtime:
            state = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(),
                confirmed=False,
            )

        self.assertFalse(state.submission_attempted)
        self.assertIsNone(state.job_id)
        self.assertIn("No job was submitted", state.message)
        load_runtime.assert_not_called()
        self.service.backend.assert_not_called()
        sampler.run.assert_not_called()

    def test_exactly_one_submission_after_confirmation(self) -> None:
        job = Mock()
        job.job_id.return_value = "mock-job-123"
        sampler = Mock()
        def check_manifest_precedes_submission(*args, **kwargs):
            manifests = list(Path(self._manifest_directory()).glob("*.json"))
            self.assertEqual(len(manifests), 1)
            saved_manifest = manifests[0].read_text(encoding="utf-8")
            self.assertIn("PRE_SUBMISSION_CONFIRMED", saved_manifest)
            progress_records = list(
                (Path(self._manifest_directory()) / "progress").glob("*.json")
            )
            self.assertEqual(len(progress_records), 1)
            progress = json.loads(progress_records[0].read_text(encoding="utf-8"))
            self.assertEqual(progress["record_type"], "IBM_HARDWARE_EXECUTION_PROGRESS_NOT_VERIFIED")
            self.assertIsNone(progress["job_id"])
            sampler.run.return_value = job
            return job

        sampler.run.side_effect = check_manifest_precedes_submission
        runtime = self._runtime_mock(sampler)
        state = HardwareJobState()
        with patch("ibm_execution.importlib.import_module", return_value=runtime):
            submitted = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(shots=256),
                confirmed=True,
                state=state,
            )
            duplicate = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(shots=256),
                confirmed=True,
                state=state,
            )

        self.assertEqual(submitted.job_id, "mock-job-123")
        self.assertEqual(submitted.status, "SUBMITTED")
        self.assertEqual(submitted.backend_name, "ibm_mock_backend")
        self.assertEqual(submitted.shots, 256)
        self.assertIsNotNone(submitted.run_id)
        self.assertTrue(Path(submitted.manifest_path).exists())
        self.assertTrue(Path(submitted.progress_record_path).exists())
        saved_progress = json.loads(
            Path(submitted.progress_record_path).read_text(encoding="utf-8")
        )
        self.assertEqual(saved_progress["job_id"], "mock-job-123")
        self.assertEqual(saved_progress["status"], "SUBMITTED")
        self.assertTrue(submitted.execution_manifest["user_confirmation"]["confirmed"])
        self.assertIs(duplicate, state)
        sampler.run.assert_called_once_with([self._dry_run().transpiled_circuit], shots=256)
        self.assertEqual(ibm_execution._active_job_id, "mock-job-123")

    def _manifest_directory(self) -> str:
        from ibm_hardware_provenance import MANIFEST_DIRECTORY

        return str(MANIFEST_DIRECTORY)

    def test_missing_execution_provenance_prevents_submission(self) -> None:
        package = replace(self.parameter_result.execution_package, scenario=None)
        dry_run = replace(self._dry_run(), execution_package=package)
        sampler = Mock()
        with patch(
            "ibm_execution.importlib.import_module",
            return_value=self._runtime_mock(sampler),
        ):
            state = submit_confirmed_hardware_job(
                self.connection,
                dry_run,
                confirmed=True,
            )

        self.assertEqual(state.status, "PROVENANCE_FAILED")
        self.assertFalse(state.submission_attempted)
        self.assertIsNone(state.run_id)
        sampler.run.assert_not_called()

    def test_duplicate_execution_is_blocked_while_a_job_is_active(self) -> None:
        ibm_execution._active_job_id = "already-running"
        sampler = Mock()
        with patch("ibm_execution.importlib.import_module", return_value=self._runtime_mock(sampler)) as load_runtime:
            state = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(),
                confirmed=True,
            )

        self.assertIsNone(state.job_id)
        self.assertFalse(state.submission_attempted)
        self.assertIn("already active", state.message)
        load_runtime.assert_not_called()
        sampler.run.assert_not_called()

    def test_submission_failure_is_not_retried(self) -> None:
        sampler = Mock()
        sampler.run.side_effect = RuntimeError("private provider error")
        runtime = self._runtime_mock(sampler)
        state = HardwareJobState()
        with patch("ibm_execution.importlib.import_module", return_value=runtime):
            first = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(),
                confirmed=True,
                state=state,
            )
            second = submit_confirmed_hardware_job(
                self.connection,
                self._dry_run(),
                confirmed=True,
                state=state,
            )

        self.assertEqual(first.status, "SUBMISSION_UNKNOWN")
        self.assertIn("No retry was attempted", first.message)
        self.assertNotIn("private provider error", first.message)
        self.assertTrue(second.submission_attempted)
        sampler.run.assert_called_once()
        self.assertTrue(ibm_execution._submission_outcome_unknown)

    def test_refresh_checks_only_saved_job_id(self) -> None:
        job = Mock()
        job.status.return_value = "RUNNING"
        self.service.job.return_value = job
        state = HardwareJobState(
            submission_attempted=True,
            job_id="saved-job-id",
            backend_name="ibm_mock_backend",
            status="SUBMITTED",
        )
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )

        refreshed = refresh_hardware_job_status(self.connection, state, package)

        self.assertEqual(refreshed.status, "RUNNING")
        self.service.job.assert_called_once_with("saved-job-id")
        job.result.assert_not_called()
        self.service.backend.assert_not_called()
        self.service.run.assert_not_called()
        self.service.submit.assert_not_called()

    def test_refresh_captures_counts_from_the_existing_completed_job(self) -> None:
        job = Mock()
        job.status.return_value = "DONE"
        from qiskit.primitives.containers import BitArray

        job.result.return_value = [
            SimpleNamespace(data={"meas": BitArray.from_samples(["1010"] * 10, num_bits=4)})
        ]
        job.metrics.return_value = {
            "timestamps": {"finished": "2026-10-09T12:00:00Z"}
        }
        job.backend.return_value = self.backend
        self.service.job.return_value = job
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        state = HardwareJobState(
            job_id="completed-job",
            backend_name="ibm_mock_backend",
            submitted_circuit=package.circuit,
            run_id="progress-run",
            manifest_path="pre-submission-manifest.json",
            manifest_sha256="manifest-hash",
            execution_manifest={},
        )

        refreshed = refresh_hardware_job_status(self.connection, state, package)

        self.assertEqual(refreshed.raw_counts, (("1010", 10),))
        self.assertEqual(refreshed.most_frequent_bitstring, "1010")
        self.assertTrue(refreshed.route_valid)
        self.assertEqual(refreshed.shots_returned, 10)
        self.assertIn("meas", refreshed.result_register_counts)
        self.assertEqual(refreshed.actual_backend_name, "ibm_mock_backend")
        self.assertFalse(refreshed.actual_backend_is_simulator)
        self.assertIsNotNone(refreshed.qubo_objective_value)
        progress = json.loads(
            Path(refreshed.progress_record_path).read_text(encoding="utf-8")
        )
        self.assertEqual(progress["job_id"], "completed-job")
        self.assertEqual(progress["returned_shots"], 10)
        self.assertEqual(progress["measurement_counts"], {"1010": 10})
        self.assertEqual(
            progress["qubo_objective_value"],
            refreshed.qubo_objective_value,
        )
        self.assertTrue(progress["route_validation"]["passed"])
        self.service.job.assert_called_once_with("completed-job")
        self.service.backend.assert_not_called()

    def test_refresh_retries_missing_actual_backend_metadata_without_refetching_result(self) -> None:
        job = Mock()
        job.status.return_value = "DONE"
        from qiskit.primitives.containers import BitArray

        job.result.return_value = [
            SimpleNamespace(data={"meas": BitArray.from_samples(["1010"], num_bits=4)})
        ]
        job.metrics.return_value = {
            "timestamps": {"finished": "2026-10-09T12:00:00Z"}
        }
        job.backend.side_effect = [
            RuntimeError("synthetic metadata failure"),
            self.backend,
        ]
        self.service.job.return_value = job
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        state = HardwareJobState(
            job_id="completed-job",
            backend_name="ibm_mock_backend",
            submitted_circuit=package.circuit,
        )

        first = refresh_hardware_job_status(self.connection, state, package)
        second = refresh_hardware_job_status(self.connection, first, package)

        self.assertTrue(second.measurement_result_received)
        self.assertEqual(second.actual_backend_name, "ibm_mock_backend")
        self.assertFalse(second.actual_backend_is_simulator)
        job.result.assert_called_once()
        self.assertEqual(job.backend.call_count, 2)

    def test_shot_limit_is_enforced(self) -> None:
        result = dry_run_hardware_execution(
            self.connection,
            "ibm_mock_backend",
            QuantumCircuit(3),
            shots=ibm_execution.MAX_SHOTS + 1,
        )

        self.assertEqual(result.status, HardwareDryRunStatus.INVALID_REQUEST)
        self.service.backend.assert_not_called()

    def test_unsupported_problem_size_is_rejected_by_optimized_dry_run(self) -> None:
        parameter_result = self.parameter_result
        package = replace(
            parameter_result.execution_package,
            problem=replace(parameter_result.execution_package.problem, route_variable_count=5),
        )
        modified_result = replace(parameter_result, execution_package=package)

        result = dry_run_optimized_qaoa_execution(
            self.connection,
            "ibm_mock_backend",
            modified_result,
        )

        self.assertEqual(result.status, HardwareDryRunStatus.UNAPPROVED_PROBLEM)
        self.service.backend.assert_not_called()

    def test_non_operational_backend_is_rejected(self) -> None:
        self.backend.status.return_value = SimpleNamespace(operational=False)
        result = dry_run_hardware_execution(
            self.connection,
            "ibm_mock_backend",
            QuantumCircuit(3),
        )

        self.assertEqual(result.status, HardwareDryRunStatus.BACKEND_NOT_OPERATIONAL)

    def test_missing_credentials_are_rejected(self) -> None:
        disconnected = IBMQuantumConnection(
            connected=False,
            message="credentials unavailable",
            status=IBMQuantumStatus.NO_CREDENTIALS,
        )
        result = dry_run_hardware_execution(
            disconnected,
            "ibm_mock_backend",
            QuantumCircuit(3),
        )

        self.assertEqual(result.status, HardwareDryRunStatus.CONNECTION_UNAVAILABLE)
        self.service.backend.assert_not_called()

    def test_invalid_and_unresolved_circuits_are_rejected(self) -> None:
        invalid = dry_run_hardware_execution(
            self.connection,
            "ibm_mock_backend",
            object(),
        )
        parameterized = QuantumCircuit(3)
        parameterized.ry(Parameter("unbound"), 0)
        unresolved = dry_run_hardware_execution(
            self.connection,
            "ibm_mock_backend",
            parameterized,
        )

        self.assertEqual(invalid.status, HardwareDryRunStatus.CIRCUIT_UNSUPPORTED)
        self.assertEqual(unresolved.status, HardwareDryRunStatus.CIRCUIT_UNSUPPORTED)
        self.service.backend.assert_not_called()

    def test_caller_supplied_parameters_are_not_hardware_eligible(self) -> None:
        package = replace(
            self.parameter_result.execution_package,
            parameter_source="caller_supplied",
        )
        parameter_result = replace(self.parameter_result, execution_package=package)

        result = dry_run_optimized_qaoa_execution(
            self.connection,
            "ibm_mock_backend",
            parameter_result,
        )

        self.assertEqual(
            result.status,
            HardwareDryRunStatus.OPTIMIZED_PARAMETERS_UNAVAILABLE,
        )
        self.service.backend.assert_not_called()

    def test_demo_package_is_transpiled_for_selected_operational_hardware(self) -> None:
        transpiled = QuantumCircuit(5, 4)
        with patch("ibm_execution.dry_run_hardware_execution") as base_dry_run:
            base_dry_run.return_value = HardwareDryRunResult(
                ready=True,
                status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
                message="dry run passed",
                backend=IBMBackendMetadata(
                    name="ibm_mock_backend",
                    num_qubits=127,
                    operational=True,
                    status_message="active",
                    pending_jobs=0,
                    simulator=False,
                ),
                circuit_qubits=5,
                shots=256,
                transpiled_circuit=transpiled,
            )
            result = dry_run_optimized_qaoa_execution(
                self.connection,
                "ibm_mock_backend",
                self.parameter_result,
            )

        self.assertTrue(result.ready, result.message)
        self.assertIs(result.transpiled_circuit, transpiled)
        self.assertIs(result.execution_package, self.parameter_result.execution_package)
        base_dry_run.assert_called_once()

    def test_hardware_decode_selects_best_feasible_observed_candidate(self) -> None:
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        valid = decode_hardware_counts({"1010": 11, "0000": 3}, package)
        less_frequent_valid = decode_hardware_counts(
            {"0000": 13, "1010": 1},
            package,
        )

        self.assertTrue(valid.valid_route, valid.message)
        self.assertEqual(valid.most_frequent_bitstring, "1010")
        self.assertEqual(valid.selected_solution_bitstring, "1010")
        self.assertEqual(len(valid.routes), 2)
        self.assertTrue(less_frequent_valid.valid_route, less_frequent_valid.message)
        self.assertEqual(less_frequent_valid.most_frequent_bitstring, "0000")
        self.assertEqual(less_frequent_valid.selected_solution_bitstring, "1010")
        self.assertEqual(less_frequent_valid.raw_counts, (("0000", 13), ("1010", 1)))
        self.assertEqual(len(less_frequent_valid.routes), 2)

    def test_hardware_decode_uses_recorded_non_identity_measurement_mapping(self) -> None:
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        measured_circuit = QuantumCircuit(4, 4)
        measured_circuit.measure(0, 1)
        measured_circuit.measure(1, 0)
        measured_circuit.measure(2, 2)
        measured_circuit.measure(3, 3)
        mapping = tuple(
            replace(
                binding,
                measured_bit_index=(1 if binding.variable_index == 0 else 0)
                if binding.variable_index < 2
                else binding.measured_bit_index,
            )
            for binding in package.route_variable_mapping
        )
        mapped_package = replace(
            package,
            measured_circuit=measured_circuit,
            route_variable_mapping=mapping,
        )

        decoded = decode_hardware_counts({"1001": 256}, mapped_package)

        self.assertTrue(decoded.valid_route, decoded.message)
        self.assertEqual(len(decoded.routes), 2)

    def test_result_distribution_is_never_replaced_by_local_answers(self) -> None:
        package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        measured = {"0000": 17}

        decoded = decode_hardware_counts(measured, package)

        self.assertEqual(decoded.raw_counts, (("0000", 17),))
        self.assertFalse(decoded.valid_route)
        self.assertEqual(decoded.most_frequent_bitstring, "0000")
        self.assertIsNone(decoded.selected_solution_bitstring)
        self.assertEqual(decoded.routes, ())


if __name__ == "__main__":
    unittest.main()
