from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ibm_execution import (
    HardwareDryRunResult,
    HardwareDryRunStatus,
    HardwareJobState,
    decode_hardware_counts,
)
from ibm_hardware_evidence import (
    HardwareEvidenceError,
    REAL_EXECUTION_TYPE,
    build_hardware_evidence_record,
    render_hardware_evidence_report,
    validate_hardware_evidence_record,
    write_hardware_evidence,
)
from ibm_hardware_provenance import build_execution_manifest, write_pre_submission_manifest
from ibm_qaoa_adapter import prepare_demo_qaoa_execution_package
from route_dashboard.demo_mode import build_hackathon_demo_scenario


class IBMHardwareEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_directory = tempfile.TemporaryDirectory(
            prefix="quantumroute_manifest_test_"
        )
        caller_package = prepare_demo_qaoa_execution_package(
            {"beta_0": 0.1, "gamma_0": 0.2}
        )
        cls.package = replace(
            caller_package,
            parameter_source="local_aer_optimized",
            optimizer_metadata={
                "method": "test-only synthetic optimizer metadata",
                "configuration": {"reps": 1, "maxiter": 1, "shots": 1, "seed": 1},
                "initial_point": [0.0, 0.0],
                "history": [],
            },
        )
        cls.scenario = cls.package.scenario or build_hackathon_demo_scenario()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.manifest_directory.cleanup()

    def synthetic_completed_state(self) -> HardwareJobState:
        """Synthetic test-only state; writers below use temporary directories."""
        shots = 256
        decoded = decode_hardware_counts({"1010": shots}, self.package)
        confirmation_timestamp = datetime(2026, 10, 9, tzinfo=timezone.utc).isoformat()
        dry_run = HardwareDryRunResult(
            ready=True,
            status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
            message="synthetic test-only dry run",
            backend=SimpleNamespace(name="ibm_fez", simulator=False),
            circuit_qubits=self.package.circuit.num_qubits,
            shots=shots,
            transpiled_circuit=self.package.circuit,
            execution_package=self.package,
        )
        manifest = build_execution_manifest(
            dry_run,
            confirmation_timestamp=confirmation_timestamp,
        )
        manifest_path, manifest_hash = write_pre_submission_manifest(
            manifest,
            Path(self.manifest_directory.name),
        )
        return HardwareJobState(
            submission_attempted=True,
            is_real_hardware_execution=True,
            job_id="evidence-schema-job-001",
            backend_name="ibm_fez",
            shots=shots,
            status="DONE",
            submitted_at=datetime(2026, 10, 9, tzinfo=timezone.utc).isoformat(),
            completed_at=datetime(2026, 10, 9, 0, 1, tzinfo=timezone.utc).isoformat(),
            message=decoded.message,
            raw_counts=decoded.raw_counts,
            most_frequent_bitstring=decoded.most_frequent_bitstring,
            selected_solution_bitstring=decoded.selected_solution_bitstring,
            route_valid=decoded.valid_route,
            decoded_routes=decoded.routes,
            route_message=decoded.message,
            measurement_result_received=True,
            execution_package=self.package,
            run_id=str(manifest["run_id"]),
            manifest_path=str(manifest_path),
            manifest_sha256=manifest_hash,
            execution_manifest=manifest,
            confirmation_timestamp=confirmation_timestamp,
            actual_backend_name="ibm_fez",
            actual_backend_is_simulator=False,
            submitted_circuit=self.package.circuit,
            result_register_counts={
                "meas": {
                    "counts": {"1010": shots},
                    "num_bits": 4,
                    "num_shots": shots,
                }
            },
            shots_returned=shots,
            qubo_objective_value=decoded.qubo_objective_value,
        )

    def test_valid_real_execution_record_has_required_fields(self) -> None:
        record = build_hardware_evidence_record(
            self.synthetic_completed_state(),
            self.scenario,
        )

        validate_hardware_evidence_record(record)
        self.assertEqual(record["execution_type"], REAL_EXECUTION_TYPE)
        self.assertEqual(record["problem_statement"], "VNQFF-08")
        self.assertEqual(record["measurement_counts"], {"1010": 256})
        self.assertEqual(record["decoded_solution"]["selected_bitstring"], "1010")
        self.assertEqual(record["run_id"], record["pre_submission_manifest"]["run_id"])
        self.assertEqual(
            record["pre_submission_manifest_sha256"],
            record["execution_metadata"]["manifest_sha256"],
        )
        self.assertTrue(record["route_validation"]["passed"])
        self.assertTrue(record["decoded_solution"]["routes"])
        self.assertEqual(
            record["qubo_objective_value"],
            self.package.formulation.problem.objective.evaluate(
                [
                    int("1010"[::-1][binding.measured_bit_index])
                    for binding in sorted(
                        self.package.route_variable_mapping,
                        key=lambda item: item.variable_index,
                    )
                ]
            ),
        )
        self.assertIsNotNone(record["objective_value"])

    def test_missing_job_id_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.job_id = None

        with self.assertRaises(HardwareEvidenceError):
            build_hardware_evidence_record(state, self.scenario)

    def test_missing_backend_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.backend_name = None

        with self.assertRaises(HardwareEvidenceError):
            build_hardware_evidence_record(state, self.scenario)

    def test_missing_actual_backend_type_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.actual_backend_is_simulator = None

        with self.assertRaisesRegex(HardwareEvidenceError, "backend type"):
            build_hardware_evidence_record(state, self.scenario)

    def test_missing_measurement_counts_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.raw_counts = ()

        with self.assertRaises(HardwareEvidenceError):
            build_hardware_evidence_record(state, self.scenario)

    def test_missing_manifest_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.execution_manifest = None

        with self.assertRaisesRegex(HardwareEvidenceError, "manifest"):
            build_hardware_evidence_record(state, self.scenario)

    def test_missing_qubo_objective_is_rejected_for_validated_candidate(self) -> None:
        record = build_hardware_evidence_record(
            self.synthetic_completed_state(),
            self.scenario,
        )
        record["qubo_objective_value"] = None

        with self.assertRaisesRegex(HardwareEvidenceError, "QUBO objective"):
            validate_hardware_evidence_record(record)

    def test_manifest_binds_exact_qubo_circuit_parameters_and_layout(self) -> None:
        state = self.synthetic_completed_state()
        manifest = state.execution_manifest

        self.assertEqual(
            manifest["qubo"]["signature"],
            self.package.problem_signature,
        )
        self.assertEqual(
            manifest["qaoa"]["parameters"],
            [
                {"name": "beta_0", "value": 0.1},
                {"name": "gamma_0", "value": 0.2},
            ],
        )
        self.assertTrue(
            manifest["circuits"]["logical_bound_qaoa"]["qpy_base64"]
        )
        self.assertIn(
            "final_index_layout",
            manifest["circuits"]["selected_backend_transpiled_submitted"][
                "transpilation_layout"
            ],
        )
        self.assertEqual(
            manifest["qaoa"]["logical_qubit_to_variable_mapping"][0]["variable_name"],
            "route_0000",
        )

    def test_mock_synthetic_state_is_rejected(self) -> None:
        state = self.synthetic_completed_state()
        state.is_real_hardware_execution = False

        with tempfile.TemporaryDirectory(prefix="quantumroute_synthetic_test_") as tmp:
            evidence_directory = Path(tmp)
            with patch(
                "ibm_hardware_evidence.EVIDENCE_DIRECTORY",
                evidence_directory,
            ):
                with self.assertRaises(HardwareEvidenceError):
                    write_hardware_evidence(state, self.scenario)
            self.assertEqual(list(evidence_directory.iterdir()), [])

    def test_mock_or_synthetic_record_is_rejected(self) -> None:
        record = build_hardware_evidence_record(
            self.synthetic_completed_state(),
            self.scenario,
        )
        record["execution_metadata"]["data_origin"] = "MOCK/SYNTHETIC"

        with self.assertRaises(HardwareEvidenceError):
            validate_hardware_evidence_record(record)

    def test_malformed_evidence_is_rejected(self) -> None:
        record = build_hardware_evidence_record(
            self.synthetic_completed_state(),
            self.scenario,
        )
        record["measurement_counts"] = {"not-a-bitstring": 256}

        with self.assertRaises(HardwareEvidenceError):
            validate_hardware_evidence_record(record)

    def test_report_and_json_pair_are_generated(self) -> None:
        state = self.synthetic_completed_state()
        with tempfile.TemporaryDirectory(prefix="quantumroute_synthetic_test_") as tmp:
            json_path, report_path = write_hardware_evidence(
                state,
                self.scenario,
                Path(tmp),
            )

            saved = json.loads(json_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
            self.assertEqual(saved["job_id"], state.job_id)
            self.assertIn("REAL IBM Quantum hardware execution", report)
            self.assertIn("ibm_fez", report)
            self.assertIn("evidence-schema-job-001", report)
            self.assertIn("Feasibility validation: **PASSED**", report)
            self.assertIn("Objective", report)
            self.assertIn(
                "REAL IBM Quantum hardware execution",
                render_hardware_evidence_report(saved),
            )

    def test_non_real_failure_cannot_be_written_as_evidence(self) -> None:
        state = self.synthetic_completed_state()
        state.status = "ERROR"
        state.error_info = "Synthetic status for failure-path test."

        with tempfile.TemporaryDirectory(prefix="quantumroute_synthetic_test_") as tmp:
            with self.assertRaises(HardwareEvidenceError):
                write_hardware_evidence(state, self.scenario, Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
