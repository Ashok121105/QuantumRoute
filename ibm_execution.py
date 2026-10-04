"""Dry-run validation and explicitly confirmed IBM Quantum hardware execution.

``submit_confirmed_hardware_job`` submits only after explicit confirmation and
never retries automatically. Completed measurement bitstrings are decoded and
the resulting routes are rechecked for feasibility. The IBM Quantum connection
reads credentials from environment variables or a local ignored ``.env`` file;
credentials must never be committed.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import importlib
from math import isfinite
from numbers import Real
from threading import Lock
from typing import Mapping

from qiskit import QuantumCircuit, transpile

from ibm_quantum import IBMBackendMetadata, IBMQuantumConnection, IBMQuantumStatus


DEFAULT_SHOTS = 256
MAX_SHOTS = 1024
MIN_CIRCUIT_QUBITS = 1
MAX_FIRST_DEMO_QUBITS = 5
DEFAULT_JOB_TIMEOUT_SECONDS = 300
JOB_STATUS_POLL_INTERVAL_SECONDS = 10
MAX_CONCURRENT_EXECUTION_REQUESTS = 1
AUTOMATIC_RETRIES_ENABLED = False
EXPLICIT_USER_CONFIRMATION_REQUIRED = True


class HardwareDryRunStatus(str, Enum):
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    CONNECTION_UNAVAILABLE = "connection_unavailable"
    INVALID_REQUEST = "invalid_request"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_NOT_OPERATIONAL = "backend_not_operational"
    CIRCUIT_UNSUPPORTED = "circuit_unsupported"
    OPTIMIZED_PARAMETERS_UNAVAILABLE = "optimized_parameters_unavailable"
    UNAPPROVED_PROBLEM = "unapproved_problem"
    SIMULATOR_SELECTED = "simulator_selected"


@dataclass(frozen=True)
class HardwareJobReport:
    """Credential-free job ID, status, and message summary."""

    job_id: str
    status: str
    message: str


@dataclass(frozen=True)
class HardwareDryRunResult:
    """Validation outcome; never represents a submitted job."""

    ready: bool
    status: HardwareDryRunStatus
    message: str
    backend: IBMBackendMetadata | None
    circuit_qubits: int | None
    shots: int
    confirmation_required: bool = EXPLICIT_USER_CONFIRMATION_REQUIRED
    transpiled_circuit: QuantumCircuit | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    execution_package: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class HardwareRouteDecode:
    raw_counts: tuple[tuple[str, int], ...]
    most_frequent_bitstring: str | None
    valid_route: bool
    message: str
    routes: tuple[object, ...] = ()


@dataclass
class HardwareJobState:
    submission_attempted: bool = False
    job_id: str | None = None
    backend_name: str | None = None
    shots: int | None = None
    status: str = "No job submitted yet"
    submitted_at: str | None = None
    message: str = "No job submitted yet"
    raw_counts: tuple[tuple[str, int], ...] = ()
    most_frequent_bitstring: str | None = None
    route_valid: bool | None = None
    decoded_routes: tuple[object, ...] = ()
    route_message: str | None = None
    measurement_result_received: bool = False


DEMO_ROUTE_VARIABLE_COUNT = 4
_submission_lock = Lock()
_active_job_id: str | None = None
_submission_outcome_unknown = False


def dry_run_hardware_execution(
    connection: IBMQuantumConnection,
    selected_backend_name: str | None,
    circuit: QuantumCircuit,
    shots: int = DEFAULT_SHOTS,
) -> HardwareDryRunResult:
    """Validate an explicit hardware request without submitting a job.

    The selected backend is fetched from the already-connected service solely for
    operational checks and local transpilation. No backend run or job API is used.
    """
    circuit_qubits = getattr(circuit, "num_qubits", None)
    if not connection.connected or connection.service is None:
        return _failed(
            HardwareDryRunStatus.CONNECTION_UNAVAILABLE,
            "IBM Quantum credentials or service connection are unavailable.",
            shots,
            circuit_qubits,
        )
    if not isinstance(selected_backend_name, str) or not selected_backend_name.strip():
        return _failed(
            HardwareDryRunStatus.INVALID_REQUEST,
            "Select an IBM Quantum backend before running the dry-run check.",
            shots,
            circuit_qubits,
        )
    if (
        not isinstance(shots, int)
        or isinstance(shots, bool)
        or shots < 1
        or shots > MAX_SHOTS
    ):
        return _failed(
            HardwareDryRunStatus.INVALID_REQUEST,
            f"Shot count must be between 1 and {MAX_SHOTS}.",
            shots,
            circuit_qubits,
        )
    if not isinstance(circuit, QuantumCircuit) or circuit.parameters:
        return _failed(
            HardwareDryRunStatus.CIRCUIT_UNSUPPORTED,
            "A valid circuit with all parameters bound is required.",
            shots,
            circuit_qubits,
        )
    if (
        not isinstance(circuit_qubits, int)
        or isinstance(circuit_qubits, bool)
        or not MIN_CIRCUIT_QUBITS <= circuit_qubits <= MAX_FIRST_DEMO_QUBITS
    ):
        return _failed(
            HardwareDryRunStatus.INVALID_REQUEST,
            f"The first hardware demo supports {MIN_CIRCUIT_QUBITS}-"
            f"{MAX_FIRST_DEMO_QUBITS} circuit qubits.",
            shots,
            circuit_qubits if isinstance(circuit_qubits, int) else None,
        )

    try:
        backend = connection.service.backend(selected_backend_name.strip())
        if getattr(backend, "name", None) != selected_backend_name.strip():
            raise ValueError("backend selection did not resolve exactly")
        backend_status = backend.status()
    except Exception:
        return _failed(
            HardwareDryRunStatus.BACKEND_UNAVAILABLE,
            "The selected backend is unavailable or could not be verified.",
            shots,
            circuit_qubits,
        )

    if getattr(backend_status, "operational", None) is not True:
        return _failed(
            HardwareDryRunStatus.BACKEND_NOT_OPERATIONAL,
            "The selected backend is not confirmed operational.",
            shots,
            circuit_qubits,
        )

    backend_qubits = getattr(backend, "num_qubits", None)
    if not isinstance(backend_qubits, int) or circuit_qubits > backend_qubits:
        return _failed(
            HardwareDryRunStatus.CIRCUIT_UNSUPPORTED,
            "Circuit qubit requirement does not fit the selected backend.",
            shots,
            circuit_qubits,
        )

    try:
        compiled_circuit = transpile(
            circuit,
            backend=backend,
            optimization_level=1,
        )
    except Exception:
        return _failed(
            HardwareDryRunStatus.CIRCUIT_UNSUPPORTED,
            "The circuit is incompatible with the selected backend and could not be transpiled.",
            shots,
            circuit_qubits,
        )

    compiled_qubits = getattr(compiled_circuit, "num_qubits", None)
    if (
        not isinstance(compiled_qubits, int)
        or isinstance(compiled_qubits, bool)
        or compiled_qubits > backend_qubits
    ):
        return _failed(
            HardwareDryRunStatus.CIRCUIT_UNSUPPORTED,
            "The transpiled circuit exceeds the selected backend qubit count.",
            shots,
            circuit_qubits,
        )

    metadata_status = backend_status
    backend_metadata = IBMBackendMetadata(
        name=backend.name,
        num_qubits=backend_qubits,
        operational=True,
        status_message=_optional_string(metadata_status, "status_msg"),
        pending_jobs=_optional_int(metadata_status, "pending_jobs"),
        simulator=_optional_bool(backend, "simulator"),
        supported_operations=_optional_operations(backend),
    )
    return HardwareDryRunResult(
        ready=True,
        status=HardwareDryRunStatus.READY_FOR_CONFIRMATION,
        message=(
            "Dry run passed. No job was submitted; explicit user confirmation "
            "is required before any future execution."
        ),
        backend=backend_metadata,
        circuit_qubits=circuit_qubits,
        shots=shots,
        transpiled_circuit=compiled_circuit,
    )


def dry_run_optimized_qaoa_execution(
    connection: IBMQuantumConnection,
    selected_backend_name: str | None,
    parameter_result: object,
    shots: int = DEFAULT_SHOTS,
) -> HardwareDryRunResult:
    """Validate genuine local-Aer parameters and the approved four-variable demo."""
    from ibm_qaoa_adapter import (
        MAX_DEMO_QUBITS,
        QAOAExecutionPackage,
        build_demo_route_qubo,
        qaoa_parameter_names,
        route_qubo_fingerprint,
    )
    from ibm_qaoa_parameters import QAOAParameterProvenance

    package = getattr(parameter_result, "execution_package", None)
    names = tuple(getattr(parameter_result, "parameter_names", ()))
    values = tuple(getattr(parameter_result, "parameter_values", ()))
    reps = getattr(parameter_result, "qaoa_reps", None)
    if (
        getattr(parameter_result, "provenance", None)
        is not QAOAParameterProvenance.LOCAL_AER_OPTIMIZED
        or not getattr(parameter_result, "validation_passed", False)
        or not isinstance(package, QAOAExecutionPackage)
        or package.parameter_source != QAOAParameterProvenance.LOCAL_AER_OPTIMIZED.value
    ):
        return _failed(
            HardwareDryRunStatus.OPTIMIZED_PARAMETERS_UNAVAILABLE,
            "Genuine, validated local Aer optimized parameters are required.",
            shots,
            None,
        )
    if (
        package.problem.route_variable_count != DEMO_ROUTE_VARIABLE_COUNT
        or len(package.route_variable_mapping) != DEMO_ROUTE_VARIABLE_COUNT
        or package.logical_qubits != DEMO_ROUTE_VARIABLE_COUNT
        or package.required_qubits > MAX_DEMO_QUBITS
        or reps != package.qaoa_reps
    ):
        return _failed(
            HardwareDryRunStatus.UNAPPROVED_PROBLEM,
            "Only the approved four-variable QuantumRoute Demo Mode problem is allowed.",
            shots,
            package.required_qubits,
        )
    if (
        not isinstance(reps, int)
        or names != qaoa_parameter_names(reps)
        or len(values) != 2 * reps
        or tuple(name for name, _ in package.parameter_bindings) != names
        or tuple(value for _, value in package.parameter_bindings) != values
        or any(
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not isfinite(float(value))
            for value in values
        )
        or len(package.circuit.parameters) != 0
        or package.validation.unresolved_parameters != 0
        or not package.validation.local_transpilation_passed
    ):
        return _failed(
            HardwareDryRunStatus.OPTIMIZED_PARAMETERS_UNAVAILABLE,
            "The optimized QAOA parameters or bound circuit failed validation.",
            shots,
            package.required_qubits,
        )

    expected_signature = route_qubo_fingerprint(build_demo_route_qubo())
    problem_signature = getattr(
        getattr(parameter_result, "problem", None),
        "problem_signature",
        None,
    )
    if (
        package.problem_signature != expected_signature
        or problem_signature != expected_signature
    ):
        return _failed(
            HardwareDryRunStatus.UNAPPROVED_PROBLEM,
            "Optimized parameters do not match the approved Demo Mode QUBO.",
            shots,
            package.required_qubits,
        )

    result = dry_run_hardware_execution(
        connection,
        selected_backend_name,
        package.circuit,
        shots,
    )
    if not result.ready:
        return result
    if result.backend is None or result.backend.simulator is not False:
        return _failed(
            HardwareDryRunStatus.SIMULATOR_SELECTED,
            "Select an operational real IBM quantum hardware backend, not a simulator.",
            shots,
            package.required_qubits,
        )
    return replace(result, execution_package=package)


def submit_confirmed_hardware_job(
    connection: IBMQuantumConnection,
    dry_run: HardwareDryRunResult,
    *,
    confirmed: bool,
    state: HardwareJobState | None = None,
) -> HardwareJobState:
    """Submit one real Runtime sampler job after explicit confirmation only.

    Submission is never retried. An ambiguous submission failure permanently
    blocks another request for this process rather than risking a duplicate job.
    """
    global _active_job_id, _submission_outcome_unknown

    state = state or HardwareJobState()
    if not confirmed:
        state.message = "Explicit confirmation is required. No job was submitted."
        return state
    if state.submission_attempted or state.job_id is not None:
        if state.status == "SUBMISSION_UNKNOWN":
            state.message = (
                "Submission outcome remains unknown. No retry was attempted; "
                "check IBM Quantum job history before any further action."
            )
        else:
            state.message = "A submission was already attempted for this session; no duplicate job was created."
        return state
    if not dry_run.ready or dry_run.transpiled_circuit is None:
        state.message = "A successful hardware dry-run is required. No job was submitted."
        return state
    if (
        not connection.connected
        or connection.service is None
        or dry_run.backend is None
        or dry_run.backend.simulator is not False
        or dry_run.execution_package is None
        or dry_run.execution_package.parameter_source != "local_aer_optimized"
        or dry_run.execution_package.problem.route_variable_count != DEMO_ROUTE_VARIABLE_COUNT
        or not 1 <= dry_run.shots <= MAX_SHOTS
    ):
        state.message = "The confirmed execution request failed safety validation. No job was submitted."
        return state

    if not _submission_lock.acquire(blocking=False):
        state.message = "Another hardware submission is in progress. No job was submitted."
        return state

    try:
        if _active_job_id is not None or _submission_outcome_unknown:
            state.message = "A hardware job is already active or has an unknown submission outcome."
            return state

        state.submission_attempted = True
        state.backend_name = dry_run.backend.name
        state.shots = dry_run.shots
        state.status = "SUBMISSION_ATTEMPTED"
        state.message = "Submission requested; checking backend and submitting one job."
        _submission_outcome_unknown = True

        try:
            backend = connection.service.backend(dry_run.backend.name)
            if getattr(backend, "name", None) != dry_run.backend.name:
                raise ValueError("selected backend did not resolve exactly")
            if getattr(backend.status(), "operational", None) is not True:
                raise ValueError("selected backend is not operational")
            if getattr(backend, "simulator", None) is not False:
                raise ValueError("selected backend is not real hardware")

            runtime = importlib.import_module("qiskit_ibm_runtime")
            sampler = runtime.SamplerV2(mode=backend)
            job = sampler.run([dry_run.transpiled_circuit], shots=dry_run.shots)
            job_id_value = job.job_id()
            if not isinstance(job_id_value, str) or not job_id_value.strip():
                raise RuntimeError("Runtime did not provide a job ID")
        except Exception:
            state.status = "SUBMISSION_UNKNOWN"
            state.message = (
                "Submission outcome could not be verified. No retry was attempted; "
                "check the IBM Quantum job dashboard before taking further action."
            )
            return state

        state.job_id = job_id_value
        state.status = "SUBMITTED"
        state.submitted_at = _safe_creation_time(job)
        state.message = "REAL IBM QUANTUM HARDWARE job submitted once. Refresh status to inspect it."
        _active_job_id = job_id_value
        _submission_outcome_unknown = False
        return state
    finally:
        _submission_lock.release()


def refresh_hardware_job_status(
    connection: IBMQuantumConnection,
    state: HardwareJobState,
    execution_package: object,
) -> HardwareJobState:
    """Refresh only the saved job ID and retrieve its result only after DONE."""
    global _active_job_id

    if not state.job_id:
        state.message = "No job submitted yet."
        return state
    if not connection.connected or connection.service is None:
        state.message = "IBM Quantum connection is unavailable; the saved job ID is unchanged."
        return state

    try:
        job = connection.service.job(state.job_id)
        raw_status = job.status()
        status = _status_text(raw_status)
        state.status = status
        if status.upper() == "DONE" and not state.measurement_result_received:
            runtime_result = job.result()
            raw_counts = _sampler_counts(runtime_result, execution_package)
            decoded = decode_hardware_counts(raw_counts, execution_package)
            state.raw_counts = decoded.raw_counts
            state.most_frequent_bitstring = decoded.most_frequent_bitstring
            state.route_valid = decoded.valid_route
            state.decoded_routes = decoded.routes
            state.route_message = decoded.message
            state.measurement_result_received = True
            state.message = decoded.message
        elif status.upper() in {"ERROR", "CANCELLED"}:
            state.message = f"IBM job reached terminal status: {status}."
        else:
            state.message = f"Existing IBM job status: {status}."
    except Exception:
        state.message = "Could not refresh the existing IBM job. No new job was submitted."
        return state

    if state.status.upper() in {"DONE", "ERROR", "CANCELLED"}:
        with _submission_lock:
            if _active_job_id == state.job_id:
                _active_job_id = None
    return state


def decode_hardware_counts(
    counts: Mapping[str, int],
    execution_package: object,
) -> HardwareRouteDecode:
    """Decode the most frequent hardware bitstring through the adapter mapping."""
    from ibm_qaoa_adapter import build_demo_route_qubo, route_qubo_fingerprint
    from quantum_route_optimisation.qaoa import decode_route_selection
    from route_dashboard.demo_mode import build_hackathon_demo_scenario
    from route_dashboard.objectives import ObjectiveConfig, ObjectiveName, scenario_for_objective
    from route_dashboard.optimization import validate_route_set

    raw_counts = tuple(sorted((str(bitstring), int(count)) for bitstring, count in counts.items()))
    if not raw_counts or any(count < 0 for _, count in raw_counts):
        return HardwareRouteDecode(raw_counts, None, False, "Hardware result did not produce a valid route: no measurement counts were returned.")

    most_frequent = sorted(raw_counts, key=lambda item: (-item[1], item[0]))[0][0]
    package_signature = getattr(execution_package, "problem_signature", None)
    formulation = build_demo_route_qubo()
    if package_signature != route_qubo_fingerprint(formulation):
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: QUBO identity did not match.",
        )

    circuit = getattr(execution_package, "circuit", None)
    mapping = tuple(getattr(execution_package, "route_variable_mapping", ()))
    clbit_count = getattr(circuit, "num_clbits", None)
    variable_count = getattr(
        getattr(execution_package, "problem", None),
        "route_variable_count",
        None,
    )
    if (
        clbit_count is None
        or variable_count != DEMO_ROUTE_VARIABLE_COUNT
        or len(mapping) != DEMO_ROUTE_VARIABLE_COUNT
        or tuple(item.variable_index for item in mapping) != tuple(range(variable_count))
        or any(not 0 <= item.measured_bit_index < clbit_count for item in mapping)
    ):
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: adapter measurement mapping is invalid.",
        )

    normalized_bitstring = most_frequent.replace(" ", "")
    if len(normalized_bitstring) != clbit_count or any(bit not in "01" for bit in normalized_bitstring):
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: measurement bitstring width is invalid.",
        )

    little_endian_bits = normalized_bitstring[::-1]
    bit_vector = [
        int(little_endian_bits[item.measured_bit_index])
        for item in sorted(mapping, key=lambda entry: entry.variable_index)
    ]
    scenario = build_hackathon_demo_scenario()
    scoring_scenario = scenario_for_objective(
        scenario,
        ObjectiveConfig.for_name(ObjectiveName.COST),
    )
    try:
        decoded = decode_route_selection(
            bit_vector,
            formulation,
            scoring_scenario.vehicles,
            scoring_scenario.deliveries,
            scoring_scenario.travel,
            scoring_scenario.cost_weights,
        )
        physical_routes = validate_route_set(decoded.routes, scenario)
    except Exception:
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: measured assignment failed feasibility validation.",
        )
    return HardwareRouteDecode(
        raw_counts,
        most_frequent,
        True,
        "Hardware result decoded to a route that passed existing feasibility validation.",
        tuple(physical_routes),
    )


def _failed(
    status: HardwareDryRunStatus,
    message: str,
    shots: int,
    circuit_qubits: int | None,
) -> HardwareDryRunResult:
    return HardwareDryRunResult(
        ready=False,
        status=status,
        message=message,
        backend=None,
        circuit_qubits=circuit_qubits,
        shots=shots,
    )


def _safe_creation_time(job: object) -> str:
    try:
        value = getattr(job, "creation_date", None)
        if callable(value):
            value = value()
        if value is not None:
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


def _status_text(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(getattr(value, "value", value))


def _sampler_counts(result: object, execution_package: object) -> Mapping[str, int]:
    circuit = getattr(execution_package, "circuit", None)
    for register in getattr(circuit, "cregs", ()):
        try:
            bit_array = getattr(result[0].data, register.name)
            get_counts = getattr(bit_array, "get_counts", None)
            if callable(get_counts):
                counts = get_counts()
                if isinstance(counts, Mapping):
                    return counts
        except (AttributeError, IndexError, TypeError):
            continue
    raise ValueError("Runtime result contained no sampler measurement register")


def _optional_string(value: object, attribute: str) -> str | None:
    result = getattr(value, attribute, None)
    return str(result) if result is not None else None


def _optional_int(value: object, attribute: str) -> int | None:
    result = getattr(value, attribute, None)
    return result if isinstance(result, int) and not isinstance(result, bool) else None


def _optional_bool(value: object, attribute: str) -> bool | None:
    result = getattr(value, attribute, None)
    return result if isinstance(result, bool) else None


def _optional_operations(value: object) -> tuple[str, ...] | None:
    result = getattr(value, "operation_names", None)
    if isinstance(result, (list, tuple, set)):
        return tuple(str(operation) for operation in result)
    return None