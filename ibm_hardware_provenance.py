"""Build and persist provenance manifests for explicitly confirmed IBM runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import base64
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from qiskit import QuantumCircuit, qpy
from route_dashboard.scenario import ScenarioProblem


PROJECT_NAME = "QuantumRoute"
PROBLEM_STATEMENT = "VNQFF-08"
MANIFEST_DIRECTORY = (
    Path(__file__).resolve().parent / "artifacts" / "ibm_hardware" / "manifests"
)
PROGRESS_DIRECTORY = (
    Path(__file__).resolve().parent / "artifacts" / "ibm_hardware" / "in_progress"
)
MANIFEST_SCHEMA = "quantumroute-ibm-execution-manifest-v1"
PROGRESS_SCHEMA = "quantumroute-ibm-execution-progress-v1"


class ProvenanceError(ValueError):
    """Raised when a complete pre-submission manifest cannot be created."""


def json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProvenanceError("Manifest cannot contain non-finite numbers.")
        return value
    if isinstance(value, QuantumCircuit):
        return circuit_record(value)
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        return json_safe(value.item())
    raise ProvenanceError(
        f"Unsupported provenance value type: {type(value).__module__}.{type(value).__name__}"
    )


def circuit_record(circuit: QuantumCircuit) -> dict[str, object]:
    qpy_buffer = io.BytesIO()
    qpy.dump(circuit, qpy_buffer)
    measurements = []
    for item in circuit.data:
        if item.operation.name == "measure":
            measurements.append(
                {
                    "qubit_index": circuit.find_bit(item.qubits[0]).index,
                    "clbit_index": circuit.find_bit(item.clbits[0]).index,
                }
            )
    layout = getattr(circuit, "layout", None)
    return {
        "qpy_base64": base64.b64encode(qpy_buffer.getvalue()).decode("ascii"),
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "depth": circuit.depth(),
        "operation_counts": {
            str(name): int(count) for name, count in circuit.count_ops().items()
        },
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
        "transpilation_layout": _layout_record(layout, circuit),
    }


def _layout_record(layout: object, circuit: QuantumCircuit) -> dict[str, object]:
    if layout is None:
        return {
            "available": False,
            "reason": "Circuit has no Qiskit TranspileLayout metadata.",
        }

    result: dict[str, object] = {"available": True}
    for attribute in (
        "initial_index_layout",
        "final_index_layout",
        "initial_virtual_layout",
        "final_virtual_layout",
    ):
        method = getattr(layout, attribute, None)
        if callable(method):
            try:
                result[attribute] = json_safe(method())
            except Exception as error:
                result[attribute] = {
                    "available": False,
                    "reason": f"Layout extraction failed ({type(error).__name__}).",
                }
    for attribute in ("initial_layout", "final_layout"):
        raw_layout = getattr(layout, attribute, None)
        if raw_layout is None:
            result[attribute] = {"available": False}
            continue
        get_virtual_bits = getattr(raw_layout, "get_virtual_bits", None)
        if not callable(get_virtual_bits):
            result[attribute] = {
                "available": False,
                "reason": "Layout does not expose virtual-to-physical bit mapping.",
            }
            continue
        try:
            mapping = get_virtual_bits()
            result[attribute] = {
                "available": True,
                "mapping": [
                    {
                        "virtual_bit_index": _bit_index(bit, circuit),
                        "physical_bit_index": physical,
                    }
                    for bit, physical in mapping.items()
                ],
            }
        except Exception as error:
            result[attribute] = {
                "available": False,
                "reason": f"Layout extraction failed ({type(error).__name__}).",
            }
    return result


def _bit_index(bit: object, circuit: QuantumCircuit) -> int | None:
    try:
        return circuit.find_bit(bit).index
    except (KeyError, TypeError):
        raw_index = getattr(bit, "_index", None)
        return raw_index if isinstance(raw_index, int) else None


def qubo_record(formulation: object) -> dict[str, object]:
    problem = getattr(formulation, "problem", None)
    if problem is None:
        raise ProvenanceError("The execution package has no original QUBO problem.")
    objective = problem.objective
    variables = tuple(problem.variables)
    routes = tuple(getattr(formulation, "routes", ()))
    variable_names = tuple(getattr(formulation, "variable_names", ()))
    if not variables or len(variables) != len(routes) or len(variables) != len(variable_names):
        raise ProvenanceError("QUBO variables, names, and routes do not align.")

    route_variables = []
    for index, variable in enumerate(variables):
        route = routes[index]
        route_variables.append(
            {
                "index": index,
                "name": variable.name,
                "type": str(variable.vartype),
                "vehicle_id": route.plan.vehicle_id,
                "delivery_ids": list(route.plan.delivery_ids),
                "route_cost": float(route.total_cost),
            }
        )
    return {
        "signature": _qubo_signature(formulation),
        "variable_names": list(variable_names),
        "variables": route_variables,
        "delivery_ids": list(getattr(formulation, "delivery_ids", ())),
        "penalty_weight": float(formulation.penalty_weight),
        "penalty_constraint_semantics": {
            "delivery_exact_cover": [
                {
                    "delivery_id": delivery_id,
                    "route_variable_indexes": [
                        index
                        for index, route in enumerate(routes)
                        if delivery_id in route.plan.delivery_ids
                    ],
                    "required_selected_routes": 1,
                }
                for delivery_id in formulation.delivery_ids
            ],
            "vehicle_at_most_one_route": [
                {
                    "vehicle_id": vehicle_id,
                    "route_variable_indexes": [
                        index
                        for index, route in enumerate(routes)
                        if route.plan.vehicle_id == vehicle_id
                    ],
                    "maximum_selected_routes": 1,
                }
                for vehicle_id in dict.fromkeys(route.plan.vehicle_id for route in routes)
            ],
            "penalty_weight": float(formulation.penalty_weight),
            "encoded_as_quadratic_objective_penalties": True,
        },
        "objective": {
            "sense": str(objective.sense),
            "constant": float(objective.constant),
            "linear_coefficients": [
                {"variable": variables[index].name, "coefficient": float(value)}
                for index, value in sorted(objective.linear.to_dict().items())
            ],
            "quadratic_coefficients": [
                {
                    "variables": [variables[left].name, variables[right].name],
                    "coefficient": float(value),
                }
                for (left, right), value in sorted(objective.quadratic.to_dict().items())
            ],
        },
        "linear_constraints": [
            {
                "name": constraint.name,
                "sense": str(constraint.sense),
                "rhs": float(constraint.rhs),
                "coefficients": [
                    {"variable": variables[index].name, "coefficient": float(value)}
                    for index, value in sorted(constraint.linear.to_dict().items())
                ],
            }
            for constraint in problem.linear_constraints
        ],
        "quadratic_constraints": [
            {
                "name": constraint.name,
                "sense": str(constraint.sense),
                "rhs": float(constraint.rhs),
                "linear_coefficients": [
                    {"variable": variables[index].name, "coefficient": float(value)}
                    for index, value in sorted(constraint.linear.to_dict().items())
                ],
                "quadratic_coefficients": [
                    {
                        "variables": [variables[left].name, variables[right].name],
                        "coefficient": float(value),
                    }
                    for (left, right), value in sorted(
                        constraint.quadratic.to_dict().items()
                    )
                ],
            }
            for constraint in problem.quadratic_constraints
        ],
    }


def _qubo_signature(formulation: object) -> str:
    from ibm_qaoa_adapter import route_qubo_fingerprint

    return route_qubo_fingerprint(formulation)


def scenario_record(scenario: ScenarioProblem) -> dict[str, object]:
    return {
        "vehicles": json_safe([asdict(item) for item in scenario.vehicles]),
        "deliveries": json_safe([asdict(item) for item in scenario.deliveries]),
        "travel": {
            "distances_km": _travel_arcs(scenario.travel.distances_km),
            "durations_min": _travel_arcs(scenario.travel.durations_min),
        },
        "cost_weights": json_safe(asdict(scenario.cost_weights)),
        "coordinates": json_safe(scenario.coordinates),
        "location_names": json_safe(scenario.location_names),
        "traffic_condition": getattr(scenario, "traffic_condition"),
        "fuel_type": getattr(scenario, "fuel_type"),
        "fuel_price_per_unit": getattr(scenario, "fuel_price_per_unit"),
        "fuel_unit": getattr(scenario, "fuel_unit"),
        "data_source": getattr(scenario, "data_source"),
        "osrm_requested": getattr(scenario, "osrm_requested"),
        "osrm_active": getattr(scenario, "osrm_active"),
    }


def _travel_arcs(values: Mapping[tuple[str, str], float]) -> list[dict[str, object]]:
    return [
        {"origin": origin, "destination": destination, "value": float(value)}
        for (origin, destination), value in sorted(values.items())
    ]


def build_execution_manifest(
    dry_run: object,
    *,
    confirmation_timestamp: str,
) -> dict[str, object]:
    if getattr(dry_run, "ready", False) is not True:
        raise ProvenanceError("A successful hardware dry-run is required.")
    backend = getattr(dry_run, "backend", None)
    package = getattr(dry_run, "execution_package", None)
    transpiled_circuit = getattr(dry_run, "transpiled_circuit", None)
    if (
        backend is None
        or getattr(backend, "simulator", None) is not False
        or package is None
        or not isinstance(transpiled_circuit, QuantumCircuit)
        or not isinstance(getattr(package, "logical_circuit", None), QuantumCircuit)
        or not isinstance(getattr(package, "measured_circuit", None), QuantumCircuit)
        or getattr(package, "formulation", None) is None
        or getattr(package, "scenario", None) is None
        or getattr(package, "objective_scenario", None) is None
        or not getattr(package, "parameter_bindings", ())
        or not getattr(package, "route_variable_mapping", ())
        or not isinstance(getattr(package, "optimizer_metadata", None), Mapping)
    ):
        raise ProvenanceError("The execution package is missing required provenance.")
    optimizer_metadata = package.optimizer_metadata
    if (
        not optimizer_metadata.get("method")
        or not isinstance(optimizer_metadata.get("configuration"), Mapping)
        or not optimizer_metadata.get("initial_point")
        or optimizer_metadata.get("history") is None
    ):
        raise ProvenanceError("QAOA optimizer provenance is incomplete.")

    variables = list(package.route_variable_mapping)
    if sorted(binding.variable_index for binding in variables) != list(range(len(variables))):
        raise ProvenanceError("Logical route-variable ordering is incomplete.")
    if (
        package.problem_signature != _qubo_signature(package.formulation)
        or len(variables) != package.formulation.problem.get_num_vars()
        or package.logical_circuit.num_qubits != package.logical_qubits
        or package.measured_circuit.num_qubits != package.logical_qubits
        or package.logical_circuit.parameters
        or package.measured_circuit.parameters
    ):
        raise ProvenanceError(
            "The execution package's QUBO, parameters, and circuit do not align."
        )
    measured_wiring = {
        (
            package.measured_circuit.find_bit(instruction.qubits[0]).index,
            package.measured_circuit.find_bit(instruction.clbits[0]).index,
        )
        for instruction in package.measured_circuit.data
        if instruction.operation.name == "measure"
    }
    expected_wiring = {
        (binding.logical_qubit_index, binding.measured_bit_index)
        for binding in variables
    }
    if measured_wiring != expected_wiring:
        raise ProvenanceError(
            "Logical-to-classical mapping does not match the measured circuit."
        )
    run_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    formulation = package.formulation
    optimizer_metadata_record = json_safe(optimizer_metadata)
    return {
        "schema": MANIFEST_SCHEMA,
        "manifest_status": "PRE_SUBMISSION_CONFIRMED",
        "run_id": run_id,
        "project_name": PROJECT_NAME,
        "problem_statement": PROBLEM_STATEMENT,
        "created_at_utc": created_at,
        "user_confirmation": {
            "confirmed": True,
            "event": "Streamlit explicit REAL IBM hardware checkbox and submit button",
            "confirmed_at_utc": confirmation_timestamp,
        },
        "requested_execution": {
            "backend": backend.name,
            "shots": int(dry_run.shots),
            "transpile_optimization_level": 1,
        },
        "scenario": scenario_record(package.scenario),
        "objective_scenario": scenario_record(package.objective_scenario),
        "qubo": qubo_record(formulation),
        "qaoa": {
            "reps": int(package.qaoa_reps),
            "logical_qubit_count": int(package.logical_qubits),
            "parameters": [
                {"name": name, "value": float(value)}
                for name, value in package.parameter_bindings
            ],
            "parameter_source": package.parameter_source,
            "optimizer": optimizer_metadata_record,
            "logical_qubit_to_variable_mapping": [
                {
                    "variable_index": binding.variable_index,
                    "variable_name": binding.variable_name,
                    "logical_qubit_index": binding.logical_qubit_index,
                    "measured_bit_index": binding.measured_bit_index,
                    "vehicle_id": binding.vehicle_id,
                    "delivery_ids": list(binding.delivery_ids),
                }
                for binding in variables
            ],
            "bitstring_ordering": (
                "Qiskit counts display the highest classical-bit index at left "
                "and classical bit 0 at right; decoder reverses the string to "
                "index measured classical bits from 0."
            ),
        },
        "circuits": {
            "logical_bound_qaoa": circuit_record(package.logical_circuit),
            "logical_measured": circuit_record(package.measured_circuit),
            "representative_backend_transpiled": circuit_record(package.circuit),
            "selected_backend_transpiled_submitted": circuit_record(transpiled_circuit),
        },
        "source": {
            "git_revision": _git_revision(),
            "git_worktree_dirty": _git_worktree_dirty(),
            "source_file_sha256": _source_file_hashes(),
            "python": sys.version.split()[0],
            "packages": _package_versions(),
        },
    }


def write_pre_submission_manifest(
    manifest: Mapping[str, object],
    directory: Path | str | None = None,
) -> tuple[Path, str]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ProvenanceError("Execution manifest schema is invalid.")
    if manifest.get("manifest_status") != "PRE_SUBMISSION_CONFIRMED":
        raise ProvenanceError("Only a confirmed pre-submission manifest can be persisted.")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ProvenanceError("Execution manifest run ID is missing.")
    target_directory = Path(directory) if directory is not None else MANIFEST_DIRECTORY
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / f"quantumroute_run_{run_id}.json"
    contents = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with target.open("x", encoding="utf-8", newline="\n") as manifest_file:
        manifest_file.write(contents)
    return target, hashlib.sha256(contents.encode("utf-8")).hexdigest()


def write_execution_progress(
    state: object,
    directory: Path | str | None = None,
) -> Path:
    """Durably retain submission/result state separately from verified evidence."""
    run_id = getattr(state, "run_id", None)
    manifest_path = getattr(state, "manifest_path", None)
    manifest_sha256 = getattr(state, "manifest_sha256", None)
    manifest = getattr(state, "execution_manifest", None)
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(manifest_path, str)
        or not manifest_path
        or not isinstance(manifest_sha256, str)
        or not isinstance(manifest, Mapping)
    ):
        raise ProvenanceError("Execution progress requires its persisted pre-submission manifest.")

    routes = []
    for route in getattr(state, "decoded_routes", ()):
        plan = getattr(route, "plan", None)
        routes.append(
            {
                "vehicle_id": getattr(plan, "vehicle_id", None),
                "delivery_ids": list(getattr(plan, "delivery_ids", ())),
                "distance_km": getattr(route, "distance_km", None),
                "travel_time_min": getattr(route, "travel_time_min", None),
                "objective_cost_model_units": getattr(route, "total_cost", None),
            }
        )
    record = {
        "schema": PROGRESS_SCHEMA,
        "record_type": "IBM_HARDWARE_EXECUTION_PROGRESS_NOT_VERIFIED",
        "run_id": run_id,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha256,
        "job_id": getattr(state, "job_id", None),
        "requested_backend": getattr(state, "backend_name", None),
        "actual_backend": getattr(state, "actual_backend_name", None),
        "actual_backend_is_simulator": getattr(
            state,
            "actual_backend_is_simulator",
            None,
        ),
        "requested_shots": getattr(state, "shots", None),
        "returned_shots": getattr(state, "shots_returned", None),
        "status": getattr(state, "status", None),
        "submitted_at_utc": getattr(state, "submitted_at", None),
        "runtime_job_creation_timestamp_utc": getattr(
            state,
            "runtime_submitted_at",
            None,
        ),
        "completed_at_utc": getattr(state, "completed_at", None),
        "submission_attempted": getattr(state, "submission_attempted", False),
        "measurement_result_received": getattr(
            state,
            "measurement_result_received",
            False,
        ),
        "measurement_counts": dict(getattr(state, "raw_counts", ())),
        "measurement_registers": json_safe(
            getattr(state, "result_register_counts", {})
        ),
        "most_frequent_bitstring": getattr(state, "most_frequent_bitstring", None),
        "selected_solution_bitstring": getattr(
            state,
            "selected_solution_bitstring",
            None,
        ),
        "qubo_objective_value": getattr(state, "qubo_objective_value", None),
        "route_validation": {
            "passed": getattr(state, "route_valid", None),
            "message": getattr(state, "route_message", None),
        },
        "decoded_routes": routes if getattr(state, "route_valid", None) else None,
        "message": getattr(state, "message", None),
        "error_or_limitation": getattr(state, "error_info", None),
    }
    target_directory = Path(directory) if directory is not None else PROGRESS_DIRECTORY
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / f"quantumroute_run_{run_id}.json"
    temporary = target.with_suffix(".json.tmp")
    record = json_safe(record)
    contents = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary.write_text(contents, encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision or None


def _git_worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())


def _source_file_hashes() -> dict[str, str | None]:
    source_root = Path(__file__).resolve().parent
    paths = (
        "ibm_execution.py",
        "ibm_hardware_evidence.py",
        "ibm_hardware_provenance.py",
        "ibm_qaoa_adapter.py",
        "ibm_qaoa_parameters.py",
        "ibm_quantum.py",
        "quantum_route_optimisation/classical.py",
        "quantum_route_optimisation/feasibility.py",
        "quantum_route_optimisation/models.py",
        "quantum_route_optimisation/qaoa.py",
        "quantum_route_optimisation/qubo.py",
        "route_dashboard/currency.py",
        "route_dashboard/demo_mode.py",
        "route_dashboard/dynamic.py",
        "route_dashboard/objectives.py",
        "route_dashboard/optimization.py",
        "route_dashboard/scenario.py",
        "route_dashboard/ui.py",
    )
    hashes: dict[str, str | None] = {}
    for relative_path in paths:
        try:
            hashes[relative_path] = hashlib.sha256(
                (source_root / relative_path).read_bytes()
            ).hexdigest()
        except OSError:
            hashes[relative_path] = None
    return hashes


def _package_versions() -> dict[str, str | None]:
    names = (
        "qiskit",
        "qiskit-ibm-runtime",
        "qiskit-aer",
        "qiskit-algorithms",
        "qiskit-optimization",
    )
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions
