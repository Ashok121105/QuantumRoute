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

from ibm_quantum import IBMBackendMetadata, IBMQuantumConnection


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
    qubo_objective_value: float | None = None
    selected_solution_bitstring: str | None = None


@dataclass
class HardwareJobState:
    submission_attempted: bool = False
    is_real_hardware_execution: bool = False
    job_id: str | None = None
    backend_name: str | None = None
    shots: int | None = None
    status: str = "No job submitted yet"
    submitted_at: str | None = None
    completed_at: str | None = None
    message: str = "No job submitted yet"
    error_info: str | None = None
    raw_counts: tuple[tuple[str, int], ...] = ()
    most_frequent_bitstring: str | None = None
    route_valid: bool | None = None
    decoded_routes: tuple[object, ...] = ()
    route_message: str | None = None
    measurement_result_received: bool = False
    execution_package: object | None = field(default=None, repr=False, compare=False)
    run_id: str | None = None
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    execution_manifest: Mapping[str, object] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    confirmation_timestamp: str | None = None
    actual_backend_name: str | None = None
    submitted_circuit: QuantumCircuit | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    result_register_counts: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    shots_returned: int | None = None
    qubo_objective_value: float | None = None
    progress_record_path: str | None = None
    selected_solution_bitstring: str | None = None
    actual_backend_is_simulator: bool | None = None
    runtime_submitted_at: str | None = None


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

        sampler_invoked = False
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
            from ibm_hardware_provenance import (
                build_execution_manifest,
                write_pre_submission_manifest,
            )

            confirmation_timestamp = datetime.now(timezone.utc).isoformat()
            manifest = build_execution_manifest(
                dry_run,
                confirmation_timestamp=confirmation_timestamp,
            )
            manifest_path, manifest_sha256 = write_pre_submission_manifest(manifest)
            state.run_id = str(manifest["run_id"])
            state.manifest_path = str(manifest_path)
            state.manifest_sha256 = manifest_sha256
            state.execution_manifest = manifest
            state.confirmation_timestamp = confirmation_timestamp
            state.backend_name = dry_run.backend.name
            state.shots = dry_run.shots
            state.execution_package = dry_run.execution_package
            state.submitted_circuit = dry_run.transpiled_circuit
            state.submission_attempted = True
            state.status = "SUBMISSION_ATTEMPTED"
            state.message = "Confirmed request recorded; submitting one job."
            state.submitted_at = datetime.now(timezone.utc).isoformat()
            from ibm_hardware_provenance import write_execution_progress

            state.progress_record_path = str(write_execution_progress(state))
            _submission_outcome_unknown = True
            sampler_invoked = True
            job = sampler.run([dry_run.transpiled_circuit], shots=dry_run.shots)
            job_id_value = job.job_id()
            if not isinstance(job_id_value, str) or not job_id_value.strip():
                raise RuntimeError("Runtime did not provide a job ID")
        except Exception as error:
            if not sampler_invoked:
                state.status = "PROVENANCE_FAILED"
                state.message = (
                    "Execution provenance could not be saved completely; "
                    "no hardware job was submitted."
                )
                state.error_info = f"Pre-submission provenance failed ({type(error).__name__})."
                _persist_execution_progress(state)
                return state
            state.status = "SUBMISSION_UNKNOWN"
            state.message = (
                "Submission outcome could not be verified. No retry was attempted; "
                "check the IBM Quantum job dashboard before taking further action."
            )
            _persist_execution_progress(state)
            return state
        state.job_id = job_id_value
        state.is_real_hardware_execution = True
        state.status = "SUBMITTED"
        state.message = "REAL IBM QUANTUM HARDWARE job submitted once. Refresh status to inspect it."
        _active_job_id = job_id_value
        _submission_outcome_unknown = False
        try:
            from ibm_hardware_provenance import write_execution_progress

            state.progress_record_path = str(write_execution_progress(state))
        except (OSError, ValueError):
            state.error_info = (
                "Job ID is known, but the durable execution progress record could not be updated."
            )
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
        if status.upper() == "DONE":
            if not state.measurement_result_received:
                runtime_result = job.result()
                register_counts = _sampler_register_counts(runtime_result)
                expected_registers = tuple(
                    register.name
                    for register in getattr(state.submitted_circuit, "cregs", ())
                )
                if (
                    len(expected_registers) != 1
                    or set(register_counts) != set(expected_registers)
                ):
                    raise ValueError(
                        "Runtime result registers do not match submitted circuit registers"
                    )
                register_name = expected_registers[0]
                register_data = register_counts[register_name]
                raw_counts = register_data["counts"]
                if not isinstance(raw_counts, Mapping):
                    raise ValueError("Runtime measurement counts were unavailable")
                state.result_register_counts = register_counts
                state.shots_returned = sum(raw_counts.values())
                decoded = decode_hardware_counts(raw_counts, execution_package)
                state.completed_at = _safe_completion_time(job)
                state.raw_counts = decoded.raw_counts
                state.most_frequent_bitstring = decoded.most_frequent_bitstring
                state.selected_solution_bitstring = decoded.selected_solution_bitstring
                state.route_valid = decoded.valid_route
                state.decoded_routes = decoded.routes
                state.qubo_objective_value = decoded.qubo_objective_value
                state.route_message = decoded.message
                state.measurement_result_received = True
                state.message = decoded.message

            limitations = []
            if state.route_valid is not True:
                limitations.append(
                    state.route_message or "No measured route passed feasibility validation."
                )
            if state.completed_at is None:
                limitations.append(
                    "Runtime completion timestamp is unavailable; evidence is incomplete."
                )
            try:
                actual_backend = job.backend()
                actual_name = getattr(actual_backend, "name", None)
                if callable(actual_name):
                    actual_name = actual_name()
                if isinstance(actual_name, str):
                    state.actual_backend_name = actual_name
                else:
                    state.actual_backend_name = None
                    limitations.append(
                        "Runtime did not provide actual backend metadata; evidence is incomplete."
                    )
                simulator = getattr(actual_backend, "simulator", None)
                state.actual_backend_is_simulator = (
                    simulator if isinstance(simulator, bool) else None
                )
                if state.actual_backend_is_simulator is None:
                    limitations.append(
                        "Runtime did not provide actual backend type metadata; "
                        "evidence is incomplete."
                    )
                elif state.actual_backend_is_simulator:
                    limitations.append(
                        "Retrieved job backend is a simulator; it cannot be recorded as hardware evidence."
                    )
                if (
                    state.actual_backend_name is not None
                    and state.actual_backend_name != state.backend_name
                ):
                    limitations.append(
                        "Retrieved job backend does not match the requested backend."
                    )
                creation_date = getattr(job, "creation_date", None)
                if isinstance(creation_date, datetime):
                    state.runtime_submitted_at = creation_date.isoformat()
                elif isinstance(creation_date, str):
                    state.runtime_submitted_at = creation_date
            except Exception:
                state.actual_backend_name = None
                state.actual_backend_is_simulator = None
                limitations.append(
                    "Runtime did not provide actual backend metadata; evidence is incomplete."
                )
            state.error_info = " ".join(limitations) or None
            state.message = state.error_info or state.message
        elif status.upper() in {"ERROR", "CANCELLED"}:
            state.message = f"IBM job reached terminal status: {status}."
            state.error_info = state.message
        else:
            state.message = f"Existing IBM job status: {status}."
    except Exception:
        state.message = "Could not refresh the existing IBM job. No new job was submitted."
        state.error_info = state.message
        _persist_execution_progress(state)
        return state

    _persist_execution_progress(state)
    if state.status.upper() in {"DONE", "ERROR", "CANCELLED"}:
        with _submission_lock:
            if _active_job_id == state.job_id:
                _active_job_id = None
    return state


