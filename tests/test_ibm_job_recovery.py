from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from qiskit import QuantumCircuit
from qiskit.primitives.containers import BitArray

from ibm_hardware_evidence import HardwareEvidenceError, validate_hardware_evidence_record
from scripts.recover_ibm_job import (
    HISTORICAL_JOB_ID,
    RECOVERY_MARKER,
    build_recovery_record,
    circuit_metadata,
    extract_sampler_result,
    recover_existing_job,
)


class IBMJobRecoveryTests(unittest.TestCase):
    def test_sampler_v2_counts_are_extracted_from_actual_register_names(self) -> None:
        bits = BitArray.from_samples(
            ["0001", "0010", "0001"],
            num_bits=4,
        )
        result = [SimpleNamespace(data={"readout": bits})]

        extracted = extract_sampler_result(result)

        self.assertEqual(extracted["register_names"], ["readout"])
        self.assertEqual(
            extracted["registers"]["readout"]["counts"],
            {"0001": 2, "0010": 1},
        )
        self.assertEqual(extracted["registers"]["readout"]["num_bits"], 4)
        self.assertEqual(extracted["registers"]["readout"]["num_shots"], 3)

    def test_multiple_pub_results_are_not_guessed_or_combined(self) -> None:
        result = [SimpleNamespace(data={}), SimpleNamespace(data={})]

        extracted = extract_sampler_result(result)

        self.assertEqual(extracted["pub_count"], 2)
        self.assertEqual(extracted["registers"], {})
        self.assertIn("not guessed", extracted["unavailable"][0])

    def test_circuit_metadata_reports_actual_measurement_wiring(self) -> None:
        circuit = QuantumCircuit(2, 2)
        circuit.measure(0, 1)
        circuit.measure(1, 0)

        metadata = circuit_metadata(circuit)

        self.assertEqual(metadata["num_qubits"], 2)
        self.assertEqual(metadata["num_clbits"], 2)
        self.assertEqual(
            metadata["measurement_wiring"],
            [
                {"qubit_index": 0, "clbit_index": 1},
                {"qubit_index": 1, "clbit_index": 0},
            ],
        )

    def test_metadata_record_marks_unavailable_fields_and_never_claims_project_link(self) -> None:
        circuit = QuantumCircuit(2, 2)
        circuit.measure(0, 1)
        circuit.measure(1, 0)
        parameter_values = np.array([0.2, 0.3])
        job = SimpleNamespace(
            job_id=Mock(return_value=HISTORICAL_JOB_ID),
            status=Mock(return_value="DONE"),
            backend=Mock(return_value=SimpleNamespace(name="ibm_fez")),
            creation_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metrics=Mock(return_value={"timestamps": {"completed": "2026-01-02T00:01:00Z"}}),
            inputs={"pubs": [(circuit, parameter_values, 256)]},
        )
        sampler_result = [SimpleNamespace(data={})]

        record = build_recovery_record(job, sampler_result)

        self.assertEqual(record["record_type"], RECOVERY_MARKER)
        self.assertEqual(record["job_id"], HISTORICAL_JOB_ID)
        self.assertEqual(record["availability"]["job_id"]["value"], HISTORICAL_JOB_ID)
        self.assertEqual(record["backend"], "ibm_fez")
        self.assertEqual(
            record["reported_input_shots"],
            [{"source": "job_inputs.pubs[0][2]", "shots": 256}],
        )
        self.assertEqual(
            record["original_inputs"]["value"]["pubs"][0][1],
            [0.2, 0.3],
        )
        self.assertEqual(record["available_circuit_information"][0]["num_qubits"], 2)
        self.assertEqual(
            record["available_circuit_information"][0]["active_qubit_indexes"],
            [0, 1],
        )
        self.assertEqual(
            record["available_circuit_information"][0]["measurement_wiring"],
            [
                {"qubit_index": 0, "clbit_index": 1},
                {"qubit_index": 1, "clbit_index": 0},
            ],
        )
        self.assertEqual(
            record["quantumroute_comparison"]["connection_to_quantumroute_qaoa"],
            "UNVERIFIED",
        )
        self.assertTrue(record["availability"]["runtime_metrics"]["available"])
        self.assertFalse(record["sampler_result"]["registers"])
        with self.assertRaises(HardwareEvidenceError):
            validate_hardware_evidence_record(record)

    def test_recovery_uses_only_the_exact_job_id_and_saves_unverified_record(self) -> None:
        service = SimpleNamespace(
            job=Mock(
                return_value=SimpleNamespace(
                    job_id=Mock(return_value=HISTORICAL_JOB_ID),
                    status=Mock(return_value="DONE"),
                    backend=Mock(return_value=SimpleNamespace(name="ibm_fez")),
                    creation_date=None,
                    metrics=Mock(return_value={"timestamps": {}}),
                    inputs={"pubs": []},
                    result=Mock(return_value=[SimpleNamespace(data={})]),
                )
            ),
            backend=Mock(),
            run=Mock(),
            submit=Mock(),
            jobs=Mock(),
        )
        connection = SimpleNamespace(connected=True, service=service)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch("scripts.recover_ibm_job.connect_ibm_quantum", return_value=connection),
                patch(
                    "scripts.recover_ibm_job.RECOVERY_DIRECTORY",
                    Path(temporary_directory),
                ),
            ):
                output_path = recover_existing_job()
                self.assertTrue(output_path.exists())
                self.assertIn(RECOVERY_MARKER, output_path.read_text(encoding="utf-8"))

        service.job.assert_called_once_with(HISTORICAL_JOB_ID)
        service.job.return_value.result.assert_called_once_with()
        service.job.return_value.backend.assert_called_once_with()
        service.backend.assert_not_called()
        service.run.assert_not_called()
        service.submit.assert_not_called()
        service.jobs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
