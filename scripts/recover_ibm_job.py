"""Read-only recovery of one historical IBM Quantum Runtime job."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
import importlib.metadata
import json
import math
from pathlib import Path
import sys

from qiskit import QuantumCircuit

from ibm_quantum import connect_ibm_quantum


HISTORICAL_JOB_ID = "db0l4kavog1s73fgvrbg"
EXPECTED_BACKEND = "ibm_fez"
EXPECTED_SHOTS = 256
EXPECTED_QUBITS = 4
RECOVERY_MARKER = "UNVERIFIED_HISTORICAL_RECOVERY"
RECOVERY_DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "ibm_hardware"
    / "unverified_recovery"
)


def _text(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    raw_value = getattr(value, "value", None)
    if isinstance(raw_value, (str, int, float)):
        return str(raw_value)
    return str(value)


def _json_safe(value: object) -> object:
    if isinstance(value, QuantumCircuit):
        return {
            "type": "qiskit.QuantumCircuit",
            "metadata": circuit_metadata(value),
        }
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return {"non_finite_float": str(value)}
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return _json_safe(value.tolist())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return {"unavailable": f"Unsupported input value type: {type(value).__name__}"}


def circuit_metadata(circuit: QuantumCircuit) -> dict[str, object]:
    """Describe the circuit actually returned by Runtime, without rebuilding it."""
    measurements: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []
    active_qubits: set[int] = set()
    for instruction in circuit.data:
        operation = instruction.operation
        qubit_indexes = [circuit.find_bit(bit).index for bit in instruction.qubits]
        clbit_indexes = [circuit.find_bit(bit).index for bit in instruction.clbits]
        active_qubits.update(qubit_indexes)
        operation_record: dict[str, object] = {
            "name": operation.name,
            "qubits": qubit_indexes,
            "clbits": clbit_indexes,
            "parameters": [_json_safe(parameter) for parameter in operation.params],
        }
        operations.append(operation_record)
        if operation.name == "measure":
            measurements.append(
                {
                    "qubit_index": qubit_indexes[0] if qubit_indexes else None,
                    "clbit_index": clbit_indexes[0] if clbit_indexes else None,
                }
            )

    return {
        "num_qubits": circuit.num_qubits,
        "active_qubit_indexes": sorted(active_qubits),
        "num_clbits": circuit.num_clbits,
        "depth": circuit.depth(),
        "parameters": [str(parameter) for parameter in circuit.parameters],
        "classical_registers": [
            {
                "name": register.name,
                "size": len(register),
                "bit_indexes": [circuit.find_bit(bit).index for bit in register],
            }
            for register in circuit.cregs
        ],
        "measurement_wiring": measurements,
        "operations": operations,
    }


def extract_sampler_result(result: object) -> dict[str, object]:
    """Extract counts from all actual Sampler V2 registers, discovering names."""
    result_type = f"{type(result).__module__}.{type(result).__name__}"
    try:
        pub_results = tuple(result)  # type: ignore[arg-type]
    except TypeError:
        return {
            "result_type": result_type,
            "pub_count": None,
            "registers": {},
            "unavailable": ["Result is not iterable as a Sampler primitive result."],
        }

    if len(pub_results) != 1:
        return {
            "result_type": result_type,
            "pub_count": len(pub_results),
            "registers": {},
            "unavailable": [
                "Expected one submitted sampler PUB; register extraction was not guessed."
            ],
        }

    data = getattr(pub_results[0], "data", None)
    if data is None:
        return {
            "result_type": result_type,
            "pub_count": 1,
            "registers": {},
            "unavailable": ["Sampler PUB has no data container."],
        }

    keys = getattr(data, "keys", None)
    try:
        register_names = tuple(keys()) if callable(keys) else ()
    except Exception as error:
        return {
            "result_type": result_type,
            "pub_count": 1,
            "registers": {},
            "unavailable": [
                f"Could not enumerate result registers ({type(error).__name__})."
            ],
        }

    registers: dict[str, object] = {}
    unavailable: list[str] = []
    for register_name in register_names:
        try:
            bit_array = data[register_name]
        except (KeyError, TypeError, AttributeError):
            bit_array = getattr(data, str(register_name), None)
        get_counts = getattr(bit_array, "get_counts", None)
        if not callable(get_counts):
            continue
        try:
            counts = get_counts()
        except Exception as error:
            unavailable.append(
                f"Counts for result register {register_name!s} were unavailable "
                f"({type(error).__name__})."
            )
            continue
        if not isinstance(counts, Mapping):
            unavailable.append(
                f"Result register {register_name!s} returned non-mapping counts."
            )
            continue
        normalized: dict[str, int] = {}
        valid_counts = True
        for bitstring, count in counts.items():
            if (
                not isinstance(bitstring, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
            ):
                valid_counts = False
                break
            normalized[bitstring] = count
        if not valid_counts:
            unavailable.append(
                f"Result register {register_name!s} returned counts with unexpected types."
            )
            continue
        registers[str(register_name)] = {
            "bit_array_type": f"{type(bit_array).__module__}.{type(bit_array).__name__}",
            "num_bits": getattr(bit_array, "num_bits", None),
            "num_shots": getattr(bit_array, "num_shots", None),
            "counts": normalized,
        }

    if not registers and not unavailable:
        unavailable.append(
            "No result register exposing get_counts() was found in the Sampler V2 PUB."
        )
    return {
        "result_type": result_type,
        "pub_count": 1,
        "data_type": f"{type(data).__module__}.{type(data).__name__}",
        "register_names": [str(name) for name in register_names],
        "registers": registers,
        "unavailable": unavailable,
    }


def _read_metadata(job: object, name: str, *, call: bool = False) -> dict[str, object]:
    try:
        value = getattr(job, name)
        if call:
            value = value()
        if value is None:
            return {"available": False, "reason": "Runtime returned no value."}
        return {"available": True, "value": _json_safe(value)}
    except Exception as error:
        return {
            "available": False,
            "reason": f"Runtime metadata access failed ({type(error).__name__}).",
        }


def _backend_name(job: object) -> dict[str, object]:
    try:
        backend = job.backend()
        if backend is None:
            return {"available": False, "reason": "Runtime returned no backend."}
        name = getattr(backend, "name", None)
        if callable(name):
            name = name()
        if not isinstance(name, str):
            return {
                "available": False,
                "reason": "Runtime backend object did not expose a backend name.",
            }
        return {"available": True, "value": name}
    except Exception as error:
        return {
            "available": False,
            "reason": f"Runtime backend access failed ({type(error).__name__}).",
        }


def _circuit_summaries(value: object, path: str = "inputs") -> list[dict[str, object]]:
    if isinstance(value, QuantumCircuit):
        return [{"path": path, **circuit_metadata(value)}]
    if isinstance(value, Mapping):
        return [
            summary
            for key, item in value.items()
            for summary in _circuit_summaries(item, f"{path}.{key}")
        ]
    if isinstance(value, (list, tuple)):
        return [
            summary
            for index, item in enumerate(value)
            for summary in _circuit_summaries(item, f"{path}[{index}]")
        ]
    return []


def _sampler_input_shots(inputs: object) -> list[dict[str, object]]:
    reported: list[dict[str, object]] = []
    if not isinstance(inputs, Mapping):
        return reported

    direct_shots = inputs.get("shots")
    if isinstance(direct_shots, int) and not isinstance(direct_shots, bool):
        reported.append({"source": "job_inputs.shots", "shots": direct_shots})

    pubs = inputs.get("pubs")
    if isinstance(pubs, (list, tuple)):
        for index, pub in enumerate(pubs):
            if not isinstance(pub, (list, tuple)) or len(pub) < 3:
                continue
            shots = pub[2]
            if isinstance(shots, int) and not isinstance(shots, bool):
                reported.append(
                    {"source": f"job_inputs.pubs[{index}][2]", "shots": shots}
                )
    return reported


def build_recovery_record(
    job: object,
    sampler_result: object | None = None,
) -> dict[str, object]:
    """Build explicitly unverified raw recovery metadata for one existing job."""
    job_id = _read_metadata(job, "job_id", call=True)
    status = _read_metadata(job, "status", call=True)
    raw_inputs = _read_raw_metadata(job, "inputs")
    inputs = {
        "available": raw_inputs["available"],
        **(
            {"value": _json_safe(raw_inputs["value"])}
            if raw_inputs["available"]
            else {"reason": raw_inputs["reason"]}
        ),
    }
    backend = _backend_name(job)
    creation_date = _read_metadata(job, "creation_date")
    runtime_metrics = _read_metadata(job, "metrics", call=True)

    input_value = raw_inputs.get("value") if raw_inputs.get("available") else None
    input_circuits = _circuit_summaries(input_value) if input_value is not None else []
    input_shots = _sampler_input_shots(input_value)
    raw_status = status.get("value") if status.get("available") else None
    normalized_status = _text(raw_status).upper() if raw_status is not None else None
    if normalized_status in {"DONE", "COMPLETED"} and sampler_result is None:
        result_data: dict[str, object] = {
            "available": False,
            "reason": "Completed status was reported, but result retrieval was unavailable.",
        }
    elif sampler_result is None:
        result_data = {
            "available": False,
            "reason": "Result was not requested because the job is not reported completed.",
        }
    else:
        result_data = {"available": True, **extract_sampler_result(sampler_result)}

    try:
        versions: dict[str, str | None] = {
            package: importlib.metadata.version(package)
            for package in (
                "qiskit",
                "qiskit-ibm-runtime",
                "qiskit-aer",
                "qiskit-optimization",
            )
        }
    except importlib.metadata.PackageNotFoundError:
        versions = {
            package: _package_version(package)
            for package in (
                "qiskit",
                "qiskit-ibm-runtime",
                "qiskit-aer",
                "qiskit-optimization",
            )
        }
    runtime_metrics_value = (
        runtime_metrics.get("value")
        if runtime_metrics.get("available")
        else None
    )
    runtime_caller = (
        runtime_metrics_value.get("caller")
        if isinstance(runtime_metrics_value, Mapping)
        else None
    )
    program = (
        "Sampler"
        if isinstance(runtime_caller, str) and "sampler" in runtime_caller.casefold()
        else None
    )

    return {
        "record_type": RECOVERY_MARKER,
        "job_id": job_id.get("value") if job_id.get("available") else None,
        "backend": backend.get("value") if backend.get("available") else None,
        "status": _json_safe(raw_status) if raw_status is not None else None,
        "program": program,
        "timestamps": {
            "creation_date": creation_date,
            "runtime_metrics": runtime_metrics,
        },
        "expected_historical_metadata": {
            "job_id": HISTORICAL_JOB_ID,
            "backend": EXPECTED_BACKEND,
            "shots": EXPECTED_SHOTS,
            "circuit_qubits": EXPECTED_QUBITS,
        },
        "reported_input_shots": input_shots,
        "original_inputs": inputs,
        "available_circuit_information": input_circuits,
        "sampler_result": result_data,
        "software_versions": versions,
        "quantumroute_comparison": {
            "adapter": "ibm_qaoa_adapter.py builds and locally transpiles a route-QUBO QAOA circuit; it does not identify historical jobs.",
            "execution": "ibm_execution.py accepts a saved job ID only when it has an execution package; recovery does not create that package or assert a prior submission.",
            "qubo": "The current route QUBO and its signature cannot be attributed to this historical job without retrievable inputs or provenance.",
            "evidence_validator": "This marked recovery record is deliberately not a verified evidence record and must not be passed as hardware evidence.",
            "connection_to_quantumroute_qaoa": "UNVERIFIED",
        },
        "availability": {
            "job_id": job_id,
            "backend": backend,
            "status": status,
            "original_inputs": inputs,
            "creation_date": creation_date,
            "runtime_metrics": runtime_metrics,
        },
    }


def _package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _read_raw_metadata(job: object, name: str, *, call: bool = False) -> dict[str, object]:
    try:
        value = getattr(job, name)
        if call:
            value = value()
        if value is None:
            return {"available": False, "reason": "Runtime returned no value."}
        return {"available": True, "value": value}
    except Exception as error:
        return {
            "available": False,
            "reason": f"Runtime metadata access failed ({type(error).__name__}).",
        }


def recover_existing_job() -> Path:
    """Read one exact historical job and save only an unverified recovery record."""
    connection = connect_ibm_quantum()
    if not connection.connected or connection.service is None:
        raise RuntimeError(connection.message)

    job = connection.service.job(HISTORICAL_JOB_ID)
    job_id = _read_metadata(job, "job_id", call=True)
    if not job_id.get("available") or job_id.get("value") != HISTORICAL_JOB_ID:
        raise RuntimeError("Runtime did not return the exact requested historical job.")

    status = _read_metadata(job, "status", call=True)
    status_value = status.get("value") if status.get("available") else None
    completed = status_value is not None and _text(status_value).upper() in {
        "DONE",
        "COMPLETED",
    }
    sampler_result = None
    result_error_type = None
    if completed:
        try:
            sampler_result = job.result()
        except Exception as error:
            result_error_type = type(error).__name__
    record = build_recovery_record(job, sampler_result)
    if result_error_type is not None:
        record["sampler_result"] = {
            "available": False,
            "reason": f"Runtime result retrieval failed ({result_error_type}).",
        }

    RECOVERY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = RECOVERY_DIRECTORY / f"job_{HISTORICAL_JOB_ID}.json"
    with output_path.open("x", encoding="utf-8", newline="\n") as output_file:
        json.dump(record, output_file, indent=2, ensure_ascii=False, allow_nan=False)
        output_file.write("\n")
    return output_path


def main() -> int:
    try:
        output_path = recover_existing_job()
    except Exception as error:
        print(f"Historical job recovery failed safely ({type(error).__name__}).")
        return 1

    print(f"Unverified historical recovery saved to: {output_path}")
    print(f"Record marker: {RECOVERY_MARKER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