def _persist_execution_progress(state: HardwareJobState) -> None:
    if not state.run_id or not state.manifest_path:
        return
    try:
        from ibm_hardware_provenance import write_execution_progress

        state.progress_record_path = str(write_execution_progress(state))
    except (OSError, ValueError):
        state.error_info = (
            "Execution state changed, but its durable progress record could not be updated."
        )


def decode_hardware_counts(
    counts: Mapping[str, int],
    execution_package: object,
) -> HardwareRouteDecode:
    """Select the lowest-QUBO-energy feasible candidate from observed outcomes."""
    from ibm_qaoa_adapter import route_qubo_fingerprint
    from quantum_route_optimisation.qaoa import decode_route_selection
    from route_dashboard.optimization import validate_route_set

    if not counts or any(
        not isinstance(bitstring, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for bitstring, count in counts.items()
    ):
        return HardwareRouteDecode(
            (),
            None,
            False,
            "Hardware result did not produce a valid route: measurement counts were unavailable or malformed.",
        )
    if sum(counts.values()) < 1:
        return HardwareRouteDecode(
            (),
            None,
            False,
            "Hardware result did not produce a valid route: no measurement shots were returned.",
        )
    raw_counts = tuple(sorted(counts.items()))
    if not raw_counts:
        return HardwareRouteDecode(raw_counts, None, False, "Hardware result did not produce a valid route: no measurement counts were returned.")

    most_frequent = sorted(raw_counts, key=lambda item: (-item[1], item[0]))[0][0]
    package_signature = getattr(execution_package, "problem_signature", None)
    formulation = getattr(execution_package, "formulation", None)
    scenario = getattr(execution_package, "scenario", None)
    objective_scenario = getattr(execution_package, "objective_scenario", None)
    if (
        formulation is None
        or scenario is None
        or objective_scenario is None
        or package_signature != route_qubo_fingerprint(formulation)
    ):
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: QUBO identity did not match.",
        )

    circuit = getattr(execution_package, "circuit", None)
    measured_circuit = getattr(execution_package, "measured_circuit", None)
    mapping = tuple(getattr(execution_package, "route_variable_mapping", ()))
    clbit_count = getattr(circuit, "num_clbits", None)
    variable_count = getattr(
        getattr(execution_package, "problem", None),
        "route_variable_count",
        None,
    )
    if (
        clbit_count is None
        or measured_circuit is None
        or measured_circuit.num_clbits != clbit_count
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
    actual_measurements = {
        (
        measured_circuit.find_bit(item.qubits[0]).index,
        measured_circuit.find_bit(item.clbits[0]).index,
        )
        for item in measured_circuit.data
        if item.operation.name == "measure"
    }
    if actual_measurements != {
        (item.logical_qubit_index, item.measured_bit_index) for item in mapping
    }:
        return HardwareRouteDecode(
        raw_counts,
        most_frequent,
        False,
        "Hardware result did not produce a valid route: logical measurement wiring did not match the execution package.",
        )

    candidates: list[tuple[float, int, str, tuple[object, ...]]] = []
    for bitstring, frequency in raw_counts:
        if frequency == 0:
            continue
        normalized_bitstring = bitstring.replace(" ", "")
        if (
            len(normalized_bitstring) != clbit_count
            or any(bit not in "01" for bit in normalized_bitstring)
        ):
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
        try:
            qubo_objective_value = float(
                formulation.problem.objective.evaluate(bit_vector)
            )
            if not isfinite(qubo_objective_value):
                raise ValueError("QUBO objective is not finite")
        except (ArithmeticError, TypeError, ValueError):
            return HardwareRouteDecode(
                raw_counts,
                most_frequent,
                False,
                "Hardware result did not produce a valid route: original QUBO objective could not be evaluated.",
            )
        try:
            decoded = decode_route_selection(
                bit_vector,
                formulation,
                objective_scenario.vehicles,
                objective_scenario.deliveries,
                objective_scenario.travel,
                objective_scenario.cost_weights,
            )
            physical_routes = validate_route_set(decoded.routes, scenario)
        except ValueError:
            continue
        candidates.append(
            (qubo_objective_value, frequency, normalized_bitstring, tuple(physical_routes))
        )

    if not candidates:
        return HardwareRouteDecode(
            raw_counts,
            most_frequent,
            False,
            "Hardware result did not produce a valid route: no observed assignment passed feasibility validation.",
            (),
            None,
        )
    candidates.sort(key=lambda item: (item[0], -item[1], item[2]))
    objective, _, selected_bitstring, routes = candidates[0]
    return HardwareRouteDecode(
        raw_counts,
        most_frequent,
        True,
        "Lowest-QUBO-energy observed candidate decoded to a route that passed existing feasibility validation.",
        routes,
        objective,
        selected_bitstring,
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


def _safe_completion_time(job: object) -> str | None:
    try:
        metrics = getattr(job, "metrics", None)
        if callable(metrics):
            timestamps = metrics().get("timestamps", {})
            if isinstance(timestamps, Mapping):
                finished = timestamps.get("finished")
                if isinstance(finished, str):
                    return finished
    except Exception:
        pass
    for attribute in ("time_completed", "completion_date", "end_time"):
        try:
            value = getattr(job, attribute, None)
            if callable(value):
                value = value()
            if value is not None:
                if hasattr(value, "isoformat"):
                    return value.isoformat()
                if isinstance(value, str):
                    return value
        except Exception:
            continue
    return None


def _status_text(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(getattr(value, "value", value))


def _sampler_register_counts(result: object) -> dict[str, dict[str, object]]:
    try:
        pub_results = tuple(result)
    except TypeError as error:
        raise ValueError("Runtime result is not an iterable Sampler V2 result") from error
    if len(pub_results) != 1:
        raise ValueError("Runtime result must contain exactly one Sampler V2 PUB")
    data = getattr(pub_results[0], "data", None)
    keys = getattr(data, "keys", None)
    if not callable(keys):
        raise ValueError("Runtime Sampler V2 result has no enumerable data registers")
    registers: dict[str, dict[str, object]] = {}
    for raw_name in keys():
        name = str(raw_name)
        bit_array = data[raw_name]
        get_counts = getattr(bit_array, "get_counts", None)
        if not callable(get_counts):
            continue
        counts = get_counts()
        if not isinstance(counts, Mapping):
            raise ValueError(f"Runtime result register {name} has invalid counts")
        registers[name] = {
            "counts": dict(counts),
            "num_bits": getattr(bit_array, "num_bits", None),
            "num_shots": getattr(bit_array, "num_shots", None),
        }
    if not registers:
        raise ValueError("Runtime result contained no Sampler measurement registers")
    return registers


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